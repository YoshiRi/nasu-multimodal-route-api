from pathlib import Path

from gtfs_multifeed.cli import main


FIXTURE_DIR = Path(__file__).parent / "fixtures" / "minimal_gtfs"


def test_search_bus_prints_direct_bus_table(capsys) -> None:
    exit_code = main(
        [
            "search-bus",
            "--gtfs-dir",
            str(FIXTURE_DIR),
            "--from",
            "stop_yumoto",
            "--to",
            "stop_ropeway",
            "--time",
            "10:00",
            "--date",
            "2026-08-17",
        ]
    )

    captured = capsys.readouterr()

    assert exit_code == 0
    assert "Direct bus trips:" in captured.out
    assert "那須線" in captured.out
    assert "那須ロープウェイ行" in captured.out
    assert "10:15:00" in captured.out
    assert "10:35:00" in captured.out


def test_search_bus_prints_json(capsys) -> None:
    exit_code = main(
        [
            "search-bus",
            "--gtfs-dir",
            str(FIXTURE_DIR),
            "--from",
            "stop_yumoto",
            "--to",
            "stop_ropeway",
            "--time",
            "10:00",
            "--date",
            "2026-08-17",
            "--json",
        ]
    )

    captured = capsys.readouterr()

    assert exit_code == 0
    assert '"trip_id": "trip_1"' in captured.out
    assert '"departure_time": "10:15:00"' in captured.out


def test_search_bus_prints_no_results(capsys) -> None:
    exit_code = main(
        [
            "search-bus",
            "--gtfs-dir",
            str(FIXTURE_DIR),
            "--from",
            "stop_station",
            "--to",
            "stop_ropeway",
            "--time",
            "10:00",
        ]
    )

    captured = capsys.readouterr()

    assert exit_code == 0
    assert captured.out.strip() == "No direct bus trips found."
