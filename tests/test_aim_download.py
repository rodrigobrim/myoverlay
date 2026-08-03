"""Device listing/download (ingest/aim.py) against a fake transport."""

import zlib

import pytest

import media_tools.ingest.aim as aim
from media_tools.aim import NoDeviceError
from media_tools.aim.catalog import as_catalog, parse_sessions, strip_status_word

XRK = b"<hCNF" + b"x" * 100  # stand-in for real telemetry payload

CSV = (
    "name,size,laps,best\r\n"
    f"a_0186.xrz,{len(zlib.compress(XRK))},12,52123\r\n"
    f"a_0187.xrz,{len(zlib.compress(XRK))},9,51987\r\n"
)


class FakeTransport:
    kind = "USB"

    def __init__(self, csv_text=CSV, payload=None):
        self.csv_text = csv_text
        self.payload = zlib.compress(XRK) if payload is None else payload
        self.closed = False
        self.fetched = []

    def session_csv(self):
        return self.csv_text

    def fetch(self, name, fh, expected, progress=None):
        self.fetched.append(name)
        fh.write(self.payload)
        if progress:
            progress(len(self.payload), expected)
        return len(self.payload)

    def close(self):
        self.closed = True


@pytest.fixture
def dl_cfg(cfg, tmp_path):
    d = tmp_path / "downloads"
    cfg.mychron.data_dirs = [d]
    return cfg, d


def test_catalog_parses_device_csv():
    columns, rows = parse_sessions(CSV)
    assert columns == ["name", "size", "laps", "best"]
    assert len(rows) == 2
    _c, _r, catalog = as_catalog(CSV)
    assert set(catalog) == {"a_0186.xrz", "a_0187.xrz"}
    assert catalog["a_0186.xrz"]["laps"] == "12"


def test_strip_status_word_detects_prefix():
    assert strip_status_word(b"name,size\r\n") == "name,size\r\n"
    assert strip_status_word(b"\x00\x00\x00\x00name,size\r\n") == "name,size\r\n"


def test_list_remote_sessions(dl_cfg, monkeypatch):
    cfg, d = dl_cfg
    dev = FakeTransport()
    monkeypatch.setattr("media_tools.aim.connect", lambda prefer=None, verbose=True: dev)

    result = aim.list_remote_sessions(cfg)
    assert result.transport == "USB"
    assert [s.name for s in result.sessions] == ["a_0186.xrz", "a_0187.xrz"]
    assert all(not s.downloaded for s in result.sessions)
    assert dev.closed

    # One session already on disk (with a legacy venue prefix): default
    # listing hides it, include_downloaded shows it flagged.
    d.mkdir(parents=True)
    (d / "KGV 101 _Race_a_0186.xrk").write_bytes(b"x")
    hidden = aim.list_remote_sessions(cfg)
    assert [s.name for s in hidden.sessions] == ["a_0187.xrz"]
    shown = aim.list_remote_sessions(cfg, include_downloaded=True)
    assert {s.name: s.downloaded for s in shown.sessions} == {
        "a_0186.xrz": True,
        "a_0187.xrz": False,
    }


def test_list_remote_sessions_no_device(dl_cfg, monkeypatch):
    cfg, _d = dl_cfg

    def no_device(prefer=None, verbose=True):
        raise NoDeviceError("No MyChron found.")

    monkeypatch.setattr("media_tools.aim.connect", no_device)
    result = aim.list_remote_sessions(cfg)
    assert result.transport is None
    assert not result.sessions
    assert result.notes and result.notes[0].startswith("!")


def test_download_decompresses_xrz_to_xrk(dl_cfg, monkeypatch):
    cfg, d = dl_cfg
    dev = FakeTransport()
    monkeypatch.setattr("media_tools.aim.connect", lambda prefer=None, verbose=True: dev)

    report = aim.download_sessions(cfg)
    assert not report.errors and not report.missing
    assert sorted(p.split("\\")[-1] for p in report.downloaded) == [
        "a_0186.xrk",
        "a_0187.xrk",
    ]
    assert (d / "a_0186.xrk").read_bytes() == XRK
    assert dev.closed

    # Re-run: everything already downloaded, nothing fetched again.
    dev2 = FakeTransport()
    monkeypatch.setattr("media_tools.aim.connect", lambda prefer=None, verbose=True: dev2)
    again = aim.download_sessions(cfg)
    assert again.skipped_existing == 2
    assert not dev2.fetched


def test_download_named_and_missing(dl_cfg, monkeypatch):
    cfg, d = dl_cfg
    dev = FakeTransport()
    monkeypatch.setattr("media_tools.aim.connect", lambda prefer=None, verbose=True: dev)

    report = aim.download_sessions(cfg, names=["a_0187.xrz", "nope.xrz"])
    assert dev.fetched == ["a_0187.xrz"]
    assert report.missing == ["nope.xrz"]
    assert (d / "a_0187.xrk").is_file()


def test_download_size_mismatch_is_an_error(dl_cfg, monkeypatch):
    cfg, d = dl_cfg
    dev = FakeTransport(payload=b"\x78\x9c-short")  # wrong length vs listing
    monkeypatch.setattr("media_tools.aim.connect", lambda prefer=None, verbose=True: dev)

    report = aim.download_sessions(cfg, names=["a_0186.xrz"])
    assert report.errors and "expected" in report.errors[0]
    assert not (d / "a_0186.xrk").exists()
