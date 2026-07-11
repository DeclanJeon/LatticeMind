#!/usr/bin/env bash
set -euo pipefail
OWNER='latticemind-job-v1'
xml_escape() {
  python3 - "$1" <<'PY'
import html,sys
print(html.escape(sys.argv[1], quote=True), end='')
PY
}
export_existing() {
  local path="$1"
  if [[ -e "$path" ]] && ! grep -Fq "<!-- owner=$OWNER schema=job-definition-v1 -->" "$path"; then
    cp "$path" "$path.pre-latticemind-export"
    echo "ownership collision: exported $path to $path.pre-latticemind-export" >&2
    return 1
  fi
}
# latticemind-job-v1: native files are owned exclusively by this installer.
DIR="${HOME}/Library/LaunchAgents"
BIN="${HOME}/.local/bin/latticemind-maintain"
EXPORT="${LATTICEMIND_JOB_EXPORT:-$DIR/latticemind-jobs.json}"
export_records=()
mkdir -p "$DIR"
write() {
  local id="$1" mode="$2" hour="$3" minute="$4" weekday="$5" enabled="$6"
  local path="$DIR/com.latticemind.$id.plist"
  export_existing "$path"
  cat > "$path" <<EOF
<?xml version="1.0" encoding="UTF-8"?>
<!-- owner=$OWNER schema=job-definition-v1 -->
<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN" "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
<plist version="1.0"><dict>
<key>Label</key><string>com.latticemind.$id</string>
<key>ProgramArguments</key><array><string>$(xml_escape "$BIN")</string><string>$(xml_escape "$mode")</string><string>--slot-state</string><string>$(xml_escape "$HOME/.local/state/latticemind/slots.json")</string></array>
<key>StartCalendarInterval</key><dict><key>Hour</key><integer>$hour</integer><key>Minute</key><integer>$minute</integer>$weekday</dict>
<key>ProcessType</key><string>Background</string><key>TimeOut</key><integer>900</integer>
<key>EnvironmentVariables</key><dict>
<key>HOME</key><string>$(xml_escape "$HOME")</string>
<key>XDG_CONFIG_HOME</key><string>$(xml_escape "${XDG_CONFIG_HOME:-$HOME/.config}")</string>
<key>XDG_DATA_HOME</key><string>$(xml_escape "${XDG_DATA_HOME:-$HOME/.local/share}")</string>
<key>XDG_RUNTIME_DIR</key><string>$(xml_escape "${XDG_RUNTIME_DIR:-/tmp}")</string>
</dict>
<key>ThrottleInterval</key><integer>10</integer><key>Disabled</key><$([[ "$enabled" == true ]] && echo false || echo true)/>
</dict></plist>
EOF
  export_records+=("{\"job_id\":\"$id\",\"platform\":\"launchd\",\"label\":\"com.latticemind.$id\",\"path\":\"$path\",\"owner\":\"$OWNER\",\"schema\":\"job-definition-v1\",\"enabled\":$enabled}")
  launchctl bootout "gui/$(id -u)" "$path" 2>/dev/null || true
  if [[ "$enabled" == true ]]; then
    launchctl bootstrap "gui/$(id -u)" "$path" 2>/dev/null || true
  fi
}
write morning morning 8 7 '' false
write nightly nightly 22 17 '' false
write weekly weekly 18 17 '<key>Weekday</key><integer>6</integer>' false
write freshness freshness 19 17 '<key>Weekday</key><integer>1</integer>' true
write health health 21 17 '<key>Weekday</key><integer>1</integer>' true
printf '{"jobs":[%s]}\n' "$(IFS=,; echo "${export_records[*]}")" > "$EXPORT"
