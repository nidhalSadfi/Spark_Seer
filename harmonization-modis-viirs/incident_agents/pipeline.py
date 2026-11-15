"""One run: plan -> fetch each source -> filter -> judge -> locate -> tickets -> satellite check.

Every step reports an event (see events.Reporter). A source that fails is reported
and skipped; the run continues with the others.
"""

from __future__ import annotations

import unicodedata
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone

from . import config
from .events import Reporter
from .geo import Geocoder, in_box
from .judge import judge
from .llm import Groq
from .planner import Plan, PlanError, make_plan
from .sources import Record, SourceError, parse_iso, utc_iso
from .sources.bluesky import Bluesky
from .sources.firms import Firms
from .sources.gdelt import Gdelt
from .sources.kanari import ATTRIBUTION, Kanari
from .sources.twitter import TwitterAPI
from .store import Store
from .tickets import Located, satellite_check, upsert

ALL_SOURCES = ("kanari", "bluesky", "x", "gdelt")
# Saharan gas flares pass satellite vegetation filters; the main project cuts at 34°N for the same reason.
FLARE_COUNTRIES = {"DZ", "TN", "LY"}
FLARE_LAT = 34.0


@dataclass
class Options:
    sources: tuple[str, ...] = ALL_SOURCES
    satellite_only_tickets: bool = False
    firms_check: bool = True


@dataclass
class Clients:
    llm: Groq
    geocoder: Geocoder = field(default_factory=Geocoder)
    kanari: Kanari = field(default_factory=Kanari)
    bluesky: Bluesky = field(default_factory=Bluesky)
    x: TwitterAPI = field(default_factory=TwitterAPI)
    gdelt: Gdelt = field(default_factory=Gdelt)
    firms: Firms | None = None


def _fold(s: str) -> str:
    s = unicodedata.normalize("NFKD", (s or "").casefold())
    return "".join(c for c in s if not unicodedata.combining(c))


def mentions_terms(text: str, terms: list[str]) -> bool:
    t = _fold(text)
    return any(_fold(term) in t for term in terms)


def in_window(rec: Record, since: datetime) -> bool:
    d = parse_iso(rec.published_at)
    return d is None or d >= since


def fetch_all(plan: Plan, c: Clients, opts: Options, store: Store, report: Reporter,
              since_iso: str) -> list[Record]:
    caps = config.caps()
    records: list[Record] = []
    countries = plan.countries

    if "kanari" in opts.sources:
        report("fetch", "running", "verified public signals", source="kanari")
        try:
            recs = [r for r in c.kanari.signal_records(plan.hours, countries)
                    if any(in_box(r.lat, r.lon, z.box) for z in plan.zones if z.country == r.country)]
            records += recs
            report("fetch", "ok", f"{len(recs)} posts in the zones ({ATTRIBUTION})", source="kanari",
                   data={"count": len(recs)})
        except SourceError as e:
            report("fetch", "error", str(e), source="kanari")

    if "bluesky" in opts.sources:
        if not c.bluesky.available:
            report("fetch", "skipped", "no Bluesky handle / app password", source="bluesky")
        else:
            for lang, terms in plan.keywords.items():
                report("fetch", "running", f"search {lang}: {', '.join(terms)}", source="bluesky")
                try:
                    recs = c.bluesky.search(terms, lang, since_iso, caps.BLUESKY_LIMIT_PER_QUERY)
                    records += recs
                    report("fetch", "ok", f"{lang}: {len(recs)} posts", source="bluesky", data={"count": len(recs)})
                except SourceError as e:
                    report("fetch", "error", str(e), source="bluesky")

    if "x" in opts.sources:
        if not c.x.available:
            report("fetch", "skipped", "no TwitterAPI.io key", source="x")
        else:
            pages_left = caps.TWITTER_PAGES_PER_RUN
            for lang, terms in plan.keywords.items():
                day_left = caps.TWITTER_PAGES_PER_DAY - store.usage_today("x")
                budget = min(pages_left, day_left, 1)
                if budget <= 0:
                    report("fetch", "skipped", f"{lang}: page cap reached (run {caps.TWITTER_PAGES_PER_RUN}, "
                           f"day {caps.TWITTER_PAGES_PER_DAY})", source="x")
                    continue
                report("fetch", "running", f"search {lang}", source="x")
                try:
                    recs, pages = c.x.search(terms, lang, since_iso, budget)
                    store.add_usage("x", pages)
                    pages_left -= pages
                    records += recs
                    report("fetch", "ok", f"{lang}: {len(recs)} posts ({pages} page)", source="x",
                           data={"count": len(recs), "pages": pages})
                except SourceError as e:
                    store.add_usage("x", 1)
                    report("fetch", "error", str(e), source="x")

    if "gdelt" in opts.sources:
        for cc in countries:
            from .planner import LANGS_BY_COUNTRY
            for lang in LANGS_BY_COUNTRY.get(cc, list(plan.keywords)[:1]):
                terms = plan.keywords.get(lang)
                if not terms:
                    continue
                report("fetch", "running", f"{cc} news in {lang}", source="gdelt")
                try:
                    recs = c.gdelt.search(terms, lang, cc, plan.hours, caps.GDELT_MAX_RECORDS)
                    for r in recs:
                        r.country = cc
                    records += recs
                    report("fetch", "ok", f"{cc}/{lang}: {len(recs)} headlines", source="gdelt",
                           data={"count": len(recs)})
                except SourceError as e:
                    report("fetch", "error", str(e), source="gdelt")
    return records


