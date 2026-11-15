"""Offline tests for incident_agents. Synthetic records only; no network, no real post text."""

from datetime import datetime, timedelta, timezone

import pytest

from incident_agents import judge as judge_mod
from incident_agents import planner, tickets
from incident_agents.pipeline import Clients, Options, filter_records, run
from incident_agents.planner import Plan, Zone
from incident_agents.sources import Record, utc_iso
from incident_agents.store import Store

FR_BOX = (-5.2, 41.3, 9.6, 51.1)
NOW = datetime.now(timezone.utc).replace(microsecond=0)


def iso(dt):
    return dt.isoformat().replace("+00:00", "Z")


def rec(i, text="Incendie de forêt près de Ville-A", source="bluesky", kind="post", hours_ago=1, **kw):
    return Record(source=source, kind=kind, record_id=f"id{i}", url=f"https://example.test/{source}/{i}",
                  text=text, published_at=iso(NOW - timedelta(hours=hours_ago)), fetched_at=iso(NOW), **kw)


class FakeGeocoder:
    places = {"Ville-A": (45.00, 5.00, "town", 5.0), "Ville-B": (45.03, 5.02, "town", 5.0),
              "Ville-C": (48.00, 2.00, "town", 5.0)}

    def zone_box(self, name, country):
        if country == "FR" and name in ("France", "Var"):
            return (FR_BOX if name == "France" else (5.6, 42.9, 6.9, 43.8)), name
        return None

    def country_at(self, lat, lon):
        return "ES" if lon < 0 and lat < 43 else "FR"

    def place(self, name, country, box=None):
        from incident_agents.geo import Place
        if country == "FR" and name in self.places:
            lat, lon, p, r = self.places[name]
            return Place(name, "FR", lat, lon, p, r)
        return None


class FakeLLM:
    model = "fake"

    def __init__(self, plans=None, verdict=None):
        self.plans = list(plans or [{"zones": [{"name": "France", "country": "FR", "kind": "country"}],
                                     "keywords": [{"lang": "fr", "terms": ["incendie"]}]}])
        self.verdict = verdict or (lambda item: {"id": item["id"], "category": "active_fire_report",
                                                 "timing": "ongoing", "firsthand": "firsthand",
                                                 "place_phrase": "Ville-A", "country": "FR", "reason": "test"})
        self.total_tokens = 0
        self.calls = []

    def json(self, system, user, schema, name="result", **kw):
        import json
        self.calls.append(name)
        if name == "search_plan":
            return self.plans.pop(0) if len(self.plans) > 1 else self.plans[0]
        items = json.loads(user)
        return {"results": [self.verdict(it) for it in items]}


# --- sources ------------------------------------------------------------------
def test_utc_iso_formats():
    assert utc_iso("Sat Nov 14 22:12:55 +0000 2026") == "2026-11-14T22:12:55Z"
    assert utc_iso("2026-11-14T19:34:29.199233Z") == "2026-11-14T19:34:29Z"
    assert utc_iso("20261114T221500Z") == "2026-11-14T22:15:00Z"
    assert utc_iso("2026-11-14T23:00:00+01:00") == "2026-11-14T22:00:00Z"
    assert utc_iso("") is None


# --- planner ------------------------------------------------------------------
def test_validate_rejects_bad_country_and_unknown_zone():
    zones, kw, errors = planner.validate(
        {"zones": [{"name": "France", "country": "FRA", "kind": "country"},
                   {"name": "Atlantis", "country": "FR", "kind": "region"}], "keywords": []}, FakeGeocoder())
    assert not zones and len(errors) == 2


def test_finish_keywords_adds_country_languages_and_drops_invented_ones():
    z = [Zone("Algeria", "DZ", "country", (0, 0, 1, 1), "Algeria")]
    kw = planner.finish_keywords({"ka": ["tazmert"], "fr": ["a", "b", "c", "d"]}, z)
    assert set(kw) == {"ar", "fr"} and "incendie" in kw["fr"]
    assert len(kw["fr"]) <= planner.MAX_TERMS_PER_LANG


