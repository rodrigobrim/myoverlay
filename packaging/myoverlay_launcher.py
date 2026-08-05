"""MyOverlay - self-updating launcher for the media-tools pipeline.

This is the entry point of the frozen (PyInstaller) executable that friends
run. It bundles a Python runtime, every pipeline dependency, MinGit and
ffmpeg - nothing needs to be installed.

On every start it:
  1. clones the repo on first run (into ~\\myoverlay\\repo), or fast-forward
     pulls new commits; data from an older install under %LOCALAPPDATA%
     \\MyOverlay is moved to ~\\myoverlay once;
  2. creates ~\\myoverlay\\config.toml on first run from the repo's shipped
     config.toml template (every option commented out; the Google
     credentials live next to it: client_secret.json and google-token);
  3. puts the bundled git/ffmpeg on PATH;
  4. imports the *pulled* media_tools package and forwards the command line
     to its CLI - so `MyOverlay run`, `MyOverlay slice ...` etc. behave
     exactly like `uv run mt ...` in a dev checkout.

Because the pipeline source comes from the repo (not the frozen bundle),
friends get code updates automatically. Only when the repo grows a NEW
third-party dependency does the exe need a rebuild - that failure mode is
detected and explained.

Environment overrides:
  MYOVERLAY_REPO       working copy location (default ~\\myoverlay\\repo)
  MYOVERLAY_REPO_URL   git remote to clone/pull (default the official repo)
  MYOVERLAY_NO_UPDATE  set to 1 to skip the git pull (same as --no-update)
  MYOVERLAY_BRANCH     run this branch instead of the default (same as
                       --branch NAME). A branch that exists only locally is
                       checked out and run as-is (no pull, never reset) - the
                       way to test unmerged work through the exe.
"""

from __future__ import annotations

import os
import re
import shutil
import subprocess
import sys
from pathlib import Path

DEFAULT_REPO_URL = "https://github.com/rodrigobrim/myoverlay.git"


def bundle_dir() -> Path:
    return Path(getattr(sys, "_MEIPASS", Path(__file__).resolve().parent))


# Open by _open_status_log() for google-setup runs: mirrors every say() line
# into ~\myoverlay\google-setup.log, which the MSI wizard tails for live
# status. Without it the clone/pull phase is invisible from the wizard.
_TEE = None


def say(msg: str) -> None:
    print(f"[MyOverlay] {msg}")
    if _TEE is not None:
        try:
            _TEE.write(msg + "\n")
            _TEE.flush()
        except OSError:
            pass


def _open_status_log(home: Path) -> None:
    """Start the live status log for a google-setup run (truncates any old
    one). MYOVERLAY_SETUP_LOG_ACTIVE tells media_tools' google-setup to append
    to it instead of truncating it again."""
    global _TEE
    try:
        home.mkdir(parents=True, exist_ok=True)
        _TEE = (home / "google-setup.log").open("w", encoding="utf-8")
        os.environ["MYOVERLAY_SETUP_LOG_ACTIVE"] = "1"
    except OSError:
        _TEE = None


def run_git(git: Path, args: list[str], cwd: Path | None = None, timeout: int = 300):
    return subprocess.run(
        [str(git), *args],
        cwd=str(cwd) if cwd else None,
        capture_output=True,
        text=True,
        timeout=timeout,
    )


def default_home() -> Path:
    """The user-facing data dir: config.toml, google-token, client_secret.json
    and the managed repo clone all live here."""
    return Path.home() / "myoverlay"


def default_repo_path() -> Path:
    return default_home() / "repo"


def _legacy_appdata_dir() -> Path | None:
    """Where installs before the ~\\myoverlay layout kept everything."""
    base = os.environ.get("LOCALAPPDATA")
    return Path(base) / "MyOverlay" if base else None


def migrate_legacy_layout(home: Path, repo: Path) -> None:
    """One-time move of an older install's data into ~\\myoverlay.

    Old layout: everything under %LOCALAPPDATA%\\MyOverlay - the clone with
    config.toml/token.json/client_secret.json inside it, plus the sign-in
    browser profile. New layout: the clone at <home>/repo stays a disposable
    code cache, while config and credentials live in <home> directly. Each
    piece moves only when the destination does not exist yet, so a partially
    migrated (or already current) setup is never overwritten.
    """
    old = _legacy_appdata_dir()
    if old is None or not old.is_dir() or old.resolve() == home.resolve():
        return
    try:
        home.mkdir(parents=True, exist_ok=True)
        old_repo = old / "repo"
        if old_repo.is_dir() and not repo.exists():
            say(f"moving your MyOverlay data to {home}")
            shutil.move(str(old_repo), str(repo))
        moves = [
            (repo / "token.json", home / "google-token"),
            (repo / "client_secret.json", home / "client_secret.json"),
        ]
        # config.toml is only a USER file in pre-rename clones (they shipped
        # config.example.toml as the template). In current clones the tracked
        # template itself is named config.toml and must stay in the repo.
        if (repo / "config.example.toml").is_file():
            moves.insert(0, (repo / "config.toml", home / "config.toml"))
        for src, dst in moves:
            if src.exists() and not dst.exists():
                shutil.move(str(src), str(dst))
    except OSError as exc:
        say(f"warning: could not finish moving data to {home}: {exc}")


