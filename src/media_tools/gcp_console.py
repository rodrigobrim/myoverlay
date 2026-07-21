"""Best-effort Google Cloud Console browser automation.

Google exposes no API to configure an OAuth consent screen or create a
Desktop OAuth client for a personal (no-organization) account - the
`iap oauth-brands` API is org-only and the shared gcloud/ADC client blocks
the YouTube scope. So, exactly like the Race Studio 3 situation (rs3.py),
the only zero-touch path is driving the vendor's own UI. This module drives
the Cloud Console with Playwright and mirrors rs3.py's philosophy: every
step is defensive, any failure is reported as text (never raised), and a
troubleshoot mode snapshots each step so the procedure can be refined when
Google shifts the UI.

What it automates (idempotent - each step is skipped when already done):
  1. OAuth consent screen: configure (External) and publish to production
  2. Desktop OAuth client: create and download its JSON
  3. save the JSON at cfg.youtube.client_secret_file

What it never automates:
  - Google sign-in. Credentials are yours alone: the first run opens the
    sign-in page in the automation's own (persistent) browser profile and
    simply waits for you to log in. Later runs reuse the profile silently.

One-time dependency setup:  uv sync && uv run playwright install chromium
"""

from __future__ import annotations

import re
import shutil
import subprocess
import time
from pathlib import Path

from .config import Config

CONSOLE = "https://console.cloud.google.com"
_STEP_TIMEOUT_MS = 15_000


class _NeedsLogin(Exception):
    """Raised when the Console bounces to Google sign-in. Google rejects
    sign-in attempts inside an automation-controlled browser ('This browser
    or app may not be secure'), so login must happen in a PLAIN browser on
    the same profile - the caller handles that handoff."""


class _Shoot:
    """Numbered per-step screenshots into <library_root>/gcp_troubleshoot/,
    same contract as rs3._Troubleshoot: capture everything, break nothing."""

    def __init__(self, cfg: Config, enabled: bool) -> None:
        self.enabled = enabled
        self.dir = Path(cfg.library_root) / "gcp_troubleshoot"
        self.n = 0
        if enabled:
            self.dir.mkdir(parents=True, exist_ok=True)
            for old in self.dir.glob("*.png"):
                try:
                    old.unlink()
                except OSError:
                    pass

    def snap(self, page, name: str) -> None:
        if not self.enabled:
            return
        self.n += 1
        try:
            page.screenshot(path=str(self.dir / f"{self.n:02d}_{name}.png"), full_page=False)
        except Exception:  # noqa: BLE001 - a snapshot must never break the run
            pass

    def dump_html(self, page, name: str) -> None:
        """The web equivalent of rs3's control-tree dump: the live DOM, for
        working out the real element behind a control a screenshot can't
        disambiguate."""
        if not self.enabled:
            return
        self.n += 1
        try:
            (self.dir / f"{self.n:02d}_{name}.html").write_text(
                page.content(), encoding="utf-8"
            )
        except Exception:  # noqa: BLE001
            pass


def _run_gcloud(args: list[str], **kw):
    """Run gcloud (a .cmd on Windows) via cmd /c so it resolves from PATH."""
    return subprocess.run(["cmd", "/c", "gcloud", *args], **kw)


def ensure_project(cfg: Config, report: list[str]) -> bool:
    """gcloud side of setup: sign in (once, interactive), then create the
    project (default 'myoverlay') or reuse it if it already exists, and enable
    the YouTube Data API. Returns True when the project is ready. The browser
    steps that follow (in setup_google_api) need this done first."""
    if shutil.which("gcloud") is None:
        report.append("! Google Cloud SDK (gcloud) not found on PATH")
        return False
    project = cfg.youtube.project_id or "myoverlay"

    def active_account() -> str:
        who = _run_gcloud(
            ["auth", "list", "--filter=status:ACTIVE", "--format=value(account)"],
            capture_output=True, text=True,
        )
        return (who.stdout or "").strip()

    # 1. Sign in if there is no active account (opens a browser once).
    if not active_account():
        report.append("opening a browser to sign in to Google (one time)...")
        _run_gcloud(["auth", "login", "--brief"])
        if not active_account():
            report.append("! sign-in did not complete; re-run `mt google-setup`")
            return False
    report.append(f"signed in as {active_account()}")

    # 2. Reuse the project if it exists, else create it named 'myoverlay'.
    describe = _run_gcloud(
        ["projects", "describe", project], capture_output=True, text=True
    )
    if describe.returncode == 0:
        report.append(f"reusing existing project '{project}'")
    else:
        report.append(f"creating project '{project}'")
        created = _run_gcloud(
            ["projects", "create", project, "--name=myoverlay"],
            capture_output=True, text=True,
        )
        if created.returncode != 0:
            report.append(
                f"! could not create project '{project}': "
                + (created.stderr or "").strip()[:300]
            )
            return False
    _run_gcloud(["config", "set", "project", project], capture_output=True, text=True)

    # 3. Enable the YouTube Data API (free tier, no billing account needed).
    report.append("enabling the YouTube Data API...")
    enabled = _run_gcloud(
        ["services", "enable", "youtube.googleapis.com", f"--project={project}"],
        capture_output=True, text=True,
    )
    if enabled.returncode != 0:
        report.append(
            "! could not enable the YouTube Data API: "
            + (enabled.stderr or "").strip()[:300]
        )
        return False
    return True


