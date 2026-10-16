#!/usr/bin/env bash
# Copyright 2026 KU Leuven.
# Licensed under the Apache License, Version 2.0, see LICENSE for details.
# SPDX-License-Identifier: Apache-2.0
# Download the pinned Claude Code CLI release and verify its SHA-256 checksum.
# ARES pins the CLI version because the cost accounting depends on how the CLI
# reports usage per call.
set -euo pipefail
PINNED_VERSION="2.1.181"
PINNED_SHA256="35ffd4e9d9a86395d0ba4e05f8b23bf098bfeab95e97deacd6537909d1324e9c"  # linux-x64
# Official Claude Code release bucket (the source of the public installer).
BUCKET="https://storage.googleapis.com/claude-code-dist-86c565f3-f756-42ad-8dfa-d59b1c096819/claude-code-releases"

if [[ "${1:-}" == "--help" || "${1:-}" == "-h" ]]; then
    cat <<HELP
Usage: CLAUDE_BIN=/path/outside/this/repo/claude bash fetch_cli.sh [--check]

Downloads Claude Code $PINNED_VERSION (linux-x64) to CLAUDE_BIN and verifies its
SHA-256 checksum. --check verifies an existing file and downloads nothing.

Optional settings:
  CLAUDE_VERSION        Another version; it also requires CLAUDE_SHA256.
  CLAUDE_SHA256         Expected checksum of that version.
  CLAUDE_PLATFORM       Release platform (default: linux-x64).
  CLAUDE_DOWNLOAD_URL   Direct download URL instead of the release bucket.
HELP
    exit 0
fi
if [[ $# -gt 1 || ( $# -eq 1 && "$1" != "--check" ) ]]; then
    echo "[FAIL] usage: fetch_cli.sh [--check|--help]" >&2
    exit 2
fi

VERSION="${CLAUDE_VERSION:-$PINNED_VERSION}"
PLATFORM="${CLAUDE_PLATFORM:-linux-x64}"
if [[ "$VERSION" != "$PINNED_VERSION" || "$PLATFORM" != "linux-x64" ]]; then
    : "${CLAUDE_SHA256:?Set CLAUDE_SHA256 for a version or platform other than $PINNED_VERSION linux-x64}"
fi
SHA256="${CLAUDE_SHA256:-$PINNED_SHA256}"
if [[ ! "$SHA256" =~ ^[0-9a-fA-F]{64}$ ]]; then
    echo "[FAIL] CLAUDE_SHA256 must contain exactly 64 hexadecimal characters" >&2
    exit 2
fi
: "${CLAUDE_BIN:?Set CLAUDE_BIN to the destination path outside this repository}"

# The CLI must stay outside the repository, which the container never mounts.
PROJECT="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)"
DEST="$(realpath -m -- "$CLAUDE_BIN")"
case "$DEST" in
    "$PROJECT"|"$PROJECT"/*) echo "[FAIL] CLAUDE_BIN must be outside $PROJECT" >&2; exit 2 ;;
esac

verify() {
    local checksum
    [[ -f "$DEST" ]] || return 1
    checksum="$(sha256sum -- "$DEST")"
    [[ "${checksum%% *}" == "${SHA256,,}" ]]
}
if verify; then
    echo "[fetch] checksum verified: $DEST"
    exit 0
fi
if [[ "${1:-}" == "--check" ]]; then
    echo "[FAIL] binary is missing or its checksum does not match: $DEST" >&2
    exit 1
fi

URL="${CLAUDE_DOWNLOAD_URL:-$BUCKET/$VERSION/$PLATFORM/claude}"
mkdir -p -- "$(dirname -- "$DEST")"
PART="$(mktemp -- "${DEST}.part.XXXXXX")"
trap 'rm -f -- "$PART"' EXIT
echo "[fetch] downloading Claude Code $VERSION ($PLATFORM) ..."
curl --fail --location --silent --show-error --output "$PART" "$URL"
CHECKSUM="$(sha256sum -- "$PART")"
if [[ "${CHECKSUM%% *}" != "${SHA256,,}" ]]; then
    echo "[FAIL] the download does not match the expected checksum $SHA256" >&2
    exit 1
fi
chmod 755 -- "$PART"
mv -- "$PART" "$DEST"
echo "[fetch] installed checksum-verified binary: $DEST"
