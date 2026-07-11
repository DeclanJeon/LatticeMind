#!/usr/bin/env bash
set -euo pipefail
umask 077

RELEASE_BASE="${LATTICEMIND_RELEASE_BASE:-https://github.com/DeclanJeon/LatticeMind/releases/latest/download}"
export LATTICEMIND_MANIFEST_URL="${LATTICEMIND_MANIFEST_URL:-$RELEASE_BASE/release-manifest-v1.json}"
export LATTICEMIND_SIGNATURE_URL="${LATTICEMIND_SIGNATURE_URL:-$RELEASE_BASE/release-manifest-v1.sig}"
export LATTICEMIND_ASSET_URL="${LATTICEMIND_ASSET_URL:-$RELEASE_BASE/latticemind-dist.zip}"

SOURCE="${BASH_SOURCE[0]:-}"
if [[ -f "$SOURCE" ]]; then
  ROOT_DIR="$(cd "$(dirname "$SOURCE")" && pwd)"
  exec bash "$ROOT_DIR/scripts/install-local.sh" "$@"
fi

# A raw-GitHub pipe has no checkout to trust. Bootstrap only the pinned
# verifier and installer support; lifecycle bytes are accepted only from the
# signed extracted release payload.
BOOTSTRAP_DIR="$(mktemp -d "${TMPDIR:-/tmp}/latticemind-bootstrap.XXXXXX")"
trap 'rm -rf "$BOOTSTRAP_DIR"' EXIT
BOOTSTRAP_BASE="${LATTICEMIND_BOOTSTRAP_BASE:-https://raw.githubusercontent.com/DeclanJeon/LatticeMind/main}"
mkdir -p "$BOOTSTRAP_DIR/scripts" "$BOOTSTRAP_DIR/latticemind_core"
fetch() {
  curl --fail --location --proto '=https' --tlsv1.2 "$1" -o "$2"
}
verify_pin() {
  local expected="$1" path="$2" actual
  actual="$(python3 -c 'import hashlib,sys; print(hashlib.sha256(open(sys.argv[1],"rb").read()).hexdigest())' "$path")"
  [[ "$actual" == "$expected" ]] || {
    printf 'Bootstrap support hash mismatch: %s\n' "$path" >&2
    exit 76
  }
}
fetch "$BOOTSTRAP_BASE/scripts/install-local.sh" "$BOOTSTRAP_DIR/scripts/install-local.sh"
fetch "$BOOTSTRAP_BASE/scripts/latticemind-verify" "$BOOTSTRAP_DIR/scripts/latticemind-verify"
fetch "$BOOTSTRAP_BASE/latticemind_core/release.py" "$BOOTSTRAP_DIR/latticemind_core/release.py"
fetch "$BOOTSTRAP_BASE/latticemind_core/trust_root.py" "$BOOTSTRAP_DIR/latticemind_core/trust_root.py"
fetch "$BOOTSTRAP_BASE/latticemind_core/__init__.py" "$BOOTSTRAP_DIR/latticemind_core/__init__.py"
verify_pin "2b085c3ac9ef408be250c8ba65723008b0512d6a11988fcd4c0bd26b0041efd8" "$BOOTSTRAP_DIR/scripts/install-local.sh"
verify_pin "2e4ab0eff554e1b4dc64e877cbd4fcb1c8aef9029f7d67cc31306cc5cd0a3be5" "$BOOTSTRAP_DIR/scripts/latticemind-verify"
verify_pin "79c7d6c76d238683ef52a3c2035f0fab06f60ede27503df4b44fefdd4bd481ce" "$BOOTSTRAP_DIR/latticemind_core/release.py"
verify_pin "0d005eab9b2f4df946e90ed0db6e44ad1320309023a05d228a79ce8ba40f0f11" "$BOOTSTRAP_DIR/latticemind_core/trust_root.py"
verify_pin "9d7c4b56155e3f94a61293858aea80fa312975bd92ca2d413f95c7f1f0f5d536" "$BOOTSTRAP_DIR/latticemind_core/__init__.py"
chmod 755 "$BOOTSTRAP_DIR/scripts/install-local.sh" "$BOOTSTRAP_DIR/scripts/latticemind-verify"
exec bash "$BOOTSTRAP_DIR/scripts/install-local.sh" "$@"
