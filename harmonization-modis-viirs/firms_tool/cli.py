from __future__ import annotations

import argparse
import getpass
import json
import os
import sys
import time
from datetime import date, datetime, timezone
from pathlib import Path

from . import area_api, portal, public
from .config import ConfigError, load_config, plan_jobs, skipped_sensors
from .sensors import SENSORS, get_sensor

DEFAULT_SESSION = Path(os.environ.get("FIRMS_SESSION_FILE", ".firms_session.json"))


def _print_json(obj) -> None:
    print(json.dumps(obj, indent=2, default=str))


def _jobs_or_exit(args):
    cfg = load_config(args.config)
    jobs = plan_jobs(cfg)
    for msg in skipped_sensors(cfg):
        print(f"skip  {msg}", file=sys.stderr)
    if not jobs:
        sys.exit("nothing to request: no sensor coverage overlaps the date range")
    return cfg, jobs


def cmd_sensors(args) -> None:
    for s in SENSORS.values():
        end = s.coverage_end or "present"
        arch = s.archive_product or "-"
        print(f"{s.key:13} {s.portal_code:7} {s.coverage_start}..{end!s:10}  archive={arch:12} api={s.api_sp}  {s.label}")


def _render_plan(cfg, jobs) -> None:
    print(f"{len(jobs)} request(s) -> {portal.BASE}{cfg.form['endpoint']} "
          f"[{cfg.form['method']} {cfg.form['encoding']}]"
          + ("" if cfg.form["verified"] else "  (field mapping NOT verified; run inspect-form)"),
          file=sys.stderr)
    out = []
    for i, j in enumerate(jobs, 1):
        out.append({"n": i, "sensor": j.sensor.key, "start": j.start, "end": j.end, "days": j.days,
                    "payload": j.payload, "warnings": j.warnings})
    _print_json(out)


def cmd_plan(args) -> None:
    cfg, jobs = _jobs_or_exit(args)
    _render_plan(cfg, jobs)


def cmd_submit(args) -> None:
    cfg, jobs = _jobs_or_exit(args)
    if not args.yes:
        _render_plan(cfg, jobs)
        print("\nDRY RUN: nothing was sent. Re-run with --yes to submit.", file=sys.stderr)
        return
    if not cfg.email:
        sys.exit("refusing to submit: set `email` in the config")
    if not cfg.form["verified"] and not args.allow_unverified_form:
        sys.exit("refusing to submit: form mapping is unverified. Run `firms inspect-form`, update the "
                 "`form:` section to match, and set `form.verified: true` (or pass --allow-unverified-form).")
    s = portal.load_session(args.session)
    me = portal.require_login(s)
    print(f"logged in as {me.get('email') or me.get('id')}", file=sys.stderr)
    log = Path(args.log)
    for i, j in enumerate(jobs, 1):
        res = portal.submit(s, cfg.form, j.payload)
        rec = {"at": datetime.now(timezone.utc).isoformat(), "sensor": j.sensor.key,
               "start": j.start.isoformat(), "end": j.end.isoformat(), "payload": j.payload, "response": res}
        with log.open("a") as fh:
            fh.write(json.dumps(rec, default=str) + "\n")
        print(f"[{i}/{len(jobs)}] {j.sensor.key} {j.start}..{j.end}: HTTP {res['status_code']} {str(res['body'])[:200]}")
        if res["status_code"] >= 400:
            sys.exit("stopping after a failed submission; see " + str(log))
        if i < len(jobs):
            time.sleep(args.delay)


def cmd_login(args) -> None:
    s = portal.new_session()
    if args.earthdata:
        portal.login_earthdata(s, os.environ.get("EARTHDATA_USERNAME"), os.environ.get("EARTHDATA_PASSWORD"))
    else:
        email = args.email or input("FIRMS email: ")
        portal.login_email(s, email, lambda e: getpass.getpass(f"6-digit code emailed to {e}: "))
    portal.save_session(s, args.session)
    me = portal.whoami(s)
    print(f"logged in as {me.get('email') or me.get('id')}; session saved to {args.session}")


def cmd_whoami(args) -> None:
    me = portal.whoami(portal.load_session(args.session))
    if not me:
        sys.exit("not logged in")
    _print_json(me)


def cmd_inspect_form(args) -> None:
    s = portal.load_session(args.session)
    portal.require_login(s)
    report = portal.inspect_form(s)
    if args.out:
        Path(args.out).write_text(json.dumps(report, indent=2))
        print(f"wrote {args.out}", file=sys.stderr)
    _print_json(report)


def cmd_status(args) -> None:
    rows = portal.list_requests(portal.load_session(args.session))
    if args.json:
        _print_json(rows)
        return
    if not rows:
        print("no requests listed")
    for r in rows:
        print(" | ".join(r["cells"]))
        for link in r["links"]:
            print(f"    {link}")


def cmd_download(args) -> None:
    s = portal.load_session(args.session)
    links = portal.downloadable_links(portal.list_requests(s))
    if not links:
        print("no download links on list.php yet")
    for url in links:
        if args.dry_run:
            print(f"would download {url}")
            continue
        print(f"downloaded {portal.download(s, url, Path(args.out))}")


