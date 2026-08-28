#!/usr/bin/env python3
"""
brief-matin — le planning du jour (Zeus) et les devoirs (Obsidian),
dans une petite fenetre, tous les matins.

  render        regenere le HTML du brief
  show          regenere puis ouvre la fenetre
  zeus-groupes  liste les groupes Zeus (pour remplir la config)
  zeus-test     verifie l'acces a Zeus
  doctor        verifie l'installation
"""

import argparse, datetime as dt, html, json, os, re, subprocess, sys, unicodedata
import urllib.error
import urllib.request
from pathlib import Path

HOME = Path.home()
BASE = HOME / ".local/share/brief-matin"
CONFIG_PATH = BASE / "config.json"
OUT_HTML = BASE / "brief.html"
APP = "/Applications/Brief Matin.app"

JOURS = ["lundi", "mardi", "mercredi", "jeudi", "vendredi", "samedi", "dimanche"]
MOIS = ["janvier", "février", "mars", "avril", "mai", "juin", "juillet",
        "août", "septembre", "octobre", "novembre", "décembre"]

def log(m, level="INFO"):
    print(f"[{level}] {m}", file=sys.stderr, flush=True)

def load_config():
    if not CONFIG_PATH.exists():
        raise SystemExit(f"Config absente: {CONFIG_PATH}\nLance ./install.sh depuis le depot.")
    cfg = json.loads(CONFIG_PATH.read_text())
    cfg["vault"] = os.path.expanduser(cfg.get("vault") or "")
    return cfg

def date_fr(d):
    return f"{JOURS[d.weekday()]} {d.day} {MOIS[d.month - 1]}"

# --------------------------------------------------------------------------
# Obsidian : devoirs et revisions
# --------------------------------------------------------------------------

FM_RE = re.compile(r"^---\n(.*?)\n---", re.S)

def frontmatter(txt):
    m = FM_RE.match(txt)
    if not m:
        return {}
    out = {}
    for line in m.group(1).splitlines():
        if ":" in line:
            k, v = line.split(":", 1)
            out[k.strip()] = v.strip().strip('"')
    return out

# les titres de section qui portent des vraies echeances, et ceux qui
# portent du travail de revision
def sans_accents(s):
    s = unicodedata.normalize("NFKD", str(s or ""))
    return "".join(c for c in s if not unicodedata.combining(c)).lower()

# compares sur du texte sans accents : les notes ecrites a la main comme celles
# generees n'accentuent pas toujours leurs titres
SECTIONS_DEVOIRS = re.compile(r"a\s*faire|devoirs?|a\s*rendre|travail|todo", re.I)
SECTIONS_REVISION = re.compile(r"retravailler|reviser|a\s*revoir|revisions?", re.I)

DATE_TXT = re.compile(
    r"\b(?:le|avant\s+le|pour\s+le|d'ici\s+(?:au\s+)?le|rendre\s+le)\s+"
    r"(\d{1,2})(?:er)?(?:\s+(" + "|".join(sans_accents(m) for m in MOIS) + r"))?\b",
    re.I)
DATE_JOUR = re.compile(
    r"\b(?:pour|avant|d'ici)\s+(?:le\s+)?(" + "|".join(JOURS) + r")\b", re.I)
DATE_ISO = re.compile(r"(\d{4}-\d{2}-\d{2})")

def echeance(texte, ref):
    """Devine une date d'echeance dans le texte d'une tache.
    Renvoie None plutot que d'inventer une date quand rien n'est sur."""
    plat = sans_accents(texte)
    m = DATE_ISO.search(texte)
    if m:
        try:
            return dt.date.fromisoformat(m.group(1))
        except ValueError:
            pass
    m = DATE_JOUR.search(plat)
    if m:
        cible = JOURS.index(m.group(1).lower())
        delta = (cible - ref.weekday()) % 7 or 7
        return ref + dt.timedelta(days=delta)
    m = DATE_TXT.search(plat)
    if m:
        jour = int(m.group(1))
        mois = MOIS.index(m.group(2).lower()) + 1 if m.group(2) else ref.month
        annee = ref.year
        try:
            d = dt.date(annee, mois, jour)
        except ValueError:
            return None
        if d < ref - dt.timedelta(days=7):     # date passee -> mois/annee suivant
            d = dt.date(annee + (mois == 12), mois % 12 + 1, jour) if not m.group(2) \
                else dt.date(annee + 1, mois, jour)
        return d
    return None

