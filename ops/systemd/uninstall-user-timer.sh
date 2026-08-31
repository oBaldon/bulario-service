#!/usr/bin/env bash
set -euo pipefail

USER_UNIT_DIR="${XDG_CONFIG_HOME:-${HOME}/.config}/systemd/user"

systemctl --user disable --now bulario-incremental.timer 2>/dev/null || true
rm -f   "${USER_UNIT_DIR}/bulario-incremental.timer"   "${USER_UNIT_DIR}/bulario-incremental.service"
systemctl --user daemon-reload

echo "bulario_incremental_timer_uninstalled=true"