def _years(spec: str) -> list[int]:
    if "-" in spec:
        a, b = spec.split("-")
        return list(range(int(a), int(b) + 1))
    return [int(y) for y in spec.split(",")]


def cmd_archive(args) -> None:
    sensors = [get_sensor(k) for k in args.sensors]
    if args.config:
        cfg = load_config(args.config)
        sensors = sensors or cfg.sensors
        years = list(range(cfg.start.year, cfg.end.year + 1))
        countries = args.countries or [cfg.area.archive_country or ("all" if cfg.area.type != "country" else None)]
        if None in countries:
            sys.exit("set area.archive_country (archive file name, e.g. United_States) or pass --countries")
    else:
        years = _years(args.years)
        countries = args.countries or ["all"]
    index = public.archive_index()
    s = portal.new_session()
    for sensor in sensors:
        if not sensor.archive_product:
            print(f"skip  {sensor.key}: not in yearly archive (use a portal request or the area API)", file=sys.stderr)
            continue
        for year in years:
            for country in countries:
                url = public.archive_url(sensor, year, country)
                name = url.rsplit("/", 1)[1]
                if name not in index:
                    print(f"skip  {name}: not published", file=sys.stderr)
                    continue
                if args.dry_run:
                    print(url)
                else:
                    print(f"downloaded {portal.download(s, url, Path(args.out) / sensor.key)}")


NO_KEY = ("no MAP_KEY: set FIRMS_MAP_KEY_1 (or FIRMS_MAP_KEY) or pass --map-key "
          "(free: https://firms.modaps.eosdis.nasa.gov/api/map_key/)")


def _client_or_exit(args) -> area_api.Client:
    key = area_api.resolve_map_key(args.map_key)
    if not key:
        sys.exit(NO_KEY)
    return area_api.Client(key)


def cmd_mapkey_status(args) -> None:
    _print_json(_client_or_exit(args).status())


def cmd_availability(args) -> None:
    for source, (lo, hi) in sorted(_client_or_exit(args).availability().items()):
        print(f"{source:18} {lo} .. {hi}")


def cmd_area_api(args) -> None:
    key = area_api.resolve_map_key(args.map_key)
    if args.config:
        cfg = load_config(args.config)
        if cfg.area.type not in ("bbox", "world"):
            sys.exit("area API needs area.type: bbox or world (the country endpoint is not working)")
        bbox = "world" if cfg.area.type == "world" else area_api.bbox_arg(cfg.area.bbox)
        start, end, sensors = cfg.start, cfg.end, cfg.sensors
    else:
        if not (args.bbox and args.start and args.end):
            sys.exit("pass --config, or --bbox, --start and --end")
        bbox = args.bbox if args.bbox == "world" else area_api.bbox_arg(tuple(float(v) for v in args.bbox.split(",")))
        start, end = date.fromisoformat(args.start), date.fromisoformat(args.end)
        sensors = [get_sensor(k) for k in args.sensors]
    if not key and not args.dry_run:
        sys.exit(NO_KEY)
    client = area_api.Client(key or "")
    availability = None
    if key and args.mode == "auto":
        availability = client.availability()
    elif args.mode == "auto":
        print("note: no key, so SP/NRT split is unknown; planning SP only", file=sys.stderr)

    cache = Path(args.out) / "chunks"
    for sensor in sensors:
        windows = area_api.plan_sensor(sensor, start, end, args.mode, availability)
        if not windows:
            print(f"skip  {sensor.key}: no data for {start}..{end}", file=sys.stderr)
            continue
        counts = area_api.estimate(windows)
        cached = sum(area_api.cache_path(cache, bbox, w).exists() for w in windows)
        tx = area_api.estimate_transactions(windows, bbox)
        floor_min = tx / area_api.DEFAULT_LIMIT * area_api.LIMIT_WINDOW_S / 60
        print(f"# {sensor.key}: {len(windows)} call(s) {counts}, {cached} already cached, "
              f"~{tx} transactions (rate-limit floor ~{floor_min:.0f} min)", file=sys.stderr)
        if args.dry_run:
            for w in windows:
                print(area_api.Client("<MAP_KEY>").area_url(w, bbox))
            continue
        paths = []
        for i, (w, path, rows, was_cached) in enumerate(
                area_api.download(client, windows, bbox, cache, args.delay, args.check_every, args.reserve), 1):
            paths.append(path)
            tag = "cached" if was_cached else "fetched"
            print(f"  [{i}/{len(windows)}] {w.source} {w.start}..{w.end}: {rows} rows ({tag})", file=sys.stderr)
        dest = Path(args.out) / f"{sensor.key}_{bbox.replace(',', '_')}_{start}_{end}.csv"
        print(f"{sensor.key}: {area_api.merge(paths, dest)} rows -> {dest}")


def cmd_compare_archive(args) -> None:
    from .compare import compare

    client = _client_or_exit(args)
    day = date.fromisoformat(args.day)
    for key in args.sensors:
        _print_json(compare(client, get_sensor(key), day, args.country))


