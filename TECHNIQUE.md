# brief-matin — notes techniques

[← Retour au README](README.md)

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

L'installeur détecte le vault Obsidian, compile et signe `Brief Matin.app` dans
`/Applications`, installe la commande `brief-matin` et met en place deux agents :
l'ouverture automatique de la fenêtre, et la veille des nouveaux devoirs. Il se
termine par un diagnostic.

Cliquer sur l'icône rafraîchit et affiche le brief. ⌘R rafraîchit à la volée.

**Les cases à cocher sont vraies.** Cliquer un devoir dans la fenêtre écrit
`- [x]` dans la note Obsidian d'où il vient, et la tâche disparaît du brief.
L'écriture est verrouillée : le fichier doit être dans le vault et la ligne
visée doit bien être une case à cocher, sinon rien n'est écrit.

**Trois vues**, au clic sur les onglets ou aux touches `1` `2` `3` :

| Vue | Contenu |
| --- | --- |
| **Jour** | la timeline du jour, les devoirs groupés par urgence, les révisions |
| **Semaine** | l'emploi du temps en grille, cette semaine et la suivante (`‹` `›`) |
| **Focus** | *une seule chose à faire maintenant*, en grand, et rien d'autre |

La vue **Focus** choisit elle-même la chose la plus importante : le cours en
cours s'il y en a un, sinon un devoir en retard, sinon le cours qui commence
dans moins de 45 min, sinon le devoir dû aujourd'hui, et ainsi de suite jusqu'aux
révisions. « C'est fait » coche le devoir dans Obsidian, « Autre chose » passe au
suivant, et un aperçu « et ensuite » montre les trois qui viennent.

**La journée est vivante.** Une barre situe l'heure courante parmi tes cours,
le cours en cours est mis en avant, et le suivant affiche un compte à rebours
qui se met à jour tout seul. Les séances qui débordent sur plusieurs jours —
semaines de rattrapage, stages — sont ramenées à la journée affichée.

## Notifications de nouveaux devoirs

Un agent relit le vault toutes les trois minutes, et immédiatement à chaque
écriture dans les dossiers de tâches. Quand un devoir apparaît qu'il n'avait
jamais vu, il l'annonce :

> **Nouveau devoir** · Droit constitutionnel
> Préparer une fiche sur le contrôle de conventionnalité — à rendre le 12/09

Au-delà de trois d'un coup, il regroupe en une seule notification. Le premier
passage se contente d'enregistrer l'existant, sans rien annoncer.

Les empreintes déjà vues ne sont **jamais** oubliées avant six mois : une note
lue pendant sa sauvegarde, un devoir coché puis décoché, un dossier
temporairement déplacé ne déclenchent pas de fausse alerte.

macOS demande l'autorisation d'envoyer des notifications au premier lancement de
la fenêtre. Si elle est refusée, l'agent retombe sur `osascript` — la
notification arrive quand même, simplement attribuée au Script Editor. Pour
l'accorder après coup : Réglages Système → Notifications → Brief Matin.

## Quand la fenêtre s'ouvre

Tous les jours à 9 h, réglable dans la config :

```json
"heure_matin": "09:00",
"jours_matin": "tous"
```

`jours_matin` accepte `"tous"`, `"semaine"` (lundi–vendredi) ou une liste
d'indices launchd (`0` = dimanche). Relance `./install.sh` après modification.

En ligne de commande :