def filter_records(records: list[Record], plan: Plan, since: datetime, report: Reporter) -> list[Record]:
    """Drop items outside the time window, items that do not contain any search term, and duplicate URLs.
    A Kanari signal wins over the same Bluesky post because it is already located."""
    all_terms = [t for ts in plan.keywords.values() for t in ts]
    by_url: dict[str, Record] = {}
    dropped = {"old": 0, "no_term": 0, "duplicate": 0}
    for r in sorted(records, key=lambda r: r.kind != "verified_signal"):
        if not in_window(r, since):
            dropped["old"] += 1
            continue
        if r.kind != "verified_signal" and not mentions_terms(r.text, all_terms):
            dropped["no_term"] += 1
            continue
        if r.url in by_url:
            dropped["duplicate"] += 1
            by_url[r.url].meta.setdefault("also_found_by", []).append(r.source)
            continue
        by_url[r.url] = r
    kept = list(by_url.values())
    report("filter", "ok", f"kept {len(kept)} of {len(records)} (dropped {dropped})", data=dropped)
    return kept


def locate(rec: Record, verdict: dict, plan: Plan, geocoder: Geocoder) -> Located | None:
    if rec.kind == "verified_signal" and rec.lat is not None:
        return Located(rec, verdict, rec.place or "?", rec.country, rec.lat, rec.lon, "town", 10.0)
    phrase, cc = verdict.get("place_phrase"), verdict.get("country")
    if not phrase or not cc:
        return None
    zones = [z for z in plan.zones if z.country == cc]
    for z in zones:
        p = geocoder.place(phrase, cc, z.box)
        if p:
            return Located(rec, verdict, p.name, cc, p.lat, p.lon, p.precision, p.radius_km)
    return None


