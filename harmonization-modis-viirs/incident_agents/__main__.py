"""CLI.

  python -m incident_agents run "forest fires in France" --hours 24
  python -m incident_agents replay "France" --from 2026-08-25 --to 2026-08-31   # past window
  python -m incident_agents serve            # page at http://127.0.0.1:8765, scheduler inside
  python -m incident_agents schedule-loop    # scheduler only, no page
  python -m incident_agents tickets          # print stored tickets
"""

from __future__ import annotations

import argparse
import json
import sys

from .pipeline import ALL_SOURCES, Options, replay, run
from .store import Store


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(prog="incident_agents")
    sub = p.add_subparsers(dest="cmd", required=True)
    r = sub.add_parser("run", help="one run now")
    r.add_argument("query")
    r.add_argument("--hours", type=int, default=24)
    r.add_argument("--sources", default=",".join(ALL_SOURCES))
    r.add_argument("--satellite-only", action="store_true", help="also open tickets from satellite clusters alone")
    r.add_argument("--no-firms", action="store_true")
    rp = sub.add_parser("replay", help="rebuild a past window from archived fires (Kanari archive starts 2026-08-03)")
    rp.add_argument("query")
    rp.add_argument("--from", dest="start", required=True, help="YYYY-MM-DD")
    rp.add_argument("--to", dest="end", required=True, help="YYYY-MM-DD, before today")
    rp.add_argument("--max-fires", type=int, default=None)
    rp.add_argument("--sources", default="bluesky,x")
    rp.add_argument("--no-firms", action="store_true")
    s = sub.add_parser("serve", help="page + scheduler on 127.0.0.1")
    s.add_argument("--port", type=int, default=8765)
    s.add_argument("--no-scheduler", action="store_true")
    sub.add_parser("schedule-loop", help="run due schedules forever")
    t = sub.add_parser("tickets")
    t.add_argument("--limit", type=int, default=20)
    a = p.parse_args(argv)

    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8")
        sys.stderr.reconfigure(encoding="utf-8")

    if a.cmd == "run":
        opts = Options(sources=tuple(x for x in a.sources.split(",") if x), satellite_only_tickets=a.satellite_only,
                       firms_check=not a.no_firms)
        print(json.dumps(run(a.query, a.hours, Store(), opts=opts), ensure_ascii=False, indent=1))
    elif a.cmd == "replay":
        opts = Options(sources=tuple(x for x in a.sources.split(",") if x), firms_check=not a.no_firms)
        print(json.dumps(replay(a.query, a.start, a.end, Store(), opts=opts, max_fires=a.max_fires),
                         ensure_ascii=False, indent=1))
    elif a.cmd == "tickets":
        for t in Store().tickets_with_sources(a.limit):
            print(f"#{t['id']} [{t['status']}]{' REPLAY' if t.get('mode') == 'replay' else ''} {t['title']} ({t['country']}, {t['precision']} ±{t['radius_km']} km) "
                  f"{t['first_seen']} .. {t['last_seen']}")
            for s_ in t["sources"]:
                print(f"    {s_['source']:<14} {s_['published_at']}  fetched {s_['fetched_at']}  {s_['url']}")
    elif a.cmd == "serve":
        import uvicorn

        from .server import create_app
        uvicorn.run(create_app(scheduler=not a.no_scheduler), host="127.0.0.1", port=a.port, log_level="warning")
    elif a.cmd == "schedule-loop":
        from .scheduler import Scheduler
        Scheduler(Store()).loop()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
