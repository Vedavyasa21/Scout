#!/usr/bin/env bash
# Run every check CI runs, locally.
set -euo pipefail

cd "$(dirname "$0")/.."

python -m ruff check src tests
python -m ruff format --check src tests
python -m pytest