def run(query: str, hours: int, store: Store, clients: Clients | None = None, opts: Options | None = None,
        schedule_id: int | None = None, echo: bool = True) -> dict:
    opts = opts or Options()
    run_id = store.start_run(query, hours, schedule_id)
    report = Reporter(store, run_id, echo)
    summary: dict = {"run_id": run_id}
    plan = None
    try:
        report("run", "started", f"query: {query} | last {hours} h")
        clients = clients or Clients(llm=Groq())
        if hasattr(clients.llm, "on_call"):
            clients.llm.on_call = lambda: store.add_usage("groq")
        if store.usage_today("groq") >= config.caps().GROQ_CALLS_PER_DAY:
            raise RuntimeError("daily Groq call cap reached (free tier: 1,000 requests per day); try tomorrow")
        if opts.firms_check and clients.firms is None:
            clients.firms = Firms()
        plan = make_plan(query, hours, clients.llm, clients.geocoder, report)
        now = datetime.now(timezone.utc)
        since = now - timedelta(hours=hours)
        since_iso = utc_iso(since.isoformat())

        records = fetch_all(plan, clients, opts, store, report, since_iso)
        records = filter_records(records, plan, since, report)

        caps = config.caps()
        verdicts: dict[str, dict] = {}
        signals = [r for r in records if r.kind == "verified_signal"]
        for r in signals:  # already checked twice by Kanari's own models
            verdicts[r.record_id] = {"category": "active_fire_report", "timing": "recent", "firsthand": "relayed",
                                     "place_phrase": r.place, "country": r.country, "ticketable": True,
                                     "reason": "verified by kanari.io"}
        candidates = [r for r in records if r.kind != "verified_signal"]
        cached = store.cached_verdicts([r.url for r in candidates])
        for r in candidates:
            if r.url in cached:
                cv = dict(cached[r.url])
                # the cached country check must still respect this run's countries
                cv["ticketable"] = bool(cv.get("ticketable")) and cv.get("country") in plan.countries
                verdicts[r.record_id] = cv
        if cached:
            report("judge", "ok", f"reused {len(cached)} verdicts from earlier runs (no model call)")
        to_judge = sorted((r for r in candidates if r.url not in cached),
                          key=lambda r: r.published_at or "", reverse=True)[:caps.JUDGE_MAX_ITEMS_PER_RUN]
        allow = lambda: store.usage_today("groq") < caps.GROQ_CALLS_PER_DAY
        for src in ("bluesky", "x", "gdelt"):
            batch = [r for r in to_judge if r.source == src]
            if not batch:
                continue
            report("judge", "running", f"{len(batch)} items with {clients.llm.model}", source=src)
            new = judge(batch, plan.countries, clients.llm, caps.JUDGE_BATCH, report, src, allow)
            verdicts.update(new)
            for r in batch:
                if r.record_id in new:
                    store.save_verdict(r.url, new[r.record_id], clients.llm.model)
            n_ok = sum(1 for r in batch if verdicts.get(r.record_id, {}).get("ticketable"))
            cats: dict[str, int] = {}
            for r in batch:
                key = verdicts.get(r.record_id, {}).get("category", "no_verdict")
                cats[key] = cats.get(key, 0) + 1
            report("judge", "ok", f"{n_ok} of {len(batch)} look like a current fire report in scope; {cats}",
                   source=src, data={"categories": cats, "ticketable": n_ok,
                                     "rejected": [{"url": r.url, "text": r.text[:140],
                                                   **{k: verdicts.get(r.record_id, {}).get(k) for k in
                                                      ("category", "timing", "country", "place_phrase", "reason")}}
                                                  for r in batch if not verdicts.get(r.record_id, {}).get("ticketable")][:40]})

        located, unlocated_items = [], []
        for r in records:
            v = verdicts.get(r.record_id)
            if not v or not v.get("ticketable"):
                continue
            loc = locate(r, v, plan, clients.geocoder)
            if loc is None:
                unlocated_items.append({"url": r.url, "source": r.source, "text": r.text[:200],
                                        "place_phrase": v.get("place_phrase"), "country": v.get("country")})
            else:
                located.append(loc)
        unlocated = len(unlocated_items)
        report("locate", "ok", f"{len(located)} located, {unlocated} relevant but without a usable place in the zones",
               data={"unlocated": unlocated_items})

        if opts.satellite_only_tickets and "kanari" in opts.sources:
            seen_clusters: set = set()
            for z in plan.zones:
                try:
                    box = None if z.kind == "country" else z.box
                    fires = clients.kanari.fires_in(z.country, box, hours)
                    flares = [f for f in fires if z.country in FLARE_COUNTRIES and (f.get("lat") or 0) < FLARE_LAT]
                    if flares:
                        report("filter", "ok", f"skipped {len(flares)} satellite clusters south of {FLARE_LAT}°N in "
                               f"{z.country} (oil and gas fields; likely flares, not vegetation fires)",
                               source="kanari")
                    keep = sorted((f for f in fires if f not in flares), key=lambda f: -(f.get("detections") or 0))
                    if len(keep) > caps.SATELLITE_TICKETS_PER_ZONE:
                        report("filter", "ok", f"kept the {caps.SATELLITE_TICKETS_PER_ZONE} largest of {len(keep)} "
                               f"satellite clusters in {z.label}", source="kanari")
                    for f in keep[:caps.SATELLITE_TICKETS_PER_ZONE]:
                        if f.get("lat") is None or f.get("id") in seen_clusters:
                            continue  # a cluster can fall in two overlapping zones
                        seen_clusters.add(f.get("id"))
                        rec = Record(source="kanari_satellite", kind="satellite", record_id=str(f.get("id")),
                                     url=f.get("mapUrl") or f"https://kanari.io/en?ev={f.get('id')}",
                                     text=f"{f.get('detections')} satellite detections near {f.get('place')}",
                                     published_at=utc_iso(f.get("firstSeen")), fetched_at=utc_iso(now.isoformat()),
                                     author="kanari.io", meta={"attribution": ATTRIBUTION})
                        v = {"category": "satellite", "place_phrase": f.get("place"), "country": z.country}
                        located.append(Located(rec, v, f.get("place") or "?", z.country, f["lat"], f["lon"],
                                               "cluster", 3.0))
                    report("fetch", "ok", f"satellite clusters for {z.label}", source="kanari")
                except SourceError as e:
                    report("fetch", "error", str(e), source="kanari")

        open_tickets = store.recent_tickets(utc_iso((now - timedelta(hours=72)).isoformat()))
        touched, created = [], 0
        for loc in located:
            tid, new = upsert(store, loc, run_id, open_tickets)
            created += new
            if tid not in touched:
                touched.append(tid)
            report("ticket", "created" if new else "updated",
                   f"#{tid} {loc.place} ({loc.country}) from {loc.rec.source}", source=loc.rec.source,
                   data={"ticket_id": tid, "url": loc.rec.url})

        statuses = {}
        for i, tid in enumerate(touched):
            firms = clients.firms if (opts.firms_check and i < caps.FIRMS_CHECKS_PER_RUN) else None
            st = satellite_check(store, tid, clients.kanari, firms, hours, report)
            statuses[st] = statuses.get(st, 0) + 1
            report("satellite", "ok", f"#{tid}: {st}", data={"ticket_id": tid, "status": st})

        summary.update(records=len(records), located=len(located), unlocated=unlocated, tickets_touched=len(touched),
                       tickets_created=created, statuses=statuses, llm_tokens=clients.llm.total_tokens)
        report("run", "done", f"{created} new and {len(touched) - created} updated tickets; "
               f"{clients.llm.total_tokens} model tokens", data=summary)
        store.finish_run(run_id, "done", plan.to_dict(), summary)
    except (PlanError, Exception) as e:  # report every failure as an event, then re-raise nothing
        summary["error"] = config.redact(f"{type(e).__name__}: {e}")
        report("run", "error", summary["error"])
        store.finish_run(run_id, "error", plan.to_dict() if plan else None, summary)
    return summary
