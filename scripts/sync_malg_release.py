"""Synchronize a validated MALG release asset into Sisyphus manifests.

The asset is checked completely, together with every target label, before any
repository file is changed.  This prevents an incomplete Flux update from
publishing a release tuple that cannot pass the infrastructure checker.
"""
from __future__ import annotations

import argparse
import json
import os
import re
import tempfile
from pathlib import Path
from typing import Any

_REQUIRED_KEYS = {
    "release_tag",
    "source_sha",
    "backend_image",
    "frontend_image",
    "contract_version",
    "contract_hash",
    "required_database_revision",
}
_TAG = re.compile(r"^v[0-9]+\.[0-9]+\.[0-9]+$")
_LABEL = re.compile(r"(?m)^(\s*malg-release:)\s*[^\s#]+(\s*(?:#.*)?)$")
_LABEL_FILES = (
    Path("clusters/sisyphus/malg.yaml"),
    Path("clusters/sisyphus/malg-crm-schema.yaml"),
)


def _load_asset(path: Path) -> tuple[bytes, dict[str, Any]]:
    try:
        raw = path.read_bytes()
        value = json.loads(raw)
    except (OSError, json.JSONDecodeError) as exc:
        raise ValueError(f"invalid release asset: {exc}") from exc
    if not isinstance(value, dict) or set(value) != _REQUIRED_KEYS:
        raise ValueError(f"release asset keys must be exactly {sorted(_REQUIRED_KEYS)}")
    for key, field in value.items():
        if key == "contract_version":
            if not isinstance(field, int) or isinstance(field, bool):
                raise ValueError("contract_version must be an integer")
        elif not isinstance(field, str) or not field:
            raise ValueError(f"release asset field {key!r} is missing")
    tag = value["release_tag"]
    if not isinstance(tag, str) or not _TAG.fullmatch(tag):
        raise ValueError("release_tag must be a semantic vX.Y.Z tag")
    return raw, value


def _atomic_write(path: Path, content: bytes) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, temporary = tempfile.mkstemp(prefix=f".{path.name}.", dir=path.parent)
    try:
        with os.fdopen(fd, "wb") as handle:
            handle.write(content)
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temporary, path)
    except BaseException:
        try:
            os.unlink(temporary)
        except FileNotFoundError:
            pass
        raise


def sync(root: Path, asset_path: Path) -> None:
    raw, release = _load_asset(asset_path)
    tag = release["release_tag"]
    targets: list[tuple[Path, bytes]] = []
    release_path = root / "infrastructure/malg/release.json"
    targets.append((release_path, raw))
    replacement = rf"\g<1> {tag}\g<2>"
    for relative in _LABEL_FILES:
        path = root / relative
        try:
            content = path.read_bytes()
        except OSError as exc:
            raise ValueError(f"missing label manifest: {path}") from exc
        text = content.decode("utf-8")
        matches = list(_LABEL.finditer(text))
        if len(matches) != 2:
            raise ValueError(f"{relative} must contain exactly two malg-release labels")
        updated = _LABEL.sub(replacement, text)
        targets.append((path, updated.encode("utf-8")))

    # All validation and rendering happens before the first replacement.
    for path, content in targets:
        _atomic_write(path, content)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("asset", type=Path, help="downloaded malg-release.json path")
    parser.add_argument("--root", type=Path, default=Path.cwd())
    args = parser.parse_args()
    try:
        sync(args.root.resolve(), args.asset.resolve())
    except (OSError, UnicodeError, ValueError) as exc:
        print(f"MALG release sync failed: {exc}")
        return 1
    print("Synchronized MALG release tuple")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