def _managed_marker(repo: Path) -> Path:
    # Inside .git so it can never show up as an untracked file.
    return repo / ".git" / "myoverlay-managed"


def is_managed(repo: Path) -> bool:
    """True when this clone is ours to reset.

    The default location is always ours (that also covers clones made by
    older builds, before the marker existed). A custom MYOVERLAY_REPO is
    only ours if we created it - it may be someone's dev checkout, which
    must never be hard-reset.
    """
    if _managed_marker(repo).is_file():
        return True
    try:
        return repo.resolve() == default_repo_path().resolve()
    except OSError:
        return False


def _resync(git: Path, repo: Path) -> bool:
    """Hard-reset the managed clone onto the remote.

    Only ever called for clones this launcher created: it is a disposable
    cache of the code, so local commits/edits to tracked files are not
    something to preserve (that includes the shipped config.toml template;
    the USER config lives in ~\\myoverlay, outside the clone). Untracked
    files like credentials survive untouched.
    """
    fetch = run_git(git, ["fetch", "origin"], cwd=repo, timeout=300)
    if fetch.returncode != 0:
        return False
    head = run_git(git, ["symbolic-ref", "--short", "refs/remotes/origin/HEAD"], cwd=repo)
    target = head.stdout.strip() if head.returncode == 0 else "origin/main"
    reset = run_git(git, ["reset", "--hard", target], cwd=repo, timeout=120)
    return reset.returncode == 0


def _current_branch(git: Path, repo: Path) -> str | None:
    proc = run_git(git, ["branch", "--show-current"], cwd=repo)
    return proc.stdout.strip() or None if proc.returncode == 0 else None


def _has_upstream(git: Path, repo: Path) -> bool:
    proc = run_git(
        git, ["rev-parse", "--abbrev-ref", "--symbolic-full-name", "@{upstream}"], cwd=repo
    )
    return proc.returncode == 0


def _default_branch(git: Path, repo: Path) -> str:
    """The clone's default branch (origin/HEAD), 'main' when unreadable."""
    proc = run_git(git, ["symbolic-ref", "--short", "refs/remotes/origin/HEAD"], cwd=repo)
    ref = proc.stdout.strip() if proc.returncode == 0 else ""
    return ref.split("/", 1)[1] if "/" in ref else "main"


def _return_to_default_branch(git: Path, repo: Path) -> None:
    """Put a managed clone back on the default branch before pulling.

    `--branch NAME` is sticky - it checks the branch out and nothing ever
    switches back - so a single old --branch run left the clone pulling that
    branch forever. The pull succeeds ("Already up to date" for that branch),
    the resync path never fires because nothing failed, and the launcher keeps
    reporting "pipeline is up to date" while the code sits commits behind main:
    the one stale state that announces itself as current.

    Local edits that block the switch are stashed, never discarded - the clone
    is disposable, but deciding that someone's uncommitted work is not is a
    call this launcher does not get to make silently.
    """
    branch = _default_branch(git, repo)
    if _current_branch(git, repo) == branch:
        return
    if run_git(git, ["checkout", branch], cwd=repo, timeout=120).returncode != 0:
        stashed = run_git(
            git,
            # A stash writes commit objects, so it needs an identity. Supply
            # one: a friend's clone may have no user.name/user.email at all,
            # and "Please tell me who you are" must not be what stands between
            # them and a current pipeline.
            ["-c", "user.email=myoverlay@localhost", "-c", "user.name=MyOverlay",
             "stash", "push", "-u",
             "-m", f"MyOverlay: local edits before returning to {branch}"],
            cwd=repo,
            timeout=120,
        )
        if stashed.returncode != 0:
            say(f"warning: could not return this copy to {branch}; running it as-is")
            return
        say(f"local changes in this copy were stashed to return to {branch}")
        say("(recover them with: git stash list / git stash pop)")
        if run_git(git, ["checkout", branch], cwd=repo, timeout=120).returncode != 0:
            say(f"warning: could not return this copy to {branch}; running it as-is")
            return
    say(f"returned this copy to the {branch} branch")


