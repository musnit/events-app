#!/usr/bin/env bash
# Everything CI would run: backend tests, frontend install/tests/type-check/build.
set -euo pipefail
cd "$(dirname "$0")/.."
python3 -m unittest discover -s tests -t .
npm --prefix web ci --no-audit --no-fund
npm --prefix web test
npm --prefix web run build
echo "All checks passed."
