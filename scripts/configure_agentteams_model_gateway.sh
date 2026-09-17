#!/usr/bin/env bash
# Align AgentTeams' persisted Higress AI route with the current Controller env.
# AgentTeams v1.2.0 can update the Provider while leaving an older route/service
# source in place, which makes every Worker call the previous upstream.
set -Eeuo pipefail

CONTROLLER="${CONTROLLER:-agentteams-controller}"
docker inspect "$CONTROLLER" >/dev/null

docker exec -i "$CONTROLLER" bash <<'INNER'
set -Eeuo pipefail
source /opt/agentteams/scripts/lib/gateway-api.sh
gateway_ensure_session

if [ "${AGENTTEAMS_LLM_PROVIDER:-}" != "openai-compat" ]; then
  echo "当前只验收 openai-compat AgentTeams Provider" >&2
  exit 1
fi
test -n "${AGENTTEAMS_OPENAI_BASE_URL:-}"
test -n "${AGENTTEAMS_LLM_API_KEY:-}"

tmpdir=$(mktemp -d)
trap 'rm -rf "$tmpdir"' EXIT
export REVGUARD_GATEWAY_TMPDIR="$tmpdir"

python3 - <<'PY'
import json
import os
import urllib.parse
from pathlib import Path

base = os.environ["AGENTTEAMS_OPENAI_BASE_URL"].rstrip("/")
key = os.environ["AGENTTEAMS_LLM_API_KEY"]
url = urllib.parse.urlparse(base)
if url.scheme not in {"http", "https"} or not url.hostname:
    raise SystemExit("AGENTTEAMS_OPENAI_BASE_URL 无效")
port = url.port or (443 if url.scheme == "https" else 80)
root = Path(os.environ["REVGUARD_GATEWAY_TMPDIR"])
(root / "service.json").write_text(json.dumps({
    "type": "dns",
    "name": "openai-compat",
    "port": port,
    "protocol": url.scheme,
    "proxyName": "",
    "domain": url.hostname,
}, separators=(",", ":")))
(root / "provider.json").write_text(json.dumps({
    "type": "openai",
    "name": "openai-compat",
    "tokens": [key],
    "version": 0,
    "protocol": "openai/v1",
    "tokenFailoverConfig": {"enabled": False},
    "rawConfigs": {
        "openaiCustomUrl": base,
        "openaiCustomServiceName": "openai-compat.dns",
        "openaiCustomServicePort": port,
        "agentteamsMode": True,
    },
}, separators=(",", ":")))
PY

request_file() {
  local method="$1" path="$2" payload="$3" code
  code=$(curl -sS -o "$tmpdir/response.json" -w '%{http_code}' \
    -X "$method" "http://127.0.0.1:8001$path" \
    -b "$HIGRESS_COOKIE_FILE" -H 'Content-Type: application/json' \
    -d @"$payload")
  case "$code" in 200|201|204) ;; *) echo "Higress $method $path failed: HTTP $code" >&2; exit 1;; esac
}

service_code=$(curl -s -o /dev/null -w '%{http_code}' -b "$HIGRESS_COOKIE_FILE" \
  http://127.0.0.1:8001/v1/service-sources/openai-compat)
if [ "$service_code" = 200 ]; then
  request_file PUT /v1/service-sources/openai-compat "$tmpdir/service.json"
else
  request_file POST /v1/service-sources "$tmpdir/service.json"
fi

provider_code=$(curl -s -o /dev/null -w '%{http_code}' -b "$HIGRESS_COOKIE_FILE" \
  http://127.0.0.1:8001/v1/ai/providers/openai-compat)
if [ "$provider_code" = 200 ]; then
  request_file PUT /v1/ai/providers/openai-compat "$tmpdir/provider.json"
else
  request_file POST /v1/ai/providers "$tmpdir/provider.json"
fi

curl -fsS -b "$HIGRESS_COOKIE_FILE" \
  http://127.0.0.1:8001/v1/ai/routes/default-ai-route > "$tmpdir/route-before.json"
python3 - <<'PY'
import json
import os
from pathlib import Path

root = Path(os.environ["REVGUARD_GATEWAY_TMPDIR"])
route = json.loads((root / "route-before.json").read_text()).get("data") or {}
if route.get("name") != "default-ai-route" or len(route.get("upstreams") or []) != 1:
    raise SystemExit("default-ai-route 结构不符合预期")
route["upstreams"][0]["provider"] = "openai-compat"
(root / "route.json").write_text(json.dumps(route, separators=(",", ":")))
(root / "consumer-count").write_text(str(len(
    (route.get("authConfig") or {}).get("allowedConsumers") or []
)))
PY
request_file PUT /v1/ai/routes/default-ai-route "$tmpdir/route.json"

curl -fsS -b "$HIGRESS_COOKIE_FILE" \
  http://127.0.0.1:8001/v1/service-sources/openai-compat > "$tmpdir/service-after.json"
curl -fsS -b "$HIGRESS_COOKIE_FILE" \
  http://127.0.0.1:8001/v1/ai/routes/default-ai-route > "$tmpdir/route-after.json"
python3 - <<'PY'
import json
import os
import urllib.parse
from pathlib import Path

root = Path(os.environ["REVGUARD_GATEWAY_TMPDIR"])
expected = urllib.parse.urlparse(os.environ["AGENTTEAMS_OPENAI_BASE_URL"])
service = json.loads((root / "service-after.json").read_text()).get("data") or {}
route = json.loads((root / "route-after.json").read_text()).get("data") or {}
before = int((root / "consumer-count").read_text())
after = len((route.get("authConfig") or {}).get("allowedConsumers") or [])
assert service.get("domain") == expected.hostname
assert int(service.get("port", 0)) == (expected.port or (443 if expected.scheme == "https" else 80))
assert service.get("protocol") == expected.scheme
assert [item.get("provider") for item in route.get("upstreams") or []] == ["openai-compat"]
assert before == after and after > 0
print({
    "provider": "openai-compat",
    "route_consumers_preserved": after,
    "service_source_matches": True,
})
PY
INNER
