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
"""

from __future__ import annotations

import json
import os
import re
import shutil
import subprocess
import sys
from pathlib import Path

DEFAULT_REPO_URL = "https://github.com/rodrigobrim/media-tools.git"


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


def ensure_repo(git: Path, repo: Path, url: str, skip_update: bool) -> None:
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


def installer_settings() -> dict:
    """Choices made in the MSI setup wizard, if this exe was installed by it.

    The installer writes install_settings.json next to myoverlay.exe:
      {"language": "pt", "resolution": "fhd",
       "client_secret": "C:\\...\\client_secret.json", "google_skipped": false}
    A zip/dev deployment has no such file; everything keeps its default.
    """
    exe_dir = (
        Path(sys.executable).resolve().parent
        if getattr(sys, "frozen", False)
        else Path(__file__).resolve().parent
    )
    f = exe_dir / "install_settings.json"
    if not f.is_file():
        return {}
    try:
        return json.loads(f.read_text(encoding="utf-8-sig"))
    except (OSError, ValueError):
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


def ensure_config(repo: Path) -> None:
    cfg = repo / "config.toml"
    example = repo / "config.example.toml"
    if cfg.is_file() or not example.is_file():
        return
    shutil.copy2(example, cfg)
    _apply_installer_settings(repo, cfg, installer_settings())
    say("=" * 62)
    say("Created your configuration file:")
    say(f"    {cfg}")
    say("Open it in Notepad and set at least:")
    say("    library_root      (where processed videos will live)")
    say("    [mychron] rs3_data_dirs  (Race Studio 3 data folder)")
    say("For YouTube upload, see the README section 'YouTube setup'.")
    say("=" * 62)


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
    # Bundled tools first on PATH: the pipeline invokes ffmpeg/ffprobe by name.
    os.environ["PATH"] = os.pathsep.join(
        [str(ffmpeg_dir), str(git.parent), os.environ.get("PATH", "")]
    )

    argv = list(sys.argv[1:])
    skip_update = os.environ.get("MYOVERLAY_NO_UPDATE") == "1"
    if "--no-update" in argv:
        argv.remove("--no-update")
        skip_update = True

    repo = Path(os.environ.get("MYOVERLAY_REPO") or default_repo_path())
    url = os.environ.get("MYOVERLAY_REPO_URL", DEFAULT_REPO_URL)

    ensure_repo(git, repo, url, skip_update)
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
