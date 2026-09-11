"""Prepare local-only secrets without printing any credential."""
from __future__ import annotations

import json
import secrets
from pathlib import Path


def main():
    root = Path(__file__).resolve().parent.parent
    path = root / ".env"
    lines = path.read_text().splitlines() if path.exists() else []
    env = {}
    for line in lines:
        if "=" in line and not line.lstrip().startswith("#"):
            key, value = line.split("=", 1)
            if len(value) >= 2 and value[0] == value[-1] and value[0] in "\"'":
                value = value[1:-1]
            env[key] = value
    principals = json.loads(env.get("REVGUARD_API_KEYS_JSON") or "{}")
    # Preserve a local demo's existing identities before adding a viewer.
    if not principals and env.get("REVGUARD_ALLOW_INSECURE_DEMO_KEYS", "true") == "true":
        principals = json.loads((root / "config/demo_principals.json").read_text())
    directory = root / ".runtime/observability"
    directory.mkdir(parents=True, exist_ok=True, mode=0o700)
    token_path = directory / "metrics-token"
    token = token_path.read_text().strip() if token_path.exists() else secrets.token_hex(32)
    principals[token] = {"actor": "api-viewer", "roles": ["viewer"], "scopes": []}
    token_path.write_text(token)
    token_path.chmod(0o600)
    password = directory / "grafana-password"
    if not password.exists():
        password.write_text(secrets.token_urlsafe(24))
    password.chmod(0o600)
    lines = [line for line in lines if not line.startswith("REVGUARD_API_KEYS_JSON=")]
    lines.append("REVGUARD_API_KEYS_JSON=" + json.dumps(principals, separators=(",", ":")))
    path.write_text("\n".join(lines) + "\n")
    path.chmod(0o600)
    print("Read-only metrics credential and Grafana password prepared in .runtime/observability")


if __name__ == "__main__":
    main()
