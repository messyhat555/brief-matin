#!/bin/bash
# Installe brief-matin : script, config, application et réveil du matin.
set -euo pipefail
REPO="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
BASE="$HOME/.local/share/brief-matin"
LABEL="com.briefmatin.agent"
PLIST="$HOME/Library/LaunchAgents/$LABEL.plist"
APP="/Applications/Brief Matin.app"

say() { printf '  %s\n' "$*"; }

# Le PATH de l'installeur n'est pas celui du shell de connexion : on teste dans
# un shell vierge, sinon la commande reste introuvable dans un vrai terminal.
ensure_path() {
  bin="$1"
  if env -i HOME="$HOME" "${SHELL:-/bin/zsh}" -lc 'echo $PATH' 2>/dev/null \
       | tr ':' '\n' | grep -qx "$bin"; then
    say "PATH        $bin déjà accessible"
    return 0
  fi
  case "${SHELL:-/bin/zsh}" in
    *bash) prof="$HOME/.bash_profile" ;;
    *)     prof="${ZDOTDIR:-$HOME}/.zprofile" ;;
  esac
  if grep -qF "$bin:\$PATH" "$prof" 2>/dev/null; then
    say "PATH        déjà déclaré dans $prof"
  else
    {
      printf '\n# rend accessibles les commandes installées dans %s\n' "$bin"
      printf 'export PATH="%s:$PATH"\n' "$bin"
    } >> "$prof"
    say "PATH        $bin ajouté à $prof — ouvre un nouveau terminal"
  fi
}

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
ensure_path "$BIN"

"$REPO/build_app.sh" /Applications >/dev/null && say "application $APP"

# --- agents launchd (macOS) ------------------------------------------------
if [ "$(uname)" = "Darwin" ]; then
  cp "$REPO/plists.py" "$BASE/plists.py"
  python3 "$BASE/plists.py" "$BIN" >/dev/null
  for L in com.briefmatin.agent com.briefmatin.veille; do
    plutil -lint "$HOME/Library/LaunchAgents/$L.plist" >/dev/null
    launchctl bootout "gui/$(id -u)/$L" 2>/dev/null || true
    launchctl bootstrap "gui/$(id -u)" "$HOME/Library/LaunchAgents/$L.plist"
  done
  python3 "$BASE/plists.py" "$BIN" >/dev/null
else
  say "agents      launchd ignoré (hors macOS) — utilise cron :"
  say "            0 9 * * *   open -a '$APP'"
  say "            */3 * * * * $BIN/brief-matin veille"
fi

echo
"$BIN/brief-matin" doctor || true
echo "Ouvre la fenêtre :  brief-matin show"
echo
