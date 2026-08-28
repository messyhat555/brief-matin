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

import argparse, datetime as dt, hashlib, html, json, os, re, subprocess, sys
import unicodedata
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
    r"\b(?:pour|avant|d'ici|le|la|du|de)\s+(" + "|".join(JOURS) + r")\b", re.I)
SEMAINE_PRO = re.compile(r"semaine\s+(?:prochaine|pro)\b", re.I)
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
    m = DATE_JOUR.search(plat)
    if m:
        cible = JOURS.index(m.group(1).lower())
        return ref + dt.timedelta(days=(cible - ref.weekday()) % 7 or 7)
    if SEMAINE_PRO.search(plat):               # -> le lundi qui vient
        return ref + dt.timedelta(days=(0 - ref.weekday()) % 7 or 7)
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

TYPES_COURS = {
    "integratedlecture": "Cours intégré",
    "lecture": "Cours magistral",
    "followup": "Suivi",
    "conference": "Conférence",
    "meeting": "Réunion",
    "exam": "Examen",
    "tutorial": "TD",
    "practicalwork": "TP",
    "project": "Projet",
    "other": "",
}

def libelle_type(v):
    """Zeus renvoie des noms d'enumeration ("CourseType.Exam") : on les rend
    lisibles, sans masquer une valeur inconnue."""
    brut = str(v or "").split(".")[-1].strip()
    if not brut:
        return ""
    return TYPES_COURS.get(brut.lower(), brut)

def moment_local(s):
    """Zeus renvoie de l'UTC suffixe 'Z' : on repasse en heure locale, sinon
    tout le planning est decale."""
    if not s:
        return None
    try:
        d = dt.datetime.fromisoformat(str(s).replace("Z", "+00:00"))
    except ValueError:
        return None
    return (d.astimezone() if d.tzinfo is not None else d).replace(tzinfo=None)

def heure_locale(s):
    d = moment_local(s)
    return f"{d:%H:%M}" if d else str(s or "")[11:16]

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
            "type": libelle_type(r.get("typeName")),
            "debut": heure_locale(r.get("startDate")),
            "fin": heure_locale(r.get("endDate")),
            "debut_dt": moment_local(r.get("startDate")),
            "fin_dt": moment_local(r.get("endDate")),
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

