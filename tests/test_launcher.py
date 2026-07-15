"""Tests for the myoverlay launcher's repo management.

The launcher ships inside the frozen exe (it is not imported by the
pipeline), so it is loaded here by path. These tests use real git repos:
the update logic is exactly where silent staleness would hide.
"""

from __future__ import annotations

import importlib.util
import shutil
import subprocess
from pathlib import Path

import pytest

LAUNCHER = Path(__file__).parents[1] / "packaging" / "myoverlay_launcher.py"


def load_launcher():
    spec = importlib.util.spec_from_file_location("myoverlay_launcher", LAUNCHER)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def have_git() -> bool:
    return shutil.which("git") is not None


pytestmark = pytest.mark.skipif(not have_git(), reason="git not available")


def git(args, cwd):
    return subprocess.run(
        ["git", *args], cwd=str(cwd), capture_output=True, text=True, check=True
    )


@pytest.fixture
def origin(tmp_path):
    """A tiny upstream repo with one commit."""
    up = tmp_path / "origin"
    up.mkdir()
    git(["init", "-b", "main"], up)
    git(["config", "user.email", "t@t"], up)
    git(["config", "user.name", "t"], up)
    (up / "file.txt").write_text("v1\n")
    git(["add", "-A"], up)
    git(["commit", "-q", "-m", "v1"], up)
    return up


def head(repo: Path) -> str:
    return git(["rev-parse", "HEAD"], repo).stdout.strip()


def test_clone_then_pull_updates(tmp_path, origin):
    mod = load_launcher()
    git_exe = Path(shutil.which("git"))
    repo = tmp_path / "clone"

    mod.ensure_repo(git_exe, repo, str(origin), skip_update=False)
    assert (repo / "file.txt").read_text() == "v1\n"
    # the launcher marks clones it created as its own
    assert mod.is_managed(repo)

    (origin / "file.txt").write_text("v2\n")
    git(["commit", "-qam", "v2"], origin)
    mod.ensure_repo(git_exe, repo, str(origin), skip_update=False)
    assert (repo / "file.txt").read_text() == "v2\n"


def test_skip_update_leaves_clone_untouched(tmp_path, origin):
    mod = load_launcher()
    git_exe = Path(shutil.which("git"))
    repo = tmp_path / "clone"
    mod.ensure_repo(git_exe, repo, str(origin), skip_update=False)

    (origin / "file.txt").write_text("v2\n")
    git(["commit", "-qam", "v2"], origin)
    mod.ensure_repo(git_exe, repo, str(origin), skip_update=True)
    assert (repo / "file.txt").read_text() == "v1\n"


def test_diverged_managed_clone_resyncs(tmp_path, origin):
    """A clone that cannot fast-forward must NOT silently keep old code."""
    mod = load_launcher()
    git_exe = Path(shutil.which("git"))
    repo = tmp_path / "clone"
    mod.ensure_repo(git_exe, repo, str(origin), skip_update=False)

    # Local divergence: a commit that upstream doesn't have...
    (repo / "file.txt").write_text("local edit\n")
    git(["config", "user.email", "t@t"], repo)
    git(["config", "user.name", "t"], repo)
    git(["commit", "-qam", "local"], repo)
    # ...plus upstream moving on: pull --ff-only is now impossible.
    (origin / "file.txt").write_text("v2\n")
    git(["commit", "-qam", "v2"], origin)

    mod.ensure_repo(git_exe, repo, str(origin), skip_update=False)
    assert (repo / "file.txt").read_text() == "v2\n"
    assert head(repo) == head(origin)


def test_unmanaged_checkout_is_never_reset(tmp_path, origin):
    """A dev checkout (not created by the launcher) keeps its local work."""
    mod = load_launcher()
    git_exe = Path(shutil.which("git"))
    repo = tmp_path / "devcheckout"
    git(["clone", "-q", str(origin), str(repo)], tmp_path)
    git(["config", "user.email", "t@t"], repo)
    git(["config", "user.name", "t"], repo)
    (repo / "file.txt").write_text("precious local work\n")
    git(["commit", "-qam", "local"], repo)
    (origin / "file.txt").write_text("v2\n")
    git(["commit", "-qam", "v2"], origin)

    assert not mod.is_managed(repo)
    mod.ensure_repo(git_exe, repo, str(origin), skip_update=False)
    assert (repo / "file.txt").read_text() == "precious local work\n"


def test_ignored_files_survive_resync(tmp_path, origin):
    """config.toml / token.json live in the managed clone and are gitignored:
    a re-sync must not delete them."""
    mod = load_launcher()
    git_exe = Path(shutil.which("git"))
    (origin / ".gitignore").write_text("config.toml\ntoken.json\n")
    git(["add", "-A"], origin)
    git(["commit", "-qm", "ignore config"], origin)

    repo = tmp_path / "clone"
    mod.ensure_repo(git_exe, repo, str(origin), skip_update=False)
    (repo / "config.toml").write_text("library_root = 'D:/karting'\n")
    (repo / "token.json").write_text("{}")

    # force divergence + upstream change
    git(["config", "user.email", "t@t"], repo)
    git(["config", "user.name", "t"], repo)
    (repo / "file.txt").write_text("local\n")
    git(["commit", "-qam", "local"], repo)
    (origin / "file.txt").write_text("v2\n")
    git(["commit", "-qam", "v2"], origin)

    mod.ensure_repo(git_exe, repo, str(origin), skip_update=False)
    assert (repo / "config.toml").read_text() == "library_root = 'D:/karting'\n"
    assert (repo / "token.json").is_file()
    assert (repo / "file.txt").read_text() == "v2\n"


def test_ensure_config_seeds_from_example(tmp_path):
    mod = load_launcher()
    repo = tmp_path / "repo"
    repo.mkdir()
    (repo / "config.example.toml").write_text("library_root = 'CHANGE_ME'\n")

    mod.ensure_config(repo)
    assert (repo / "config.toml").read_text() == "library_root = 'CHANGE_ME'\n"

    # An existing config is never overwritten.
    (repo / "config.toml").write_text("library_root = 'D:/karting'\n")
    mod.ensure_config(repo)
    assert (repo / "config.toml").read_text() == "library_root = 'D:/karting'\n"
