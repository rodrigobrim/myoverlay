"""GUI/CLI feature-parity guard.

The CLI command tree (the same walk that generates docs/CLI.md) is the source
of truth for the app's feature surface. The GUI declares its coverage in
media_tools.gui.registry, partitioned into GUI_COMMANDS (exposed), CLI_ONLY
(deliberately excluded, with a reason) and PLANNED (GUI backlog). This test
fails the moment a CLI command exists in none of the three buckets - so every
new CLI feature forces an explicit decision about its place in the GUI.
"""

from media_tools.docs_gen import iter_command_paths
from media_tools.gui.registry import CLI_ONLY, GUI_COMMANDS, PLANNED


def test_every_cli_command_is_accounted_for_in_the_gui():
    all_commands = set(iter_command_paths())
    accounted = GUI_COMMANDS.keys() | CLI_ONLY.keys() | PLANNED.keys()

    missing = all_commands - accounted
    assert not missing, (
        f"CLI commands unaccounted for in the GUI: {sorted(missing)}. "
        "Either expose them in the GUI (register in "
        "src/media_tools/gui/registry.py GUI_COMMANDS), defer them (PLANNED), "
        "or exclude them with a reason (CLI_ONLY)."
    )


def test_registry_names_only_real_commands():
    """A registry entry for a renamed/removed command must fail, not linger."""
    all_commands = set(iter_command_paths())
    for bucket_name, bucket in (
        ("GUI_COMMANDS", GUI_COMMANDS),
        ("CLI_ONLY", CLI_ONLY),
        ("PLANNED", PLANNED),
    ):
        stale = bucket.keys() - all_commands
        assert not stale, f"{bucket_name} names commands that do not exist: {sorted(stale)}"


def test_registry_buckets_are_disjoint():
    overlaps = (
        (GUI_COMMANDS.keys() & CLI_ONLY.keys())
        | (GUI_COMMANDS.keys() & PLANNED.keys())
        | (CLI_ONLY.keys() & PLANNED.keys())
    )
    assert not overlaps, f"commands listed in more than one bucket: {sorted(overlaps)}"


def test_every_exclusion_has_a_reason():
    for bucket in (GUI_COMMANDS, CLI_ONLY, PLANNED):
        for command, reason in bucket.items():
            assert reason and reason.strip(), f"'{command}' needs a non-empty reason/description"


def test_registered_gui_commands_are_actually_dispatched():
    """GUI_COMMANDS must stay honest: every registered command name appears in
    an mtclient/app dispatch (the literal arg lists the GUI sends to `mt`)."""
    from pathlib import Path

    gui_dir = Path(__file__).resolve().parents[1] / "src" / "media_tools" / "gui"
    source = "\n".join(
        p.read_text(encoding="utf-8") for p in sorted(gui_dir.glob("*.py")) if p.name != "registry.py"
    )
    for command in GUI_COMMANDS:
        first_token = command.split()[0]
        assert f'"{first_token}"' in source, (
            f"'{command}' is registered as exposed, but no GUI module dispatches it"
        )
