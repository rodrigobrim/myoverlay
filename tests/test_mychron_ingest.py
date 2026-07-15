from datetime import date, datetime, timezone
from zoneinfo import ZoneInfo

import media_tools.ingest.mychron as mychron
from media_tools.ingest.mychron import XrkInfo, _parse_log_datetime, ingest_mychron
from media_tools.library import Lap, Library


def fake_info(**over):
    base = dict(
        start_utc=datetime(2026, 7, 12, 13, 0, tzinfo=timezone.utc),
        duration_s=900.0,
        laps=[Lap(num=1, start_s=10.0, end_s=72.5), Lap(num=2, start_s=72.5, end_s=134.0)],
        venue="Interlagos",
        driver="Rodrigo",
        channels=["Engine RPM", "GPS Speed"],
    )
    base.update(over)
    return XrkInfo(**base)


def test_parse_log_datetime_formats():
    tz = ZoneInfo("America/Sao_Paulo")
    # Real MyChron 6 metadata is US month-first: 10/29/2047 03:59:12.
    dt = _parse_log_datetime({"Log Date": "10/29/2047", "Log Time": "03:59:12"}, tz)
    assert dt == datetime(2047, 10, 29, 6, 59, 12, tzinfo=timezone.utc)
    # Month-first is preferred for ambiguous dates.
    dt2 = _parse_log_datetime({"Log Date": "07/12/2026", "Log Time": "10:00:00"}, tz)
    assert dt2 == datetime(2026, 7, 12, 13, 0, tzinfo=timezone.utc)
    # Day-first still parses when month-first is impossible.
    dt3 = _parse_log_datetime({"Log Date": "29/10/2047", "Log Time": "00:00:00"}, tz)
    assert dt3.date().isoformat() == "2047-10-29"
    assert _parse_log_datetime({}, tz) is None


def test_clock_offset_corrects_session_times(cfg, tmp_path, monkeypatch):
    from datetime import date, timedelta

    cfg.mychron.clock_reads = datetime(2047, 10, 27)
    cfg.mychron.clock_actual = datetime(2026, 7, 13)
    rs3 = tmp_path / "rs3"
    rs3.mkdir()
    (rs3 / "s.xrk").write_bytes(b"x")

    wrong_start = datetime(2047, 10, 27, 6, 59, tzinfo=timezone.utc)
    monkeypatch.setattr(
        mychron, "parse_xrk", lambda p, tz: fake_info(start_utc=wrong_start, laps=[])
    )
    ingest_mychron(cfg, extra_sources=[rs3])

    lib = Library(cfg.library_root)
    m = lib.load_day(date(2026, 7, 13))
    assert len(m.telemetry) == 1
    assert m.telemetry[0].start_utc == datetime(2026, 7, 13, 6, 59, tzinfo=timezone.utc)


def test_ingest_mychron_with_mocked_parser(cfg, tmp_path, monkeypatch):
    rs3 = tmp_path / "rs3data"
    rs3.mkdir()
    (rs3 / "session_a.xrk").write_bytes(b"fake-xrk-a")
    (rs3 / "notes.txt").write_bytes(b"ignored")

    monkeypatch.setattr(mychron, "parse_xrk", lambda p, tz: fake_info())

    report = ingest_mychron(cfg, extra_sources=[rs3])
    assert len(report.copied) == 1 and not report.errors

    lib = Library(cfg.library_root)
    m = lib.load_day(date(2026, 7, 12))
    assert m.track == "Interlagos"
    t = m.telemetry[0]
    assert t.start_utc == datetime(2026, 7, 12, 13, 0, tzinfo=timezone.utc)
    assert t.end_utc == datetime(2026, 7, 12, 13, 15, tzinfo=timezone.utc)
    assert len(t.laps) == 2
    assert (lib.day_dir(date(2026, 7, 12)) / t.file).is_file()

    # idempotent
    report2 = ingest_mychron(cfg, extra_sources=[rs3])
    assert report2.copied == [] and report2.skipped_known == 1

    # Ingest must never delete or alter the RS3 originals.
    assert (rs3 / "session_a.xrk").read_bytes() == b"fake-xrk-a"
    assert (rs3 / "notes.txt").is_file()


def test_corrupt_xrk_reports_error_and_continues(cfg, tmp_path, monkeypatch):
    rs3 = tmp_path / "rs3data"
    rs3.mkdir()
    (rs3 / "bad.xrk").write_bytes(b"junk")
    (rs3 / "good.xrk").write_bytes(b"fine")

    def parse(p, tz):
        if p.name == "bad.xrk":
            raise ValueError("corrupt file")
        return fake_info()

    monkeypatch.setattr(mychron, "parse_xrk", parse)
    report = ingest_mychron(cfg, extra_sources=[rs3])
    assert len(report.copied) == 1
    assert len(report.errors) == 1 and "bad.xrk" in report.errors[0]
