#!/usr/bin/env bash
# Copies the capture frames each shot uses (light and dark) from a take into assets/shots/, as
# JPEGs. The frame numbers belong to the filmed take of 2026-11-15 (docs/demo.md "The README
# video"); after a new take, pick its frames again from demo/video/.local/frames/run/.
set -euo pipefail
cd "$(dirname "$0")"
FRAMES=../.local/frames/run
OUT=assets/shots
mkdir -p "$OUT"
while read -r key frame; do
  [ -z "$key" ] && continue
  for theme in light dark; do
    src=$(ls "$FRAMES"/"$frame"-*."$theme".png)
    ffmpeg -nostdin -loglevel error -y -i "$src" -q:v 2 "$OUT/$key.$theme.jpg"
  done
done <<'SHOTS'
quiet 0000
watching 0018
down 0027
look1 0030
look2 0033
look3 0036
look4 0039
look5 0043
plan 0055
checking 0058
passed 0076
fixed 0078
allquiet 0089
SHOTS
# The phone's messages (demo/video/phone.py) as a script the composition loads.
printf 'window.BLEEP_PHONE = %s;\n' "$(cat ../.local/run/phone.json)" > assets/phone.js
