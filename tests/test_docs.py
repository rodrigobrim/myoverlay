from pathlib import Path

import typer.main

from media_tools.cli import app
from media_tools.docs_gen import generate_cli_docs

REPO_ROOT = Path(__file__).resolve().parents[1]
CLI_MD = REPO_ROOT / "docs" / "CLI.md"


def test_cli_md_is_up_to_date():
    committed = CLI_MD.read_text(encoding="utf-8").replace("\r\n", "\n")
    assert committed == generate_cli_docs(), (
        "docs/CLI.md is out of date with the CLI. Regenerate it with: uv run mt docs"
    )


def test_every_command_is_documented():
    """Guard against a walk bug that silently drops commands."""
    docs = generate_cli_docs()
    root = typer.main.get_command(app)
    for name, cmd in root.commands.items():
        assert f"## mt {name}" in docs, f"top-level command '{name}' missing from docs"
        for sub in getattr(cmd, "commands", {}):
            assert f"## mt {name} {sub}" in docs, f"sub-command '{name} {sub}' missing from docs"
