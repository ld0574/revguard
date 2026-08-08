# RevGuard API 服务镜像
# 与 AgentTeams 同机部署时，Worker 通过 http://revguard-api:9000 调用 Skill 层
FROM python:3.11-slim

WORKDIR /app

# 先装依赖，利用镜像层缓存
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# 拷贝代码与数据（fixtures / golden_cases 为只读演示数据）
COPY revguard/ ./revguard/
COPY scripts/ ./scripts/
COPY data/fixtures/ ./data/fixtures/
COPY data/golden_cases/ ./data/golden_cases/

ENV REVGUARD_DB_PATH=/app/runtime/revguard.db \
    REVGUARD_FIXTURES_DIR=/app/data/fixtures \
    REVGUARD_OUTPUT_DIR=/app/data/outputs \
    REVGUARD_REPORT_DIR=/app/docs/reports \
    REVGUARD_APPROVAL_MODE=wait \
    REVGUARD_FINANCE_FAIL_TIMES=1

# 运行产物目录（可挂载卷持久化沉淀物）
RUN mkdir -p /app/data/outputs /app/docs/reports /app/runtime
VOLUME ["/app/data/outputs", "/app/docs/reports", "/app/runtime"]

EXPOSE 9000

# 启动时种子演示案件，随后启动 API
CMD python scripts/seed_demo.py --db /app/runtime/revguard.db && \
    uvicorn revguard.api:app --host 0.0.0.0 --port 9000
