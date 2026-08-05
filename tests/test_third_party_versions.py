"""third_party_versions.json pins every third-party tool the product ships
(ffmpeg, MinGit, uv, Google Cloud SDK). The packaging scripts download those
exact versions; CI installs ffmpeg and uv from the same file before running
this suite. These tests keep the pin file well-formed and, when
MYOVERLAY_ENFORCE_PINNED_TOOLS=1 (set by the tests workflow), assert that the
tools the suite is actually running against ARE the pinned versions - so the
suite can never quietly validate a different ffmpeg than the one users get.
"""

import json
import os
import re
import shutil
import subprocess
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[1]
PINS = json.loads((REPO_ROOT / "third_party_versions.json").read_text(encoding="utf-8"))

TOOLS = ("ffmpeg", "git", "uv", "gcloud")
ENFORCE = os.environ.get("MYOVERLAY_ENFORCE_PINNED_TOOLS") == "1"


def test_every_shipped_tool_is_pinned():
    for tool in TOOLS:
        assert tool in PINS, f"{tool} missing from third_party_versions.json"
        entry = PINS[tool]
        assert re.fullmatch(r"\d+(\.\d+)+", entry["version"]), (
            f"{tool} version {entry['version']!r} is not a dotted number"
        )
        assert entry["url"].startswith("https://"), f"{tool} url must be https"


def test_pinned_urls_embed_the_pinned_version():
    # gcloud is the documented exception: Google publishes no versioned
    # bundled-python archive, so its url is the rolling channel url and the
    # MSI build verifies the archive's VERSION file against the pin instead.
    for tool in ("ffmpeg", "git", "uv"):
        version, url = PINS[tool]["version"], PINS[tool]["url"]
        assert version in url, (
            f"{tool} url does not contain the pinned version {version} - "
            "version and url were not bumped together"
        )
    assert PINS["gcloud"]["version"] not in PINS["gcloud"]["url"]


def _installed_version(tool: str, args: list[str], pattern: str) -> str | None:
    exe = shutil.which(tool)
    if exe is None:
        if ENFORCE:
            pytest.fail(f"{tool} not on PATH but MYOVERLAY_ENFORCE_PINNED_TOOLS=1")
        pytest.skip(f"{tool} not available")
    first_line = subprocess.run(
        [exe, *args], capture_output=True, text=True
    ).stdout.splitlines()[0]
    match = re.search(pattern, first_line)
    assert match, f"could not parse {tool} version from {first_line!r}"
    return match.group(1)


def _assert_or_skip_pin(tool: str, installed: str, pinned: str):
    if installed == pinned:
        return
    if ENFORCE:
        pytest.fail(f"suite is running against {tool} {installed}, pin is {pinned}")
    pytest.skip(f"local {tool} is {installed}, pin is {pinned}; only enforced in CI")


def test_suite_runs_against_the_pinned_ffmpeg():
    # "ffmpeg version 9.0-essentials_build-www.gyan.dev ..." -> 9.0
    installed = _installed_version("ffmpeg", ["-version"], r"ffmpeg version (\S+)")
    _assert_or_skip_pin("ffmpeg", installed.split("-")[0], PINS["ffmpeg"]["version"])


def test_suite_runs_against_the_pinned_uv():
    # "uv 0.12.1 (hash date)" -> 0.12.1
    installed = _installed_version("uv", ["--version"], r"^uv (\S+)")
    _assert_or_skip_pin("uv", installed, PINS["uv"]["version"])
