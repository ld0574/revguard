# RevGuard API 服务镜像
# 与 AgentTeams 同机部署时，Worker 通过 http://revguard-api:9000 调用 Skill 层
ARG PYTHON_IMAGE=python:3.11-slim
FROM node:20-alpine AS demo-ui-build

WORKDIR /ui

# 独立构建录制驾驶舱，只把静态产物带入最终 Python 镜像。
COPY demo-ui/package.json demo-ui/package-lock.json ./
RUN npm ci
COPY demo-ui/ ./
RUN npm run build

FROM ${PYTHON_IMAGE}

USER root

ARG PIP_INDEX_URL=https://pypi.org/simple

ARG REVGUARD_VERSION=0.4.0
LABEL org.opencontainers.image.title="RevGuard" \
      org.opencontainers.image.version="$REVGUARD_VERSION"

WORKDIR /app

# 先装依赖，利用镜像层缓存
COPY requirements.txt requirements.lock ./
RUN pip install --no-cache-dir -r requirements.lock && \
    pip uninstall --yes setuptools

# 拷贝代码与数据（fixtures / golden_cases 为只读演示数据）
COPY revguard/ ./revguard/
COPY scripts/ ./scripts/
COPY migrations/ ./migrations/
COPY config/demo_principals.json ./config/demo_principals.json
COPY data/fixtures/ ./data/fixtures/
COPY data/golden_cases/ ./data/golden_cases/
COPY docs/evaluation-summary.json \
     docs/value-evaluation-synthetic.json \
     docs/synthetic-data-validation.json \
     docs/polardb-local-verification-2026-08-27.json \
     docs/polardb-local-instance-acceptance-2026-08-29.json \
     ./docs/
COPY docs/evidence/demo-rehearsal/manifest.json ./docs/evidence/demo-rehearsal/manifest.json
COPY --from=demo-ui-build /ui/dist/client/ ./demo-ui/dist/client/

RUN getent group revguard >/dev/null || addgroup --system revguard; \
    id -u revguard >/dev/null 2>&1 || adduser --system --ingroup revguard revguard

ENV REVGUARD_DB_PATH=/app/runtime/revguard.db \
    REVGUARD_FIXTURES_DIR=/app/data/fixtures \
    REVGUARD_OUTPUT_DIR=/app/data/outputs \
    REVGUARD_REPORT_DIR=/app/docs/reports \
    REVGUARD_GATEWAY_STATE_PATH=/app/runtime/revguard.gateway.json \
    REVGUARD_APPROVAL_MODE=wait \
    REVGUARD_FINANCE_FAIL_TIMES=1 \
    REVGUARD_RESET_ON_START=false \
    REVGUARD_RELEASE_VERSION=$REVGUARD_VERSION \
    PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1

# 运行产物目录（可挂载卷持久化沉淀物）
RUN mkdir -p /app/data/outputs /app/docs/reports /app/runtime && \
    chmod +x /app/scripts/start_api.sh && \
    chown -R revguard:revguard /app
VOLUME ["/app/data/outputs", "/app/docs/reports", "/app/runtime"]

USER revguard

EXPOSE 9000

HEALTHCHECK --interval=15s --timeout=3s --start-period=10s --retries=3 \
  CMD python -c "import json,urllib.request; assert json.load(urllib.request.urlopen('http://127.0.0.1:9000/api/v1/health', timeout=2))['status']=='ok'"

CMD ["/app/scripts/start_api.sh"]