```bash
brief-matin show      # régénère et ouvre la fenêtre
brief-matin render    # régénère seulement le HTML
brief-matin doctor    # diagnostic
brief-matin veille    # cherche les nouveaux devoirs maintenant
brief-matin veille --rejouer   # oublie l'historique et re-annonce
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

**Se connecter depuis l'app** — le plus simple, et le seul geste à retenir.

En bas de la fenêtre, une pastille indique en permanence l'état de l'accès —
`Zeus · 23 h`, `Zeus · 40 min` en orange, `Zeus expiré` en rouge. **Elle est
cliquable à tout moment**, même quand tout va bien. Quand le jeton manque ou
approche de l'expiration, un bouton **« Se connecter à Zeus »** apparaît en plus
là où le planning aurait dû être.

Un clic ouvre une fenêtre de connexion : tu t'y connectes normalement, et dès que Zeus range son
jeton, l'app le récupère, l'enregistre et referme la fenêtre toute seule.

Disponible aussi à tout moment par le menu (**⌘L**) ou en ligne de commande :

```bash
brief-matin zeus-connexion
```

La connexion se fait dans une vue web appartenant à l'app, qui ne lit donc que
son propre stockage — aucun accès à ton navigateur. La session y est persistante :
les fois suivantes, la fenêtre se referme souvent sans que tu aies à retaper quoi
que ce soit.

**Coller un jeton à la main** — si la fenêtre de connexion est refusée par le
fournisseur d'identité, la voie manuelle reste disponible. Connecte-toi à Zeus
dans ton navigateur, ouvre la console (⌥⌘I), et colle :

```js
copy(JSON.parse(localStorage.getItem("ZEUS-AUTH")).token)
```

puis, dans un terminal :

```bash
brief-matin zeus-coller
```

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

## Sécurité

L'authentification se fait dans une vue web appartenant à l'application. C'est
commode, mais c'est un compromis assumé, et voici lequel.

**Ce qui est protégé.**
Le jeton vit dans le **trousseau macOS**, pas dans un fichier : chiffré au repos,
rattaché à cette application. La config n'en garde qu'un marqueur. La fenêtre de
connexion **n'autorise la navigation que vers Zeus et l'authentification
Microsoft** — toute redirection ailleurs est refusée et affichée. Comme il n'y a
pas de barre d'adresse, le **titre de la fenêtre affiche l'hôte réel** en
permanence. Le jeton transite par l'entrée standard, jamais en argument de
commande.

**Ce qui reste vrai malgré tout.**
Une application qui héberge une vue web peut en lire le contenu — c'est le
mécanisme même de cette fonctionnalité, et c'est pourquoi les fournisseurs
d'identité déconseillent les vues embarquées. Rien n'empêche techniquement une
telle application de lire un mot de passe saisi dans sa vue. Celle-ci ne lit que
`localStorage.getItem('ZEUS-AUTH')`, dans `ConnexionZeus.chercherJeton()` — une
quinzaine de lignes, vérifiables. La confiance repose sur la lecture du code, pas
sur une garantie du système.

Par ailleurs, une vue embarquée ne bénéficie pas de l'anti-hameçonnage du
navigateur, ni du gestionnaire de mots de passe, ni forcément des clés de
sécurité matérielles.

**Portée réelle du jeton.** Il porte `rol: VISITOR`, `groups: []`,
`aud: zeus-app` : lecture d'emplois du temps, et rien d'autre. Ce n'est pas un
jeton Microsoft, et il expire en 24 h.

**Tout effacer**, jeton et session web de l'application comprises :

```bash
brief-matin zeus-deconnexion
```

La session de ton propre navigateur n'est pas touchée.

**Si ce compromis ne te convient pas**, la voie manuelle par le presse-papiers
n'utilise aucune vue web embarquée : tu te connectes dans ton vrai navigateur,
avec toutes ses protections, et tu ne transmets que le jeton final.

## Configuration

`~/.local/share/brief-matin/config.json` (voir `config.example.json`) :

| Clé | Rôle |
| --- | --- |
| `vault` | chemin du vault, détecté à l'installation |
| `prenom` | prénom affiché dans la salutation (facultatif) |
| `dossiers_taches` | dossiers où chercher les cases à cocher |
| `max_devoirs`, `max_revisions` | nombre de lignes affichées |
| `zeus` | accès et groupe (voir ci-dessus) |
| `semaines_avant`, `semaines_apres` | étendue de la vue semaine (1 et 5 par défaut) |
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
