"""The shipped browser extension must stay consistent with the Linux companion."""
from __future__ import annotations

import json
import shutil
import subprocess
from pathlib import Path

import pytest

from musicrpc import native_install
from musicrpc.constants import DEV_EXTENSION_ID, NATIVE_HOST_NAME
from musicrpc.extension_id import (
    chrome_origin,
    extension_id_from_key,
    extension_id_from_manifest,
    is_extension_id,
)

ROOT = Path(__file__).resolve().parents[1]
EXTENSION = ROOT / "extension"


def load(flavour: str) -> dict:
    return json.loads((EXTENSION / flavour / "manifest.json").read_text(encoding="utf-8"))


def test_derived_extension_id_matches_the_shipped_manifest():
    derived = extension_id_from_manifest(EXTENSION / "chromium" / "manifest.json")
    assert derived == DEV_EXTENSION_ID
    assert is_extension_id(derived)
    assert chrome_origin(derived) == f"chrome-extension://{derived}/"


def test_derivation_matches_the_chromium_algorithm():
    # 32 hex characters -> 32 letters a..p
    result = extension_id_from_key("MFkwEwYHKoZIzj0CAQYIKoZIzj0DAQcDQgAEx0HFTtCf0+64Xw==")
    assert len(result) == 32
    assert set(result) <= set("abcdefghijklmnop")


def test_chromium_manifest_declares_what_the_companion_needs():
    manifest = load("chromium")
    assert manifest["manifest_version"] == 3
    assert "nativeMessaging" in manifest["permissions"]
    assert manifest["background"]["service_worker"] == "background.js"
    scripts = manifest["content_scripts"]
    assert len(scripts) == 1
    assert scripts[0]["matches"] == ["https://music.youtube.com/*"]
    # Ordinary YouTube must not be scraped at all.
    assert not any("youtube.com" in match and "music." not in match
                   for match in scripts[0]["matches"])
    assert manifest["key"].strip()


def test_background_script_uses_the_shared_host_name():
    source = (EXTENSION / "chromium" / "background.js").read_text(encoding="utf-8")
    assert f'"{NATIVE_HOST_NAME}"' in source
    assert "connectNative" in source
    assert "runtime.onMessage" in source


def test_generated_copies_match_the_shared_sources():
    for name in ("content.js", "background.js"):
        source = (EXTENSION / "src" / name).read_text(encoding="utf-8")
        for flavour in ("chromium", "firefox"):
            copy = (EXTENSION / flavour / name).read_text(encoding="utf-8")
            assert copy == source, f"{flavour}/{name} is out of date; run extension/build.sh"


def test_firefox_manifest_matches_the_native_host_manifest():
    manifest = load("firefox")
    assert manifest["browser_specific_settings"]["gecko"]["id"] == \
        native_install.FIREFOX_EXTENSION_ID
    assert manifest["background"]["scripts"] == ["background.js"]
    assert manifest["content_scripts"][0]["matches"] == ["https://music.youtube.com/*"]


def test_icons_exist():
    for flavour in ("chromium", "firefox"):
        for size in (16, 32, 48, 128):
            icon = EXTENSION / flavour / "icons" / f"icon-{size}.png"
            assert icon.is_file() and icon.stat().st_size > 0


@pytest.mark.skipif(shutil.which("node") is None, reason="node is not installed")
def test_content_script_unit_tests_pass():
    result = subprocess.run(
        ["node", "--test", str(EXTENSION / "tests" / "content.test.mjs")],
        capture_output=True, text=True, timeout=120)
    assert result.returncode == 0, result.stdout + result.stderr
