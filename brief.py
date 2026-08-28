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

import argparse, datetime as dt, fcntl, hashlib, html, json, os, re, subprocess, sys
import unicodedata
import urllib.error
import urllib.request
from pathlib import Path

HOME = Path.home()
BASE = HOME / ".local/share/brief-matin"
CONFIG_PATH = BASE / "config.json"
OUT_HTML = BASE / "brief.html"
TACHES_VUES = BASE / "taches_connues.json"
VERROU = BASE / "veille.lock"
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
            for no, line in enumerate(txt.splitlines(), 1):
                if line.startswith("#"):
                    section = line.lstrip("#").strip()
                    continue
                m = re.match(r"\s*[-*]\s*\[( |x|X)\]\s*(.+)", line)
                if not m or m.group(1).lower() == "x":
                    continue
                texte = re.sub(r"[📅🔺⏫🔼🔽]", "", m.group(2)).strip()
                item = {"texte": texte, "matiere": matiere, "note": f.stem,
                        "note_date": note_date, "echeance": None,
                        "fichier": str(f), "ligne": no}
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

def zeus_reservations(cfg, d0, d1, tok=None):
    """Interroge Zeus une seule fois pour une plage de dates."""
    z = cfg.get("zeus") or {}
    tok = tok or zeus_token(cfg)
    debut = dt.datetime.combine(d0, dt.time(0, 0)).isoformat()
    fin = dt.datetime.combine(d1, dt.time(23, 59)).isoformat()
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
    return data if isinstance(data, list) else []

def mise_en_forme(data, jour):
    """Ramene les reservations a une journee donnee.

    Certaines couvrent plusieurs jours (semaines de rattrapage, stages) : sans
    ce decoupage elles ecraseraient tout l'affichage."""
    jour_debut = dt.datetime.combine(jour, dt.time(0, 0))
    jour_fin = dt.datetime.combine(jour, dt.time(23, 59, 59))
    out = []
    for r in data:
        d0 = moment_local(r.get("startDate"))
        d1 = moment_local(r.get("endDate"))
        if d0 and d1 and (d1 < jour_debut or d0 > jour_fin):
            continue                      # cette seance ne touche pas ce jour
        entiere = bool(d0 and d1 and d0 <= jour_debut and d1 >= jour_fin)
        if d0:
            d0 = max(d0, jour_debut)
        if d1:
            d1 = min(d1, jour_fin)
        out.append({
            "toute_la_journee": entiere,
            "id": r.get("idReservation"),
            "nom": r.get("name") or "Cours",
            "type": libelle_type(r.get("typeName")),
            "debut": f"{d0:%H:%M}" if d0 else "",
            "fin": f"{d1:%H:%M}" if d1 else "",
            "debut_dt": d0,
            "fin_dt": d1,
            "salles": ", ".join(s.get("name", "") for s in (r.get("rooms") or []) if s),
            "profs": ", ".join(
                " ".join(filter(None, (t.get("firstName"), t.get("lastName"))))
                for t in (r.get("teachers") or []) if t),
            "en_ligne": bool(r.get("isOnline")),
        })
    out.sort(key=lambda c: c["debut"])
    return out

def zeus_planning(cfg, jour=None):
    jour = jour or dt.date.today()
    return mise_en_forme(zeus_reservations(cfg, jour, jour), jour)

