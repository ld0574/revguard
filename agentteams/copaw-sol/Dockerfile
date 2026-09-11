ARG BASE_IMAGE
FROM ${BASE_IMAGE}
COPY patch_bridge.py /tmp/revguard-patch-sol-bridge.py
RUN python3 /tmp/revguard-patch-sol-bridge.py && rm /tmp/revguard-patch-sol-bridge.py
LABEL revguard.copaw.sol-bridge="1"
