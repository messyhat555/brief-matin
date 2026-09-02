#!/bin/bash
# Compile Brief Matin.app depuis BriefMatin.swift.
set -euo pipefail
REPO="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
DEST="${1:-/Applications}"
APP="$DEST/Brief Matin.app"

command -v swiftc >/dev/null || {
  echo "swiftc introuvable. Installe les outils Xcode :" >&2
  echo "  xcode-select --install" >&2; exit 1; }

rm -rf "$APP"
mkdir -p "$APP/Contents/MacOS" "$APP/Contents/Resources"

# Binaire universel : une tranche par architecture, reunies par lipo. La
# machine qui compile n'est pas forcement celle qui executera.
TMP="$(mktemp -d)"
TRANCHES=""
for A in arm64 x86_64; do
  if swiftc -O -target "$A-apple-macos12" -o "$TMP/BriefMatin-$A" \
       "$REPO/BriefMatin.swift" -framework Cocoa -framework WebKit 2>/dev/null; then
    TRANCHES="$TRANCHES $TMP/BriefMatin-$A"
    echo "  tranche $A compilée"
  else
    echo "  tranche $A indisponible, ignorée"
  fi
done
if [ -z "$TRANCHES" ]; then
  echo "Aucune architecture n'a pu être compilée." >&2
  exit 1
fi
lipo -create $TRANCHES -output "$APP/Contents/MacOS/BriefMatin"
rm -rf "$TMP"

cat > "$APP/Contents/Info.plist" <<'PLIST'
<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN" "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
<plist version="1.0">
<dict>
  <key>CFBundleName</key><string>Brief Matin</string>
  <key>CFBundleDisplayName</key><string>Brief Matin</string>
  <key>CFBundleIdentifier</key><string>com.briefmatin.app</string>
  <key>CFBundleExecutable</key><string>BriefMatin</string>
  <key>CFBundleIconFile</key><string>AppIcon</string>
  <key>CFBundlePackageType</key><string>APPL</string>
  <key>CFBundleShortVersionString</key><string>1.0</string>
  <key>LSMinimumSystemVersion</key><string>12.0</string>
  <key>NSHighResolutionCapable</key><true/>
</dict>
</plist>
PLIST

# icone : le soleil levant du jeu de symboles systeme, rendu en PNG puis en icns
ICONSET="$(mktemp -d)/AppIcon.iconset"; mkdir -p "$ICONSET"
python3 - "$ICONSET" <<'PY' 2>/dev/null || true
import subprocess, sys, pathlib
out = pathlib.Path(sys.argv[1])
svg = '''<svg xmlns="http://www.w3.org/2000/svg" width="1024" height="1024">
<defs><linearGradient id="g" x1="0" y1="0" x2="0" y2="1">
<stop offset="0" stop-color="#ffb45c"/><stop offset="1" stop-color="#ff7a59"/>
</linearGradient></defs>
<rect width="1024" height="1024" rx="230" fill="url(#g)"/>
<circle cx="512" cy="600" r="185" fill="#fff8ef"/>
<rect x="150" y="700" width="724" height="34" rx="17" fill="#fff8ef"/>
<rect x="250" y="790" width="524" height="34" rx="17" fill="#fff8ef" opacity=".7"/>
</svg>'''
p = out.parent/"icon.svg"; p.write_text(svg)
for s in (16, 32, 64, 128, 256, 512, 1024):
    subprocess.run(["qlmanage", "-t", "-s", str(s), "-o", str(out), str(p)],
                   capture_output=True)
PY
if command -v iconutil >/dev/null && ls "$ICONSET"/*.png >/dev/null 2>&1; then
  for f in "$ICONSET"/*.png; do mv "$f" "$ICONSET/icon_512x512.png" 2>/dev/null || true; done
  sips -z 512 512 "$ICONSET/icon_512x512.png" --out "$ICONSET/icon_512x512.png" >/dev/null 2>&1 || true
  for s in 16 32 128 256; do
    sips -z $s $s "$ICONSET/icon_512x512.png" --out "$ICONSET/icon_${s}x${s}.png" >/dev/null 2>&1 || true
  done
  iconutil -c icns "$ICONSET" -o "$APP/Contents/Resources/AppIcon.icns" 2>/dev/null || true
fi

# signature ad-hoc : sans elle macOS refuse les notifications a l'app
codesign --force --deep --sign - "$APP" 2>/dev/null || \
  echo "  (signature impossible — les notifications risquent d'être bloquées)"
LSREG="/System/Library/Frameworks/CoreServices.framework/Frameworks/LaunchServices.framework/Support/lsregister"
[ -x "$LSREG" ] && "$LSREG" -f "$APP" 2>/dev/null

touch "$APP"
echo "Compilé et signé : $APP"
