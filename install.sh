#!/bin/bash
# Installe brief-matin : script, config, application et réveil du matin.
set -euo pipefail
REPO="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
BASE="$HOME/.local/share/brief-matin"
LABEL="com.briefmatin.agent"
PLIST="$HOME/Library/LaunchAgents/$LABEL.plist"
APP="/Applications/Brief Matin.app"

say() { printf '  %s\n' "$*"; }
echo; echo "brief-matin — installation"; echo

command -v python3 >/dev/null || { echo "python3 requis." >&2; exit 1; }

if [ -d "$HOME/.local/bin" ]; then BIN="$HOME/.local/bin"
elif [ -d "$HOME/bin" ];        then BIN="$HOME/bin"
else BIN="$HOME/.local/bin"; mkdir -p "$BIN"; fi

mkdir -p "$BASE"
cp "$REPO/brief.py" "$BASE/brief.py"
say "script      $BASE/brief.py"

if [ -f "$BASE/config.json" ]; then
  say "config      conservée (existante)"
else
  cp "$REPO/config.example.json" "$BASE/config.json"
  python3 - "$BASE/config.json" <<'PY'
import json, sys, pathlib
p = pathlib.Path(sys.argv[1]); cfg = json.loads(p.read_text())
home = pathlib.Path.home()
for cand in (home/"Library/Application Support/obsidian/obsidian.json",
             home/".config/obsidian/obsidian.json"):
    try:
        v = json.loads(cand.read_text()).get("vaults", {})
    except (OSError, ValueError):
        continue
    if v:
        cfg["vault"] = max(v.values(), key=lambda x: x.get("ts", 0)).get("path"); break
p.write_text(json.dumps(cfg, indent=2, ensure_ascii=False))
print("  vault       " + str(cfg.get("vault") or "NON DÉTECTÉ — à renseigner"))
PY
fi

cat > "$BIN/brief-matin" <<'WRAP'
#!/bin/sh
exec python3 "$HOME/.local/share/brief-matin/brief.py" "$@"
WRAP
chmod +x "$BIN/brief-matin"
say "commande    $BIN/brief-matin"

"$REPO/build_app.sh" /Applications >/dev/null && say "application $APP"

HEURE="$(python3 -c 'import json,sys;print(json.load(open(sys.argv[1])).get("heure_matin") or "07:30")' "$BASE/config.json")"
H="${HEURE%%:*}"; M="${HEURE##*:}"
mkdir -p "$HOME/Library/LaunchAgents"
sed -e "s|__APP__|$APP|g" -e "s|__BASE__|$BASE|g" \
    -e "s|__H__|$((10#$H))|g" -e "s|__M__|$((10#$M))|g" \
    "$REPO/agent.plist.template" > "$PLIST"
plutil -lint "$PLIST" >/dev/null
launchctl bootout "gui/$(id -u)/$LABEL" 2>/dev/null || true
launchctl bootstrap "gui/$(id -u)" "$PLIST"
say "réveil      chaque jour ouvré à $HEURE"

echo
"$BIN/brief-matin" doctor || true
echo "Ouvre la fenêtre :  brief-matin show"
echo