def zeus_semaine(cfg, jour=None):
    """Le planning de la semaine, jour par jour (lundi -> dimanche)."""
    jour = jour or dt.date.today()
    lundi = jour - dt.timedelta(days=jour.weekday())
    jours = [lundi + dt.timedelta(days=i) for i in range(7)]
    data = zeus_reservations(cfg, jours[0], jours[-1])
    return {j: mise_en_forme(data, j) for j in jours}

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
  --bg:#f6f5f2; --fg:#15141a; --fg2:#4b4753; --muted:#8b8794;
  --line:#e6e2db; --ligne-douce:#efece6;
  --card:#fffdfa; --card2:#fbf9f5;
  --ombre:0 1px 2px rgba(24,20,32,.045), 0 6px 18px -10px rgba(24,20,32,.18);
  --ombre-haute:0 2px 4px rgba(24,20,32,.06), 0 14px 30px -14px rgba(24,20,32,.26);
  --liseré:inset 0 1px 0 rgba(255,255,255,.9);
  --accent:#5b46e0; --accent-doux:#ece8ff; --accent-vif:#7a63ff;
  --rouge:#b3392c; --rouge-doux:#fceeec; --orange:#96641c; --orange-doux:#faf1e0;
  --vert:#1d6742; --survol:#f3f0fb;
  --sat:56%; --lum:40%; --lum-bloc:44%;
  --atmo1:transparent; --atmo2:transparent;
}
@media (prefers-color-scheme: dark) {
  :root {
    --bg:#111015; --fg:#f3f2ef; --fg2:#c5c2cb; --muted:#8b8694;
    --line:#26242d; --ligne-douce:#1e1c25;
    --card:#1b1a21; --card2:#201f27;
    --ombre:0 1px 2px rgba(0,0,0,.4); --ombre-haute:0 10px 28px -12px rgba(0,0,0,.7);
    --liseré:inset 0 1px 0 rgba(255,255,255,.045);
    --accent:#a390ff; --accent-doux:#231e3c; --accent-vif:#b9a9ff;
    --rouge:#ff9384; --rouge-doux:#2c1a1a; --orange:#e3b06a; --orange-doux:#282013;
    --vert:#78d7a5; --survol:#232130;
    --sat:48%; --lum:70%; --lum-bloc:32%;
  }
}
/* l'ambiance suit l'heure : le brief du matin ne ressemble pas a celui du soir */
body[data-moment="aube"]  { --atmo1:#ffd9b0; --atmo2:#ffeede; }
body[data-moment="matin"] { --atmo1:#cfe3ff; --atmo2:#eaf3ff; }
body[data-moment="jour"]  { --atmo1:#dfe7f5; --atmo2:#f2f5fa; }
body[data-moment="soir"]  { --atmo1:#e6cdf2; --atmo2:#f6ecfb; }
body[data-moment="nuit"]  { --atmo1:#c8cbe8; --atmo2:#eceef8; }
@media (prefers-color-scheme: dark) {
  body[data-moment="aube"]  { --atmo1:#4a2f1d; --atmo2:#1a1519; }
  body[data-moment="matin"] { --atmo1:#1e3350; --atmo2:#14131a; }
  body[data-moment="jour"]  { --atmo1:#1d2a3d; --atmo2:#131218; }
  body[data-moment="soir"]  { --atmo1:#3a2450; --atmo2:#161320; }
  body[data-moment="nuit"]  { --atmo1:#1c1f3a; --atmo2:#121218; }
}

* { box-sizing:border-box; }
html { -webkit-font-smoothing:antialiased; }
body {
  margin:0; color:var(--fg); overflow-x:hidden;
  font:14.5px/1.55 -apple-system,BlinkMacSystemFont,"SF Pro Text",sans-serif;
  padding:30px 24px 40px;
  background:
    radial-gradient(120% 340px at 50% -80px, var(--atmo1) 0%, var(--atmo2) 46%,
                    transparent 78%),
    var(--bg);
  background-attachment:fixed;
}
::selection { background:var(--accent-doux); }

.jour { font-size:11px; text-transform:uppercase; letter-spacing:.14em;
  color:var(--muted); font-weight:600; }
h1 { font-family:ui-serif,"New York",Georgia,serif; font-size:31px; line-height:1.08;
  margin:6px 0 9px; letter-spacing:-.4px; font-weight:600; }
.apercu { color:var(--fg2); font-size:13.5px; margin-bottom:22px; }
.apercu b { color:var(--fg); font-weight:650; }

h2 { font-size:10.5px; text-transform:uppercase; letter-spacing:.13em;
  color:var(--muted); margin:0 0 12px; font-weight:650;
  display:flex; align-items:center; gap:9px; }
h2::after { content:""; flex:1; height:1px;
  background:linear-gradient(to right, var(--line), transparent); }
section { margin-bottom:28px; }

/* apparition en cascade */
@keyframes monte { from { opacity:0; transform:translateY(7px) } to { opacity:1; transform:none } }
.vue.active > section, .vue.active > .bloc-sem, .focus { animation:monte .4s both; }
.vue.active > section:nth-child(2) { animation-delay:.05s; }
.vue.active > section:nth-child(3) { animation-delay:.1s; }

/* ---- barre de journee ---- */
.arc { position:relative; height:38px; margin:0 0 24px; }
.arc .piste { position:absolute; inset:15px 0 auto; height:7px; border-radius:4px;
  background:var(--ligne-douce); box-shadow:var(--liseré); }
.arc .seg { position:absolute; top:15px; height:7px; border-radius:4px;
  background:linear-gradient(to bottom, hsl(var(--h) var(--sat) calc(var(--lum) + 8%)),
                             hsl(var(--h) var(--sat) var(--lum))); }
.arc .curseur { position:absolute; top:7px; width:2px; height:23px; border-radius:2px;
  background:var(--accent); transition:left .8s cubic-bezier(.4,0,.2,1); }
.arc .curseur::after { content:""; position:absolute; top:-4px; left:-3.5px; width:9px;
  height:9px; border-radius:50%; background:var(--accent);
  box-shadow:0 0 0 4px color-mix(in srgb, var(--accent) 18%, transparent); }
.arc .borne { position:absolute; top:26px; font-size:10px; color:var(--muted);
  font-variant-numeric:tabular-nums; letter-spacing:.02em; }
.arc .borne.fin { right:0; }

/* ---- onglets ---- */
.onglets { display:flex; gap:3px; margin:0 0 22px; background:var(--ligne-douce);
  padding:3px; border-radius:11px; box-shadow:var(--liseré); }
.onglet { flex:1; text-align:center; font-size:12.5px; font-weight:600; padding:6px 0;
  border-radius:8px; color:var(--fg2); cursor:pointer; user-select:none;
  transition:background .2s, color .2s, box-shadow .2s; }
.onglet:hover { color:var(--fg); }
.onglet.actif { background:var(--card); color:var(--fg); box-shadow:var(--ombre); }
.onglet .cle { font-size:9px; opacity:.45; margin-left:4px; }
.vue { display:none; }
.vue.active { display:block; }

/* ---- cartes ---- */
.bloc, .tache, .vide, .bande, .jour-col, .bouton {
  box-shadow:var(--ombre), var(--liseré);
}
.creneau { display:grid; grid-template-columns:54px 1fr; gap:14px; }
.creneau + .creneau { margin-top:5px; }
.rail { text-align:right; padding-top:12px; }
.rail .h { font-size:13px; font-weight:660; font-variant-numeric:tabular-nums;
  color:var(--fg); display:block; letter-spacing:-.1px; }
.rail .f { font-size:11.5px; color:var(--muted); font-variant-numeric:tabular-nums; }
.bloc { background:linear-gradient(to bottom, var(--card), var(--card2));
  border:1px solid var(--line); border-radius:13px; padding:12px 14px;
  border-left:3px solid hsl(var(--h) var(--sat) var(--lum));
  transition:transform .18s, box-shadow .18s; }
.creneau:hover .bloc { transform:translateY(-1px); box-shadow:var(--ombre-haute), var(--liseré); }
.bloc .nom { font-weight:640; letter-spacing:-.15px; font-size:14.5px; }
.bloc .meta { color:var(--muted); font-size:12.5px; margin-top:3px; }
.creneau.encours .bloc { background:linear-gradient(to bottom,
  color-mix(in srgb, var(--accent-doux) 75%, var(--card)), var(--accent-doux));
  border-color:transparent; border-left-color:var(--accent); }
.etiq { display:block; font-size:9.5px; font-weight:750; letter-spacing:.1em;
  text-transform:uppercase; color:var(--accent); margin-bottom:4px; }
.battement { grid-column:2; color:var(--muted); font-size:11.5px;
  padding:6px 0 6px 14px; border-left:1px dashed var(--line); }

/* ---- taches ---- */
.groupe { margin-bottom:16px; }
.groupe-titre { font-size:11.5px; font-weight:660; margin-bottom:7px; color:var(--fg2);
  letter-spacing:.01em; }
.groupe.retard .groupe-titre { color:var(--rouge); }
.groupe.bientot .groupe-titre { color:var(--orange); }
.tache { display:grid; grid-template-columns:16px 1fr; gap:11px;
  background:linear-gradient(to bottom, var(--card), var(--card2));
  border:1px solid var(--line); border-radius:12px; padding:11px 13px; margin-bottom:6px;
  cursor:pointer; transition:opacity .28s, transform .22s, box-shadow .18s, background .18s; }
.tache:hover { background:var(--survol); transform:translateY(-1px);
  box-shadow:var(--ombre-haute), var(--liseré); }
.tache:active { transform:scale(.99); }
.groupe.retard .tache { background:var(--rouge-doux); border-color:transparent; }
.groupe.bientot .tache { background:var(--orange-doux); border-color:transparent; }
.case { width:15px; height:15px; border:1.6px solid var(--muted); border-radius:5px;
  margin-top:3px; opacity:.5; position:relative;
  transition:background .18s, border-color .18s, opacity .18s; }
.tache:hover .case { border-color:var(--accent); opacity:1; }
.tache.faite { opacity:0; transform:translateX(16px); pointer-events:none; }
.tache.faite .txt { text-decoration:line-through; }
.tache.faite .case { background:var(--accent); border-color:var(--accent); }
.tache.faite .case::after { content:"✓"; position:absolute; inset:0; color:#fff;
  font-size:10px; line-height:12px; text-align:center; font-weight:800; }
.txt { font-size:13.5px; color:var(--fg); }
.bas { display:flex; align-items:center; gap:8px; margin-top:6px; flex-wrap:wrap; }
.matiere { font-size:10.5px; color:var(--fg2); display:inline-flex; align-items:center;
  gap:5px; font-weight:600; letter-spacing:.04em; text-transform:uppercase; }
.pastille { width:7px; height:7px; border-radius:50%; flex:0 0 auto;
  background:hsl(var(--h) var(--sat) var(--lum));
  box-shadow:0 0 0 2.5px color-mix(in srgb, hsl(var(--h) var(--sat) var(--lum)) 16%, transparent); }
.quand { font-size:11px; color:var(--muted); font-variant-numeric:tabular-nums; }

/* ---- revisions ---- */
.rev { display:flex; gap:10px; padding:8px 2px; font-size:13px; color:var(--fg2);
  border-bottom:1px solid var(--ligne-douce); align-items:flex-start; }
.rev:last-child { border-bottom:none; }
.rev .pastille { margin-top:6px; }

.vide { color:var(--muted); font-size:13px; padding:13px 15px; background:var(--card);
  border:1px dashed var(--line); border-radius:12px; box-shadow:none; }
.alerte { background:var(--orange-doux); color:var(--orange); border-radius:12px;
  padding:11px 14px; font-size:12.5px; margin-bottom:20px;
  border:1px solid color-mix(in srgb, var(--orange) 22%, transparent); }
.alerte b { font-weight:670; }
.zeus-actions { margin-top:11px; }
.bouton.petit { font-size:12px; padding:7px 14px; display:inline-block; }
.sem-vide .zeus-actions, .vide .zeus-actions { margin-top:13px; }

/* ---- vue semaine ---- */
.sem-tete { display:flex; align-items:center; justify-content:space-between;
  margin-bottom:13px; }
.sem-tete .titre { font-size:13.5px; font-weight:660; letter-spacing:-.1px; }
.fleche { cursor:pointer; padding:2px 10px; border-radius:8px; color:var(--fg2);
  background:var(--card); border:1px solid var(--line); font-size:13px;
  user-select:none; transition:color .15s, border-color .15s, transform .12s; }
.fleche:hover { color:var(--accent); border-color:var(--accent); }
.fleche:active { transform:scale(.94); }
.bande { display:flex; gap:6px; align-items:center;
  background:linear-gradient(to bottom, var(--card), var(--card2));
  border:1px solid var(--line);
  border-left:3px solid hsl(var(--h) var(--sat) var(--lum));
  border-radius:9px; padding:6px 10px; font-size:11.5px; margin-bottom:8px; }
.bande .jours { color:var(--muted); margin-left:auto; font-size:10.5px; }
.grille { display:grid; grid-template-columns:28px repeat(var(--nj), minmax(0,1fr));
  gap:4px; }
.axe { position:relative; }
.axe span { position:absolute; right:2px; font-size:9px; color:var(--muted);
  transform:translateY(-4px); font-variant-numeric:tabular-nums; }
.jour-col { position:relative; background:var(--card); border:1px solid var(--line);
  border-radius:9px; overflow:hidden;
  background-image:repeating-linear-gradient(to bottom, transparent 0,
    transparent calc(var(--pas) - 1px), var(--ligne-douce) calc(var(--pas) - 1px),
    var(--ligne-douce) var(--pas)); }
.jour-col.aujourdhui { border-color:color-mix(in srgb, var(--accent) 45%, transparent);
  box-shadow:0 0 0 1px color-mix(in srgb, var(--accent) 22%, transparent), var(--ombre); }
.jour-tete { text-align:center; font-size:9.5px; font-weight:680; color:var(--fg2);
  padding:4px 0 5px; text-transform:uppercase; letter-spacing:.07em; }
.jour-tete.aujourdhui { color:var(--accent); }
.jour-tete small { display:block; font-weight:600; color:var(--muted); font-size:11px;
  letter-spacing:0; margin-top:1px; }
.seance { position:absolute; left:3px; right:3px; border-radius:7px; padding:4px 5px;
  font-size:9px; line-height:1.25; overflow:hidden; color:#fff;
  overflow-wrap:anywhere; hyphens:auto;
  background:linear-gradient(155deg, hsl(var(--h) var(--sat) calc(var(--lum-bloc) + 7%)),
                             hsl(var(--h) var(--sat) var(--lum-bloc)));
  box-shadow:0 1px 3px rgba(20,16,30,.22); }
@media (prefers-color-scheme: dark) { .seance { color:#eeecf5; } }
.seance b { display:block; font-weight:750; font-size:8.5px; opacity:.8;
  font-variant-numeric:tabular-nums; }
.ligne-now { position:absolute; left:0; right:0; height:1.5px; background:var(--accent);
  z-index:3; box-shadow:0 0 7px color-mix(in srgb, var(--accent) 55%, transparent); }
.ligne-now::before { content:""; position:absolute; left:0; top:-2.5px; width:6px;
  height:6px; border-radius:50%; background:var(--accent); }
.sem-vide { color:var(--muted); font-size:12.5px; padding:26px 12px; text-align:center; }

/* ---- vue focus ---- */
.focus { text-align:center; padding:26px 6px 8px; }
.focus .cadre { font-size:10.5px; text-transform:uppercase; letter-spacing:.14em;
  color:var(--accent); font-weight:750; margin-bottom:18px; }
.focus .chose { font-family:ui-serif,"New York",Georgia,serif; font-size:25px;
  line-height:1.28; font-weight:600; letter-spacing:-.35px; margin:0 auto 16px;
  max-width:20em; }
.focus .contexte { color:var(--fg2); font-size:11px; margin-bottom:5px;
  text-transform:uppercase; letter-spacing:.07em; font-weight:600; }
.focus .quand-gros { color:var(--muted); font-size:12.5px; margin-bottom:26px;
  font-variant-numeric:tabular-nums; }
.actions { display:flex; gap:9px; justify-content:center; flex-wrap:wrap; }
.bouton { font-size:13px; font-weight:620; padding:10px 19px; border-radius:11px;
  cursor:pointer; user-select:none; border:1px solid var(--line);
  background:linear-gradient(to bottom, var(--card), var(--card2)); color:var(--fg);
  transition:transform .14s, filter .16s, background .16s; }
.bouton:hover { background:var(--survol); }
.bouton:active { transform:scale(.96); }
.bouton.fort { border-color:transparent; color:#fff;
  background:linear-gradient(to bottom, var(--accent-vif), var(--accent));
  box-shadow:0 2px 10px -3px color-mix(in srgb, var(--accent) 60%, transparent); }
.bouton.fort:hover { filter:brightness(1.07); }
.apres { margin-top:30px; padding-top:16px; border-top:1px solid var(--ligne-douce);
  text-align:left; }
.apres-titre { font-size:10px; text-transform:uppercase; letter-spacing:.13em;
  color:var(--muted); font-weight:650; margin-bottom:9px; }
.apres-item { font-size:12.5px; color:var(--fg2); padding:4px 0; display:flex;
  gap:9px; align-items:flex-start; }
.apres-item .pastille { margin-top:5px; }
.focus-vide { color:var(--muted); font-size:15px; padding:52px 12px; text-align:center;
  font-family:ui-serif,"New York",Georgia,serif; }
.pied { margin-top:30px; padding-top:13px; border-top:1px solid var(--ligne-douce);
  color:var(--muted); font-size:10.5px; display:flex; justify-content:space-between;
  letter-spacing:.03em; }
"""

JS = r"""<script>
(() => {
  const pont = window.webkit?.messageHandlers?.brief;
  const donnees = JSON.parse(document.getElementById("donnees").textContent);

  /* --- cocher un devoir : ecrit dans la note Obsidian ------------------ */
  document.querySelectorAll(".tache[data-fichier]").forEach(el => {
    el.addEventListener("click", () => {
      if (el.classList.contains("faite")) return;
      if (!pont) { el.animate([{transform:"translateX(0)"},{transform:"translateX(-5px)"},
        {transform:"translateX(5px)"},{transform:"translateX(0)"}], {duration:220}); return; }
      el.classList.add("faite");
      pont.postMessage({action:"cocher", fichier:el.dataset.fichier,
                        ligne:parseInt(el.dataset.ligne, 10)});
      setTimeout(() => {
        const g = el.closest(".groupe");
        el.remove();
        if (g && !g.querySelector(".tache")) g.remove();
        majCompteurs();
      }, 260);
    });
  });

  function majCompteurs() {
    const n = document.querySelectorAll(".tache[data-fichier]").length;
    document.querySelectorAll(".groupe").forEach(g => {
      const t = g.querySelector(".groupe-titre");
      const c = g.querySelectorAll(".tache").length;
      if (t) t.textContent = t.textContent.replace(/ · \d+$/, " · " + c);
    });
    const p = document.querySelector(".pied span");
    if (p) p.textContent = n + " devoir(s) · " + donnees.revisions + " à réviser";
    const sec = document.querySelector("#rendre .vide-apres");
    if (n === 0 && sec) sec.hidden = false;
  }

  /* --- le temps qui passe --------------------------------------------- */
  const cours = donnees.cours.map(c => ({...c, d:new Date(c.debut), f:new Date(c.fin)}));

  function humain(ms) {
    const min = Math.round(ms / 60000);
    if (min < 60) return "dans " + min + " min";
    const h = Math.floor(min / 60), r = min % 60;
    return "dans " + h + " h" + (r ? " " + String(r).padStart(2,"0") : "");
  }

  function tic() {
    const now = new Date();
    let encours = null, suivant = null;
    for (const c of cours) {
      if (c.d <= now && now <= c.f) encours = c;
      else if (c.d > now && (!suivant || c.d < suivant.d)) suivant = c;
    }
    document.querySelectorAll(".creneau[data-id]").forEach(el => {
      const id = el.dataset.id;
      const est = encours && String(encours.id) === id;
      el.classList.toggle("encours", !!est);
      const et = el.querySelector(".etiq");
      if (et) {
        if (est) { et.textContent = "en ce moment"; et.hidden = false; }
        else if (suivant && String(suivant.id) === id) {
          et.textContent = "prochain · " + humain(suivant.d - now); et.hidden = false;
        } else et.hidden = true;
      }
    });
    const cur = document.querySelector(".arc .curseur");
    if (cur && donnees.arc) {
      const t0 = new Date(donnees.arc.debut), t1 = new Date(donnees.arc.fin);
      const r = Math.min(1, Math.max(0, (now - t0) / (t1 - t0)));
      cur.style.left = "calc(" + (r * 100).toFixed(2) + "% - 1px)";
      cur.hidden = false;
    }
  }

  /* --- navigation entre les vues -------------------------------------- */
  function montrer(nom) {
    document.querySelectorAll(".vue").forEach(v => v.classList.toggle("active", v.id === "v-" + nom));
    document.querySelectorAll(".onglet").forEach(o => o.classList.toggle("actif", o.dataset.vue === nom));
    if (nom === "focus") dessineFocus();
  }
  document.querySelectorAll(".onglet").forEach(o =>
    o.addEventListener("click", () => montrer(o.dataset.vue)));
  document.addEventListener("keydown", ev => {
    if (ev.key === "1") montrer("jour");
    else if (ev.key === "2") montrer("semaine");
    else if (ev.key === "3") montrer("focus");
  });

  /* --- semaine precedente / suivante ----------------------------------- */
  let sem = 0;
  document.querySelectorAll(".fleche").forEach(f =>
    f.addEventListener("click", () => {
      const n = donnees.nb_semaines;
      sem = Math.min(Math.max(sem + parseInt(f.dataset.pas, 10), 0), n - 1);
      document.querySelectorAll(".bloc-sem").forEach(b =>
        b.hidden = parseInt(b.dataset.sem, 10) !== sem);
    }));

  /* --- focus : une seule chose ----------------------------------------- */
  let iFocus = 0;
  const faits = new Set();

  function dessineFocus() {
    const restants = donnees.focus.filter((_, i) => !faits.has(i));
    const cible = document.getElementById("focus-ici");
    if (!restants.length) {
      cible.innerHTML = '<div class="focus-vide">Rien d\'urgent.<br>Tu peux souffler.</div>';
      return;
    }
    iFocus = Math.min(iFocus, restants.length - 1);
    const c = restants[iFocus];
    const idx = donnees.focus.indexOf(c);
    let quand = c.quand || "";
    if (c.genre === "cours" && c.debut) {
      const d = new Date(c.debut), f = new Date(c.fin), now = new Date();
      quand = (d <= now && now <= f) ? "jusqu'à " + f.toTimeString().slice(0, 5)
                                     : "à " + d.toTimeString().slice(0, 5) + " · " + humain(d - now);
    }
    const suite = restants.slice(iFocus + 1, iFocus + 4).map(x =>
      '<div class="apres-item"><span class="pastille" style="--h:'
      + (x.teinte ?? 250) + '"></span>' + echapper(x.titre) + "</div>").join("");

    cible.innerHTML =
      '<div class="focus"><div class="cadre">' + echapper(c.cadre) + "</div>"
      + '<div class="chose">' + echapper(c.titre) + "</div>"
      + (c.contexte ? '<div class="contexte">' + echapper(c.contexte) + "</div>" : "")
      + (quand ? '<div class="quand-gros">' + echapper(quand) + "</div>" : "")
      + '<div class="actions">'
      + (c.genre === "devoir" ? '<div class="bouton fort" id="f-fait">C\'est fait</div>' : "")
      + (restants.length > 1 ? '<div class="bouton" id="f-autre">Autre chose</div>' : "")
      + "</div>"
      + (suite ? '<div class="apres"><div class="apres-titre">Et ensuite</div>' + suite + "</div>" : "")
      + "</div>";

    const autre = document.getElementById("f-autre");
    if (autre) autre.addEventListener("click", () => {
      iFocus = (iFocus + 1) % restants.length; dessineFocus();
    });
    const fait = document.getElementById("f-fait");
    if (fait) fait.addEventListener("click", () => {
      if (pont && c.fichier) {
        pont.postMessage({action: "cocher", fichier: c.fichier, ligne: c.ligne});
        const jumeau = document.querySelector('.tache[data-fichier="' + CSS.escape(c.fichier)
                                              + '"][data-ligne="' + c.ligne + '"]');
        if (jumeau) { jumeau.remove(); majCompteurs(); }
      }
      faits.add(idx); iFocus = 0; dessineFocus();
    });
  }

  function echapper(t) {
    const d = document.createElement("div"); d.textContent = t ?? ""; return d.innerHTML;
  }

  tic();
  setInterval(tic, 20000);
})();
</script>"""

# huit teintes choisies pour rester lisibles et s'accorder entre elles ; une
# teinte tiree au hasard sur 360 donnait des voisinages criards
TEINTES = [222, 168, 38, 340, 272, 146, 198, 14]

BOUTON_ZEUS = ('<div class="zeus-actions">'
               '<span class="bouton fort petit" data-connecter>Se connecter à Zeus</span>'
               "</div>")

def teinte(nom):
    """Une couleur stable par matiere, pour la reconnaitre d'un coup d'oeil."""
    h = int(hashlib.md5(sans_accents(nom).encode()).hexdigest()[:6], 16)
    return TEINTES[h % len(TEINTES)]

def duree_humaine(minutes):
    h, m = divmod(int(minutes), 60)
    if h and m:
        return f"{h} h {m:02d}"
    return f"{h} h" if h else f"{m} min"

def moment_du_jour(maintenant):
    h = maintenant.hour
    if h < 8:
        return "aube"
    if h < 12:
        return "matin"
    if h < 18:
        return "jour"
    if h < 22:
        return "soir"
    return "nuit"

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

def render_grille(jours_map, maintenant):
    """Une semaine en grille : colonnes = jours, axe vertical = heures."""
    bandes, timees = [], {}
    for j, cs in jours_map.items():
        timees[j] = [c for c in cs if not c.get("toute_la_journee")]
        for c in cs:
            if c.get("toute_la_journee"):
                bandes.append((j, c))

    jours = sorted(jours_map)
    # samedi et dimanche n'apparaissent que s'il s'y passe quelque chose
    visibles = [j for j in jours
                if j.weekday() < 5 or timees.get(j) or any(b[0] == j for b in bandes)]
    if not visibles:
        return '<div class="sem-vide">Aucun cours cette semaine.</div>'

    heures = [c["debut_dt"].hour + c["debut_dt"].minute / 60
              for j in visibles for c in timees.get(j, [])]
    fins = [c["fin_dt"].hour + c["fin_dt"].minute / 60
            for j in visibles for c in timees.get(j, [])]
    h0 = int(min(heures)) if heures else 8
    h1 = int(fins and -(-max(fins) // 1) or 19)
    h0, h1 = max(6, min(h0, 9)), min(23, max(h1, 18))
    span = max(h1 - h0, 1)
    hauteur = int(span * 30)

    P = []
    for j, c in bandes:
        P.append('<div class="bande" style="--h:' + str(teinte(c["nom"])) + '"><span>'
                 + e(c["nom"]) + '</span><span class="jours">'
                 + JOURS[j.weekday()][:3] + " · toute la journée</span></div>")

    P.append('<div class="grille" style="--nj:' + str(len(visibles)) + '">')
    P.append('<div></div>')
    for j in visibles:
        cl = " aujourdhui" if j == maintenant.date() else ""
        P.append('<div class="jour-tete' + cl + '">' + JOURS[j.weekday()][:3]
                 + "<small>" + str(j.day) + "</small></div>")

    axe = ['<div class="axe" style="height:' + str(hauteur) + 'px">']
    for h in range(h0, h1 + 1):
        top = (h - h0) / span * 100
        axe.append('<span style="top:' + f"{top:.1f}" + '%">' + f"{h:02d}" + "</span>")
    axe.append("</div>")
    P.append("".join(axe))

    for j in visibles:
        cl = " aujourdhui" if j == maintenant.date() else ""
        col = ['<div class="jour-col' + cl + '" style="height:' + str(hauteur)
               + "px;--pas:" + f"{100 / span:.4f}" + '%">']
        for c in timees.get(j, []):
            d = c["debut_dt"].hour + c["debut_dt"].minute / 60
            f = c["fin_dt"].hour + c["fin_dt"].minute / 60
            top = max((d - h0) / span * 100, 0)
            haut = max((f - d) / span * 100, 4.5)
            col.append('<div class="seance" style="--h:' + str(teinte(c["nom"]))
                       + ";top:" + f"{top:.2f}" + "%;height:" + f"{haut:.2f}" + '%"'
                       + ' title="' + e(c["nom"]) + " · " + e(c["debut"]) + "–"
                       + e(c["fin"]) + (" · " + e(c["salles"]) if c["salles"] else "")
                       + '"><b>' + e(c["debut"]) + "</b>" + e(c["nom"]) + "</div>")
        if j == maintenant.date():
            now = maintenant.hour + maintenant.minute / 60
            if h0 <= now <= h1:
                col.append('<div class="ligne-now" style="top:'
                           + f"{(now - h0) / span * 100:.2f}" + '%"></div>')
        col.append("</div>")
        P.append("".join(col))
    P.append("</div>")
    return "".join(P)


def candidats_focus(planning, devoirs, revisions, maintenant):
    """La liste ordonnee de ce qu'il y a de plus important a faire, maintenant.

    Le premier element est "la chose" ; les suivants servent au bouton
    "autre chose" et a l'apercu du dessous."""
    a = maintenant.date()
    out = []
    for c in planning:
        d0, d1 = c.get("debut_dt"), c.get("fin_dt")
        if not (d0 and d1) or d1 < maintenant:
            continue
        imminent = (d0 - maintenant).total_seconds() / 60
        encours = d0 <= maintenant <= d1
        lieu = "en ligne" if c["en_ligne"] else c["salles"]
        out.append({
            "rang": 0 if encours else (2 if imminent <= 45 else 4),
            "genre": "cours",
            "cadre": "Tu es en cours" if encours else "Prochain cours",
            "titre": c["nom"],
            "contexte": " · ".join(x for x in (c["type"], lieu, c["profs"]) if x),
            "debut": d0.isoformat(), "fin": d1.isoformat(),
            "quand": ("jusqu'à " + c["fin"]) if encours else ("à " + c["debut"]),
        })
    for d in devoirs:
        jours = None if d["echeance"] is None else (d["echeance"] - a).days
        rang = (1 if (jours is not None and jours < 0) else
                3 if jours == 0 else 5 if jours == 1 else
                6 if (jours is not None and jours <= 7) else 8)
        _, lbl = urgence(d["echeance"], a)
        out.append({
            "rang": rang, "genre": "devoir",
            "cadre": "À rendre" + (" — en retard" if rang == 1 else ""),
            "titre": d["texte"], "contexte": d["matiere"], "quand": lbl,
            "fichier": d.get("fichier"), "ligne": d.get("ligne"),
            "teinte": teinte(d["matiere"]),
        })
    for r in revisions[:4]:
        out.append({"rang": 7, "genre": "revision", "cadre": "À réviser",
                    "titre": r["texte"], "contexte": r["matiere"], "quand": "",
                    "teinte": teinte(r["matiere"])})
    out.sort(key=lambda x: x["rang"])
    return out


def render_html(cfg, planning, planning_err, devoirs, revisions, semaines=None):
    maintenant = dt.datetime.now()
    a = maintenant.date()
    P = ['<!doctype html><html lang="fr"><head><meta charset="utf-8">',
         "<title>Brief du matin</title><style>", CSS,
         '</style></head><body data-moment="' + moment_du_jour(maintenant) + '">']

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

    if planning:
        bornes = [c["debut_dt"] for c in planning if c.get("debut_dt")] \
               + [c["fin_dt"] for c in planning if c.get("fin_dt")]
        if bornes:
            t0 = min(bornes).replace(minute=0)
            t1 = max(bornes)
            # la fourchette minimale se cale sur le jour affiche, pas sur aujourd'hui
            jour_ref = min(bornes).date()
            t0 = min(t0, dt.datetime.combine(jour_ref, dt.time(8, 0)))
            t1 = max(t1, dt.datetime.combine(jour_ref, dt.time(19, 0)))
            span = max((t1 - t0).total_seconds(), 1)
            segs = []
            for c in planning:
                if not (c.get("debut_dt") and c.get("fin_dt")):
                    continue
                g = (c["debut_dt"] - t0).total_seconds() / span * 100
                l = max((c["fin_dt"] - c["debut_dt"]).total_seconds() / span * 100, 1.2)
                segs.append('<div class="seg" style="--h:' + str(teinte(c["nom"]))
                            + ";left:" + f"{g:.2f}" + "%;width:" + f"{l:.2f}" + '%"></div>')
            P.append('<div class="arc"><div class="piste"></div>' + "".join(segs)
                     + '<div class="curseur" hidden></div>'
                     + '<div class="borne">' + f"{t0:%H:%M}" + "</div>"
                     + '<div class="borne fin">' + f"{t1:%H:%M}" + "</div></div>")
            cfg["_arc"] = {"debut": t0.isoformat(), "fin": t1.isoformat()}

    z = cfg.get("zeus") or {}
    if z.get("token"):
        exp = expiration(z["token"])
        if exp:
            reste = (exp - maintenant).total_seconds() / 3600
            if reste < 0:
                P.append('<div class="alerte"><b>Accès Zeus expiré.</b> '
                         "Reconnecte-toi pour retrouver ton planning."
                         + BOUTON_ZEUS + "</div>")
            elif reste < 6:
                P.append('<div class="alerte"><b>Accès Zeus bientôt expiré</b> ('
                         + str(int(reste)) + " h)." + BOUTON_ZEUS + "</div>")

    P.append('<div class="onglets">'
             '<div class="onglet actif" data-vue="jour">Jour<span class="cle">1</span></div>'
             '<div class="onglet" data-vue="semaine">Semaine<span class="cle">2</span></div>'
             '<div class="onglet" data-vue="focus">Focus<span class="cle">3</span></div>'
             "</div>")
    P.append('<div class="vue active" id="v-jour">')
    P.append('<section><h2>La journée</h2>')
    if planning_err:
        P.append('<div class="vide">Planning indisponible — ' + e(planning_err)
                 + BOUTON_ZEUS + "</div>")
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
            morceaux = [c["type"], lieu, c["profs"]]
            if c.get("toute_la_journee"):
                morceaux.insert(0, "toute la journée")
            meta = " · ".join(x for x in morceaux if x)
            if c is encours:
                etiq = '<span class="etiq">en ce moment</span>'
            elif c is suivant:
                etiq = '<span class="etiq">prochain</span>'
            else:
                etiq = '<span class="etiq" hidden></span>'
            P.append('<div class="' + classe + '" data-id="'
                     + str(c.get("id", id(c))) + '" style="--h:' + str(teinte(c["nom"]))
                     + '"><div class="rail">'
                     + ('<span class="h">jour</span>' if c.get("toute_la_journee")
                        else '<span class="h">' + e(c["debut"])
                             + '</span><span class="f">' + e(c["fin"]) + "</span>")
                     + '</div><div class="bloc">' + etiq
                     + '<div class="nom">' + e(c["nom"]) + "</div>"
                     + ('<div class="meta">' + e(meta) + "</div>" if meta else "")
                     + "</div></div>")
            precedent = c
    P.append("</section>")

    P.append('<section id="rendre"><h2>À rendre</h2>')
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
                P.append('<div class="tache" data-fichier="' + e(d.get("fichier", ""))
                         + '" data-ligne="' + str(d.get("ligne", 0))
                         + '" style="--h:' + str(teinte(d["matiere"]))
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
    P.append("</div>")   # fin de la vue Jour

    P.append('<div class="vue" id="v-semaine">')
    sems = semaines or {}
    if not sems:
        P.append('<div class="sem-vide">Semaine indisponible — accès Zeus non '
                 "configuré." + BOUTON_ZEUS + "</div>")
    for idx, (lundi, jours_map) in enumerate(sorted(sems.items())):
        P.append('<div class="bloc-sem" data-sem="' + str(idx) + '"'
                 + ("" if idx == 0 else " hidden") + '>')
        fin = lundi + dt.timedelta(days=6)
        titre = ("Semaine du " + str(lundi.day) + " " + MOIS[lundi.month - 1]
                 if lundi.month == fin.month else
                 "Du " + str(lundi.day) + " " + MOIS[lundi.month - 1] + " au "
                 + str(fin.day) + " " + MOIS[fin.month - 1])
        P.append('<div class="sem-tete"><span class="fleche" data-pas="-1">‹</span>'
                 '<span class="titre">' + e(titre) + "</span>"
                 '<span class="fleche" data-pas="1">›</span></div>')
        P.append(render_grille(jours_map, maintenant))
        P.append("</div>")
    P.append("</div>")

    P.append('<div class="vue" id="v-focus"><div id="focus-ici"></div></div>')

    donnees = {
        "revisions": len(revisions),
        "focus": candidats_focus(planning, devoirs, revisions, maintenant),
        "nb_semaines": len(sems),
        "arc": cfg.pop("_arc", None),
        "cours": [{"id": c.get("id", i), "nom": c["nom"],
                   "debut": c["debut_dt"].isoformat() if c.get("debut_dt") else None,
                   "fin": c["fin_dt"].isoformat() if c.get("fin_dt") else None}
                  for i, c in enumerate(planning) if c.get("debut_dt")],
    }
    P.append('<script id="donnees" type="application/json">'
             + json.dumps(donnees, ensure_ascii=False).replace("</", "<\\/")
             + "</script>")
    P.append(JS)
    P.append("</body></html>")
    return "\n".join(P)

# --------------------------------------------------------------------------
# commandes
# --------------------------------------------------------------------------

def build(cfg):
    devoirs, revisions = lire_taches(cfg)
    planning, semaines, err = [], {}, None
    try:
        # un seul appel couvre le jour, la semaine en cours et la suivante
        auj = dt.date.today()
        lundi = auj - dt.timedelta(days=auj.weekday())
        data = zeus_reservations(cfg, lundi, lundi + dt.timedelta(days=13))
        planning = mise_en_forme(data, auj)
        for w in (0, 1):
            l = lundi + dt.timedelta(days=7 * w)
            semaines[l] = {l + dt.timedelta(days=i): mise_en_forme(
                data, l + dt.timedelta(days=i)) for i in range(7)}
    except ZeusError as ex:
        err = str(ex)
        log(f"Zeus: {ex}", "WARN")
    BASE.mkdir(parents=True, exist_ok=True)
    OUT_HTML.write_text(render_html(cfg, planning, err, devoirs, revisions, semaines),
                        encoding="utf-8")
    return planning, err, devoirs, revisions

def empreinte(t):
    """Identifie une tache independamment de sa position dans le fichier."""
    return hashlib.sha256(
        (str(t.get("fichier", "")) + "|" + t["texte"]).encode()).hexdigest()[:16]

def notifier(titre, sous_titre, corps):
    """Affiche une notification macOS. On passe par l'app pour qu'elle soit
    attribuee a Brief Matin ; sinon on retombe sur osascript."""
    binaire = Path(APP) / "Contents/MacOS/BriefMatin"
    if binaire.exists():
        try:
            r = subprocess.run([str(binaire), "--notifier", titre, sous_titre, corps],
                               capture_output=True, text=True, timeout=15)
            if r.returncode == 0 and "ERREUR" not in r.stdout:
                return True
            log(f"notification via l'app impossible : {r.stdout.strip() or r.stderr.strip()}",
                "WARN")
        except (OSError, subprocess.SubprocessError) as ex:
            log(f"notification via l'app impossible : {ex}", "WARN")
    def ap(x):
        """Echappe pour une chaine AppleScript."""
        return str(x or "").replace("\\", "\\\\").replace('"', '\\"')
    script = (f'display notification "{ap(corps)}" with title "{ap(titre)}"'
              + (f' subtitle "{ap(sous_titre)}"' if sous_titre else ""))
    try:
        subprocess.run(["osascript", "-e", script], capture_output=True, timeout=15)
        return True
    except (OSError, subprocess.SubprocessError):
        return False

def cmd_veille(cfg, args):
    """Repere les devoirs apparus depuis le dernier passage et les annonce.

    L'agent se declenche a l'ecriture d'une note *et* toutes les trois minutes :
    sans verrou, deux passages simultanes se marchent dessus et annoncent deux
    fois le meme devoir."""
    BASE.mkdir(parents=True, exist_ok=True)
    verrou = open(VERROU, "w")
    try:
        fcntl.flock(verrou, fcntl.LOCK_EX | fcntl.LOCK_NB)
    except OSError:
        print("  un autre passage est en cours")
        return 0
    if args.rejouer:
        TACHES_VUES.unlink(missing_ok=True)
    devoirs, _ = lire_taches(cfg)
    actuels = {empreinte(d): d for d in devoirs}

    # On memorise TOUT ce qu'on a deja vu, pas seulement ce qui existe
    # aujourd'hui : si une note est lue pendant sa sauvegarde, ou si un devoir
    # est coche puis decoche, on ne veut pas le re-annoncer.
    vues = {}
    premier = True
    try:
        brut = json.loads(TACHES_VUES.read_text())
        vues = ({k: str(v) for k, v in brut.items()} if isinstance(brut, dict)
                else {k: "" for k in brut})
        premier = False
    except (OSError, ValueError):
        pass

    auj = dt.date.today()
    def enregistrer():
        garde = {}
        for k, v in vues.items():
            try:
                if (auj - dt.date.fromisoformat(v)).days > 180:
                    continue          # on oublie au bout de six mois
            except ValueError:
                pass
            garde[k] = v
        for k in actuels:
            garde[k] = auj.isoformat()
        tmp = TACHES_VUES.with_suffix(".tmp")
        tmp.write_text(json.dumps(garde, ensure_ascii=False))
        os.replace(tmp, TACHES_VUES)

    if premier:
        enregistrer()
        print(f"  premier passage — {len(actuels)} devoir(s) enregistré(s), "
              "aucune notification")
        return 0

    nouveaux = [d for k, d in actuels.items() if k not in vues]
    enregistrer()
    if not nouveaux:
        print("  aucun nouveau devoir")
        return 0

    if len(nouveaux) > 3:
        mats = sorted({d["matiere"] for d in nouveaux})
        notifier(f"{len(nouveaux)} nouveaux devoirs", ", ".join(mats),
                 " · ".join(d["texte"][:60] for d in nouveaux[:3]) + "…")
    else:
        for d in nouveaux:
            quand = ""
            if d["echeance"]:
                _, lbl = urgence(d["echeance"], dt.date.today())
                quand = f" — à rendre {lbl} ({d['echeance']:%d/%m})"
            notifier("Nouveau devoir", d["matiere"], d["texte"] + quand)
    print(f"  {len(nouveaux)} nouveau(x) devoir(s) annoncé(s)")
    for d in nouveaux:
        print(f"     [{d['matiere']}] {d['texte'][:60]}")
    return 0

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

def enregistrer_jeton(cfg, tok):
    """Valide un JWT et l'ecrit dans la config. Renvoie (ok, message)."""
    tok = (tok or "").strip().strip('"').strip("'")
    if not tok:
        return False, "jeton vide"
    try:
        charge = decode_jwt(tok)
    except (ValueError, json.JSONDecodeError) as ex:
        return False, str(ex)
    cfg.setdefault("zeus", {})["token"] = tok
    CONFIG_PATH.write_text(json.dumps(cfg, indent=2, ensure_ascii=False))
    os.chmod(CONFIG_PATH, 0o600)
    qui = charge.get("name") or charge.get("unique_name") or charge.get("sub") or "?"
    exp = expiration(tok)
    quand = f", valable jusqu'au {exp:%d/%m à %H:%M}" if exp else ""
    return True, f"jeton enregistré pour {qui}{quand}"

def cmd_zeus_enregistrer(cfg, args):
    """Recoit le jeton sur l'entree standard.

    Par l'entree standard et non en argument : un argument de ligne de commande
    est visible de tout le systeme dans la liste des processus."""
    ok, msg = enregistrer_jeton(cfg, sys.stdin.read())
    print(("  [OK ] " if ok else "  [KO ] ") + msg)
    return 0 if ok else 1

def cmd_zeus_connexion(cfg, args):
    """Ouvre la fenetre de connexion Zeus et attend le jeton."""
    binaire = Path(APP) / "Contents/MacOS/BriefMatin"
    if not binaire.exists():
        print(f"  [KO ] application introuvable ({APP}) — lance ./install.sh")
        return 1
    print("  Une fenêtre s'ouvre : connecte-toi normalement.")
    print("  Elle se referme seule dès que le jeton est récupéré.")
    r = subprocess.run([str(binaire), "--connexion"], capture_output=True,
                       text=True, timeout=360)
    sortie = (r.stdout + r.stderr).strip()
    print("  " + (sortie or "aucune réponse"))
    return r.returncode

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

def cmd_cocher(cfg, args):
    """Coche (ou decoche) une case dans une note du vault.

    On verifie que le fichier est bien dans le vault et que la ligne visee est
    bien une case a cocher : sans cela, un decalage de numerotation ecrirait
    n'importe ou dans une note."""
    vault = Path(cfg["vault"]).resolve()
    cible = Path(args.fichier).resolve()
    try:
        cible.relative_to(vault)
    except ValueError:
        print(f"  [KO ] {cible} est hors du vault, rien n'est écrit")
        return 1
    lignes = cible.read_text(encoding="utf-8").splitlines(keepends=True)
    i = args.ligne - 1
    if not 0 <= i < len(lignes):
        print(f"  [KO ] la ligne {args.ligne} n'existe pas dans {cible.name}")
        return 1
    # on met la fin de ligne de cote : '.' ne la capture pas, et sans elle
    # la ligne suivante se retrouverait collee a celle-ci
    corps, fin = lignes[i], ""
    for term in ("\r\n", "\n", "\r"):
        if corps.endswith(term):
            corps, fin = corps[: -len(term)], term
            break
    m = re.match(r"(\s*[-*]\s*\[)( |x|X)(\].*)$", corps)
    if not m:
        print(f"  [KO ] la ligne {args.ligne} n'est pas une case à cocher")
        return 1
    neuf = "x" if args.etat == "fait" else " "
    lignes[i] = m.group(1) + neuf + m.group(3) + fin
    tmp = cible.with_suffix(cible.suffix + ".tmp")
    tmp.write_text("".join(lignes), encoding="utf-8")
    os.replace(tmp, cible)
    print(f"  [OK ] {cible.name} ligne {args.ligne} -> [{neuf.strip() or ' '}]")
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
    heure = cfg.get("heure_matin", "09:00")
    jours = cfg.get("jours_matin", "tous")
    chk("ouverture automatique", plist.exists(),
        f"{'tous les jours' if jours == 'tous' else jours} à {heure}")
    veille = HOME / "Library/LaunchAgents/com.briefmatin.veille.plist"
    detail = ""
    try:
        n = len(json.loads(TACHES_VUES.read_text()))
        detail = f"{n} devoir(s) connus"
    except (OSError, ValueError):
        detail = "pas encore amorcée"
    chk("veille des nouveaux devoirs", veille.exists(), detail)
    print()
    return 0 if ok else 1

def main():
    ap = argparse.ArgumentParser(prog="brief-matin")
    sub = ap.add_subparsers(dest="cmd")
    sub.add_parser("render"); sub.add_parser("show")
    p = sub.add_parser("zeus-groupes"); p.add_argument("--filtre")
    sub.add_parser("zeus-test"); sub.add_parser("zeus-coller")
    sub.add_parser("zeus-enregistrer"); sub.add_parser("zeus-connexion")
    p = sub.add_parser("zeus-groupe"); p.add_argument("ids", nargs="+")
    p = sub.add_parser("cocher")
    p.add_argument("--fichier", required=True)
    p.add_argument("--ligne", type=int, required=True)
    p.add_argument("--etat", choices=("fait", "afaire"), default="fait")
    p = sub.add_parser("veille")
    p.add_argument("--rejouer", action="store_true",
                   help="oublie les devoirs connus et re-annonce")
    sub.add_parser("doctor")
    args = ap.parse_args()
    cfg = load_config()
    return {"render": cmd_render, "show": cmd_show, "zeus-groupes": cmd_zeus_groupes,
            "zeus-test": cmd_zeus_test, "zeus-coller": cmd_zeus_coller, "zeus-enregistrer": cmd_zeus_enregistrer,
            "zeus-connexion": cmd_zeus_connexion, "zeus-groupe": cmd_zeus_groupe,
            "cocher": cmd_cocher, "veille": cmd_veille, "doctor": cmd_doctor,
            }[args.cmd or "show"](cfg, args)

if __name__ == "__main__":
    sys.exit(main() or 0)
