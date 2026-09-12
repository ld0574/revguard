"""Exercise the installed CoPaw bridge on disposable, synthetic workspaces."""
import importlib.util
import json
import tempfile
from pathlib import Path


def verify(bridge):
    _write_agent_json = bridge._write_agent_json
    _write_providers_json = bridge._write_providers_json
    config = {
        "agents": {"defaults": {"model": {"primary": "test/gpt-5.6-luna"}}},
        "models": {"providers": {"test": {"baseUrl": "http://isolated.invalid/v1", "apiKey": "synthetic", "models": [
            {"id": "gpt-5.6-luna"}, {"id": "gpt-5.6-sol"}, {"id": "other-model"},
        ]}}},
        "channels": {"matrix": {"enabled": True, "groups": {"!synthetic:test": {"requireMention": True}}}},
    }
    for profile in ("worker", "manager"):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            for existing in (False, True):
                path = root / "workspaces/default/agent.json"
                if existing:
                    agent = json.loads(path.read_text())
                    agent["heartbeat"] = {"enabled": True, "every": "30m", "target": "main"}
                    agent["running"] = {"max_iters": 17}
                    path.write_text(json.dumps(agent))
                _write_agent_json(config, root, True, profile=profile)
                agent = json.loads(path.read_text())
                assert agent["heartbeat"]["enabled"] is False
                assert agent["channels"]["matrix"]["enabled"] is True
                assert agent["channels"]["matrix"]["groups"] == config["channels"]["matrix"]["groups"]
                if existing:
                    assert agent["running"]["max_iters"] == 17
                    assert agent["heartbeat"]["every"] == "30m"
                _write_providers_json(config, root, True)
                providers = json.loads((root / "providers.json").read_text())
                assert providers["active_llm"] == {"provider_id": "test", "model": "gpt-5.6-luna"}
                models = {m["id"]: m for m in providers["custom_providers"]["test"]["models"]}
                for model in ("gpt-5.6-luna", "gpt-5.6-sol"):
                    assert models[model]["generate_kwargs"]["reasoning_effort"] == "none"
                assert "generate_kwargs" not in models["other-model"]

paths = sorted(Path("/opt").glob("**/site-packages/copaw_worker/bridge.py"))
assert paths, "Installed bridge is missing"
for path in paths:
    spec = importlib.util.spec_from_file_location("isolated_copaw_bridge", path)
    bridge = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(bridge)
    verify(bridge)
    print(f"Installed CoPaw bridge fresh/restart worker and manager contracts passed: {path}")