def lire_taches(cfg):
    """Parcourt le vault et separe devoirs et revisions selon la section."""
    vault = Path(cfg["vault"])
    devoirs, revisions = [], []
    aujourdhui = dt.date.today()
    for dossier in cfg.get("dossiers_taches") or ["10 Cours"]:
        racine = vault / dossier
        if not racine.is_dir():
            continue
        for f in sorted(racine.rglob("*.md")):
            txt = f.read_text(encoding="utf-8", errors="replace")
            fm = frontmatter(txt)
            matiere = fm.get("matiere") or f.parent.name
            note_date = fm.get("date") or ""
            section = ""
            for line in txt.splitlines():
                if line.startswith("#"):
                    section = line.lstrip("#").strip()
                    continue
                m = re.match(r"\s*[-*]\s*\[( |x|X)\]\s*(.+)", line)
                if not m or m.group(1).lower() == "x":
                    continue
                texte = re.sub(r"[📅🔺⏫🔼🔽]", "", m.group(2)).strip()
                item = {"texte": texte, "matiere": matiere, "note": f.stem,
                        "note_date": note_date, "echeance": None}
                # la revision se teste en premier : "a retravailler" contient
                # "travail" et serait sinon pris pour un devoir
                sec = sans_accents(section)
                if SECTIONS_REVISION.search(sec):
                    revisions.append(item)
                elif SECTIONS_DEVOIRS.search(sec):
                    item["echeance"] = echeance(texte, aujourdhui)
                    devoirs.append(item)
    devoirs.sort(key=lambda i: (i["echeance"] is None, i["echeance"] or dt.date.max))
    revisions.sort(key=lambda i: i["note_date"], reverse=True)
    return devoirs, revisions

# --------------------------------------------------------------------------
# Zeus : planning du jour
# --------------------------------------------------------------------------

class ZeusError(RuntimeError):
    pass

def http(url, data=None, token=None, timeout=20, method=None):
    req = urllib.request.Request(url, method=method or ("POST" if data else "GET"))
    req.add_header("Accept", "application/json")
    if token:
        req.add_header("Authorization", f"Bearer {token}")
    body = None
    if data is not None:
        body = json.dumps(data).encode()
        req.add_header("Content-Type", "application/json")
    try:
        with urllib.request.urlopen(req, body, timeout=timeout) as r:
            raw = r.read().decode("utf-8", "replace")
    except urllib.error.HTTPError as e:
        detail = e.read().decode("utf-8", "replace")[:200]
        if e.code in (401, 403):
            raise ZeusError("authentification Zeus refusée (jeton absent ou expiré)")
        raise ZeusError(f"Zeus a repondu {e.code} : {detail}")
    except (urllib.error.URLError, OSError) as e:
        raise ZeusError(f"Zeus injoignable : {e}")
    try:
        return json.loads(raw)
    except json.JSONDecodeError:
        return raw

def zeus_token(cfg):
    z = cfg.get("zeus") or {}
    if z.get("app_id"):
        tok = http(f"{z['base_url']}/api/Application/Login", {"appId": z["app_id"]})
        if isinstance(tok, dict):
            tok = tok.get("accessToken") or tok.get("token")
        if not tok:
            raise ZeusError("Le login par app_id n'a pas renvoyé de jeton")
        return str(tok).strip('"')
    if z.get("token"):
        return z["token"].strip()
    raise ZeusError("Aucun accès Zeus configuré — renseigne app_id ou token dans la config")

def zeus_planning(cfg, jour=None):
    z = cfg.get("zeus") or {}
    jour = jour or dt.date.today()
    tok = zeus_token(cfg)
    debut = dt.datetime.combine(jour, dt.time(0, 0)).isoformat()
    fin = dt.datetime.combine(jour, dt.time(23, 59)).isoformat()
    if z.get("groupes"):
        data = http(f"{z['base_url']}/api/reservation/filter/displayable",
                    {"groups": z["groupes"], "startDate": debut, "endDate": fin}, tok)
    elif z.get("groupe_nom"):
        data = http(f"{z['base_url']}/api/reservation/byGroupName",
                    {"parentName": z.get("groupe_parent") or "",
                     "groupName": z["groupe_nom"],
                     "startDate": debut, "endDate": fin}, tok)
    else:
        raise ZeusError("Aucun groupe configuré — renseigne zeus.groupes ou zeus.groupe_nom")
    if not isinstance(data, list):
        return []
    out = []
    for r in data:
        out.append({
            "nom": r.get("name") or "Cours",
            "type": r.get("typeName") or "",
            "debut": (r.get("startDate") or "")[11:16],
            "fin": (r.get("endDate") or "")[11:16],
            "salles": ", ".join(s.get("name", "") for s in (r.get("rooms") or []) if s),
            "profs": ", ".join(
                " ".join(filter(None, (t.get("firstName"), t.get("lastName"))))
                for t in (r.get("teachers") or []) if t),
            "en_ligne": bool(r.get("isOnline")),
        })
    out.sort(key=lambda c: c["debut"])
    return out

