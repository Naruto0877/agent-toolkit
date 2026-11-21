#!/bin/sh
set -eu
cd "$(dirname "$0")"
task_python="${AGENT_TOOLKIT_PYTHON:-python3}"
exec "$task_python" scripts/install.py