def test_satellite_only_skips_flares_south_of_34N():
    class K(FakeKanariFull):
        def fires_in(self, country, box, hours, limit=50):
            return [{"id": "a", "lat": 31.6, "lon": 6.1, "place": "Hassi Messaoud", "detections": 9},
                    {"id": "b", "lat": 36.8, "lon": 6.3, "place": "El Milia", "detections": 5,
                     "firstSeen": iso(NOW), "mapUrl": "https://kanari.io/x"}]

    class G(FakeGeocoder):
        def zone_box(self, name, country):
            return ((-8.7, 18.9, 12.0, 37.1), "Algeria") if country == "DZ" else None

    llm = FakeLLM(plans=[{"zones": [{"name": "Algeria", "country": "DZ", "kind": "country"}], "keywords": []}])
    s = Store(":memory:")
    run("Algeria", 24, s, Clients(llm=llm, geocoder=G(), kanari=K()),
        Options(sources=("kanari",), satellite_only_tickets=True, firms_check=False), echo=False)
    assert [t["place"] for t in s.tickets_with_sources()] == ["El Milia"]


def test_make_plan_retries_on_validation_error_then_succeeds():
    bad = {"zones": [{"name": "Nowhere", "country": "FR", "kind": "region"}], "keywords": []}
    good = {"zones": [{"name": "Var", "country": "FR", "kind": "region"}],
            "keywords": [{"lang": "fr", "terms": ["feu de forêt"]}]}
    llm, events = FakeLLM(plans=[bad, good]), []
    plan = planner.make_plan("Var", 24, llm, FakeGeocoder(), lambda *a, **k: events.append(a[:2]))
    assert plan.attempts == 2 and plan.zones[0].name == "Var"
    assert ("validate", "retry") in events


def test_make_plan_gives_up_after_retries():
    bad = {"zones": [{"name": "Nowhere", "country": "FR", "kind": "region"}], "keywords": []}
    with pytest.raises(planner.PlanError):
        planner.make_plan("x", 24, FakeLLM(plans=[bad]), FakeGeocoder())


# --- judge --------------------------------------------------------------------
def test_check_result_drops_place_not_in_text_and_out_of_scope_country():
    r = rec(1, text="Gros feu de forêt ce soir")
    v = judge_mod.check_result({"category": "active_fire_report", "timing": "ongoing", "firsthand": "firsthand",
                                "place_phrase": "Marseille", "country": "FR", "reason": ""}, r, ["FR"])
    assert v["place_phrase"] == "" and v["ticketable"]
    v = judge_mod.check_result({"category": "active_fire_report", "timing": "ongoing", "firsthand": "news",
                                "place_phrase": "", "country": "CA", "reason": ""}, r, ["FR"])
    assert v["country"] == "" and not v["ticketable"]
    v = judge_mod.check_result({"category": "building_or_urban_fire", "timing": "ongoing", "firsthand": "news",
                                "place_phrase": "", "country": "FR", "reason": ""}, r, ["FR"])
    assert not v["ticketable"]


def test_judge_ignores_unknown_ids():
    llm = FakeLLM(verdict=lambda it: {"id": "zzz", "category": "active_fire_report", "timing": "ongoing",
                                      "firsthand": "firsthand", "place_phrase": "", "country": "FR", "reason": ""})
    assert judge_mod.judge([rec(1), rec(2)], ["FR"], llm) == {}


# --- filtering ----------------------------------------------------------------
def test_filter_drops_old_no_term_and_duplicates_kanari_first():
    plan = Plan("q", 24, [Zone("France", "FR", "country", FR_BOX, "France")], {"fr": ["incendie"]})
    sig = rec(1, source="kanari_signal", kind="verified_signal", text="anything")
    dup = rec(1, source="bluesky")
    dup.url = sig.url
    old = rec(2, hours_ago=50)
    noterm = rec(3, text="Bonne soirée")
    kept = filter_records([dup, old, noterm, sig], plan, NOW - timedelta(hours=24), lambda *a, **k: None)
    assert [r.source for r in kept] == ["kanari_signal"]
    assert kept[0].meta["also_found_by"] == ["bluesky"]


