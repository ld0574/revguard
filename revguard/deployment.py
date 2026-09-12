"""A durable deployment fence; only the Docker deployment operator removes it."""
from __future__ import annotations

import json
import os
from pathlib import Path


def fence_path() -> Path:
    # Docker persists this directory across API replacements, for both backends.
    default = Path(__file__).resolve().parents[1] / "data" / "revguard.db"
    return Path(os.getenv("REVGUARD_DB_PATH", str(default))).parent / ".deployment-fence.json"


def deployment_pending() -> bool:
    try:
        fence_path().stat()
    except FileNotFoundError:
        return False
    # Permission/storage errors must not reopen write access.
    return True


def prepare_fence(owner: str) -> None:
    if not owner or not owner.isalnum():
        raise ValueError("Invalid deployment owner")
    target = fence_path()
    target.parent.mkdir(parents=True, exist_ok=True)
    temporary = target.with_name(target.name + "." + owner)
    try:
        with temporary.open("w", encoding="utf-8") as handle:
            os.chmod(temporary, 0o600)
            json.dump({"owner": owner, "purpose": "deployment"}, handle)
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temporary, target)
        _sync_directory(target.parent)
    finally:
        temporary.unlink(missing_ok=True)


def release_fence(owner: str) -> None:
    target = fence_path()
    if json.loads(target.read_text())["owner"] != owner:
        raise ValueError("Deployment owner changed; fence retained")
    target.unlink()
    _sync_directory(target.parent)


def _sync_directory(path: Path) -> None:
    descriptor = os.open(path, os.O_RDONLY | os.O_DIRECTORY)
    try:
        os.fsync(descriptor)
    finally:
        os.close(descriptor)