def setup_google_api(cfg: Config, troubleshoot: bool = False) -> list[str]:
    """Configure the Google side of `mt publish` end to end. Returns a report;
    never raises (the manual Console path always remains as fallback)."""
    report: list[str] = []
    try:
        from playwright.sync_api import sync_playwright
    except ImportError:
        report.append(
            "! playwright not installed; run: uv sync && uv run playwright install chromium"
        )
        return report

    # gcloud preamble: sign in, create/reuse the project, enable the API.
    if not ensure_project(cfg, report):
        return report
    project = cfg.youtube.project_id or "myoverlay"

    ts = _Shoot(cfg, troubleshoot)
    profile_dir = Path(cfg.library_root) / "gcp_browser_profile"
    profile_dir.mkdir(parents=True, exist_ok=True)

    try:
        with sync_playwright() as pw:
            # Two attempts: when the first bounces to Google sign-in, the
            # login handoff (plain browser, same profile) runs between them.
            for attempt in (1, 2):
                try:
                    _automated_pass(pw, cfg, project, profile_dir, report, ts)
                    break
                except _NeedsLogin:
                    if attempt == 2 or not _manual_login(profile_dir, report):
                        report.append(
                            "! still not signed in - sign in once in the window that "
                            "opens, close it, then re-run `mt google-setup`"
                        )
                        break
    except Exception as exc:  # noqa: BLE001 - never kill the caller
        report.append(f"! google setup automation failed: {exc!r}")
    if troubleshoot:
        report.append(f"troubleshoot snapshots -> {ts.dir}")
    return report


def _automated_pass(
    pw, cfg: Config, project: str, profile_dir: Path, report: list[str], ts: _Shoot
) -> None:
    """One automated browser session over the whole flow. Raises _NeedsLogin
    when the profile has no (valid) Google session."""
    # Headed + persistent profile: the Google session established by the
    # manual-login handoff lives in the profile and is reused silently here.
    # Engine order: bundled chromium, then installed Chrome, then Edge.
    # (The bundled build can fail to start on some Windows editions -
    # observed as a side-by-side configuration error on Win10 Home -
    # and the OS-installed browsers are the reliable fallback there.)
    browser = None
    engine_errors: list[str] = []
    for channel in (None, "chrome", "msedge"):
        try:
            browser = pw.chromium.launch_persistent_context(
                user_data_dir=str(profile_dir),
                channel=channel,
                headless=False,
                accept_downloads=True,
                viewport={"width": 1400, "height": 900},
            )
            if channel:
                report.append(f"using installed browser: {channel}")
            break
        except Exception as exc:  # noqa: BLE001 - try the next engine
            engine_errors.append(f"{channel or 'bundled chromium'}: {exc}".splitlines()[0])
    if browser is None:
        report.append("! no usable browser engine:")
        report.extend(f"  {e}" for e in engine_errors)
        return
    try:
        page = browser.pages[0] if browser.pages else browser.new_page()
        page.set_default_timeout(_STEP_TIMEOUT_MS)
        _run_flow(cfg, page, project, report, ts)
    finally:
        browser.close()


