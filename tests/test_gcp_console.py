"""google-setup helpers that touch the filesystem (no browser involved)."""

import json
import sqlite3
from pathlib import Path

import pytest

from media_tools import gcp_console
from media_tools.config import Config
from media_tools.gcp_console import (
    _already_configured,
    _claim_secret_path,
    _engine_exe,
    _has_google_session,
    _rotate_secret_aside,
    _secret_project,
    _session_cookie_state,
    setup_google_api,
)


def _write_secret(path, project: str) -> None:
    path.write_text(
        json.dumps({"installed": {"client_id": "x.apps.googleusercontent.com",
                                  "project_id": project}}),
        encoding="utf-8",
    )


def test_secret_project_reads_project_id(tmp_path):
    secret = tmp_path / "client_secret.json"
    _write_secret(secret, "myoverlay-abc123")
    assert _secret_project(secret) == "myoverlay-abc123"


def test_secret_project_none_on_foreign_file(tmp_path):
    secret = tmp_path / "client_secret.json"
    secret.write_text("not json", encoding="utf-8")
    assert _secret_project(secret) is None


def test_rotate_secret_aside_keeps_the_old_secret(tmp_path):
    secret = tmp_path / "client_secret.json"
    _write_secret(secret, "myoverlay-dead01")

    kept = _rotate_secret_aside(secret, "myoverlay-dead01")

    # The name records the project, the path is free for the new client, and
    # the old secret still exists - Google reveals it only once.
    assert kept.name == "client_secret.myoverlay-dead01.bak.json"
    assert not secret.exists()
    assert _secret_project(kept) == "myoverlay-dead01"


def test_rotate_secret_aside_never_clobbers_an_earlier_backup(tmp_path):
    secret = tmp_path / "client_secret.json"
    _write_secret(secret, "myoverlay-dead01")
    first = _rotate_secret_aside(secret, "myoverlay-dead01")

    _write_secret(secret, "myoverlay-dead01")
    second = _rotate_secret_aside(secret, "myoverlay-dead01")

    assert second != first and first.is_file() and second.is_file()
    assert second.name == "client_secret.myoverlay-dead01.1.bak.json"


def test_claim_secret_path_rotates_only_at_write_time(tmp_path):
    """The old secret must survive a run that fails before minting a new one:
    rotating up front once left the checkout with no client_secret.json at all
    when the browser closed mid-flow."""
    secret = tmp_path / "client_secret.json"
    _write_secret(secret, "myoverlay-dead01")
    report: list[str] = []

    _claim_secret_path(secret, report)

    assert not secret.exists()  # free for the replacement about to be written
    assert (tmp_path / "client_secret.myoverlay-dead01.bak.json").is_file()
    assert report and "kept as" in report[0]


def test_claim_secret_path_is_a_noop_without_an_existing_secret(tmp_path):
    dest = tmp_path / "nested" / "client_secret.json"
    report: list[str] = []

    _claim_secret_path(dest, report)

    assert dest.parent.is_dir() and not dest.exists() and report == []


def _cfg_with_secret(tmp_path, project: str | None) -> Config:
    cfg = Config(library_root=tmp_path / "lib")
    cfg.youtube.project_id = "myoverlay-live01"
    cfg.youtube.client_secret_file = tmp_path / "client_secret.json"
    if project:
        _write_secret(cfg.youtube.client_secret_file, project)
    return cfg


def test_already_configured_only_for_a_matching_secret(tmp_path):
    assert _already_configured(_cfg_with_secret(tmp_path, "myoverlay-live01"),
                               "myoverlay-live01")
    # A secret for another project cannot authorize this one.
    assert not _already_configured(_cfg_with_secret(tmp_path, "myoverlay-old99"),
                                   "myoverlay-live01")
    # No secret at all: the client still has to be created.
    assert not _already_configured(_cfg_with_secret(tmp_path, None),
                                   "myoverlay-live01")


