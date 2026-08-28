# brief-matin

Le planning du jour et les devoirs à rendre, dans une petite fenêtre, chaque
matin. Le planning vient de **Zeus** (IONIS), les devoirs des notes de cours
d'**Obsidian** — celles que produit
[flow2obsidian](https://github.com/messyhat555/flow2obsidian), ou n'importe
quelle note contenant des cases à cocher.

Application native macOS (WKWebView, ~150 lignes de Swift), sans dépendance :
ni pip, ni npm, ni navigateur.

## Installation

```bash
git clone https://github.com/messyhat555/brief-matin.git
cd brief-matin
./install.sh
```

L'installeur détecte le vault Obsidian, compile `Brief Matin.app` dans
`/Applications`, installe la commande `brief-matin` et programme l'ouverture
automatique chaque jour ouvré. Il se termine par un diagnostic.

Cliquer sur l'icône rafraîchit et affiche le brief. ⌘R rafraîchit à la volée.

**Les cases à cocher sont vraies.** Cliquer un devoir dans la fenêtre écrit
`- [x]` dans la note Obsidian d'où il vient, et la tâche disparaît du brief.
L'écriture est verrouillée : le fichier doit être dans le vault et la ligne
visée doit bien être une case à cocher, sinon rien n'est écrit.

**La journée est vivante.** Une barre situe l'heure courante parmi tes cours,
le cours en cours est mis en avant, et le suivant affiche un compte à rebours
qui se met à jour tout seul. Les séances qui débordent sur plusieurs jours —
semaines de rattrapage, stages — sont ramenées à la journée affichée.

En ligne de commande :

```bash
brief-matin show      # régénère et ouvre la fenêtre
brief-matin render    # régénère seulement le HTML
brief-matin doctor    # diagnostic
brief-matin cocher --fichier <note.md> --ligne 42   # cocher sans la fenêtre
```

## Les devoirs, depuis Obsidian

Toutes les cases à cocher non cochées du vault sont lues, puis réparties selon
le titre de la section qui les contient :

| Section | Colonne |
| --- | --- |
| `À faire`, `Devoirs`, `À rendre`, `Travail`, `TODO` | **À rendre** |
| `À retravailler`, `À réviser`, `Révisions` | **À réviser** |

La comparaison ignore accents et casse, donc `## A faire` et `## À faire`
fonctionnent aussi bien.

Les échéances sont devinées dans le texte de la tâche :

| Écrit dans la note | Compris comme |
| --- | --- |
| `à rendre le 12` | le 12 du mois courant, ou du suivant s'il est passé |
| `pour le TP de jeudi`, `avant mardi` | la prochaine occurrence de ce jour |
| `pour la semaine prochaine` | le lundi qui vient |
| `2026-09-12` | cette date |

Sans indice, la tâche est affichée sans étiquette plutôt qu'avec une date
inventée. Le tri va du plus urgent au plus lointain, les sans-date à la fin.

## Le planning, depuis Zeus

L'API Zeus est documentée sur
[`/swagger`](https://zeus.ionis-it.com/swagger/index.html) et **tout** y demande
un jeton JWT — y compris les flux ICS. Il n'existe aucun endpoint
identifiant/mot de passe : l'authentification se fait par `appId` d'application
ou par jeton Microsoft.

Deux façons de configurer l'accès, dans
`~/.local/share/brief-matin/config.json` :

**Un `appId`** — si l'école t'en délivre un. C'est la voie durable : l'app
demande un jeton neuf à chaque ouverture.

```json
"zeus": { "app_id": "ton-app-id", "groupes": [1234] }
```

**Un JWT copié depuis ta session** — fonctionne tout de suite, mais expire.

Le front de Zeus range son jeton dans `localStorage`, sous la clé `AUTH`. Le
plus simple est de passer par le presse-papiers, pour que le jeton n'ait jamais
à transiter ailleurs : connecte-toi à Zeus, ouvre la console du navigateur
(⌥⌘I, onglet *Console*), colle ceci puis Entrée —

```js
copy(Object.entries(localStorage).map(([k, v]) => { try { const o = JSON.parse(v); return o && o.token } catch (e) { return /^eyJ[\w-]+\.[\w-]+\./.test(v) && v } }).find(Boolean))
```

puis, dans un terminal :

```bash
brief-matin zeus-coller
```

La commande valide le jeton, affiche à quel compte il appartient et jusqu'à
quand il est valable, puis l'enregistre en restreignant la config à ton seul
compte (`chmod 600`). `brief-matin doctor` te prévient quand il approche de
l'expiration : il suffit de refaire la manip.

Une fois l'accès en place, retrouve l'identifiant de ton groupe et enregistre-le :

```bash
brief-matin zeus-groupes --filtre cyber
brief-matin zeus-groupe 641
```

`zeus-groupe` enregistre et vérifie dans la foulée.

**Choisis ton sous-groupe, pas la promo.** Interroger un groupe parent renvoie
l'union des séances de tous ses sous-groupes : tu récupérerais les TD des autres
groupes en plus des tiens. Le sous-groupe, lui, contient déjà les événements
communs à toute la promo (rentrée, conférences). Dans le doute, compare :

```bash
brief-matin zeus-groupes --filtre "cyber 3"
```

Tu peux aussi désigner ton groupe par son nom, sans connaître son id :

```json
"zeus": { "token": "...", "groupe_nom": "A1", "groupe_parent": "EPITA" }
```

Si Zeus est injoignable ou le jeton expiré, la fenêtre s'affiche quand même :
seule la section *Aujourd'hui* est remplacée par un message. Les devoirs, eux,
ne dépendent de rien d'autre que du vault.

## Configuration

`~/.local/share/brief-matin/config.json` (voir `config.example.json`) :

| Clé | Rôle |
| --- | --- |
| `vault` | chemin du vault, détecté à l'installation |
| `prenom` | prénom affiché dans la salutation (facultatif) |
| `dossiers_taches` | dossiers où chercher les cases à cocher |
| `max_devoirs`, `max_revisions` | nombre de lignes affichées |
| `zeus` | accès et groupe (voir ci-dessus) |
| `heure_matin` | heure d'ouverture automatique, jours ouvrés |

Après avoir changé `heure_matin`, relance `./install.sh` pour reprogrammer le
réveil.

## Désinstallation

```bash
./uninstall.sh
```

Retire l'application, le réveil et la commande. Ne touche pas au vault.

## Licence

MIT — voir [LICENSE](LICENSE).
