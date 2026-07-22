"""myoverlay - self-updating launcher for the media-tools pipeline.

This is the entry point of the frozen (PyInstaller) executable that friends
run. It bundles a Python runtime, every pipeline dependency, MinGit and
ffmpeg - nothing needs to be installed.

On every start it:
  1. clones the repo on first run (into %LOCALAPPDATA%\\myoverlay\\repo),
     or fast-forward pulls new commits;
  2. creates config.toml from config.example.toml on first run;
  3. puts the bundled git/ffmpeg on PATH;
  4. imports the *pulled* media_tools package and forwards the command line
     to its CLI - so `myoverlay run`, `myoverlay slice ...` etc. behave
     exactly like `uv run mt ...` in a dev checkout.

Because the pipeline source comes from the repo (not the frozen bundle),
friends get code updates automatically. Only when the repo grows a NEW
third-party dependency does the exe need a rebuild - that failure mode is
detected and explained.

Environment overrides:
  MYOVERLAY_REPO       working copy location (default %LOCALAPPDATA%\\myoverlay\\repo)
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


def say(msg: str) -> None:
    print(f"[myoverlay] {msg}")


def run_git(git: Path, args: list[str], cwd: Path | None = None, timeout: int = 300):
    return subprocess.run(
        [str(git), *args],
        cwd=str(cwd) if cwd else None,
        capture_output=True,
        text=True,
        timeout=timeout,
    )


def default_repo_path() -> Path:
    return Path(os.environ["LOCALAPPDATA"]) / "myoverlay" / "repo"


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
    something to preserve. config.toml and token.json are gitignored, so
    they survive untouched.
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
    if not (repo / ".git").is_dir():
        say(f"first run: downloading the pipeline from {url}")
        repo.parent.mkdir(parents=True, exist_ok=True)
        proc = run_git(git, ["clone", url, str(repo)], timeout=600)
        if proc.returncode != 0:
            say("ERROR: could not download the pipeline repository.")
            say(proc.stderr.strip()[:800])
            sys.exit(2)
        _managed_marker(repo).write_text("created by myoverlay\n", encoding="ascii")
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
        say("myoverlay); continuing with the current version")
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

    The installer writes install_settings.yaml next to myoverlay.exe:
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


def _apply_installer_settings(repo: Path, cfg: Path, settings: dict) -> None:
    """Seed a just-created config.toml with the setup wizard's choices."""
    text = cfg.read_text(encoding="utf-8-sig")
    lang = settings.get("language")
    if lang:
        text = re.sub(r'(?m)^language = ".*"$', f'language = "{lang}"', text, count=1)
        say(f"video output language: {lang}")
    res = settings.get("resolution")
    if res:
        text = re.sub(
            r'(?m)^resolution = ".*?"', f'resolution = "{res}"', text, count=1
        )
        say(f"default output resolution: {res}")
    cfg.write_text(text, encoding="utf-8")

    secret = settings.get("client_secret")
    if secret and Path(secret).is_file() and not (repo / "client_secret.json").is_file():
        shutil.copy2(secret, repo / "client_secret.json")
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


def _upsert_install_dir(cfg: Path, install_dir: str) -> None:
    """Write [tools] install_dir into config.toml, refreshing a stale value.

    Idempotent: a no-op when the value is already current, so it doesn't
    rewrite the file on every launch. Forward slashes keep the TOML string
    valid without backslash escaping. Modeled on gcp_console._persist_project_id.
    """
    value = install_dir.replace("\\", "/").rstrip("/")
    try:
        text = cfg.read_bytes().decode("utf-8-sig")
    except OSError:
        return
    if re.search(r"(?m)^\s*install_dir\s*=", text):
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


def ensure_config(repo: Path) -> None:
    cfg = repo / "config.toml"
    example = repo / "config.example.toml"
    settings = installer_settings()
    if not cfg.is_file():
        if not example.is_file():
            return
        shutil.copy2(example, cfg)
        _apply_installer_settings(repo, cfg, settings)
        say("=" * 62)
        say("Created your configuration file:")
        say(f"    {cfg}")
        say("Open it in Notepad and set at least:")
        say("    library_root      (where processed videos will live)")
        say("    [mychron] rs3_data_dirs  (Race Studio 3 data folder)")
        say("For YouTube upload, see the README section 'YouTube setup'.")
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

    ensure_repo(git, repo, url, skip_update, branch)
    ensure_config(repo)

    src = repo / "src"
    if not (src / "media_tools").is_dir():
        say(f"ERROR: {src} does not contain media_tools - wrong repository?")
        sys.exit(2)
    sys.path.insert(0, str(src))
    os.chdir(repo)  # config.toml discovery + relative paths

    try:
        from media_tools.cli import app
    except ImportError as exc:
        say(f"ERROR: the pipeline needs a package this launcher build lacks: {exc}")
        say("Ask for an updated myoverlay build (the code moved ahead of it).")
        sys.exit(2)

    sys.argv = ["myoverlay", *argv]
    app()


if __name__ == "__main__":
    main()
