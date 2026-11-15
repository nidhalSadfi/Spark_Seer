import json
from pathlib import Path

import pytest

from pipeline.weather import centre, grid_count, grid_summary, load_areas, main, world_grid_areas

ROOT = Path(__file__).resolve().parents[1]


def test_demo_areas_file_is_the_four_boxes():
    areas, is_grid = load_areas(ROOT / "docs/p1/demo_areas.json")
    assert is_grid is False
    assert [area["id"] for area in areas] == ["bizerte", "kabylie", "ne_algeria", "petite_kabylie"]
    assert centre(areas[0]["bbox"]) == (36.6, 9.05)


def test_half_degree_world_grid_count_and_corners():
    assert grid_count(0.5) == 259_200
    summary = grid_summary(0.5)
    assert summary["count"] == 259_200
    assert centre(summary["first_bbox"]) == (-89.75, -179.75)
    assert centre(summary["last_bbox"]) == (89.75, 179.75)
    areas = world_grid_areas(90)
    assert len(areas) == grid_count(90) == 8
    assert areas[0]["id"] == "-90_-180"
    assert areas[0]["bbox"] == [-180.0, -90.0, -90.0, 0.0]
    assert centre(areas[0]["bbox"]) == (-45.0, -135.0)
    assert len({area["id"] for area in areas}) == len(areas)
    with pytest.raises(ValueError):
        world_grid_areas(7)


def test_global_areas_file_dry_run(capsys):
    payload = json.loads((ROOT / "docs/p1/global_areas.json").read_text(encoding="utf-8"))
    assert payload["grid"]["step_deg"] == 0.5
    assert "areas" not in payload
    main([str(ROOT / "docs/p1/global_areas.json"), "data/weather-global", "--dry-run"])
    out = capsys.readouterr().out
    assert "areas=259200" in out
    assert "step=0.5" in out
    assert "-89.7500,-179.7500" in out
    assert "89.7500,179.7500" in out
    assert "Open-Meteo was not called" in out
    coarse, is_grid = load_areas(ROOT / "docs/p1/global_areas.json", step=90)
    assert is_grid is True
    assert len(coarse) == 8


def test_demo_dry_run_lists_four_centres(capsys):
    main([str(ROOT / "docs/p1/demo_areas.json"), "data/weather", "--dry-run"])
    out = capsys.readouterr().out
    assert "areas=4" in out
    assert "bizerte" in out
    assert "petite_kabylie" in out
    assert "36.6000,9.0500" in out
    assert "Open-Meteo was not called" in out


def test_world_grid_refuses_fetch_without_calling(monkeypatch):
    def boom(*_args, **_kwargs):
        raise AssertionError("Open-Meteo")

    monkeypatch.setattr("pipeline.weather.fetch_area", boom)
    with pytest.raises(SystemExit, match="Open-Meteo was not called"):
        main(["--world-grid", "--step", "90", "data/weather-global"])


def test_custom_area_file_dry_run(tmp_path: Path, capsys):
    path = tmp_path / "one.json"
    path.write_text(
        json.dumps({"areas": [{"id": "box", "name": "Box", "bbox": [0, 0, 2, 2]}]}),
        encoding="utf-8",
    )
    main([str(path), str(tmp_path / "out"), "--dry-run"])
    out = capsys.readouterr().out
    assert "areas=1" in out
    assert "1.0000,1.0000" in out
    assert not (tmp_path / "out").exists()


def _fake_record(area):
    lat, lon = centre(area["bbox"])
    return {
        "area_id": area["id"],
        "name": area["name"],
        "bbox": area["bbox"],
        "centre": {"latitude": lat, "longitude": lon},
        "fetched_at": "t",
        "response": {"daily": {"time": ["2001-01-01"]}, "latitude": lat, "longitude": lon},
    }


def test_custom_area_file_fetch(tmp_path: Path, monkeypatch):
    monkeypatch.setattr("pipeline.weather.time.sleep", lambda *_args, **_kwargs: None)
    monkeypatch.setattr("pipeline.weather.fetch_area", lambda area, _session: _fake_record(area))
    path = tmp_path / "areas.json"
    path.write_text(
        json.dumps({
            "areas": [
                {"id": "west", "name": "West", "bbox": [10, -5, 12, -3]},
                {"id": "east", "name": "East", "bbox": [100, 1, 102, 3]},
            ]
        }),
        encoding="utf-8",
    )
    out = tmp_path / "weather"
    main([str(path), str(out)])
    saved = json.loads((out / "west.json").read_text(encoding="utf-8"))
    assert saved["centre"]["latitude"] == -4
    assert saved["centre"]["longitude"] == 11
    assert (out / "east.json").is_file()


def test_fetch_tiny_world_grid(tmp_path: Path, monkeypatch):
    monkeypatch.setattr("pipeline.weather.time.sleep", lambda *_args, **_kwargs: None)
    seen = []

    def fake(area, _session):
        seen.append(area["id"])
        return _fake_record(area)

    monkeypatch.setattr("pipeline.weather.fetch_area", fake)
    out = tmp_path / "out"
    main(["--world-grid", "--step", "180", "--fetch", str(out)])
    assert seen == ["-90_-180", "-90_0"]
    assert sorted(path.name for path in out.glob("*.json")) == ["-90_-180.json", "-90_0.json"]