def _checkout_branch(git: Path, repo: Path, branch: str) -> None:
    if _current_branch(git, repo) == branch:
        return
    proc = run_git(git, ["checkout", branch], cwd=repo, timeout=120)
    if proc.returncode != 0:
        say(f"ERROR: branch {branch!r} not found in {repo}")
        say(proc.stderr.strip()[:400])
        sys.exit(2)
    say(f"switched to branch {branch}")


def ensure_repo(
    git: Path, repo: Path, url: str, skip_update: bool, branch: str | None = None
) -> None:
    # .git is a directory in a normal clone and a file in a git worktree
    # (it points at the real one): both are checkouts we must not clone over.
    if not (repo / ".git").exists():
        say(f"first run: downloading the pipeline from {url}")
        repo.parent.mkdir(parents=True, exist_ok=True)
        proc = run_git(git, ["clone", url, str(repo)], timeout=600)
        if proc.returncode != 0:
            say("ERROR: could not download the pipeline repository.")
            say(proc.stderr.strip()[:800])
            sys.exit(2)
        _managed_marker(repo).write_text("created by MyOverlay\n", encoding="ascii")
        say("download complete")
        if branch:
            _checkout_branch(git, repo, branch)
        return

    if branch:
        # An explicitly chosen branch is developer intent: check it out and
        # ff-pull only if it tracks a remote. A local-only branch runs as-is,
        # and a chosen branch is NEVER hard-reset - it may hold unmerged work.
        _checkout_branch(git, repo, branch)
        if skip_update:
            return
        if not _has_upstream(git, repo):
            say(f"running local branch {branch} (no remote tracking; skipping update)")
            return
        proc = run_git(git, ["pull", "--ff-only"], cwd=repo, timeout=120)
        if proc.returncode == 0:
            head = run_git(git, ["rev-parse", "--short", "HEAD"], cwd=repo)
            say(f"branch {branch} at {head.stdout.strip()}")
        else:
            say(f"warning: could not update branch {branch} (offline, or it diverged")
            say("from its remote); continuing with the current version")
        return
    if skip_update:
        return

    # No branch asked for means the default branch, not "whatever an old
    # --branch run left checked out". Managed clones only: a dev checkout's
    # branch is the developer's business.
    if is_managed(repo):
        _return_to_default_branch(git, repo)

    proc = run_git(git, ["pull", "--ff-only"], cwd=repo, timeout=120)
    if proc.returncode == 0:
        out = (proc.stdout or "").strip()
        if "Already up to date" in out or "Already up-to-date" in out:
            say("pipeline is up to date")
        else:
            head = run_git(git, ["rev-parse", "--short", "HEAD"], cwd=repo)
            say(f"pipeline updated to {head.stdout.strip()}")
        return

    # A fast-forward is impossible: either the network is down, or this clone
    # diverged from the remote (rewritten history, stray local commit). Never
    # silently keep running old code - re-sync the managed clone instead.
    if not is_managed(repo):
        say("warning: could not update (offline, or this checkout is not managed by")
        say("MyOverlay); continuing with the current version")
        return
    say("this copy diverged from the remote; re-syncing to the official version")
    if _resync(git, repo):
        head = run_git(git, ["rev-parse", "--short", "HEAD"], cwd=repo)
        say(f"pipeline re-synced to {head.stdout.strip()}")
    else:
        say("warning: could not reach the remote (offline?); using the current version")


def _parse_settings_yaml(text: str) -> dict:
    """Parse the flat `key: value` install_settings.yaml.

    Deliberately tiny (no PyYAML dependency): the installer only ever writes a
    flat map of scalars. Splits on the first colon (so Windows paths like
    C:/... keep their drive letter), unquotes, and coerces true/false to bool
    to match the old JSON semantics of google_skipped.
    """
    out: dict = {}
    for line in text.splitlines():
        line = line.strip()
        if not line or line.startswith("#"):
            continue
        key, sep, value = line.partition(":")
        if not sep:
            continue
        key = key.strip()
        value = value.strip()
        if len(value) >= 2 and value[0] == value[-1] and value[0] in "\"'":
            value = value[1:-1]
        low = value.lower()
        out[key] = True if low == "true" else False if low == "false" else value
    return out


