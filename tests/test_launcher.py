"""Tests for the MyOverlay launcher's repo management.

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


def test_branch_option_checks_out_remote_branch(tmp_path, origin):
    """--branch NAME switches the clone to that branch and ff-pulls it."""
    mod = load_launcher()
    git_exe = Path(shutil.which("git"))
    git(["checkout", "-qb", "feature"], origin)
    (origin / "file.txt").write_text("feature v1\n")
    git(["commit", "-qam", "feature v1"], origin)
    git(["checkout", "-q", "main"], origin)

    repo = tmp_path / "clone"
    mod.ensure_repo(git_exe, repo, str(origin), skip_update=False, branch="feature")
    assert (repo / "file.txt").read_text() == "feature v1\n"

    # branch moves upstream -> next run ff-pulls it
    git(["checkout", "-q", "feature"], origin)
    (origin / "file.txt").write_text("feature v2\n")
    git(["commit", "-qam", "feature v2"], origin)
    git(["checkout", "-q", "main"], origin)
    mod.ensure_repo(git_exe, repo, str(origin), skip_update=False, branch="feature")
    assert (repo / "file.txt").read_text() == "feature v2\n"


def test_branch_option_runs_local_only_branch_untouched(tmp_path, origin):
    """A branch that exists only locally runs as-is: no pull, never reset -
    the way to test unmerged work through the exe."""
    mod = load_launcher()
    git_exe = Path(shutil.which("git"))
    repo = tmp_path / "clone"
    mod.ensure_repo(git_exe, repo, str(origin), skip_update=False)

    git(["config", "user.email", "t@t"], repo)
    git(["config", "user.name", "t"], repo)
    git(["checkout", "-qb", "wip"], repo)
    (repo / "file.txt").write_text("unmerged work\n")
    git(["commit", "-qam", "wip"], repo)
    git(["checkout", "-q", "main"], repo)
    # upstream moves on meanwhile
    (origin / "file.txt").write_text("v2\n")
    git(["commit", "-qam", "v2"], origin)

    mod.ensure_repo(git_exe, repo, str(origin), skip_update=False, branch="wip")
    assert (repo / "file.txt").read_text() == "unmerged work\n"
    # and it stays on the branch on a repeat run
    mod.ensure_repo(git_exe, repo, str(origin), skip_update=False, branch="wip")
    assert (repo / "file.txt").read_text() == "unmerged work\n"


def test_bare_run_returns_a_branched_clone_to_the_default_branch(tmp_path, origin):
    """--branch is sticky, so a later run WITHOUT it must come back to main -
    otherwise the clone pulls that branch forever while reporting itself up to
    date, which is staleness that announces itself as current."""
    mod = load_launcher()
    git_exe = Path(shutil.which("git"))
    git(["checkout", "-qb", "feature"], origin)
    (origin / "file.txt").write_text("feature\n")
    git(["commit", "-qam", "feature"], origin)
    git(["checkout", "-q", "main"], origin)

    repo = tmp_path / "clone"
    mod.ensure_repo(git_exe, repo, str(origin), skip_update=False, branch="feature")
    assert (repo / "file.txt").read_text() == "feature\n"

    # main moves on; a bare run must land there, not on the stale branch.
    (origin / "file.txt").write_text("v2\n")
    git(["commit", "-qam", "v2"], origin)
    mod.ensure_repo(git_exe, repo, str(origin), skip_update=False)
    assert git(["branch", "--show-current"], repo).stdout.strip() == "main"
    assert (repo / "file.txt").read_text() == "v2\n"


def test_returning_to_default_branch_stashes_local_edits(tmp_path, origin):
    """Local edits that block the switch are stashed, not destroyed."""
    mod = load_launcher()
    git_exe = Path(shutil.which("git"))
    git(["checkout", "-qb", "feature"], origin)
    (origin / "file.txt").write_text("feature\n")
    git(["commit", "-qam", "feature"], origin)
    git(["checkout", "-q", "main"], origin)

    repo = tmp_path / "clone"
    mod.ensure_repo(git_exe, repo, str(origin), skip_update=False, branch="feature")
    # A dirty file that differs between the branches: checkout main refuses.
    (repo / "file.txt").write_text("precious local edit\n")

    mod.ensure_repo(git_exe, repo, str(origin), skip_update=False)

    assert git(["branch", "--show-current"], repo).stdout.strip() == "main"
    assert (repo / "file.txt").read_text() == "v1\n"
    # The edit is recoverable, which is the whole point of stashing over reset.
    assert "MyOverlay" in git(["stash", "list"], repo).stdout
    git(["checkout", "-q", "feature"], repo)
    git(["stash", "pop"], repo)
    assert (repo / "file.txt").read_text() == "precious local edit\n"


def test_dev_checkout_branch_is_never_switched(tmp_path, origin):
    """The branch of an unmanaged checkout is the developer's business."""
    mod = load_launcher()
    git_exe = Path(shutil.which("git"))
    repo = tmp_path / "devcheckout"
    git(["clone", "-q", str(origin), str(repo)], tmp_path)
    git(["config", "user.email", "t@t"], repo)
    git(["config", "user.name", "t"], repo)
    git(["checkout", "-qb", "my-feature"], repo)

    mod.ensure_repo(git_exe, repo, str(origin), skip_update=False)
    assert git(["branch", "--show-current"], repo).stdout.strip() == "my-feature"


