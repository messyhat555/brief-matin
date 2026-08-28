#!/bin/bash
set -euo pipefail
LABEL="com.briefmatin.agent"
launchctl bootout "gui/$(id -u)/$LABEL" 2>/dev/null || true
rm -f "$HOME/Library/LaunchAgents/$LABEL.plist"
rm -rf "/Applications/Brief Matin.app"
rm -f "$HOME/.local/bin/brief-matin" "$HOME/bin/brief-matin"
echo "Application, réveil et commande retirés."
echo "Conservé : ~/.local/share/brief-matin (config). Ton vault n'a pas été touché."
