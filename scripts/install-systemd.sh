#!/usr/bin/env bash
set -euo pipefail

UNIT_DIR="${XDG_CONFIG_HOME:-$HOME/.config}/systemd/user"
BIN="$HOME/.local/bin/latticemind-maintain"
mkdir -p "$UNIT_DIR"

write_service() {
  local mode="$1" description="$2"
  cat > "$UNIT_DIR/latticemind-$mode.service" <<EOF
[Unit]
Description=$description
After=network-online.target

[Service]
Type=oneshot
ExecStart=$BIN $mode
Nice=10
EOF
}

write_timer() {
  local mode="$1" description="$2" calendar="$3" delay="$4"
  cat > "$UNIT_DIR/latticemind-$mode.timer" <<EOF
[Unit]
Description=$description

[Timer]
OnCalendar=$calendar
Persistent=true
RandomizedDelaySec=$delay
Unit=latticemind-$mode.service

[Install]
WantedBy=timers.target
EOF
}

write_service morning "LatticeMind morning note"
write_service nightly "LatticeMind nightly consolidation"
write_service weekly "LatticeMind weekly review"
write_service health "LatticeMind health audit"
write_timer morning "Run LatticeMind every morning" "*-*-* 08:07:00" "5m"
write_timer nightly "Run LatticeMind nightly" "*-*-* 22:17:00" "10m"
write_timer weekly "Run LatticeMind weekly" "Fri *-*-* 18:17:00" "10m"
write_timer health "Run LatticeMind health audit" "Sun *-*-* 21:17:00" "10m"

systemctl --user daemon-reload
systemctl --user enable --now \
  latticemind-morning.timer latticemind-nightly.timer \
  latticemind-weekly.timer latticemind-health.timer