def test_branch_option_unknown_branch_exits(tmp_path, origin):
    mod = load_launcher()
    git_exe = Path(shutil.which("git"))
    repo = tmp_path / "clone"
    mod.ensure_repo(git_exe, repo, str(origin), skip_update=False)
    with pytest.raises(SystemExit):
        mod.ensure_repo(git_exe, repo, str(origin), skip_update=False, branch="nope")


def test_ensure_config_seeds_from_template(tmp_path):
    mod = load_launcher()
    repo = tmp_path / "repo"
    repo.mkdir()
    home = tmp_path / "home"
    home.mkdir()
    (repo / "config.toml").write_text("# library_root = 'CHANGE_ME'\n")

    mod.ensure_config(repo, home)
    assert (home / "config.toml").read_text() == "# library_root = 'CHANGE_ME'\n"

    # An existing config is never overwritten.
    (home / "config.toml").write_text("library_root = 'D:/karting'\n")
    mod.ensure_config(repo, home)
    assert (home / "config.toml").read_text() == "library_root = 'D:/karting'\n"


def test_ensure_config_dev_checkout_is_noop(tmp_path):
    # Unmanaged dev checkout: data_dir IS the repo, so the tracked template
    # is already the config; nothing is copied or rewritten.
    mod = load_launcher()
    repo = tmp_path / "repo"
    repo.mkdir()
    (repo / "config.toml").write_text("# language = 'en'\n")
    mod.ensure_config(repo, repo)
    assert (repo / "config.toml").read_text() == "# language = 'en'\n"


def test_migrate_legacy_layout_moves_everything(tmp_path, monkeypatch):
    """An old %LOCALAPPDATA%\\MyOverlay install moves to ~\\myoverlay once:
    the clone to <home>/repo, config and credentials out of the clone into
    <home> (token.json becomes google-token)."""
    mod = load_launcher()
    appdata = tmp_path / "AppData"
    old_repo = appdata / "MyOverlay" / "repo"
    old_repo.mkdir(parents=True)
    # Pre-rename clones shipped config.example.toml; its presence is what
    # marks config.toml as a USER file safe to move out.
    (old_repo / "config.example.toml").write_text("# template\n")
    (old_repo / "config.toml").write_text("library_root = 'D:/karting'\n")
    (old_repo / "token.json").write_text("{}")
    (old_repo / "client_secret.json").write_text('{"installed": {}}')
    monkeypatch.setenv("LOCALAPPDATA", str(appdata))

    home = tmp_path / "home" / "myoverlay"
    repo = home / "repo"
    mod.migrate_legacy_layout(home, repo)

    assert not old_repo.exists()  # clone moved
    assert (home / "config.toml").read_text() == "library_root = 'D:/karting'\n"
    assert (home / "google-token").read_text() == "{}"
    assert (home / "client_secret.json").is_file()
    assert not (repo / "token.json").exists()

    # Re-running is a no-op (nothing left to move, nothing overwritten).
    (home / "config.toml").write_text("library_root = 'E:/other'\n")
    mod.migrate_legacy_layout(home, repo)
    assert (home / "config.toml").read_text() == "library_root = 'E:/other'\n"


def test_migrate_never_moves_the_tracked_template(tmp_path, monkeypatch):
    """Current clones ship config.toml as the tracked template (no
    config.example.toml); migration must leave it in the repo even when the
    user's home config is missing."""
    mod = load_launcher()
    appdata = tmp_path / "AppData"
    (appdata / "MyOverlay").mkdir(parents=True)
    monkeypatch.setenv("LOCALAPPDATA", str(appdata))

    home = tmp_path / "home" / "myoverlay"
    repo = home / "repo"
    repo.mkdir(parents=True)
    (repo / "config.toml").write_text("# language = 'en'\n")

    mod.migrate_legacy_layout(home, repo)
    assert (repo / "config.toml").is_file()
    assert not (home / "config.toml").exists()