# --- tickets ------------------------------------------------------------------
def located(i, place="Ville-A", hours_ago=1, lat=45.0, lon=5.0):
    return tickets.Located(rec(i, hours_ago=hours_ago), {"category": "active_fire_report"}, place, "FR",
                           lat, lon, "town", 5.0)


def test_upsert_merges_nearby_reports_and_never_counts_a_url_twice():
    s, open_t = Store(":memory:"), []
    t1, c1 = tickets.upsert(s, located(1), 1, open_t)
    t2, c2 = tickets.upsert(s, located(2, "Ville-B", 5, 45.03, 5.02), 1, open_t)
    t3, c3 = tickets.upsert(s, located(1), 1, open_t)
    t4, c4 = tickets.upsert(s, located(4, "Ville-C", 1, 48.0, 2.0), 1, open_t)
    assert (c1, c2, c3, c4) == (True, False, False, True)
    assert t1 == t2 == t3 != t4
    assert len(s.q("SELECT * FROM ticket_sources WHERE ticket_id=?", (t1,))) == 2


def test_upsert_does_not_merge_reports_days_apart():
    s, open_t = Store(":memory:"), []
    a, _ = tickets.upsert(s, located(1, hours_ago=1), 1, open_t)
    b, created = tickets.upsert(s, located(2, hours_ago=100), 1, open_t)
    assert created and a != b


class FakeKanari:
    def __init__(self, fires=None, fail=False):
        self.fires, self.fail = fires or [], fail

    def fires_near(self, lat, lon, radius, hours, country=None):
        if self.fail:
            raise RuntimeError("down")
        return self.fires


def test_satellite_status_words():
    s = Store(":memory:")
    tid, _ = tickets.upsert(s, located(1), 1, [])
    fire = {"place": "X", "lat": 45.01, "lon": 5.01, "detections": 4, "sensors": {"viirs": 4},
            "firstSeen": iso(NOW - timedelta(hours=2)), "lastSeen": iso(NOW), "mapUrl": "https://kanari.io"}
    noop = lambda *a, **k: None
    assert tickets.satellite_check(s, tid, FakeKanari([fire]), None, 24, noop) == "Satellite overlap"
    assert tickets.satellite_check(s, tid, FakeKanari([]), None, 24, noop) == "No satellite support"
    assert tickets.satellite_check(s, tid, FakeKanari(fail=True), None, 24, noop) == "Mentioned"


# --- whole run, offline -------------------------------------------------------
class FakeSource:
    available = True

    def __init__(self, recs):
        self.recs = recs

    def search(self, terms, lang, since_iso, limit):
        return self.recs


class FakeKanariFull(FakeKanari):
    def signal_records(self, hours, countries):
        return []


def test_run_end_to_end_offline_creates_one_ticket_with_events():
    s = Store(":memory:")
    clients = Clients(llm=FakeLLM(), geocoder=FakeGeocoder(), kanari=FakeKanariFull(),
                      bluesky=FakeSource([rec(1), rec(2)]))
    summary = run("France", 24, s, clients, Options(sources=("kanari", "bluesky"), firms_check=False), echo=False)
    assert summary["tickets_created"] == 1 and summary["tickets_touched"] == 1
    t = s.tickets_with_sources()[0]
    assert t["status"] == "No satellite support" and len(t["sources"]) == 2
    assert all(src["fetched_at"] and src["published_at"] for src in t["sources"])
    steps = {(e["step"], e["status"]) for e in s.events_after(0)}
    assert {("plan", "ok"), ("judge", "ok"), ("ticket", "created"), ("run", "done")} <= steps


