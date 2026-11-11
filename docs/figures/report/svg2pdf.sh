#!/usr/bin/env bash
# Convert flowchart SVGs to cropped PDFs for the LaTeX report, using headless Edge.
#   bash docs/figures/report/svg2pdf.sh [name ...]      (default: every *.svg here)
# The SVGs name Arial Unicode MS; this swaps in Noto Sans TC, the face installed here.
set -e
cd "$(dirname "$0")"
EDGE="/c/Program Files (x86)/Microsoft/Edge/Application/msedge.exe"
TMP="$(mktemp -d)"
names=("$@"); [ ${#names[@]} -eq 0 ] && names=($(ls *.svg | sed 's/\.svg$//'))
for n in "${names[@]}"; do
  read W H < <(grep -o '^<svg[^>]*' "$n.svg" | sed -E 's/.*width="([0-9.]+)" height="([0-9.]+)".*/\1 \2/')
  { echo "<!doctype html><meta charset=utf-8><style>@page{size:${W}px ${H}px;margin:0}html,body{margin:0}svg{display:block}</style>"
    sed 's/font-family="Arial Unicode MS"/font-family="Noto Sans TC"/g' "$n.svg"; } > "$TMP/$n.html"
  "$EDGE" --headless=new --disable-gpu --no-pdf-header-footer \
    --print-to-pdf="$(cygpath -w "$PWD/$n.pdf")" "file:///$(cygpath -m "$TMP/$n.html")" 2>/dev/null
  echo "$n.pdf (${W}x${H})"
done
rm -rf "$TMP"