def _browser_exe() -> Path | None:
    """An installed plain browser for the manual-login handoff."""
    import os

    candidates = [
        Path(os.environ.get("ProgramFiles", r"C:\Program Files"))
        / "Google/Chrome/Application/chrome.exe",
        Path(os.environ.get("ProgramFiles(x86)", r"C:\Program Files (x86)"))
        / "Google/Chrome/Application/chrome.exe",
        Path(os.environ.get("LOCALAPPDATA", "")) / "Google/Chrome/Application/chrome.exe",
        Path(os.environ.get("ProgramFiles(x86)", r"C:\Program Files (x86)"))
        / "Microsoft/Edge/Application/msedge.exe",
    ]
    for c in candidates:
        if c.is_file():
            return c
    return None


def _has_google_session(profile_dir: Path) -> bool:
    """True once the profile holds a Google *auth session* cookie. Only SID /
    __Secure-1PSID / __Secure-3PSID appear after a successful sign-in - the
    pre-login cookies (NID, CONSENT, AEC) are deliberately excluded so this
    never fires on the bare login page."""
    import sqlite3

    for rel in ("Default/Network/Cookies", "Network/Cookies", "Default/Cookies"):
        db = profile_dir / rel
        if not db.is_file():
            continue
        try:
            # immutable=1: read a snapshot without taking a lock while Chrome
            # holds the DB open.
            con = sqlite3.connect(f"file:{db}?immutable=1", uri=True)
            try:
                row = con.execute(
                    "SELECT 1 FROM cookies WHERE host_key LIKE '%.google.com' "
                    "AND name IN ('SID','__Secure-1PSID','__Secure-3PSID') LIMIT 1"
                ).fetchone()
            finally:
                con.close()
            if row:
                return True
        except Exception:  # noqa: BLE001 - locked/absent/mid-write is fine
            continue
    return False


def _close_profile_browsers(profile_dir: Path) -> None:
    """Close every browser process bound to this profile so the automated pass
    can take the profile lock. Safe because we only call it once the session
    cookie is already flushed to disk - a terminate loses nothing."""
    import psutil

    key = str(profile_dir)
    victims = []
    for p in psutil.process_iter(["name", "cmdline"]):
        try:
            name = (p.info["name"] or "").lower()
            if ("chrome" in name or "msedge" in name) and key in " ".join(
                p.info["cmdline"] or []
            ):
                victims.append(p)
        except Exception:  # noqa: BLE001 - vanished process
            continue
    for p in victims:
        try:
            p.terminate()
        except Exception:  # noqa: BLE001
            pass
    _, alive = psutil.wait_procs(victims, timeout=10)
    for p in alive:  # force stragglers so the profile lock is released
        try:
            p.kill()
        except Exception:  # noqa: BLE001
            pass
    time.sleep(2)  # let the OS release the profile lock file


def _manual_login(profile_dir: Path, report: list[str]) -> bool:
    """Google rejects sign-in inside an automation-controlled browser, so open
    a PLAIN browser on the same profile and let the human sign in there. We
    watch the profile for the Google session cookie and close the window
    OURSELVES once it appears - the user just signs in, nothing to close. The
    retry pass then reuses the session; no credential is ever typed by us."""
    import subprocess

    exe = _browser_exe()
    if exe is None:
        report.append("! no installed Chrome/Edge found for the sign-in step")
        return False
    report.append(
        "Google blocks sign-in inside automated browsers - opening a normal "
        "browser window instead. Just sign in; this window closes on its own."
    )
    try:
        proc = subprocess.Popen(
            [
                str(exe),
                f"--user-data-dir={profile_dir}",
                "--no-first-run",
                "--new-window",
                "https://accounts.google.com/ServiceLogin?continue="
                "https%3A%2F%2Fconsole.cloud.google.com%2F",
            ]
        )
    except Exception as exc:  # noqa: BLE001
        report.append(f"! manual-login browser failed: {exc!r}")
        return False

    # Poll: close ourselves the moment the session cookie lands; also honor a
    # user who closes the window by hand; give up after 10 minutes.
    deadline = time.monotonic() + 600
    while time.monotonic() < deadline:
        if proc.poll() is not None:  # user closed it themselves
            time.sleep(2)
            return True
        if _has_google_session(profile_dir):
            report.append("sign-in detected - closing the login window")
            _close_profile_browsers(profile_dir)
            return True
        time.sleep(3)

    report.append("! sign-in not detected within 10 min")
    _close_profile_browsers(profile_dir)
    return False