CSS = """
:root {
  --bg:#f7f6f3; --fg:#17161a; --fg2:#4a4750; --muted:#8c8792;
  --line:#e7e4de; --card:#fffefc; --ombre:0 1px 2px rgba(20,18,25,.05);
  --accent:#6b4dff; --accent-doux:#efeaff;
  --rouge:#c0392b; --rouge-doux:#fdeceb; --orange:#a86a1e; --orange-doux:#fbf1e2;
  --sat:58%; --lum:42%;
}
@media (prefers-color-scheme: dark) {
  :root {
    --bg:#131217; --fg:#f2f1ee; --fg2:#c3c0c9; --muted:#8b8694;
    --line:#28262f; --card:#1c1b21; --ombre:none;
    --accent:#a996ff; --accent-doux:#241f3a;
    --rouge:#ff9384; --rouge-doux:#2e1c1c; --orange:#e5b167; --orange-doux:#2b2317;
    --sat:52%; --lum:70%;
  }
}
* { box-sizing:border-box; }
html { -webkit-font-smoothing:antialiased; }
body {
  margin:0; background:var(--bg); color:var(--fg);
  font:14.5px/1.55 -apple-system,BlinkMacSystemFont,"SF Pro Text",sans-serif;
  padding:26px 22px 34px;
}
.jour { font-size:12px; text-transform:uppercase; letter-spacing:.1em;
  color:var(--muted); font-weight:600; }
h1 { font-size:26px; line-height:1.15; margin:3px 0 8px; letter-spacing:-.5px; }
.apercu { color:var(--fg2); font-size:13.5px; margin-bottom:26px; }
.apercu b { color:var(--fg); font-weight:600; }
h2 { font-size:11px; text-transform:uppercase; letter-spacing:.1em;
  color:var(--muted); margin:0 0 11px; font-weight:600;
  display:flex; align-items:center; gap:8px; }
h2::after { content:""; flex:1; height:1px; background:var(--line); }
section { margin-bottom:26px; }
.creneau { display:grid; grid-template-columns:52px 1fr; gap:13px; }
.creneau + .creneau { margin-top:4px; }
.rail { text-align:right; padding-top:11px; }
.rail .h { font-size:13px; font-weight:650; font-variant-numeric:tabular-nums;
  color:var(--fg); display:block; }
.rail .f { font-size:11.5px; color:var(--muted); font-variant-numeric:tabular-nums; }
.bloc { background:var(--card); border:1px solid var(--line); border-radius:11px;
  padding:11px 13px; box-shadow:var(--ombre);
  border-left:3px solid hsl(var(--h) var(--sat) var(--lum)); }
.bloc .nom { font-weight:620; letter-spacing:-.1px; }
.bloc .meta { color:var(--muted); font-size:12.5px; margin-top:2px; }
.creneau.encours .bloc { background:var(--accent-doux); border-color:transparent;
  border-left-color:var(--accent); }
.etiq { display:block; font-size:10px; font-weight:700; letter-spacing:.07em;
  text-transform:uppercase; color:var(--accent); margin-bottom:3px; }
.battement { grid-column:2; color:var(--muted); font-size:11.5px;
  padding:5px 0 5px 13px; border-left:1px dashed var(--line); }
.groupe { margin-bottom:14px; }
.groupe-titre { font-size:11.5px; font-weight:650; margin-bottom:6px; color:var(--fg2); }
.groupe.retard .groupe-titre { color:var(--rouge); }
.groupe.bientot .groupe-titre { color:var(--orange); }
.tache { display:grid; grid-template-columns:15px 1fr; gap:10px;
  background:var(--card); border:1px solid var(--line); border-radius:10px;
  padding:10px 12px; margin-bottom:5px; box-shadow:var(--ombre); }
.groupe.retard .tache { background:var(--rouge-doux); border-color:transparent; }
.groupe.bientot .tache { background:var(--orange-doux); border-color:transparent; }
.case { width:14px; height:14px; border:1.5px solid var(--muted); border-radius:4px;
  margin-top:3px; opacity:.55; }
.txt { font-size:13.5px; color:var(--fg); }
.bas { display:flex; align-items:center; gap:7px; margin-top:5px; flex-wrap:wrap; }
.matiere { font-size:11px; color:var(--fg2); display:inline-flex; align-items:center;
  gap:5px; font-weight:550; }
.pastille { width:7px; height:7px; border-radius:50%;
  background:hsl(var(--h) var(--sat) var(--lum)); flex:0 0 auto; }
.quand { font-size:11px; color:var(--muted); font-variant-numeric:tabular-nums; }
.rev { display:flex; gap:9px; padding:7px 2px; font-size:13px; color:var(--fg2);
  border-bottom:1px solid var(--line); align-items:flex-start; }
.rev:last-child { border-bottom:none; }
.rev .pastille { margin-top:6px; }
.vide { color:var(--muted); font-size:13px; padding:11px 13px; background:var(--card);
  border:1px dashed var(--line); border-radius:10px; }
.alerte { background:var(--orange-doux); color:var(--orange); border-radius:10px;
  padding:10px 13px; font-size:12.5px; margin-bottom:20px; }
.alerte b { font-weight:650; }
.pied { margin-top:28px; padding-top:12px; border-top:1px solid var(--line);
  color:var(--muted); font-size:11px; display:flex; justify-content:space-between; }
"""