def test_run_reports_plan_failure_as_event():
    s = Store(":memory:")
    bad = {"zones": [], "keywords": []}
    summary = run("??", 24, s, Clients(llm=FakeLLM(plans=[bad]), geocoder=FakeGeocoder(), kanari=FakeKanariFull()),
                  Options(sources=("kanari",), firms_check=False), echo=False)
    assert "error" in summary
    assert s.q("SELECT status FROM runs")[0]["status"] == "error"


def test_second_run_reuses_verdicts_without_model_calls():
    s = Store(":memory:")
    llm = FakeLLM()
    clients = Clients(llm=llm, geocoder=FakeGeocoder(), kanari=FakeKanariFull(), bluesky=FakeSource([rec(1), rec(2)]))
    opts = Options(sources=("kanari", "bluesky"), firms_check=False)
    run("France", 24, s, clients, opts, echo=False)
    first = llm.calls.count("verdicts")
    summary = run("France", 24, s, clients, opts, echo=False)
    assert first >= 1 and llm.calls.count("verdicts") == first
    assert summary["tickets_created"] == 0 and summary["tickets_touched"] == 1


def test_country_zone_uses_mainland_table_whatever_the_name():
    zones, _, errors = planner.validate({"zones": [{"name": "الجزائر", "country": "DZ", "kind": "country"}],
                                         "keywords": []}, FakeGeocoder())
    assert not errors and zones[0].label == "Algeria" and zones[0].box[3] == pytest.approx(37.1)


def test_multi_part_place_prefers_hit_matching_the_other_parts():
    from incident_agents.geo import Geocoder

    class G(Geocoder):
        def _om_search(self, name, country):
            if name == "Jasper":
                return [{"name": "Jasper", "latitude": 33.8, "longitude": -87.3, "feature_code": "PPL",
                         "admin1": "Alabama", "country_code": "US"},
                        {"name": "Jasper", "latitude": 30.5, "longitude": -82.9, "feature_code": "PPL",
                         "admin1": "Florida", "country_code": "US"}]
            return []

        def zone_box(self, name, country):
            return None

    p = G().place("Jasper, Florida", "US")
    assert p and p.admin1 == "Florida" and p.lat == pytest.approx(30.5)


# --- scheduler ----------------------------------------------------------------
def test_scheduler_runs_due_schedule_once_and_moves_next_run():
    from incident_agents.scheduler import Scheduler
    s, calls = Store(":memory:"), []
    s.add_schedule("France", 24, 60)
    sched = Scheduler(s, runner=lambda q, h, store, **k: calls.append((q, h, k.get("schedule_id"))))
    assert sched.tick() == 1 and calls == [("France", 24, 1)]
    assert sched.tick() == 0  # next run is an hour away
    assert s.q("SELECT next_run_at > last_run_at AS later FROM schedules")[0]["later"] == 1


# --- server -------------------------------------------------------------------
def test_server_endpoints_with_fake_runner():
    from fastapi.testclient import TestClient

    from incident_agents.server import create_app
    s, calls = Store(":memory:"), []
    app = create_app(s, scheduler=False, runner=lambda q, h, store, **k: calls.append((q, h)))
    c = TestClient(app)
    assert c.get("/").status_code == 200
    assert c.post("/api/runs", json={"query": "France", "hours": 24}).json()["started"]
    r = c.post("/api/schedules", json={"query": "France", "hours": 24, "interval_min": 5}).json()
    assert r["interval_min"] == 15  # floor protects free quotas
    assert len(c.get("/api/schedules").json()) == 1
    assert c.get("/api/tickets").json() == []
    assert c.post("/api/runs", json={"query": "x", "hours": 999}).status_code == 422


# --- history, lead time, replay ---------------------------------------------------
@pytest.fixture(autouse=True)
def _no_real_history(monkeypatch, tmp_path):
    """Tests never read the real data/cells.parquet."""
    from incident_agents import history
    monkeypatch.setattr(history, "CELLS_PATH", tmp_path / "missing.parquet")
    history._frame.cache_clear()