# --------------------------------------------------------------------------
# rendu HTML
# --------------------------------------------------------------------------

def e(s):
    return html.escape(str(s or ""))

def urgence(d, aujourdhui):
    if d is None:
        return "", ""
    delta = (d - aujourdhui).days
    if delta < 0:
        return "retard", f"en retard de {-delta} j"
    if delta == 0:
        return "auj", "aujourd'hui"
    if delta == 1:
        return "demain", "demain"
    if delta <= 7:
        return "semaine", f"dans {delta} j"
    return "loin", d.strftime("%d/%m")

def render_html(cfg, planning, planning_err, devoirs, revisions):
    a = dt.date.today()
    n_urgents = sum(1 for d in devoirs
                    if d["echeance"] and (d["echeance"] - a).days <= 1)
    P = []
    P.append(f"""<!doctype html><html lang="fr"><head><meta charset="utf-8">
<title>Brief du matin</title><style>
:root {{
  --bg:#faf9f7; --fg:#1c1b19; --muted:#77726b; --line:#e6e2dc; --card:#ffffff;
  --accent:#7c5cff; --rouge:#c0392b; --orange:#c07a2b; --vert:#2b7a4b;
}}
@media (prefers-color-scheme: dark) {{
  :root {{ --bg:#17161a; --fg:#ecebe8; --muted:#9b958d; --line:#2c2a30;
           --card:#1f1e23; --accent:#a08cff; --rouge:#ff8a7a; --orange:#e0a960;
           --vert:#7fd3a0; }}
}}
* {{ box-sizing:border-box; }}
body {{ margin:0; background:var(--bg); color:var(--fg); font:14px/1.5
  -apple-system,BlinkMacSystemFont,"SF Pro Text",sans-serif; padding:22px 20px 28px; }}
h1 {{ font-size:20px; margin:0 0 2px; letter-spacing:-.2px; }}
.sub {{ color:var(--muted); font-size:13px; margin-bottom:20px; }}
h2 {{ font-size:11px; text-transform:uppercase; letter-spacing:.09em;
  color:var(--muted); margin:24px 0 9px; font-weight:600; }}
h2:first-of-type {{ margin-top:0; }}
.card {{ background:var(--card); border:1px solid var(--line); border-radius:10px;
  padding:11px 13px; margin-bottom:7px; }}
.cours {{ display:flex; gap:12px; align-items:baseline; }}
.h {{ font-variant-numeric:tabular-nums; font-weight:600; white-space:nowrap;
  color:var(--accent); }}
.nom {{ font-weight:600; }}
.meta {{ color:var(--muted); font-size:12px; margin-top:2px; }}
.tache {{ display:flex; gap:9px; align-items:flex-start; }}
.puce {{ width:13px; height:13px; border:1.5px solid var(--muted); border-radius:4px;
  flex:0 0 auto; margin-top:3px; }}
.mat {{ font-size:11px; color:var(--muted); text-transform:uppercase;
  letter-spacing:.05em; margin-top:3px; }}
.tag {{ font-size:11px; padding:1px 7px; border-radius:20px; white-space:nowrap;
  border:1px solid currentColor; }}
.retard,.auj {{ color:var(--rouge); }} .demain,.semaine {{ color:var(--orange); }}
.loin {{ color:var(--muted); }}
.vide {{ color:var(--muted); font-style:italic; padding:6px 2px; }}
.err {{ border-color:var(--orange); }} .err .meta {{ color:var(--orange); }}
.pied {{ margin-top:26px; color:var(--muted); font-size:11px;
  border-top:1px solid var(--line); padding-top:11px; }}
</style></head><body>""")

    P.append(f"<h1>{e(date_fr(a).capitalize())}</h1>")
    resume = []
    if planning:
        resume.append(f"{len(planning)} cours")
    if n_urgents:
        resume.append(f"{n_urgents} à rendre sous 24 h")
    elif devoirs:
        resume.append(f"{len(devoirs)} devoir{'s' if len(devoirs) > 1 else ''}")
    P.append(f'<div class="sub">{e(" · ".join(resume) or "rien de prévu")}</div>')

    P.append("<h2>Aujourd'hui</h2>")
    if planning_err:
        P.append(f'<div class="card err"><div class="nom">Planning indisponible</div>'
                 f'<div class="meta">{e(planning_err)}</div></div>')
    elif not planning:
        P.append('<div class="vide">Aucun cours prévu.</div>')
    else:
        for c in planning:
            lieu = "en ligne" if c["en_ligne"] else (c["salles"] or "salle ?")
            meta = " · ".join(x for x in (c["type"], lieu, c["profs"]) if x)
            P.append(f'''<div class="card"><div class="cours">
<span class="h">{e(c["debut"])}–{e(c["fin"])}</span>
<span><span class="nom">{e(c["nom"])}</span>
<div class="meta">{e(meta)}</div></span></div></div>''')

    P.append("<h2>À rendre</h2>")
    if not devoirs:
        P.append('<div class="vide">Rien à rendre.</div>')
    for d in devoirs[: cfg.get("max_devoirs", 12)]:
        cls, lbl = urgence(d["echeance"], a)
        tag = f'<span class="tag {cls}">{e(lbl)}</span>' if lbl else ""
        P.append(f'''<div class="card"><div class="tache"><span class="puce"></span>
<span style="flex:1">{e(d["texte"])}<div class="mat">{e(d["matiere"])}</div></span>
{tag}</div></div>''')

    if revisions:
        P.append("<h2>À réviser</h2>")
        for r in revisions[: cfg.get("max_revisions", 6)]:
            P.append(f'''<div class="card"><div class="tache"><span class="puce"></span>
<span style="flex:1">{e(r["texte"])}<div class="mat">{e(r["matiere"])}</div></span>
</div></div>''')

    P.append(f'<div class="pied">Généré à {dt.datetime.now():%H:%M} · '
             f'{e(len(devoirs))} devoir(s), {e(len(revisions))} point(s) de révision</div>')
    P.append("</body></html>")
    return "\n".join(P)