def _run_flow(cfg: Config, page, project: str, report: list[str], ts: _Shoot) -> None:
    _goto_signed_in(page, f"{CONSOLE}/auth/overview?project={project}", report, ts)

    # -- 1. consent screen ---------------------------------------------------
    if _wait_clickable_text(page, "Get started") is not None:
        ts.snap(page, "consent_start")
        _configure_consent_screen(cfg, page, report, ts)
    elif _has_text(page, "not configured yet"):
        # Positive detection of the unconfigured state but no wizard button -
        # never silently claim success on a page we don't recognize.
        report.append("? consent screen unconfigured but 'Get started' not found")
        ts.snap(page, "consent_unknown")
    else:
        report.append("consent screen already configured")
        ts.snap(page, "consent_existing")

    # -- 2. publish to production -------------------------------------------
    _goto_signed_in(page, f"{CONSOLE}/auth/audience?project={project}", report, ts)
    if _wait_clickable_text(page, "Publish app") is not None:
        _click(page, "button", "Publish app")
        _click(page, "button", "Confirm")  # "Push to production?" dialog
        report.append("consent screen published to production")
        ts.snap(page, "published")
    elif _has_text(page, "In production"):
        report.append("consent screen already in production")
    else:
        report.append("? could not find 'Publish app' nor 'In production' - check manually")
        ts.snap(page, "publish_unknown")

    # -- 3. desktop client + JSON -------------------------------------------
    if cfg.youtube.client_secret_file.is_file():
        report.append(f"client secret already present at {cfg.youtube.client_secret_file}")
        other = _secret_project(cfg.youtube.client_secret_file)
        if other and other != project:
            # Existing file belongs to a different project. Never overwrite it
            # silently - surface the mismatch and let the human decide.
            report.append(
                f"? but it belongs to project '{other}', not '{project}' - move/rename it "
                "and re-run google-setup to mint one for this project"
            )
        return
    # Google only reveals a client's secret ONCE, in the creation dialog
    # ("viewing and downloading client secrets is no longer available" on the
    # client page afterwards). So when the secret file is missing, an existing
    # client is unusable: always create a fresh client and capture its JSON
    # right there. Stale clients can be deleted in the Console (never by this
    # tool - it clicks nothing named Delete).
    _goto_signed_in(page, f"{CONSOLE}/auth/clients/create?project={project}", report, ts)
    _create_desktop_client(cfg, page, report, ts)


def _configure_consent_screen(cfg: Config, page, report: list[str], ts: _Shoot) -> None:
    """Walk the 'Get started' wizard: app info -> External -> contact ->
    agree -> Create. Field names are matched loosely so minor UI wording
    changes don't break the flow."""
    _click(page, "button", "Get started")

    # App information: name + support email.
    _fill_first_textbox(page, re.compile("app name", re.I), "media-tools YouTube")
    _pick_first_option(page, re.compile("support email", re.I))
    _click(page, "button", "Next")

    # Audience: External (personal accounts have no Internal option that works).
    _check_radio(page, re.compile("external", re.I))
    _click(page, "button", "Next")

    # Contact information.
    _fill_first_textbox(page, re.compile("email", re.I), _account_email(page) or "")
    _click(page, "button", "Next")

    # Agree + create. This acceptance of Google's user-data policy is the
    # explicit, user-authorized purpose of this tool (see module docstring).
    _check_first_checkbox(page)
    _click(page, "button", "Continue")
    _click(page, "button", "Create")
    time.sleep(3)
    ts.snap(page, "consent_created")
    report.append("consent screen configured (External)")


