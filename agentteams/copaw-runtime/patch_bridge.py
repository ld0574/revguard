"""Preserve bounded model settings and prevent idle model heartbeats.

Run during the image build on 202. CoPaw Worker 1.0.3 regenerates legacy
providers.json at startup; its model projection otherwise discards kwargs
previously saved through the model API. Fail closed if upstream code changes.
"""

from pathlib import Path

old = '            {"id": m["id"], "name": m.get("name", m["id"])}'
new = """            {
                "id": m["id"],
                "name": m.get("name", m["id"]),
                **({"generate_kwargs": {
                    "reasoning_effort": "none",
                    "max_completion_tokens": 512,
                }}
                   if m["id"] in {"gpt-5.6-sol", "gpt-5.6-luna"}
                   else {"generate_kwargs": {"max_tokens": 512}}
                   if m["id"] == "glm-5.3-flash" else {}),
            }"""
paths = sorted(Path("/opt").glob("**/site-packages/copaw_worker/bridge.py"))
if not paths:
    raise SystemExit("No CoPaw bridge found; refusing an unpatched image")
for path in paths:
    text = path.read_text()
    if text.count(old) != 1:
        raise SystemExit(f"CoPaw bridge shape changed: {path}")
    patched = text.replace(old, new)
    heartbeat_anchor = "    # Set workspace_dir"
    if patched.count(heartbeat_anchor) != 1:
        raise SystemExit(f"CoPaw agent bridge shape changed: {path}")
    # This image is used by RevGuard's on-demand workers and idle Manager.
    # A heartbeat is an LLM task, not the process/container health check.
    patched = patched.replace(heartbeat_anchor, """    # RevGuard is event-driven; idle health monitoring is handled by Prometheus.
    agent_cfg.setdefault("heartbeat", {})["enabled"] = False

    # Set workspace_dir""")
    compile(patched, str(path), "exec")
    path.write_text(patched)
    print(f"RevGuard model and idle-heartbeat policy patched: {path}")
