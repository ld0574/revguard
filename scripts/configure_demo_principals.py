#!/usr/bin/env python3
"""Give full deployments private backend keys while preserving WebUI demo access."""
from __future__ import annotations

import argparse
import json
import secrets
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
ENV_KEY = "REVGUARD_API_KEYS_JSON"


def read_principals(env_path: Path, template_path: Path) -> dict:
    lines = env_path.read_text(encoding="utf-8").splitlines() if env_path.exists() else []
    raw = next((line.split("=", 1)[1] for line in lines
                if line.startswith(ENV_KEY + "=")), "")
    return json.loads(raw) if raw.strip() else json.loads(template_path.read_text(encoding="utf-8"))


def configure(env_path: Path, template_path: Path) -> None:
    principals = read_principals(env_path, template_path)
    private = {}
    for key, principal in principals.items():
        # Upgrade only public sample keys; preserve operator-provided secrets.
        if principal["actor"].startswith("revguard-") and key.startswith("rg-demo-"):
            key = "rg-backend-" + secrets.token_urlsafe(32)
        private[key] = principal
    line = ENV_KEY + "=" + json.dumps(private, ensure_ascii=False, separators=(",", ":"))
    lines = env_path.read_text(encoding="utf-8").splitlines() if env_path.exists() else []
    updated = [item for item in lines if not item.startswith(ENV_KEY + "=")]
    env_path.write_text("\n".join([*updated, line]) + "\n", encoding="utf-8")
    env_path.chmod(0o600)


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--env", type=Path, default=ROOT / ".env")
    parser.add_argument("--template", type=Path, default=ROOT / "config/demo_principals.json")
    parser.add_argument("--lookup", help="Internal use only: print one actor credential to a pipe")
    args = parser.parse_args()
    if args.lookup:
        principals = read_principals(args.env, args.template)
        print(next(key for key, value in principals.items() if value["actor"] == args.lookup))
    else:
        configure(args.env, args.template)
        print("后端 Principal 已配置；公开示例 Worker key 已替换，已有私密 key 保持不变。")


if __name__ == "__main__":
    main()
