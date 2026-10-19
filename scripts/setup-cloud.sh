#!/usr/bin/env bash
set -euo pipefail
cd "$(dirname "${BASH_SOURCE[0]}")/.."
python3 -c 'import sys; assert sys.version_info >= (3, 11), "Python 3.11+ is required"'
if [ ! -x .venv/bin/python ]; then
  python3 -m venv .venv
fi
mkdir -p .jarvis-data/pip-cache
.venv/bin/python -m pip install --disable-pip-version-check --cache-dir .jarvis-data/pip-cache -r requirements-dev.txt
.venv/bin/python -m pytest -q
