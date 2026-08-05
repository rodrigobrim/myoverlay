from datetime import date, datetime, timezone
from zoneinfo import ZoneInfo

import media_tools.ingest.telemetry as tel_ingest
from media_tools.ingest.telemetry import (
    XrkInfo,
    _parse_log_datetime,
    enumerate_telemetry_files,
    ingest_telemetry,
)
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


def test_session_times_are_taken_as_logged(cfg, tmp_path, monkeypatch):
    """No clock correction: a session lands on whatever day the logger
    claims, even an implausible one. Mismatches are resolved by manually
    correlating files and videos by name."""
    from datetime import date

    tel = tmp_path / "tel"
    tel.mkdir()
    (tel / "s.xrk").write_bytes(b"x")

    wrong_start = datetime(2047, 10, 27, 6, 59, tzinfo=timezone.utc)
    monkeypatch.setattr(
        tel_ingest, "parse_xrk", lambda p, tz: fake_info(start_utc=wrong_start, laps=[])
    )
    ingest_telemetry(cfg, extra_sources=[tel])

    lib = Library(cfg.library_root)
    m = lib.load_day(wrong_start.astimezone(cfg.telemetry.tzinfo()).date())
    assert len(m.telemetry) == 1
    assert m.telemetry[0].start_utc == wrong_start


def test_ingest_telemetry_with_mocked_parser(cfg, tmp_path, monkeypatch):
    tel = tmp_path / "teldata"
    tel.mkdir()
    (tel / "session_a.xrk").write_bytes(b"fake-xrk-a")
    (tel / "notes.txt").write_bytes(b"ignored")

    monkeypatch.setattr(tel_ingest, "parse_xrk", lambda p, tz: fake_info())

    report = ingest_telemetry(cfg, extra_sources=[tel])
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
    report2 = ingest_telemetry(cfg, extra_sources=[tel])
    assert report2.copied == [] and report2.skipped_known == 1

    # Ingest must never delete or alter the source originals.
    assert (tel / "session_a.xrk").read_bytes() == b"fake-xrk-a"
    assert (tel / "notes.txt").is_file()


def test_corrupt_xrk_reports_error_and_continues(cfg, tmp_path, monkeypatch):
    tel = tmp_path / "teldata"
    tel.mkdir()
    (tel / "bad.xrk").write_bytes(b"junk")
    (tel / "good.xrk").write_bytes(b"fine")

    def parse(p, tz):
        if p.name == "bad.xrk":
            raise ValueError("corrupt file")
        return fake_info()

    monkeypatch.setattr(tel_ingest, "parse_xrk", parse)
    report = ingest_telemetry(cfg, extra_sources=[tel])
    assert len(report.copied) == 1
    assert len(report.errors) == 1 and "bad.xrk" in report.errors[0]


def test_xrz_twin_is_skipped(cfg, tmp_path, monkeypatch):
    """A session is its .xrk; the compressed .xrz twin is ignored
    unconditionally (hardcoded, not configurable)."""
    tel = tmp_path / "tel"
    tel.mkdir()
    (tel / "session_a.xrk").write_bytes(b"real")
    (tel / "session_a.xrz").write_bytes(b"compressed-twin")
    monkeypatch.setattr(tel_ingest, "parse_xrk", lambda p, tz: fake_info())

    _sources, files = enumerate_telemetry_files(cfg, extra_sources=[tel])
    assert [f.name for f in files] == ["session_a.xrk"]

    report = ingest_telemetry(cfg, extra_sources=[tel])
    assert len(report.copied) == 1 and not report.errors
    lib = Library(cfg.library_root)
    m = lib.load_day(date(2026, 7, 12))
    assert [t.source_name for t in m.telemetry] == ["session_a.xrk"]


def test_enumerate_does_not_parse(cfg, tmp_path, monkeypatch):
    tel = tmp_path / "tel"
    tel.mkdir()
    (tel / "a.xrk").write_bytes(b"a")
    (tel / "b.xrk").write_bytes(b"bb")
    (tel / "notes.txt").write_bytes(b"ignored")
    monkeypatch.setattr(
        tel_ingest, "parse_xrk", lambda p, tz: (_ for _ in ()).throw(AssertionError("parsed"))
    )
    _sources, files = enumerate_telemetry_files(cfg, extra_sources=[tel])
    assert {f.name for f in files} == {"a.xrk", "b.xrk"}
    assert all(not f.ingested for f in files)


def test_only_names_selective_and_missing(cfg, tmp_path, monkeypatch):
    tel = tmp_path / "tel"
    tel.mkdir()
    (tel / "session_a.xrk").write_bytes(b"a")
    (tel / "session_b.xrk").write_bytes(b"b")
    monkeypatch.setattr(tel_ingest, "parse_xrk", lambda p, tz: fake_info())

    report = ingest_telemetry(cfg, extra_sources=[tel], only_names=["SESSION_A.XRK"])
    assert len(report.copied) == 1
    assert report.requested_missing == []

    missing = ingest_telemetry(cfg, extra_sources=[tel], only_names=["nope.xrk"])
    assert missing.copied == [] and missing.requested_missing == ["nope.xrk"]


def test_force_replaces_telemetry_in_place(cfg, tmp_path, monkeypatch):
    tel = tmp_path / "tel"
    tel.mkdir()
    (tel / "session_a.xrk").write_bytes(b"a")
    monkeypatch.setattr(tel_ingest, "parse_xrk", lambda p, tz: fake_info())

    ingest_telemetry(cfg, extra_sources=[tel])
    again = ingest_telemetry(cfg, extra_sources=[tel])
    assert again.copied == [] and again.skipped_known == 1

    forced = ingest_telemetry(cfg, extra_sources=[tel], force=True)
    assert len(forced.copied) == 1

    lib = Library(cfg.library_root)
    m = lib.load_day(date(2026, 7, 12))
    assert [t.source_name for t in m.telemetry].count("session_a.xrk") == 1