def _record_browser(monkeypatch, signed_in: bool = True, state: str | None = "ACTIVE"):
    """Record what setup would do instead of doing it: browser sessions, and
    whether the interactive gcloud phase was entered. setup_google_api never
    raises (it reports instead), so the checks are on what it called."""
    launched: list[str] = []
    monkeypatch.setattr(gcp_console, "gcloud_available", lambda: True)
    monkeypatch.setattr(
        gcp_console, "_active_account", lambda: "me@example.com" if signed_in else ""
    )
    monkeypatch.setattr(gcp_console, "_project_state", lambda project: state)
    monkeypatch.setattr(
        gcp_console,
        "ensure_project",
        lambda cfg, report: launched.append("ensure_project") or True,
    )
    monkeypatch.setattr(
        gcp_console, "_engine_exe", lambda pw, report: Path("chrome.exe")
    )
    monkeypatch.setattr(
        gcp_console,
        "_automated_pass",
        lambda pw, cfg, project, profile_dir, report, ts, exe: launched.append(project),
    )
    return launched


def test_setup_skips_everything_when_already_configured(tmp_path, monkeypatch):
    """A configured account must cost no browser and no sign-in handoff: the
    old flow only noticed inside the Console session, after the launch."""
    launched = _record_browser(monkeypatch)
    cfg = _cfg_with_secret(tmp_path, "myoverlay-live01")

    report = setup_google_api(cfg)

    assert launched == []  # not even the gcloud phase
    assert any("already set up" in line for line in report)
    assert not any(line.lstrip().startswith("!") for line in report)


def test_setup_skip_does_not_need_playwright(tmp_path, monkeypatch):
    """The skip is decided before the browser library is imported, so a
    configured account is never failed over a browser it will not open."""
    launched = _record_browser(monkeypatch)
    monkeypatch.setitem(__import__("sys").modules, "playwright.sync_api", None)
    cfg = _cfg_with_secret(tmp_path, "myoverlay-live01")

    report = setup_google_api(cfg)

    assert launched == []
    assert not any("playwright" in line for line in report)


def test_setup_skip_never_prompts_for_a_gcloud_sign_in(tmp_path, monkeypatch):
    """gcloud's credential is separate from the app's, so a configured app can
    sit on a machine where gcloud has none (restored profile, wiped gcloud
    config). Re-verification is skipped there rather than forcing a sign-in
    for checks whose answer is 'nothing to do'."""
    launched = _record_browser(monkeypatch, signed_in=False)
    cfg = _cfg_with_secret(tmp_path, "myoverlay-live01")

    report = setup_google_api(cfg)

    assert launched == []
    assert any("not signed in" in line for line in report)
    assert any("already set up" in line for line in report)


def test_setup_reconfigures_when_the_project_is_gone(tmp_path, monkeypatch):
    """A secret for a project scheduled for deletion is not 'configured':
    there is real work to do, so the full flow runs."""
    pytest.importorskip("playwright.sync_api")
    launched = _record_browser(monkeypatch, state="DELETE_REQUESTED")
    cfg = _cfg_with_secret(tmp_path, "myoverlay-live01")

    report = setup_google_api(cfg)

    assert launched == ["ensure_project", "myoverlay-live01"]
    assert any("DELETE_REQUESTED" in line for line in report)


def test_force_runs_the_browser_even_when_configured(tmp_path, monkeypatch):
    pytest.importorskip("playwright.sync_api")
    launched = _record_browser(monkeypatch)
    cfg = _cfg_with_secret(tmp_path, "myoverlay-live01")

    setup_google_api(cfg, force=True)

    assert launched == ["ensure_project", "myoverlay-live01"]


def test_setup_runs_the_browser_when_the_secret_is_missing(tmp_path, monkeypatch):
    pytest.importorskip("playwright.sync_api")
    launched = _record_browser(monkeypatch)
    cfg = _cfg_with_secret(tmp_path, None)

    setup_google_api(cfg)

    assert launched == ["ensure_project", "myoverlay-live01"]


