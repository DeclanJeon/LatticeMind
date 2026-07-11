#!/usr/bin/env bash
set -euo pipefail
OWNER='latticemind-job-v1'
systemd_quote() {
  python3 - "$1" <<'PY'
import sys
s=sys.argv[1].replace('\\','\\\\').replace('"','\\"').replace('%','%%')
print('"'+s+'"', end='')
PY
}
export_existing() {
  local path="$1"
  if [[ -e "$path" ]] && ! grep -Fq "# owner=$OWNER schema=job-definition-v1" "$path"; then
    cp "$path" "$path.pre-latticemind-export"
    echo "ownership collision: exported $path to $path.pre-latticemind-export" >&2
    return 1
  fi
}
# latticemind-job-v1: native files are owned exclusively by this installer.
UNIT_DIR="${XDG_CONFIG_HOME:-$HOME/.config}/systemd/user"
BIN="$HOME/.local/bin/latticemind-maintain"
EXPORT="${LATTICEMIND_JOB_EXPORT:-$UNIT_DIR/latticemind-jobs.json}"
export_records=()
mkdir -p "$UNIT_DIR"
write() {
  local id="$1" mode="$2" cal="$3" jitter="$4" enabled="$5"
  export_existing "$UNIT_DIR/latticemind-$id.service"
  export_existing "$UNIT_DIR/latticemind-$id.timer"
  cat > "$UNIT_DIR/latticemind-$id.service" <<EOF
# owner=$OWNER schema=job-definition-v1
[Unit]
Description=LatticeMind $mode
[Service]
Type=oneshot
ExecStart=$(systemd_quote "$BIN") $mode --slot-state $(systemd_quote "$HOME/.local/state/latticemind/slots.json")
TimeoutStartSec=900
TimeoutStopSec=10
KillMode=mixed
Environment=LATTICEMIND_JOB_OWNER=latticemind-job-v1
Environment=$(systemd_quote "HOME=$HOME")
Environment=$(systemd_quote "XDG_CONFIG_HOME=${XDG_CONFIG_HOME:-$HOME/.config}")
Environment=$(systemd_quote "XDG_DATA_HOME=${XDG_DATA_HOME:-$HOME/.local/share}")
Environment=$(systemd_quote "XDG_RUNTIME_DIR=${XDG_RUNTIME_DIR:-/tmp}")
EOF
  cat > "$UNIT_DIR/latticemind-$id.timer" <<EOF
# owner=$OWNER schema=job-definition-v1
[Unit]
Description=LatticeMind $mode timer
[Timer]
OnCalendar=$cal
Persistent=true
RandomizedDelaySec=${jitter}s
Unit=latticemind-$id.service
# observe is default; only freshness and health are enabled
[Install]
WantedBy=timers.target
EOF
  local service_path="$UNIT_DIR/latticemind-$id.service"
  local timer_path="$UNIT_DIR/latticemind-$id.timer"
  local service_hash timer_hash
  service_hash="$(sha256sum "$service_path" | cut -d' ' -f1)"
  timer_hash="$(sha256sum "$timer_path" | cut -d' ' -f1)"
  export_records+=("{\"job_id\":\"$id\",\"platform\":\"systemd\",\"path\":\"$service_path\",\"owner\":\"$OWNER\",\"schema\":\"job-definition-v1\",\"enabled\":$enabled,\"sha256\":\"$service_hash\"}")
  export_records+=("{\"job_id\":\"$id\",\"platform\":\"systemd\",\"path\":\"$timer_path\",\"owner\":\"$OWNER\",\"schema\":\"job-definition-v1\",\"enabled\":$enabled,\"sha256\":\"$timer_hash\"}")
  if [[ "$enabled" == true ]]; then
    systemctl --user enable --now "latticemind-$id.timer" 2>/dev/null || true
  else
    systemctl --user disable --now "latticemind-$id.timer" 2>/dev/null || true
  fi
}
write morning morning '*-*-* 08:07:00' 0 false
write nightly nightly '*-*-* 22:17:00' 0 false
write weekly weekly 'Fri *-*-* 18:17:00' 0 false
write freshness freshness 'Sun *-*-* 19:17:00' 0 true
write health health 'Sun *-*-* 21:17:00' 0 true
systemctl --user daemon-reload 2>/dev/null || true
printf '{"jobs":[%s]}\n' "$(IFS=,; echo "${export_records[*]}")" > "$EXPORT"