# --------------------------------------------------------------------------
# commandes
# --------------------------------------------------------------------------

def build(cfg):
    devoirs, revisions = lire_taches(cfg)
    planning, err = [], None
    try:
        planning = zeus_planning(cfg)
    except ZeusError as ex:
        err = str(ex)
        log(f"Zeus: {ex}", "WARN")
    BASE.mkdir(parents=True, exist_ok=True)
    OUT_HTML.write_text(render_html(cfg, planning, err, devoirs, revisions),
                        encoding="utf-8")
    return planning, err, devoirs, revisions

def cmd_render(cfg, args):
    planning, err, devoirs, revisions = build(cfg)
    print(f"{OUT_HTML}  ({len(planning)} cours, {len(devoirs)} devoirs, "
          f"{len(revisions)} révisions)")
    return 0

def cmd_show(cfg, args):
    cmd_render(cfg, args)
    if Path(APP).exists():
        subprocess.run(["open", "-a", APP])
    else:
        subprocess.run(["open", str(OUT_HTML)])
    return 0

def cmd_zeus_groupes(cfg, args):
    tok = zeus_token(cfg)
    data = http(f"{cfg['zeus']['base_url']}/api/group", token=tok)
    if not isinstance(data, list):
        print(data); return 1
    motif = (args.filtre or "").lower()
    for g in data:
        nom = g.get("name", "")
        if motif and motif not in nom.lower():
            continue
        print(f"  {g.get('id'):>6}  {nom}")
    print(f"\n{len(data)} groupe(s). Reporte les ids voulus dans "
          f'"zeus": {{"groupes": [...]}} de {CONFIG_PATH}')
    return 0

def cmd_zeus_test(cfg, args):
    try:
        tok = zeus_token(cfg)
        print(f"  [OK ] jeton obtenu ({len(tok)} caractères)")
        p = zeus_planning(cfg)
        print(f"  [OK ] planning du jour : {len(p)} cours")
        for c in p:
            print(f"        {c['debut']}–{c['fin']}  {c['nom']}  {c['salles']}")
    except ZeusError as ex:
        print(f"  [KO ] {ex}")
        return 1
    return 0

# --------------------------------------------------------------------------
# recuperation du jeton depuis le presse-papiers
# --------------------------------------------------------------------------

def decode_jwt(tok):
    """Lit la charge utile d'un JWT sans verifier la signature."""
    import base64
    parts = tok.split(".")
    if len(parts) != 3:
        raise ValueError("ce n'est pas un JWT (il faut trois parties séparées par des points)")
    seg = parts[1] + "=" * (-len(parts[1]) % 4)
    return json.loads(base64.urlsafe_b64decode(seg))

