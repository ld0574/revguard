# RevGuard API 服务镜像
# 与 AgentTeams 同机部署时，Worker 通过 http://revguard-api:9000 调用 Skill 层
FROM python:3.11-slim

WORKDIR /app

# 先装依赖，利用镜像层缓存
COPY requirements.txt requirements.lock ./
RUN pip install --no-cache-dir -r requirements.lock && \
    pip uninstall --yes setuptools

# 拷贝代码与数据（fixtures / golden_cases 为只读演示数据）
COPY revguard/ ./revguard/
COPY scripts/ ./scripts/
COPY config/demo_principals.json ./config/demo_principals.json
COPY data/fixtures/ ./data/fixtures/
COPY data/golden_cases/ ./data/golden_cases/

RUN addgroup --system revguard && adduser --system --ingroup revguard revguard

ENV REVGUARD_DB_PATH=/app/runtime/revguard.db \
    REVGUARD_FIXTURES_DIR=/app/data/fixtures \
    REVGUARD_OUTPUT_DIR=/app/data/outputs \
    REVGUARD_REPORT_DIR=/app/docs/reports \
    REVGUARD_GATEWAY_STATE_PATH=/app/runtime/revguard.gateway.json \
    REVGUARD_APPROVAL_MODE=wait \
    REVGUARD_FINANCE_FAIL_TIMES=1 \
    REVGUARD_RESET_ON_START=false \
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
