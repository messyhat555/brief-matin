#!/usr/bin/env python3
"""Ecrit les deux agents launchd : l'ouverture du matin et la veille."""
import json, os, plistlib, sys
from pathlib import Path

HOME = Path.home()
BASE = HOME / ".local/share/brief-matin"
AGENTS = HOME / "Library/LaunchAgents"
APP = "/Applications/Brief Matin.app"

def main(bin_dir):
    cfg = json.loads((BASE / "config.json").read_text())
    heure = str(cfg.get("heure_matin") or "09:00")
    h, _, m = heure.partition(":")
    h, m = int(h or 9), int(m or 0)

    jours = cfg.get("jours_matin", "tous")
    if jours in ("tous", None, "", "all"):
        indices = list(range(7))          # 0 = dimanche pour launchd
    elif jours in ("semaine", "ouvres"):
        indices = [1, 2, 3, 4, 5]
    else:
        indices = [int(j) for j in jours]

    AGENTS.mkdir(parents=True, exist_ok=True)

    matin = {
        "Label": "com.briefmatin.agent",
        "ProgramArguments": ["/usr/bin/open", "-a", APP],
        "StartCalendarInterval": [{"Weekday": j, "Hour": h, "Minute": m}
                                  for j in indices],
        "RunAtLoad": False,
        "StandardOutPath": str(BASE / "agent.out.log"),
        "StandardErrorPath": str(BASE / "agent.err.log"),
    }

    veille = {
        "Label": "com.briefmatin.veille",
        "ProgramArguments": [str(Path(bin_dir) / "brief-matin"), "veille"],
        "EnvironmentVariables": {
            "PATH": "/usr/bin:/bin:/usr/sbin:/sbin:/opt/homebrew/bin",
            "HOME": str(HOME),
        },
        # les notes arrivent par paquets : inutile de scruter plus souvent
        "StartInterval": int(cfg.get("veille_secondes", 180)),
        # on surveille les dossiers ou vivent les taches ; le declenchement
        # immediat complete la ronde periodique
        "WatchPaths": [str(Path(cfg["vault"]) / d)
                       for d in (cfg.get("dossiers_taches") or ["10 Cours"])
                       if (Path(cfg["vault"]) / d).is_dir()],
        "ThrottleInterval": 30,
        "RunAtLoad": True,
        "ProcessType": "Background",
        "LowPriorityIO": True,
        "StandardOutPath": str(BASE / "veille.out.log"),
        "StandardErrorPath": str(BASE / "veille.err.log"),
    }

    for d in (matin, veille):
        chemin = AGENTS / (d["Label"] + ".plist")
        with open(chemin, "wb") as f:
            plistlib.dump(d, f)
        print(chemin)

    jours_txt = ("tous les jours" if len(indices) == 7 else
                 "du lundi au vendredi" if indices == [1, 2, 3, 4, 5] else
                 f"{len(indices)} jour(s)")
    print(f"  ouverture   {jours_txt} à {h:02d}:{m:02d}", file=sys.stderr)
    print(f"  veille      toutes les {veille['StartInterval']} s", file=sys.stderr)

if __name__ == "__main__":
    main(sys.argv[1] if len(sys.argv) > 1 else str(HOME / "bin"))
