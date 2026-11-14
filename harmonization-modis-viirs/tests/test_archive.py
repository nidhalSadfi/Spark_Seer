import pytest

from firms_tool.cli import main
from firms_tool.public import archive_scope, archive_url
from firms_tool.sensors import get_sensor

BASE = "https://firms.modaps.eosdis.nasa.gov"
INDEX = {
    "modis_2020_all_countries.zip",
    "viirs-snpp_2020_all_countries.zip",
    "modis_2020_Tunisia.csv",
    "modis_2020_Algeria.csv",
}


def test_country_and_world_archive_urls():
    modis = get_sensor("modis_c61")
    viirs = get_sensor("viirs_snpp")
    world = f"{BASE}/data/country/zips/modis_2020_all_countries.zip"
    assert archive_url(modis, 2020, "all") == world
    assert archive_url(modis, 2020, "world") == world
    assert archive_url(modis, 2020, "GLOBAL") == world
    assert archive_url(viirs, 2019, "Tunisia") == (
        f"{BASE}/data/country/viirs-snpp/2019/viirs-snpp_2019_Tunisia.csv"
    )
    assert archive_url(modis, 2020, "Algeria") == (
        f"{BASE}/data/country/modis/2020/modis_2020_Algeria.csv"
    )
    assert archive_scope("United_States") == "United_States"
    assert archive_scope("Algeria") == "Algeria"


def _no_network(monkeypatch):
    monkeypatch.setattr("firms_tool.public.archive_index", lambda s=None: set(INDEX))
    monkeypatch.setattr("firms_tool.portal.new_session", lambda: object())

    def boom(*_args, **_kwargs):
        raise AssertionError("download")

    monkeypatch.setattr("firms_tool.portal.download", boom)


def test_archive_default_scope_is_the_world_zip(capsys, monkeypatch):
    _no_network(monkeypatch)
    main(["archive", "--sensors", "modis_c61", "--years", "2020", "--dry-run"])
    out = capsys.readouterr().out
    assert out.strip() == f"{BASE}/data/country/zips/modis_2020_all_countries.zip"


def test_archive_dry_run_global_prints_zip_urls(capsys, monkeypatch):
    _no_network(monkeypatch)
    main(["archive", "--sensors", "modis_c61", "viirs_snpp", "--years", "2020", "--global", "--dry-run"])
    out = capsys.readouterr().out
    assert f"{BASE}/data/country/zips/modis_2020_all_countries.zip" in out
    assert f"{BASE}/data/country/zips/viirs-snpp_2020_all_countries.zip" in out


def test_archive_countries_stay_country_csvs(capsys, monkeypatch):
    _no_network(monkeypatch)
    main([
        "archive", "--sensors", "modis_c61", "--years", "2020",
        "--countries", "Tunisia", "Algeria", "--dry-run",
    ])
    out = capsys.readouterr().out
    assert f"{BASE}/data/country/modis/2020/modis_2020_Tunisia.csv" in out
    assert f"{BASE}/data/country/modis/2020/modis_2020_Algeria.csv" in out
    assert "all_countries" not in out


def test_countries_world_matches_global(capsys, monkeypatch):
    _no_network(monkeypatch)
    main(["archive", "--sensors", "modis_c61", "--years", "2020", "--countries", "world", "--dry-run"])
    out = capsys.readouterr().out
    assert f"{BASE}/data/country/zips/modis_2020_all_countries.zip" in out


def test_global_and_countries_conflict():
    with pytest.raises(SystemExit, match="either --global or --countries"):
        main([
            "archive", "--sensors", "modis_c61", "--years", "2020",
            "--global", "--countries", "Tunisia", "--dry-run",
        ])


def test_archive_help_mentions_global(capsys):
    with pytest.raises(SystemExit) as exc:
        main(["archive", "--help"])
    assert exc.value.code == 0
    text = capsys.readouterr().out
    assert "--global" in text
    assert "all-countries" in text
    assert "default" in text
