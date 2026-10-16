#!/usr/bin/env bash
# Export the add-on's PNG assets from the SVG sources (macOS: QuickLook + sips, no installs).
# Sizes from the Alexa+ MCP quickstart: icons 72, 64, 88, 126, 180, 241 px (light set);
# carousel image 600x900.
set -euo pipefail
cd "$(dirname "$0")/../addon-package/assets"
TMP="$(mktemp -d)"; trap 'rm -rf "$TMP"' EXIT

qlmanage -t -s 512 -o "$TMP" icon.svg >/dev/null 2>&1
for size in 64 72 88 126 180 241; do
  sips -z "$size" "$size" "$TMP/icon.svg.png" --out "icon-${size}.png" >/dev/null
done

# QuickLook renders square thumbnails and scales to the width, so centre the 600x900
# artwork on a 900x900 canvas first, then crop it back out.
python3 - "$TMP" <<'PY'
import re, sys
src = open("carousel.svg").read()
inner = re.search(r"<svg[^>]*>(.*)</svg>", src, re.S).group(1)
open(f"{sys.argv[1]}/carousel-square.svg", "w").write(
    '<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 900 900" width="900" height="900">'
    f'<g transform="translate(150 0)">{inner}</g></svg>')
PY
qlmanage -t -s 900 -o "$TMP" "$TMP/carousel-square.svg" >/dev/null 2>&1
sips -c 900 600 "$TMP/carousel-square.svg.png" --out carousel-600x900.png >/dev/null

for f in icon-*.png carousel-600x900.png; do
  printf "%-22s %s\n" "$f" "$(sips -g pixelWidth -g pixelHeight "$f" | awk '/pixel/{printf "%s ", $2}')"
done