def test_cell_history_from_tiny_grid(tmp_path):
    import pandas as pd

    from incident_agents import history
    rows = []
    for year in (2003, 2007, 2021):  # the ticket's cell: fire in 3 years, two of them in August
        rows.append({"lat_i": 90, "lon_i": 10, "year": year, "month": 8 if year != 2003 else 7, "sensor": "MODIS",
                     "n": 10 * (year - 2000), "days": 2})
    rows.append({"lat_i": 91, "lon_i": 10, "year": 2010, "month": 8, "sensor": "MODIS", "n": 1, "days": 1})
    rows.append({"lat_i": 90, "lon_i": 10, "year": 2021, "month": 8, "sensor": "VIIRS", "n": 999, "days": 4})
    df = pd.DataFrame(rows)
    df["cell_id"], df["lat"], df["lon"] = "x", 0.0, 0.0
    path = tmp_path / "cells.parquet"
    df.to_parquet(path)
    h = history.cell_history(45.2, 5.1, "FR", month=8, path=path)
    assert h["cell_id"] == "45.0_5.0"
    assert h["modis"]["years_with_fire"] == 3 and h["modis"]["of_years"] == 25
    assert h["modis"]["detections"] == 30 + 70 + 210  # VIIRS never summed into MODIS
    assert h["modis"]["worst_year"] == 2021 and h["modis"]["same_month_years"] == 2
    assert h["viirs"]["detections"] == 999
    assert "3 of 25 years" in h["text"]
    assert history.cell_history(45.2, 5.1, "ES", path=path) is None   # country not in the grid
    assert history.cell_history(31.0, 5.0, "DZ", path=path) is None   # south of 34°N
    assert history.cell_history(45.2, 5.1, "FR") is None              # no grid file


def test_lead_time_signed_minutes_and_summary():
    s = Store(":memory:")
    tid, _ = tickets.upsert(s, located(1, hours_ago=3), 1, [])
    post = NOW - timedelta(hours=3)
    fire = {"place": "X", "lat": 45.01, "lon": 5.01, "detections": 4, "sensors": {"mtg": 4},
            "firstSeen": iso(post + timedelta(minutes=40)), "lastSeen": iso(NOW), "url": "https://kanari.io/f"}

    class FakeFirms:
        available = True

        def detections(self, box, start, end):
            t = post - timedelta(minutes=30)
            return [{"latitude": "45.0", "longitude": "5.0", "acq_date": t.strftime("%Y-%m-%d"),
                     "acq_time": t.strftime("%H%M"), "confidence": "n"}]

    tickets.satellite_check(s, tid, FakeKanari([fire]), FakeFirms(), 24, lambda *a, **k: None)
    lead = s.tickets_with_sources()[0]["satellite"]["lead"]
    assert lead["minutes_vs_kanari"] == 40      # people posted 40 min before Kanari's first detection
    assert lead["minutes_vs_polar"] in (-31, -30)  # a polar pass saw it 30 min before the first post (HHMM)
    summ = tickets.lead_summary(s, [tid])
    assert summ["minutes_vs_kanari"]["posts_first"] == 1 and summ["minutes_vs_polar"]["satellite_first"] == 1


def test_old_database_gets_new_columns():
    import sqlite3
    path = ":memory:"
    s = Store(path)
    cols = {r["name"] for r in s.q("PRAGMA table_info(tickets)")}
    assert {"mode", "history"} <= cols


def test_x_query_with_window_and_place():
    from incident_agents.sources.twitter import build_query
    q = build_query(["incendie", "feu de forêt"], None, "2026-08-27T08:00:00Z", "2026-08-28T14:00:00Z",
                    must="Calenzana")
    assert q.startswith('"Calenzana" (incendie OR "feu de forêt")') and "lang:" not in q
    assert "since_time:1787817600" in q and "until_time:1787925600" in q


class FakeArchiveKanari(FakeKanariFull):
    def __init__(self, fires):
        super().__init__([])
        self.archive = fires

    def archive_fires(self, country, start, end, limit=100):
        return self.archive, False


