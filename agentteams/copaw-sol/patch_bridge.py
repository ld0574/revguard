"""Keep Sol's Chat Completions tool compatibility through CoPaw bootstrap.

Run during the image build on 202. CoPaw Worker 1.0.3 regenerates legacy
providers.json at startup; its model projection otherwise discards kwargs
previously saved through the model API. Fail closed if upstream code changes.
"""

from pathlib import Path

old = '            {"id": m["id"], "name": m.get("name", m["id"])}'
new = """            {
                "id": m["id"],
                "name": m.get("name", m["id"]),
                **({"generate_kwargs": {"reasoning_effort": "none"}}
                   if m["id"] == "gpt-5.6-sol" else {}),
            }"""
paths = sorted(Path("/opt").glob("**/site-packages/copaw_worker/bridge.py"))
if not paths:
    raise SystemExit("No CoPaw bridge found; refusing an unpatched image")
for path in paths:
    text = path.read_text()
    if text.count(old) != 1:
        raise SystemExit(f"CoPaw bridge shape changed: {path}")
    patched = text.replace(old, new)
    compile(patched, str(path), "exec")
    path.write_text(patched)
    print(f"Sol bootstrap compatibility patched: {path}")
