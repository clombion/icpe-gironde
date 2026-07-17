#!/usr/bin/env bash
# Fetches self-hosted web fonts for the Cahier d'enquête map interface.
# Sources WOFF2 files from the Google Fonts CSS API (subset: latin-ext) so
# there is no runtime dependency on fonts.googleapis.com.
#
# Run from the repo root: bash carte/scripts/fetch_fonts.sh
#
# Output: carte/fonts/*.woff2

set -euo pipefail

OUT_DIR="carte/fonts"
mkdir -p "$OUT_DIR"

# Modern UA required for Google Fonts to return WOFF2 URLs instead of WOFF/TTF.
UA="Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36"

fetch_first_latinext_woff2() {
  # $1 = full google fonts css2 URL
  # $2 = output filename
  local url="$1"
  local out="$2"
  local css
  css=$(curl -sSL -H "User-Agent: $UA" "$url")
  # Google Fonts returns multiple @font-face blocks, one per subset. The
  # latin-ext block is marked with a /* latin-ext */ comment immediately
  # preceding it. Use Python to parse (portable across BSD/GNU awk).
  local woff2
  woff2=$(printf '%s' "$css" | python3 -c '
import sys, re
css = sys.stdin.read()
m = re.search(r"/\*\s*latin-ext\s*\*/\s*@font-face\s*\{[^}]*?src:\s*url\(([^)]+)\)", css)
if m:
    print(m.group(1))
')
  if [[ -z "$woff2" ]]; then
    echo "ERROR: no latin-ext block found at $url" >&2
    echo "--- CSS returned ---" >&2
    echo "$css" | head -30 >&2
    return 1
  fi
  echo "  -> $woff2"
  curl -sSL -o "$OUT_DIR/$out" "$woff2"
  echo "  wrote $OUT_DIR/$out ($(wc -c < "$OUT_DIR/$out") bytes)"
}

echo "Fetching Fraunces (variable display serif)"
fetch_first_latinext_woff2 \
  "https://fonts.googleapis.com/css2?family=Fraunces:opsz,wght@9..144,400..600&display=swap" \
  "fraunces-vf.woff2"

echo "Fetching IBM Plex Sans 400"
fetch_first_latinext_woff2 \
  "https://fonts.googleapis.com/css2?family=IBM+Plex+Sans:wght@400&display=swap" \
  "plex-sans-400.woff2"

echo "Fetching IBM Plex Sans 500"
fetch_first_latinext_woff2 \
  "https://fonts.googleapis.com/css2?family=IBM+Plex+Sans:wght@500&display=swap" \
  "plex-sans-500.woff2"

echo "Fetching IBM Plex Sans 600"
fetch_first_latinext_woff2 \
  "https://fonts.googleapis.com/css2?family=IBM+Plex+Sans:wght@600&display=swap" \
  "plex-sans-600.woff2"

echo "Fetching IBM Plex Mono 400"
fetch_first_latinext_woff2 \
  "https://fonts.googleapis.com/css2?family=IBM+Plex+Mono:wght@400&display=swap" \
  "plex-mono-400.woff2"

echo "Fetching IBM Plex Mono 500"
fetch_first_latinext_woff2 \
  "https://fonts.googleapis.com/css2?family=IBM+Plex+Mono:wght@500&display=swap" \
  "plex-mono-500.woff2"

echo
echo "Done. Font files:"
ls -lh "$OUT_DIR"
