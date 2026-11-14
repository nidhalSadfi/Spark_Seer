import pytest
from fastapi.testclient import TestClient

import export_backups
import make_fake_data
from api.main import app
from test_api import FLAGS, check_calendar


@pytest.fixture
def fake(tmp_path, monkeypatch):
    make_fake_data.main(tmp_path, optional=True)
    monkeypatch.setenv("EMBER_DATA", str(tmp_path / "harmonized.parquet"))
    return TestClient(app)


def test_fake_data_serves_every_area_with_planted_anomalies(fake):
    report = fake.get("/validation").json()
    assert report["synthetic"] is True and "SYNTHETIC" in report["note"]
    calendars = {a["id"]: fake.get(f"/calendar?area={a['id']}").json() for a in fake.get("/areas").json()["areas"]}
    seen = set()
    for body in calendars.values():
        check_calendar(body, 2001, 2025)
        assert body["meta"]["validation"]["synthetic"] is True  # visible to the front end too
        assert {"low", "high", "MODIS_days", "VIIRS_days"} <= body["months"][0].keys()
        seen |= {r["flag"] for r in body["months"]}
    assert seen == FLAGS
    for p in report["planted_anomalies"]:
        month = next(r for r in calendars[p["area"]]["months"] if (r["year"], r["month"]) == (p["year"], p["month"]))
        assert month["flag"] == ("high" if p["factor"] > 1 else "low"), p


def test_export_refuses_fake_data_for_committed_backup_folder(fake, tmp_path, monkeypatch):
    monkeypatch.setattr(export_backups, "BACKUP", tmp_path / "web" / "backup")
    with pytest.raises(SystemExit, match="SYNTHETIC"):
        export_backups.main(export_backups.BACKUP)
    assert not export_backups.BACKUP.exists()
    export_backups.main(tmp_path / "elsewhere")  # any other folder is fine, for offline-mode testing
    assert (tmp_path / "elsewhere" / "bizerte.json").exists()