class FakeWindowSource(FakeSource):
    def __init__(self, recs):
        super().__init__(recs)
        self.calls = []

    def search(self, terms, lang, since_iso, limit, until_iso=None, **kw):
        self.calls.append((terms, since_iso, until_iso))
        return self.recs


def test_replay_seeds_from_archive_and_keeps_tickets_apart():
    from incident_agents.pipeline import replay
    day = (NOW - timedelta(days=5)).replace(hour=12, minute=0, second=0)
    archive = [
        {"slug": "ville-a-1", "url": "https://kanari.io/fr/feu/ville-a-1", "place": "Ville-A", "country": "FR",
         "lat": 45.0, "lon": 5.0, "firstSeen": iso(day), "lastSeen": iso(day + timedelta(hours=2)),
         "detections": 50, "sensors": {"mtg": 50}, "witnessPosts": 0, "firstPressArticle": None},
        {"slug": "zaragoza-1", "url": "https://kanari.io/fr/feu/zaragoza-1", "place": "Zaragoza", "country": "FR",
         "lat": 41.6, "lon": -0.9, "firstSeen": iso(day), "lastSeen": iso(day), "detections": 90,
         "sensors": {"mtg": 90}, "witnessPosts": 3, "firstPressArticle": None},  # outside the France box
    ]
    post = Record(source="bluesky", kind="post", record_id="p1", url="https://example.test/p1",
                  text="Incendie de forêt à Ville-A", published_at=iso(day - timedelta(minutes=20)),
                  fetched_at=iso(NOW))
    bsky = FakeWindowSource([post])
    s = Store(":memory:")
    clients = Clients(llm=FakeLLM(), geocoder=FakeGeocoder(), kanari=FakeArchiveKanari(archive), bluesky=bsky)
    d = (day - timedelta(days=1)).date().isoformat(), (day + timedelta(days=1)).date().isoformat()
    summary = replay("France", d[0], d[1], s, clients, Options(sources=("bluesky",), firms_check=False), echo=False)
    assert summary["seeds"] == 1 and summary["tickets_created"] == 1, summary
    assert bsky.calls[0][0] == ["Ville-A"] and bsky.calls[0][2]  # place search with an upper bound
    t = s.tickets_with_sources()[0]
    assert t["mode"] == "replay" and t["status"] == "Satellite overlap" and len(t["sources"]) == 2
    assert t["satellite"]["lead"]["minutes_vs_kanari"] == 20
    assert s.q("SELECT mode FROM runs")[0]["mode"] == "replay"
    assert s.recent_tickets("2000-01-01") == []  # live runs never see replay tickets
    assert any(e["step"] == "lead" for e in s.events_after(0))


def test_replay_rejects_future_or_pre_archive_dates():
    from incident_agents.pipeline import replay
    s = Store(":memory:")
    clients = Clients(llm=FakeLLM(), geocoder=FakeGeocoder(), kanari=FakeArchiveKanari([]))
    today = NOW.date().isoformat()
    assert "error" in replay("France", today, today, s, clients, Options(firms_check=False), echo=False)
    assert "archive" in replay("France", "2026-07-01", "2026-07-02", s, clients, Options(firms_check=False),
                               echo=False)["error"]


def test_server_replay_needs_both_dates():
    from fastapi.testclient import TestClient

    from incident_agents.server import create_app
    s, calls = Store(":memory:"), []
    app = create_app(s, scheduler=False, runner=lambda *a, **k: None,
                     replay_runner=lambda q, a, b, store: calls.append((q, str(a), str(b))))
    c = TestClient(app)
    assert c.post("/api/runs", json={"query": "France", "replay_from": "2026-08-25"}).status_code == 422
    assert c.post("/api/runs", json={"query": "France", "replay_from": "2026-08-25",
                                     "replay_to": "2026-08-27"}).json()["started"]
    import time
    time.sleep(0.2)
    assert calls == [("France", "2026-08-25", "2026-08-27")]
