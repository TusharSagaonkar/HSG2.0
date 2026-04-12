#!/usr/bin/env bash
set -euo pipefail

PROJECT_DIR="/home/tushar/Documents/Projects/housing_accounting"
PYTHON_BIN="$PROJECT_DIR/.venv/bin/python"
LOG_FILE="/tmp/housing_email_queue.log"

cd "$PROJECT_DIR"
"$PYTHON_BIN" manage.py process_email_queue --limit 50 >> "$LOG_FILE" 2>&1
