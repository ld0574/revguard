#!/usr/bin/env bash
# Run in the enterprise intranet production environment (10.10.10.202); all checks execute inside this command's own Docker project.
set -euo pipefail
ROOT_DIR=$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)
if ! ip -4 addr show | grep -Eq 'inet 10[.]10[.]10[.]202/'; then
  echo "请通过 SSH 在 10.10.10.202 执行此命令；本机仅编辑和同步。" >&2
  exit 2
fi
project="revguard-verify-$(date +%s)-$$"
evidence="$ROOT_DIR/.runtime/verification/$project"
mkdir -p "$evidence"
compose=(docker compose -p "$project" --project-directory "$ROOT_DIR" -f "$ROOT_DIR/docker-compose.verify.yml")
cleanup() { "${compose[@]}" down --volumes --remove-orphans >>"$evidence/cleanup.log" 2>&1; }
trap cleanup EXIT
"${compose[@]}" config --quiet
"${compose[@]}" build checks ui 2>&1 | tee "$evidence/build.log"
"${compose[@]}" run --rm checks 2>&1 | tee "$evidence/backend.log"
"${compose[@]}" run --rm --no-deps ui 2>&1 | tee "$evidence/frontend.log"
echo "Docker 发布验证全部通过：$evidence"
