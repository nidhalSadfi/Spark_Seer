from fastapi.testclient import TestClient

import export_backups
from api.main import app
from conftest import make_dataset


def test_backups_match_live_api(tmp_path, monkeypatch):
    monkeypatch.setenv("EMBER_DATA", make_dataset(tmp_path))
    export_backups.main(tmp_path / "backup")
    c = TestClient(app)
    for area in c.get("/areas").json()["areas"]:
        live = c.get(f"/calendar?area={area['id']}&start=2001&end=2025").content
        assert (tmp_path / "backup" / f"{area['id']}.json").read_bytes() == live
    assert (tmp_path / "backup" / "validation.json").read_bytes() == c.get("/validation").content
    assert (tmp_path / "backup" / "areas.json").read_bytes() == c.get("/areas").content  # offline demo needs it