def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(prog="firms", description="NASA FIRMS archive download helper")
    p.add_argument("--session", type=Path, default=DEFAULT_SESSION, help="saved session cookie file")
    sub = p.add_subparsers(dest="cmd", required=True)

    sub.add_parser("sensors", help="list supported sensors and coverage").set_defaults(func=cmd_sensors)

    sp = sub.add_parser("plan", help="expand a config into requests and print the exact payloads")
    sp.add_argument("config")
    sp.set_defaults(func=cmd_plan)

    sp = sub.add_parser("submit", help="submit planned requests (dry run unless --yes)")
    sp.add_argument("config")
    sp.add_argument("--yes", action="store_true", help="actually send the requests")
    sp.add_argument("--allow-unverified-form", action="store_true")
    sp.add_argument("--delay", type=float, default=15.0, help="seconds between submissions")
    sp.add_argument("--log", default="submissions.jsonl")
    sp.set_defaults(func=cmd_submit)

    sp = sub.add_parser("login", help="log in (email one-time code by default) and save the session")
    sp.add_argument("--email")
    sp.add_argument("--earthdata", action="store_true", help="Earthdata Login via ~/.netrc or EARTHDATA_* env")
    sp.set_defaults(func=cmd_login)

    sub.add_parser("whoami", help="show the logged-in FIRMS user").set_defaults(func=cmd_whoami)

    sp = sub.add_parser("inspect-form", help="dump create.php form fields and endpoints (needs login)")
    sp.add_argument("--out", help="also write the report to this JSON file")
    sp.set_defaults(func=cmd_inspect_form)

    sp = sub.add_parser("status", help="show requests listed on list.php")
    sp.add_argument("--json", action="store_true")
    sp.set_defaults(func=cmd_status)

    sp = sub.add_parser("download", help="download every file linked from list.php")
    sp.add_argument("--out", default="data/requests")
    sp.add_argument("--dry-run", action="store_true")
    sp.set_defaults(func=cmd_download)

    sp = sub.add_parser("archive", help="download public yearly per-country files (no login)")
    sp.add_argument("--config")
    sp.add_argument("--sensors", nargs="*", default=[], choices=list(SENSORS))
    sp.add_argument("--years", default=str(date.today().year - 1), help="e.g. 2019-2023 or 2019,2021")
    sp.add_argument("--countries", nargs="*", help="archive names, e.g. Australia United_States, or 'all'")
    sp.add_argument("--out", default="data/archive")
    sp.add_argument("--dry-run", action="store_true")
    sp.set_defaults(func=cmd_archive)

    key_help = "MAP_KEY (default: $FIRMS_MAP_KEY_1, then $FIRMS_MAP_KEY)"

    sp = sub.add_parser("mapkey-status", help="show MAP_KEY transaction usage")
    sp.add_argument("--map-key", help=key_help)
    sp.set_defaults(func=cmd_mapkey_status)

    sp = sub.add_parser("availability", help="show SP/NRT date ranges per source")
    sp.add_argument("--map-key", help=key_help)
    sp.set_defaults(func=cmd_availability)

    sp = sub.add_parser("area-api", help="fetch a bbox via the area API (5-day windows, resumable)")
    sp.add_argument("--config")
    sp.add_argument("--map-key", help=key_help)
    sp.add_argument("--sensors", nargs="*", default=["viirs_snpp"], choices=list(SENSORS))
    sp.add_argument("--bbox", help="west,south,east,north, or 'world'")
    sp.add_argument("--start")
    sp.add_argument("--end")
    sp.add_argument("--mode", choices=["auto", "sp", "nrt"], default="auto",
                    help="auto = SP up to its last date, then NRT (needs the key for availability)")
    sp.add_argument("--out", default="data/area_api")
    sp.add_argument("--delay", type=float, default=0.5, help="seconds between calls")
    sp.add_argument("--check-every", type=int, default=25, help="check key budget every N calls")
    sp.add_argument("--reserve", type=int, default=500, help="keep this many transactions free on top of the next call's cost")
    sp.add_argument("--dry-run", action="store_true", help="print the windows/URLs (key redacted) and exit")
    sp.set_defaults(func=cmd_area_api)

    sp = sub.add_parser("compare-archive", help="compare one day of area-API SP rows with the yearly archive CSV")
    sp.add_argument("--map-key", help=key_help)
    sp.add_argument("--sensors", nargs="*", default=["modis_c61", "viirs_snpp", "viirs_noaa20"], choices=list(SENSORS))
    sp.add_argument("--day", required=True, help="YYYY-MM-DD")
    sp.add_argument("--country", default="Australia", help="archive country file name")
    sp.set_defaults(func=cmd_compare_archive)
    return p


def main(argv: list[str] | None = None) -> None:
    args = build_parser().parse_args(argv)
    try:
        args.func(args)
    except (ConfigError, portal.AuthError, area_api.ApiError, ValueError) as e:
        sys.exit(f"error: {e}")


if __name__ == "__main__":
    main()
