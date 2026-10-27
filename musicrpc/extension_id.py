"""Derive the Chromium extension id from a manifest ``key``.

Chrome computes an unpacked/crx extension id as follows:

    sha256(DER-encoded SubjectPublicKeyInfo of the manifest "key")
    -> first 16 bytes -> hex
    -> every hex digit d is replaced by chr(ord('a') + int(d, 16))

Pinning the ``key`` in ``manifest.json`` therefore gives the extension a stable id
on every machine, which lets the native messaging manifest list a real
``allowed_origins`` value instead of guessing one.
"""
from __future__ import annotations

import base64
import hashlib
import json
import re
from pathlib import Path

_ID_RE = re.compile(r"^[a-p]{32}$")


def extension_id_from_key(public_key_b64: str) -> str:
    raw = base64.b64decode(public_key_b64, validate=True)
    digest = hashlib.sha256(raw).hexdigest()[:32]
    return "".join(chr(ord("a") + int(char, 16)) for char in digest)


def extension_id_from_manifest(manifest_path: Path) -> str | None:
    try:
        data = json.loads(Path(manifest_path).read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None
    key = data.get("key")
    if not isinstance(key, str) or not key.strip():
        return None
    try:
        return extension_id_from_key(key.strip())
    except Exception:
        return None


def is_extension_id(value: str) -> bool:
    return bool(_ID_RE.match((value or "").strip()))


def chrome_origin(extension_id: str) -> str:
    return f"chrome-extension://{extension_id.strip()}/"
