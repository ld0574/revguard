"""Version recording artifacts without deleting the preceding take's evidence."""
from __future__ import annotations

import os
import re
import tempfile
from pathlib import Path


def artifact_path(root: str | Path, case: dict, suffix: str) -> Path:
    case_id = str(case["case_id"])
    recording_id = case.get("recording_id")
    for identifier in (case_id, recording_id):
        if identifier is not None and not re.fullmatch(r"[A-Za-z0-9_-]+", str(identifier)):
            raise ValueError("非法案件或录制批次标识")
    base = Path(root)
    if recording_id:
        base = base / "recordings" / recording_id
    return base / (case_id + suffix)


def write_artifact(path: Path, text: str) -> None:
    """Publish only complete files; preserve the previous file on write failure."""
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = None
    try:
        with tempfile.NamedTemporaryFile(mode="w", encoding="utf-8", dir=path.parent,
                                         prefix="." + path.name, delete=False) as handle:
            temporary = Path(handle.name)
            handle.write(text)
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temporary, path)
    finally:
        if temporary is not None:
            temporary.unlink(missing_ok=True)