def _create_desktop_client(cfg: Config, page, report: list[str], ts: _Shoot) -> None:
    ts.snap(page, "client_create_form")
    # Close any stray overlay (e.g. the search panel) before touching the form.
    try:
        page.keyboard.press("Escape")
    except Exception:  # noqa: BLE001
        pass
    # Application type dropdown -> Desktop app. Target it by its LABEL: a bare
    # role=combobox query grabs the Console's global search bar instead.
    opened = False
    for locate in (
        lambda: page.get_by_label(re.compile("application type", re.I)).first,
        lambda: page.get_by_role("combobox", name=re.compile("application type", re.I)).first,
        lambda: page.get_by_text(re.compile(r"^\s*Application type\s*\*?\s*$", re.I)).first,
    ):
        try:
            locate().click(timeout=8_000)
            opened = True
            break
        except Exception:  # noqa: BLE001
            continue
    try:
        if not opened:
            raise RuntimeError("application-type dropdown not found")
        page.get_by_role("option", name=re.compile("desktop", re.I)).first.click(timeout=8_000)
    except Exception:  # noqa: BLE001
        report.append("? could not select 'Desktop app' application type")
        ts.snap(page, "client_type_fail")
        return
    _fill_first_textbox(page, re.compile("name", re.I), "media-tools desktop")

    # The Console's sandboxed frame swallows the dialog's 'Download JSON'
    # (clicks land, no download event ever fires), so the reliable capture is
    # the create API response itself: it carries the client id and the
    # GOCSPX- secret that the dialog renders. Sniff responses around the
    # Create click and synthesize the standard installed-app JSON from them.
    sniffed: list = []

    def _on_response(resp) -> None:
        try:
            if re.search(r"client|oauth", resp.url, re.I):
                sniffed.append(resp)
        except Exception:  # noqa: BLE001
            pass

    page.on("response", _on_response)
    _click(page, "button", "Create")
    time.sleep(4)  # let the create call and dialog finish
    ts.snap(page, "client_created")

    saved = _download_json(page, cfg.youtube.client_secret_file, report)
    if not saved:
        saved = _secret_from_responses(cfg, sniffed, report)
    try:
        page.remove_listener("response", _on_response)
    except Exception:  # noqa: BLE001
        pass
    if saved:
        report.append(f"+ client secret saved -> {cfg.youtube.client_secret_file}")
        report.append("next: run `mt publish` once and click Allow in the browser")
    else:
        report.append(
            "? client created but its secret was not captured - the secret is only "
            "shown once, so delete this client in the Console and re-run"
        )
        ts.snap(page, "download_fail")
        ts.dump_html(page, "download_fail_dom")


def _secret_from_responses(cfg: Config, responses: list, report: list[str]) -> bool:
    """Extract client id + GOCSPX- secret from the create call's response and
    write the standard installed-app client_secret.json ourselves."""
    import json

    for resp in reversed(responses):  # newest first: the create call is last
        try:
            body = resp.text()
        except Exception:  # noqa: BLE001 - body may be unavailable/binary
            continue
        secret = re.search(r"GOCSPX-[\w-]+", body)
        client = re.search(r"[0-9]+-[a-z0-9]+\.apps\.googleusercontent\.com", body)
        if not (secret and client):
            continue
        data = {
            "installed": {
                "client_id": client.group(0),
                "project_id": cfg.youtube.project_id,
                "auth_uri": "https://accounts.google.com/o/oauth2/auth",
                "token_uri": "https://oauth2.googleapis.com/token",
                "auth_provider_x509_cert_url": "https://www.googleapis.com/oauth2/v1/certs",
                "client_secret": secret.group(0),
                "redirect_uris": ["http://localhost"],
            }
        }
        dest = cfg.youtube.client_secret_file
        dest.parent.mkdir(parents=True, exist_ok=True)
        dest.write_text(json.dumps(data, indent=2), encoding="utf-8")
        report.append("client secret captured from the create API response")
        return True
    return False


def _download_json(page, dest: Path, report: list[str] | None = None) -> bool:
    # 'Download JSON' is a link/text, not role=button. Scope to the dialog
    # first: a page-wide text query can land on a HIDDEN duplicate node
    # (aria-live announcements, templates) and the click then times out on an
    # invisible element. Unanchored text because the element text includes its
    # Material icon ligature ("download Download JSON").
    dialog = page.locator("mat-dialog-container, [role='dialog'], [role='alertdialog']").last
    candidates = [
        lambda: dialog.get_by_text(re.compile("download json", re.I)).last,
        lambda: page.get_by_text(re.compile("download json", re.I)).last,
        lambda: page.get_by_text(re.compile("download json", re.I)).first,
        lambda: page.get_by_role("button", name=re.compile("download", re.I)).first,
    ]
    for i, locate in enumerate(candidates):
        try:
            loc = locate()
            if loc is None:
                continue
            with page.expect_download(timeout=30_000) as dl:
                loc.click(timeout=8_000)
            dest.parent.mkdir(parents=True, exist_ok=True)
            dl.value.save_as(str(dest))
            return True
        except Exception as exc:  # noqa: BLE001
            if report is not None:
                report.append(f"~ download candidate {i}: {str(exc).splitlines()[0]}")
            continue
    return False


