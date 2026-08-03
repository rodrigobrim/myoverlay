"""google-setup helpers that touch the filesystem (no browser involved)."""

import json

from media_tools.gcp_console import (
    _claim_secret_path,
    _rotate_secret_aside,
    _secret_project,
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