def installer_settings() -> dict:
    """Choices made in the MSI setup wizard, if this exe was installed by it.

    The installer writes install_settings.yaml next to MyOverlay.exe:
      language: pt
      resolution: fhd
      client_secret: C:\\...\\client_secret.json
      google_skipped: false
      install_dir: C:/Program Files/MyOverlay
    A zip/dev deployment has no such file; everything keeps its default.
    """
    exe_dir = (
        Path(sys.executable).resolve().parent
        if getattr(sys, "frozen", False)
        else Path(__file__).resolve().parent
    )
    f = exe_dir / "install_settings.yaml"
    if not f.is_file():
        return {}
    try:
        return _parse_settings_yaml(f.read_text(encoding="utf-8-sig"))
    except OSError:
        say(f"warning: could not read {f}; using default settings")
        return {}


def _apply_installer_settings(cfg: Path, settings: dict) -> None:
    """Seed a just-created config.toml with the setup wizard's choices."""
    # The template ships every option commented out, so a wizard choice both
    # sets the value AND uncomments the line ('# language = "en"' as much as
    # 'language = "en"').
    text = cfg.read_text(encoding="utf-8-sig")
    lang = settings.get("language")
    if lang:
        text = re.sub(
            r'(?m)^#?\s*language = ".*"$', f'language = "{lang}"', text, count=1
        )
        say(f"video output language: {lang}")
    res = settings.get("resolution")
    if res:
        text = re.sub(
            r'(?m)^#?\s*resolution = ".*?"$', f'resolution = "{res}"', text, count=1
        )
        say(f"default output resolution: {res}")
    cfg.write_text(text, encoding="utf-8")

    secret = settings.get("client_secret")
    dest = cfg.parent / "client_secret.json"
    if secret and Path(secret).is_file() and not dest.is_file():
        shutil.copy2(secret, dest)
        say("Google API client secret installed (from the setup wizard)")
    elif settings.get("google_skipped"):
        say("note: Google API setup was skipped during install -")
        say("YouTube publishing is disabled until you configure it (README).")


def _runtime_install_dir() -> str | None:
    """Where this frozen exe actually runs from (its own directory), or None in
    a dev checkout. Ground truth for locating the bundled tools - it stays
    correct even if the install was moved."""
    if not getattr(sys, "frozen", False):
        return None
    return str(Path(sys.executable).resolve().parent)


# The code default for [tools] install_dir (see ToolsConfig): only a
# DIFFERENT location is worth writing into config.toml.
DEFAULT_INSTALL_DIR = "C:/Program Files/MyOverlay"


def _upsert_install_dir(cfg: Path, install_dir: str) -> None:
    """Write [tools] install_dir into config.toml, refreshing a stale value.

    Idempotent: a no-op when the value is already current, so it doesn't
    rewrite the file on every launch - and the default install location is
    never written at all (it lives in the code; config.toml only carries
    deviations). Forward slashes keep the TOML string valid without backslash
    escaping. Modeled on gcp_console._persist_project_id.
    """
    value = install_dir.replace("\\", "/").rstrip("/")
    try:
        text = cfg.read_bytes().decode("utf-8-sig")
    except OSError:
        return
    has_active = re.search(r"(?m)^\s*install_dir\s*=", text)
    if value == DEFAULT_INSTALL_DIR and not has_active:
        return  # default location, nothing overridden: keep the line commented
    if has_active:
        if re.search(rf'(?m)^\s*install_dir\s*=\s*"{re.escape(value)}"\s*$', text):
            return  # already current
        text = re.sub(r"(?m)^(\s*)install_dir\s*=.*$", rf'\1install_dir = "{value}"', text)
    elif re.search(r"(?m)^\[tools\]", text):
        text = re.sub(r"(?m)^(\[tools\][^\n]*)$", rf'\1\ninstall_dir = "{value}"', text, count=1)
    else:
        text = text.rstrip() + f'\n\n[tools]\ninstall_dir = "{value}"\n'
    # Normalize to LF and write without newline translation (see the same
    # trick in gcp_console._persist_project_id) so tomllib doesn't choke.
    text = text.replace("\r\n", "\n").replace("\r", "\n")
    try:
        cfg.write_text(text, encoding="utf-8", newline="\n")
    except OSError:
        pass