def teinte(nom):
    """Une couleur stable par matiere, pour la reconnaitre d'un coup d'oeil."""
    return int(hashlib.md5(sans_accents(nom).encode()).hexdigest()[:6], 16) % 360

def duree_humaine(minutes):
    h, m = divmod(int(minutes), 60)
    if h and m:
        return f"{h} h {m:02d}"
    return f"{h} h" if h else f"{m} min"

def salutation(maintenant):
    h = maintenant.hour
    if h < 12:
        return "Bonne matinée"
    if h < 18:
        return "Bon après-midi"
    return "Bonne soirée"

# libelle, classe css, test sur le nombre de jours restants
BUCKETS = [
    ("retard",  "En retard",     lambda d: d is not None and d < 0),
    ("bientot", "Aujourd'hui",   lambda d: d == 0),
    ("bientot", "Demain",        lambda d: d == 1),
    ("",        "Cette semaine", lambda d: d is not None and 2 <= d <= 7),
    ("",        "Plus tard",     lambda d: d is not None and d > 7),
    ("",        "Sans échéance", lambda d: d is None),
]

def render_html(cfg, planning, planning_err, devoirs, revisions):
    maintenant = dt.datetime.now()
    a = maintenant.date()
    P = ['<!doctype html><html lang="fr"><head><meta charset="utf-8">',
         "<title>Brief du matin</title><style>", CSS, "</style></head><body>"]

    prenom = (cfg.get("prenom") or "").strip()
    P.append('<div class="jour">' + e(date_fr(a)) + "</div>")
    P.append("<h1>" + e(salutation(maintenant))
             + ((" " + e(prenom)) if prenom else "") + "</h1>")

    suivant = next((c for c in planning
                    if c.get("debut_dt") and c["debut_dt"] > maintenant), None)
    encours = next((c for c in planning
                    if c.get("debut_dt") and c.get("fin_dt")
                    and c["debut_dt"] <= maintenant <= c["fin_dt"]), None)
    bouts = []
    if planning:
        bouts.append("<b>" + str(len(planning)) + "</b> cours")
    if encours:
        bouts.append(e(encours["nom"]) + " <b>en ce moment</b>")
    elif suivant:
        bouts.append("prochain à <b>" + e(suivant["debut"]) + "</b>")
    urgents = [d for d in devoirs if d["echeance"] and (d["echeance"] - a).days <= 1]
    if urgents:
        bouts.append("<b>" + str(len(urgents)) + "</b> à rendre sous 24 h")
    elif devoirs:
        bouts.append("<b>" + str(len(devoirs)) + "</b> devoirs en cours")
    P.append('<div class="apercu">'
             + (" · ".join(bouts) or "Rien de prévu aujourd’hui.") + "</div>")

    z = cfg.get("zeus") or {}
    if z.get("token"):
        exp = expiration(z["token"])
        if exp:
            reste = (exp - maintenant).total_seconds() / 3600
            if reste < 0:
                P.append('<div class="alerte"><b>Accès Zeus expiré.</b> Recopie ton '
                         "jeton, puis <code>brief-matin zeus-coller</code>.</div>")
            elif reste < 6:
                P.append('<div class="alerte"><b>Accès Zeus bientôt expiré</b> ('
                         + str(int(reste)) + " h). Pense à recopier ton jeton.</div>")

    P.append('<section><h2>La journée</h2>')
    if planning_err:
        P.append('<div class="vide">Planning indisponible — ' + e(planning_err) + "</div>")
    elif not planning:
        P.append('<div class="vide">Aucun cours prévu. Journée libre.</div>')
    else:
        precedent = None
        for c in planning:
            if precedent and precedent.get("fin_dt") and c.get("debut_dt"):
                creux = (c["debut_dt"] - precedent["fin_dt"]).total_seconds() / 60
                if creux >= 45:
                    P.append('<div class="creneau"><div></div><div class="battement">'
                             + e(duree_humaine(creux)) + " de battement</div></div>")
            classe = "creneau encours" if c is encours else "creneau"
            lieu = "en ligne" if c["en_ligne"] else c["salles"]
            meta = " · ".join(x for x in (c["type"], lieu, c["profs"]) if x)
            etiq = ""
            if c is encours:
                etiq = '<span class="etiq">en ce moment</span>'
            elif c is suivant:
                etiq = '<span class="etiq">prochain</span>'
            P.append('<div class="' + classe + '" style="--h:' + str(teinte(c["nom"]))
                     + '"><div class="rail"><span class="h">' + e(c["debut"])
                     + '</span><span class="f">' + e(c["fin"])
                     + '</span></div><div class="bloc">' + etiq
                     + '<div class="nom">' + e(c["nom"]) + "</div>"
                     + ('<div class="meta">' + e(meta) + "</div>" if meta else "")
                     + "</div></div>")
            precedent = c
    P.append("</section>")

    P.append('<section><h2>À rendre</h2>')
    if not devoirs:
        P.append('<div class="vide">Rien à rendre. Profites-en.</div>')
    else:
        restants = devoirs[: cfg.get("max_devoirs", 12)]
        for classe, titre, test in BUCKETS:
            lot = [d for d in restants
                   if test(None if d["echeance"] is None else (d["echeance"] - a).days)]
            if not lot:
                continue
            P.append('<div class="groupe ' + classe + '"><div class="groupe-titre">'
                     + e(titre) + " · " + str(len(lot)) + "</div>")
            for d in lot:
                _, lbl = urgence(d["echeance"], a)
                quand = ('<span class="quand">' + e(lbl) + "</span>") if lbl else ""
                P.append('<div class="tache" style="--h:' + str(teinte(d["matiere"]))
                         + '"><div class="case"></div><div><div class="txt">'
                         + e(d["texte"]) + '</div><div class="bas">'
                         + '<span class="matiere"><span class="pastille"></span>'
                         + e(d["matiere"]) + "</span>" + quand + "</div></div></div>")
            P.append("</div>")
    P.append("</section>")

    if revisions:
        P.append('<section><h2>À réviser</h2>')
        for r in revisions[: cfg.get("max_revisions", 6)]:
            P.append('<div class="rev" style="--h:' + str(teinte(r["matiere"]))
                     + '"><span class="pastille"></span><span>' + e(r["texte"])
                     + "</span></div>")
        P.append("</section>")

    P.append('<div class="pied"><span>' + str(len(devoirs)) + " devoir(s) · "
             + str(len(revisions)) + " à réviser</span><span>"
             + f"{maintenant:%H:%M}" + "</span></div>")
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

def cmd_zeus_groupe(cfg, args):
    """Enregistre le ou les groupes a suivre, puis verifie."""
    ids = []
    for x in args.ids:
        try:
            ids.append(int(x))
        except ValueError:
            print(f"  [KO ] '{x}' n'est pas un identifiant de groupe")
            return 1
    cfg.setdefault("zeus", {})["groupes"] = ids
    CONFIG_PATH.write_text(json.dumps(cfg, indent=2, ensure_ascii=False))
    os.chmod(CONFIG_PATH, 0o600)
    print(f"  [OK ] groupe(s) enregistre(s) : {ids}\n")
    return cmd_zeus_test(cfg, args)

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
    p = sub.add_parser("zeus-groupe"); p.add_argument("ids", nargs="+")
    sub.add_parser("doctor")
    args = ap.parse_args()
    cfg = load_config()
    return {"render": cmd_render, "show": cmd_show, "zeus-groupes": cmd_zeus_groupes,
            "zeus-test": cmd_zeus_test, "zeus-coller": cmd_zeus_coller, "zeus-groupe": cmd_zeus_groupe,
            "doctor": cmd_doctor,
            }[args.cmd or "show"](cfg, args)

if __name__ == "__main__":
    sys.exit(main() or 0)