def test_setup_stops_when_no_browser_engine_resolves(tmp_path, monkeypatch):
    """No engine means no attempt at all - the old per-attempt engine search
    ran the whole two-attempt dance before admitting it had no browser."""
    pytest.importorskip("playwright.sync_api")
    launched = _record_browser(monkeypatch)
    monkeypatch.setattr(gcp_console, "_engine_exe", lambda pw, report: None)
    cfg = _cfg_with_secret(tmp_path, None)

    report = setup_google_api(cfg)

    assert launched == ["ensure_project"]  # the browser phase never started
    assert not any("myoverlay-live01" == line for line in launched)


class _FakeChromium:
    def __init__(self, path):
        self.executable_path = str(path)


def test_engine_exe_prefers_the_installed_browser(tmp_path, monkeypatch):
    """Both phases must land on ONE binary: a profile written by the installed
    Chrome and reopened by bundled chromium loses the session, which surfaced
    only as the Console bouncing back to sign-in after a successful login."""
    installed = tmp_path / "chrome.exe"
    installed.touch()
    bundled = tmp_path / "bundled" / "chrome.exe"
    bundled.parent.mkdir()
    bundled.touch()
    monkeypatch.setattr(gcp_console, "_browser_exe", lambda: installed)

    pw = type("PW", (), {"chromium": _FakeChromium(bundled)})()
    assert _engine_exe(pw, []) == installed


def test_engine_exe_falls_back_to_bundled_chromium(tmp_path, monkeypatch):
    bundled = tmp_path / "chrome.exe"
    bundled.touch()
    monkeypatch.setattr(gcp_console, "_browser_exe", lambda: None)

    pw = type("PW", (), {"chromium": _FakeChromium(bundled)})()
    assert _engine_exe(pw, []) == bundled


def test_engine_exe_reports_when_nothing_is_installed(tmp_path, monkeypatch):
    monkeypatch.setattr(gcp_console, "_browser_exe", lambda: None)

    report: list[str] = []
    pw = type("PW", (), {"chromium": _FakeChromium(tmp_path / "missing.exe")})()

    assert _engine_exe(pw, report) is None
    assert any(line.startswith("!") for line in report)


def _cookie_db(profile_dir: Path, names: list[str]) -> None:
    db = profile_dir / "Default" / "Network" / "Cookies"
    db.parent.mkdir(parents=True, exist_ok=True)
    con = sqlite3.connect(db)
    con.execute("CREATE TABLE cookies (host_key TEXT, name TEXT)")
    con.executemany(
        "INSERT INTO cookies VALUES (?, ?)", [(".google.com", n) for n in names]
    )
    con.commit()
    con.close()


def _prefs(profile_dir: Path) -> None:
    p = profile_dir / "Default" / "Preferences"
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(json.dumps({"account_info": [{"email": "me@example.com"}]}), "utf-8")


def test_session_cookie_state_is_none_without_a_readable_db(tmp_path):
    assert _session_cookie_state(tmp_path) is None


def test_readable_cookie_db_overrules_preferences(tmp_path):
    """Preferences records a browser-level sign-in that need not have produced
    a web session. Trusting it first reported success on a profile the Console
    then bounced - so a readable cookie DB wins, in both directions."""
    _prefs(tmp_path)
    _cookie_db(tmp_path, ["NID", "CONSENT"])  # pre-login cookies only

    assert _session_cookie_state(tmp_path) is False
    assert _has_google_session(tmp_path) is False


def test_session_cookie_is_enough_without_preferences(tmp_path):
    _cookie_db(tmp_path, ["SID", "SAPISID"])

    assert _has_google_session(tmp_path) is True


def test_preferences_are_the_fallback_when_the_db_is_unreadable(tmp_path):
    """Edge keeps the cookie DB exclusively locked while it runs; Preferences
    is the only signal left there."""
    _prefs(tmp_path)

    assert _has_google_session(tmp_path) is True
