ARG BASE_IMAGE=higress-registry.cn-hangzhou.cr.aliyuncs.com/agentteams/agentteams-copaw-worker:v1.2.0
FROM ${BASE_IMAGE}
COPY patch_bridge.py /tmp/revguard-patch-runtime-bridge.py
COPY verify_bridge.py /tmp/revguard-verify-runtime-bridge.py
RUN python3 /tmp/revguard-patch-runtime-bridge.py && \
    python3 /tmp/revguard-verify-runtime-bridge.py && \
    rm /tmp/revguard-patch-runtime-bridge.py /tmp/revguard-verify-runtime-bridge.py
LABEL revguard.copaw.runtime-bridge="2"
