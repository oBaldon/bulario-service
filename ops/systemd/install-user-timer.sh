#!/usr/bin/env bash
set -euo pipefail

PROJECT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"
PYTHON_BIN="${PROJECT_DIR}/.venv/bin/python"
USER_UNIT_DIR="${XDG_CONFIG_HOME:-${HOME}/.config}/systemd/user"
SERVICE_TEMPLATE="${PROJECT_DIR}/ops/systemd/bulario-incremental.service.in"
TIMER_SOURCE="${PROJECT_DIR}/ops/systemd/bulario-incremental.timer"

if [[ ! -x "${PYTHON_BIN}" ]]; then
  echo "Python virtualenv not found: ${PYTHON_BIN}" >&2
  echo "Run 'uv sync' before installing the timer." >&2
  exit 2
fi

mkdir -p "${USER_UNIT_DIR}"

sed   -e "s|@PROJECT_DIR@|${PROJECT_DIR}|g"   -e "s|@PYTHON_BIN@|${PYTHON_BIN}|g"   "${SERVICE_TEMPLATE}"   > "${USER_UNIT_DIR}/bulario-incremental.service"

cp "${TIMER_SOURCE}"   "${USER_UNIT_DIR}/bulario-incremental.timer"

if [[ -n "${DISPLAY:-}" ]]; then
  systemctl --user import-environment DISPLAY
fi
if [[ -n "${XAUTHORITY:-}" ]]; then
  systemctl --user import-environment XAUTHORITY
fi

systemctl --user daemon-reload
systemctl --user enable --now bulario-incremental.timer

echo "bulario_incremental_timer_installed=true"
systemctl --user list-timers bulario-incremental.timer --no-pager