def ensure_config(repo: Path, data_dir: Path) -> None:
    cfg = data_dir / "config.toml"
    # The repo ships config.toml itself as the template: every option listed,
    # defaults commented out, valid as-is. (In a dev checkout data_dir IS the
    # repo, so cfg and the template are the same file and no copy happens.)
    template = repo / "config.toml"
    settings = installer_settings()
    if not cfg.is_file():
        if not template.is_file():
            return
        shutil.copy2(template, cfg)
        _apply_installer_settings(cfg, settings)
        say("=" * 62)
        say("Created your configuration file:")
        say(f"    {cfg}")
        say("It works as-is; every option inside is documented - uncomment a")
        say("line only to change it from the default.")
        say("=" * 62)

    # Record (and keep current, across reinstalls) where the frozen app runs
    # from, so the pipeline resolves the bundled ffmpeg / gcloud by full path.
    install_dir = _runtime_install_dir() or settings.get("install_dir")
    if install_dir and cfg.is_file():
        _upsert_install_dir(cfg, str(install_dir))


def main() -> None:
    bundle = bundle_dir()
    git = bundle / "git" / "cmd" / "git.exe"
    ffmpeg_dir = bundle / "ffmpeg"
    if not git.is_file():
        say(f"ERROR: bundled git missing at {git} - broken build")
        sys.exit(2)
    if not (ffmpeg_dir / "ffmpeg.exe").is_file():
        say(f"ERROR: bundled ffmpeg missing at {ffmpeg_dir} - broken build")
        sys.exit(2)
    # Point the pipeline at the bundled binaries by full path (media_tools.tools
    # reads these), so it never picks up a different ffmpeg/gcloud that happens
    # to be first on PATH. The PATH prepend below stays too, as a compatibility
    # bridge: the pipeline updates via git pull independently of this exe, so an
    # old exe (no env vars) running new code, and a new exe running old code
    # (bare names), both still resolve the bundled tools.
    os.environ["MYOVERLAY_FFMPEG_DIR"] = str(ffmpeg_dir)
    # Bundled tools first on PATH: the pipeline invokes ffmpeg/ffprobe by name.
    path_parts = [str(ffmpeg_dir), str(git.parent)]
    # The MSI installs the Google Cloud SDK next to the exe (not inside the
    # frozen bundle) and adds it to the machine PATH - but a process launched
    # by the installer itself may not see that change yet. Add it directly so
    # `mt google-setup` finds gcloud on the very first run after install.
    exe_dir = Path(sys.executable).resolve().parent if getattr(sys, "frozen", False) else None
    if exe_dir is not None:
        gcloud_bin = exe_dir / "google-cloud-sdk" / "bin"
        if gcloud_bin.is_dir():
            path_parts.append(str(gcloud_bin))
            os.environ["MYOVERLAY_GCLOUD_BIN"] = str(gcloud_bin)
    os.environ["PATH"] = os.pathsep.join(path_parts + [os.environ.get("PATH", "")])

    argv = list(sys.argv[1:])
    skip_update = os.environ.get("MYOVERLAY_NO_UPDATE") == "1"
    if "--no-update" in argv:
        argv.remove("--no-update")
        skip_update = True

    # --branch NAME / --branch=NAME (or MYOVERLAY_BRANCH): run that branch of
    # the repo - incl. a local-only branch with unmerged work - via the exe.
    branch = os.environ.get("MYOVERLAY_BRANCH") or None
    for i, arg in enumerate(argv):
        if arg == "--branch" and i + 1 < len(argv):
            branch = argv[i + 1]
            del argv[i : i + 2]
            break
        if arg.startswith("--branch="):
            branch = arg.split("=", 1)[1]
            del argv[i]
            break

    repo = Path(os.environ.get("MYOVERLAY_REPO") or default_repo_path())
    url = os.environ.get("MYOVERLAY_REPO_URL", DEFAULT_REPO_URL)

    # Managed clone: config and credentials live in ~\myoverlay, next to the
    # repo. A custom MYOVERLAY_REPO (a dev checkout) keeps the classic layout
    # with config.toml inside the checkout itself.
    if is_managed(repo):
        data_dir = default_home()
        if "google-setup" in argv:
            _open_status_log(data_dir)
        migrate_legacy_layout(data_dir, repo)
    else:
        data_dir = repo

    ensure_repo(git, repo, url, skip_update, branch)
    ensure_config(repo, data_dir)

    src = repo / "src"
    if not (src / "media_tools").is_dir():
        say(f"ERROR: {src} does not contain media_tools - wrong repository?")
        sys.exit(2)
    sys.path.insert(0, str(src))
    os.chdir(data_dir)  # config.toml discovery + relative credential paths

    try:
        from media_tools.cli import app
    except ImportError as exc:
        say(f"ERROR: the pipeline needs a package this launcher build lacks: {exc}")
        say("Ask for an updated MyOverlay build (the code moved ahead of it).")
        sys.exit(2)

    sys.argv = ["MyOverlay", *argv]
    app()


if __name__ == "__main__":
    main()
