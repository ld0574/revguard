set -eu
OUT=/out
rm -rf "$OUT"; mkdir -p "$OUT"
echo "== containers =="
docker ps --format '{{.Names}}|{{.Image}}|{{.Status}}|{{.Ports}}' | sort > "$OUT/containers.txt"
docker ps -a --format '{{.Names}}|{{.Status}}' | sort > "$OUT/containers-all.txt"
echo "== api health =="
API=$(docker ps --format '{{.Names}}' | grep -x 'revguard-api' || true)
docker exec "$API" python3 - <<'PY' > "$OUT/health.json"
import json,urllib.request
print(json.dumps(json.load(urllib.request.urlopen("http://127.0.0.1:9000/api/v1/health")),ensure_ascii=False,indent=2))
PY
docker exec "$API" python3 - <<'PY' > "$OUT/cases.json"
import json,urllib.request
d=json.load(urllib.request.urlopen("http://127.0.0.1:9000/api/v1/cases"))
print(json.dumps(d,ensure_ascii=False,indent=2)[:20000])
PY
echo "== db backend =="
docker exec "$API" sh -c 'env | grep -iE "^(REVGUARD_DB|DATABASE_URL|REVGUARD_DATABASE)" | sed "s/=.*/=<redacted>/" ' > "$OUT/db-env.txt" 2>&1 || true
echo "== prometheus =="
PROM=$(docker ps --format '{{.Names}}' | grep prometheus | head -1)
docker exec "$PROM" sh -c 'wget -qO- http://127.0.0.1:9090/api/v1/targets' > "$OUT/prom-targets.json" 2>/dev/null || docker exec "$PROM" python3 -c "
import json,urllib.request
print(json.dumps(json.load(urllib.request.urlopen('http://127.0.0.1:9090/api/v1/targets')),ensure_ascii=False)[:60000])" > "$OUT/prom-targets.json"
echo "== grafana =="
GRAF=$(docker ps --format '{{.Names}}' | grep grafana | head -1)
docker exec "$GRAF" sh -c 'wget -qO- http://127.0.0.1:3000/api/health' > "$OUT/grafana-health.json" 2>/dev/null || docker exec "$GRAF" python3 -c "
import json,urllib.request
print(json.dumps(json.load(urllib.request.urlopen('http://127.0.0.1:3000/api/health')),ensure_ascii=False))" > "$OUT/grafana-health.json"
echo "== workspace =="
head -5 /work/CHANGELOG.md > "$OUT/workspace-changelog-head.txt"
cat /work/.env 2>/dev/null | grep -iE 'version|release' | sed 's/=.*/=<redacted>/' > "$OUT/env-version.txt" 2>/dev/null || true
echo "== volumes/images =="
docker volume ls --format '{{.Name}}' | sort > "$OUT/volumes.txt"
docker images --format '{{.Repository}}:{{.Tag}}' | sort > "$OUT/images.txt"
echo COLLECT_DONE
