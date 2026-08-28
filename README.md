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

Cliquer sur l'icône rafraîchit et affiche le brief. En ligne de commande :

```bash
brief-matin show      # régénère et ouvre la fenêtre
brief-matin render    # régénère seulement le HTML
brief-matin doctor    # diagnostic
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
| `pour jeudi` | le prochain jeudi |
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
Connecte-toi à Zeus dans ton navigateur, ouvre les outils de développement,
onglet *Application* → *Local Storage*, et copie la valeur du jeton :

```json
"zeus": { "token": "eyJhbGciOi...", "groupes": [1234] }
```

Une fois l'accès en place, retrouve l'identifiant de ton groupe :

```bash
brief-matin zeus-groupes --filtre "ton-groupe"
brief-matin zeus-test
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
