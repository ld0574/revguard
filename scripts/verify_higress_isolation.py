#!/usr/bin/env python3
"""Read-only MCP tools/list probes: every role allowed only on its own server.

Run on the AgentTeams Docker host. Credentials stay inside each Worker; this
script only prints server names, HTTP statuses and tool names/counts.
"""
from __future__ import annotations

import json
import subprocess
import time
from pathlib import Path

WORKER_PROBE = r'''
import json, sys, urllib.error, urllib.request
own, servers = sys.argv[1], json.loads(sys.argv[2])
with open("config/mcporter.json") as handle:
    config = json.load(handle)["mcpServers"][own]
rows = []
for target in servers:
    marker = "/mcp-servers/" + own + "/"
    if marker not in config["url"]:
        raise RuntimeError("Unexpected MCP route")
    url = config["url"].replace(marker, "/mcp-servers/" + target + "/", 1)
    request = urllib.request.Request(url, data=json.dumps({
        "jsonrpc": "2.0", "id": 1, "method": "tools/list", "params": {}
    }).encode(), headers={**config.get("headers", {}),
        "Content-Type": "application/json", "Accept": "application/json, text/event-stream"})
    status, names = 0, []
    try:
        with urllib.request.urlopen(request, timeout=8) as response:
            status = response.status
            raw = response.read().decode()
            if raw.lstrip().startswith("{"):
                body = json.loads(raw)
            else:
                events = [json.loads(line[5:].strip()) for line in raw.splitlines()
                          if line.startswith("data:") and line[5:].strip().startswith("{")]
                body = next((event for event in events if event.get("id") == 1), {})
            names = sorted(tool["name"] for tool in body.get("result", {}).get("tools", []))
    except urllib.error.HTTPError as error:
        status = error.code
    except Exception:
        # Never echo request headers, credentials or server error bodies.
        status = 0
    rows.append({"server": target, "status": status, "tools": names})
print(json.dumps(rows))
'''


def valid_probe(own: str, expected: dict[str, list[str]], rows: list[dict]) -> bool:
    if len(rows) != len(expected) or {row["server"] for row in rows} != set(expected):
        return False
    return all(
        row["status"] == 200 and row["tools"] == sorted(expected[own])
        if row["server"] == own else row["status"] in (401, 403)
        for row in rows
    )


def main() -> None:
    root = Path(__file__).resolve().parents[1]
    manifest = json.loads((root / "agentteams/mcp/higress/manifest.json").read_text())
    expected = {f"mcp-{worker}": skills for worker, skills in manifest.items()}
    for worker in manifest:
        own = f"mcp-{worker}"
        for attempt in range(5):
            result = subprocess.run(
                ["docker", "exec", "-i", f"agentteams-worker-{worker}",
                 "python", "-", own, json.dumps(list(expected))],
                input=WORKER_PROBE, text=True, capture_output=True, timeout=90,
            )
            rows = json.loads(result.stdout) if result.returncode == 0 else []
            if valid_probe(own, expected, rows):
                break
            if attempt < 4:
                time.sleep(2)  # Allow gateway configuration propagation.
        else:
            # These rows contain only public tool names and HTTP statuses.
            raise SystemExit(f"MCP isolation FAILED for {worker}: {json.dumps(rows)}")
        print(f"{worker}: own tools/list=200 ({len(expected[own])} tools), "
              f"cross-role denied={len(expected) - 1}/{len(expected) - 1}", flush=True)
    print("MCP isolation PASS: 9 own-server probes, 72 cross-role denials.")


if __name__ == "__main__":
    main()
