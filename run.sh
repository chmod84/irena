#!/usr/bin/env bash
# The one command: run every stale stage of the evaluation, in order.
# Equivalent to `python3 pipeline.py run`; extra arguments are passed through
# (e.g. ./run.sh --force policies).
set -euo pipefail
exec python3 "$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)/pipeline.py" run "$@"
