#!/usr/bin/env python3
"""Genere les captures d'ecran du README depuis un vrai brief."""
import json, subprocess, sys, datetime as dt, importlib.util
from pathlib import Path

BRAVE = "/Applications/Brave Browser.app/Contents/MacOS/Brave Browser"
CHROME = "/Applications/Google Chrome.app/Contents/MacOS/Google Chrome"
REPO = Path(__file__).resolve().parent
SORTIE = REPO / "captures"

def navigateur():
    for c in (BRAVE, CHROME):
        if Path(c).exists():
            return c
    sys.exit("Aucun navigateur Chromium trouvé pour le rendu.")

def main():
    jour = dt.date.fromisoformat(sys.argv[1]) if len(sys.argv) > 1 else dt.date.today()
    heure = sys.argv[2] if len(sys.argv) > 2 else "10:15"
    hh, mm = (int(x) for x in heure.split(":"))
    maintenant = dt.datetime.combine(jour, dt.time(hh, mm))
    spec = importlib.util.spec_from_file_location("b", REPO / "brief.py")
    m = importlib.util.module_from_spec(spec); spec.loader.exec_module(m)
    cfg = m.load_config()
    # pour la documentation on montre l'etat normal : un acces valide, pas le
    # bandeau d'expiration du jeton reel
    cfg = dict(cfg); cfg["zeus"] = dict(cfg["zeus"])
    import base64
    b64 = lambda o: base64.urlsafe_b64encode(json.dumps(o).encode()).decode().rstrip("=")
    loin = int((dt.datetime.combine(jour, dt.time()) + dt.timedelta(days=3)).timestamp())
    cfg["zeus"]["token_affiche"] = True

    vrai_jeton = m.jeton_actuel(cfg)
    cfg["zeus"]["token"] = (b64({"alg": "HS256"}) + "." +
                            b64({"sub": "demo", "exp": loin}) + ".s")

    lundi = jour - dt.timedelta(days=jour.weekday())
    data = m.zeus_reservations(cfg, lundi, lundi + dt.timedelta(days=13),
                               tok=vrai_jeton)
    planning = m.mise_en_forme(data, jour)
    semaines = {lundi + dt.timedelta(days=7 * w): {
        lundi + dt.timedelta(days=7 * w + i): m.mise_en_forme(
            data, lundi + dt.timedelta(days=7 * w + i)) for i in range(7)}
        for w in (0, 1)}
    devoirs, revisions = m.lire_taches(cfg)

    SORTIE.mkdir(exist_ok=True)
    page = SORTIE / "_page.html"
    page.write_text(m.render_html(cfg, planning, None, devoirs, revisions, semaines,
                                  maintenant=maintenant), encoding="utf-8")

    nav = navigateur()
    for vue, hauteur in (("jour", 940), ("semaine", 720), ("focus", 780)):
        cible = SORTIE / f"{vue}.png"
        subprocess.run([
            nav, "--headless", "--disable-gpu", "--hide-scrollbars",
            "--force-dark-mode", "--enable-features=WebContentsForceDark",
            "--virtual-time-budget=4000",
            f"--screenshot={cible}", f"--window-size=520,{hauteur}",
            f"file://{page}#{vue}",
        ], capture_output=True, timeout=120)
        print(f"  {cible.name}  ({cible.stat().st_size // 1024} Ko)")
    page.unlink(missing_ok=True)

if __name__ == "__main__":
    main()
