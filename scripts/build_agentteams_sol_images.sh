#!/usr/bin/env bash
# Compatibility entry point for the recorded Sol release.
set -euo pipefail
ROOT_DIR=$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)
AGENTTEAMS_IMAGE_TAG="${AGENTTEAMS_IMAGE_TAG:-sol-20260912}" \
  bash "$ROOT_DIR/scripts/build_agentteams_images.sh"