# --- small defensive helpers (the rs3.py "click named control" idiom) -------


def _goto_signed_in(page, url: str, report: list[str], ts: _Shoot) -> None:
    """Navigate; if Google bounces to sign-in, WAIT for the human. The
    automation never types into accounts.google.com - credentials are the
    one step that stays manual by design."""
    page.goto(url)
    try:
        page.wait_for_load_state("domcontentloaded")
    except Exception:  # noqa: BLE001
        pass
    # URL PREFIX check, not substring: the sign-in URL embeds
    # console.cloud.google.com in its continue= parameter.
    if "accounts.google.com" in page.url:
        ts.snap(page, "signin_bounce")
        raise _NeedsLogin
    time.sleep(2)  # console SPAs settle slowly after load


def _visible(page, role: str, name: str) -> bool:
    try:
        return page.get_by_role(role, name=re.compile(rf"^{re.escape(name)}$", re.I)).first.is_visible()
    except Exception:  # noqa: BLE001
        return False


def _wait_visible(page, role: str, name: str, timeout_s: float = 25.0) -> bool:
    """is_visible() with patience: the Console SPA keeps rendering long after
    'load', so instant checks race the page and pick the wrong branch."""
    try:
        page.get_by_role(role, name=re.compile(rf"^{re.escape(name)}$", re.I)).first.wait_for(
            state="visible", timeout=timeout_s * 1000
        )
        return True
    except Exception:  # noqa: BLE001
        return False


def _wait_clickable_text(page, text: str, timeout_s: float = 25.0):
    """Locator for a control by its exact text, whatever element the Console
    renders it as (its 'buttons' are variously <button>, <a> or custom tags -
    a role=button query missed a perfectly visible Get started). Returns the
    locator once visible, else None."""
    loc = page.get_by_text(re.compile(rf"^\s*{re.escape(text)}\s*$", re.I)).first
    try:
        loc.wait_for(state="visible", timeout=timeout_s * 1000)
        return loc
    except Exception:  # noqa: BLE001
        return None


def _click(page, role: str, name: str) -> bool:
    """Click a control by role+name, falling back to exact-text match (the
    Console renders 'buttons' as assorted elements)."""
    try:
        page.get_by_role(role, name=re.compile(rf"^{re.escape(name)}$", re.I)).first.click(
            timeout=6_000
        )
        time.sleep(1)
        return True
    except Exception:  # noqa: BLE001
        pass
    loc = _wait_clickable_text(page, name, timeout_s=6.0)
    if loc is None:
        return False
    try:
        loc.click()
        time.sleep(1)
        return True
    except Exception:  # noqa: BLE001
        return False


def _has_text(page, text: str) -> bool:
    try:
        return page.get_by_text(text, exact=False).first.is_visible()
    except Exception:  # noqa: BLE001
        return False


def _fill_first_textbox(page, label: re.Pattern, value: str) -> None:
    if not value:
        return
    try:
        page.get_by_role("textbox", name=label).first.fill(value)
    except Exception:  # noqa: BLE001
        pass


def _pick_first_option(page, label: re.Pattern) -> None:
    """Open a labeled combobox and pick its first real option (the support
    email dropdown has exactly one entry on a personal account)."""
    try:
        page.get_by_role("combobox", name=label).first.click()
        page.get_by_role("option").first.click()
    except Exception:  # noqa: BLE001
        pass


def _check_radio(page, label: re.Pattern) -> None:
    try:
        page.get_by_role("radio", name=label).first.check()
    except Exception:  # noqa: BLE001
        pass


def _check_first_checkbox(page) -> None:
    try:
        page.get_by_role("checkbox").first.check()
    except Exception:  # noqa: BLE001
        pass


def _secret_project(path: Path) -> str | None:
    """project_id inside an installed-app client secret JSON, if readable."""
    import json

    try:
        return json.loads(path.read_text(encoding="utf-8-sig"))["installed"]["project_id"]
    except Exception:  # noqa: BLE001 - unreadable/foreign file is fine
        return None


def _account_email(page) -> str | None:
    """The signed-in account email, read from the Console top bar."""
    try:
        m = re.search(r"[\w.+-]+@[\w-]+\.[\w.]+", page.content())
        return m.group(0) if m else None
    except Exception:  # noqa: BLE001
        return None
