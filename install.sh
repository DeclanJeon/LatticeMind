#!/usr/bin/env bash
set -euo pipefail

REPO_URL="${LATTICEMIND_REPO_URL:-https://github.com/DeclanJeon/LatticeMind.git}"
REF="${LATTICEMIND_REF:-main}"
TMP_DIR="$(mktemp -d "${TMPDIR:-/tmp}/latticemind.XXXXXX")"
trap 'rm -rf "$TMP_DIR"' EXIT

printf '\n  LatticeMind · installing the living knowledge layer\n\n'
git clone --quiet --depth 1 --branch "$REF" "$REPO_URL" "$TMP_DIR/repo"
bash "$TMP_DIR/repo/scripts/install-local.sh" "$@"