def expiration(tok):
    try:
        exp = decode_jwt(tok).get("exp")
        return dt.datetime.fromtimestamp(int(exp)) if exp else None
    except (ValueError, KeyError, TypeError, json.JSONDecodeError):
        return None

def cmd_zeus_coller(cfg, args):
    """Prend le jeton dans le presse-papiers et l'enregistre dans la config."""
    try:
        brut = subprocess.run(["pbpaste"], capture_output=True, text=True,
                              timeout=10).stdout
    except (OSError, subprocess.SubprocessError) as ex:
        print(f"  [KO ] presse-papiers illisible : {ex}")
        return 1
    tok = brut.strip().strip('"').strip("'").strip()
    if not tok:
        print("  [KO ] le presse-papiers est vide.")
        print("        Refais la manip dans la console du navigateur.")
        return 1
    try:
        charge = decode_jwt(tok)
    except (ValueError, json.JSONDecodeError) as ex:
        print(f"  [KO ] {ex}")
        print(f"        Le presse-papiers contient autre chose "
              f"({len(tok)} caractères).")
        return 1

    exp = expiration(tok)
    cfg.setdefault("zeus", {})["token"] = tok
    CONFIG_PATH.write_text(json.dumps(cfg, indent=2, ensure_ascii=False))
    os.chmod(CONFIG_PATH, 0o600)   # la config contient desormais un secret

    print(f"  [OK ] jeton enregistré ({len(tok)} caractères)")
    nom = charge.get("name") or charge.get("unique_name") or charge.get("sub")
    if nom:
        print(f"        compte : {nom}")
    if exp:
        reste = exp - dt.datetime.now()
        jours, heures = reste.days, reste.seconds // 3600
        etat = "EXPIRÉ" if reste.total_seconds() < 0 else f"encore {jours} j {heures} h"
        print(f"        valable jusqu'au {exp:%d/%m/%Y à %H:%M} ({etat})")
    print(f"        config protégée en lecture seule pour toi ({CONFIG_PATH})")

    if not (cfg.get("zeus") or {}).get("groupes") and not (cfg.get("zeus") or {}).get("groupe_nom"):
        print("\n  Il reste à choisir ton groupe :")
        print("     brief-matin zeus-groupes --filtre <ta classe>")
    else:
        print()
        cmd_zeus_test(cfg, args)
    return 0

def cmd_doctor(cfg, args):
    ok = True
    def chk(label, cond, extra=""):
        nonlocal ok
        print(f"  [{'OK ' if cond else 'KO '}] {label} {extra}")
        ok = ok and cond
    print("\nbrief-matin — diagnostic\n")
    chk("vault Obsidian", Path(cfg["vault"]).is_dir(), cfg["vault"])
    d, r = lire_taches(cfg)
    chk("lecture des tâches", True, f"{len(d)} devoir(s), {len(r)} révision(s)")
    z = cfg.get("zeus") or {}
    detail = "app_id" if z.get("app_id") else ("token" if z.get("token") else "aucun")
    if z.get("token"):
        exp = expiration(z["token"])
        if exp:
            reste = exp - dt.datetime.now()
            detail = (f"token EXPIRÉ le {exp:%d/%m à %H:%M} — refais zeus-coller"
                      if reste.total_seconds() < 0
                      else f"token valable encore {reste.days} j {reste.seconds//3600} h")
    chk("accès Zeus configuré", bool(z.get("app_id") or z.get("token")), detail)
    chk("groupe Zeus configuré", bool(z.get("groupes") or z.get("groupe_nom")))
    chk("application installée", Path(APP).exists(), APP)
    plist = HOME / "Library/LaunchAgents/com.briefmatin.agent.plist"
    chk("réveil du matin installé", plist.exists())
    print()
    return 0 if ok else 1

def main():
    ap = argparse.ArgumentParser(prog="brief-matin")
    sub = ap.add_subparsers(dest="cmd")
    sub.add_parser("render"); sub.add_parser("show")
    p = sub.add_parser("zeus-groupes"); p.add_argument("--filtre")
    sub.add_parser("zeus-test"); sub.add_parser("zeus-coller")
    sub.add_parser("doctor")
    args = ap.parse_args()
    cfg = load_config()
    return {"render": cmd_render, "show": cmd_show, "zeus-groupes": cmd_zeus_groupes,
            "zeus-test": cmd_zeus_test, "zeus-coller": cmd_zeus_coller,
            "doctor": cmd_doctor,
            }[args.cmd or "show"](cfg, args)

if __name__ == "__main__":
    sys.exit(main() or 0)
