# Patrimoine

Tableau de bord **local** pour suivre un patrimoine financier (actions/ETF, crypto ; assurance-vie, PER, livrets, immobilier ; valeur saisie à la main pour ce qui n'a pas de cours). Usage personnel, une seule machine.

## Règles non négociables
1. **Lecture seule** : aucun ordre, virement ni appel en écriture vers une banque, un courtier ou une plateforme.
2. **Données locales** : montants et quantités ne quittent jamais la machine (ni GitHub, ni cloud, ni télémétrie, ni logs envoyés ailleurs).
3. **Seules requêtes sortantes** : prix publics par ticker/id, taux de change et indices publics (inflation Eurostat, D-045), taux de dépôt et d'emprunt publics d'Aave par chaîne et jeton (au plus une fois par jour, D-095), sans montant, quantité ni adresse de portefeuille.
4. **Aucun secret dans le dépôt** : clés dans `.env` (ignoré par git), `.env.example` ne contient que les noms. Jamais d'identifiants bancaires.
5. **Données fictives** dans les tests, exemples et fixtures. Débogage sur `data/demo.db`, jamais sur les vraies bases (dossier des données, D-119).
6. Serveur lié à **`127.0.0.1`** uniquement, et qui ne répond qu'aux noms `localhost` et `127.0.0.1` (D-039) ; l'API refuse les requêtes venues d'un autre site, CSP stricte (D-080).
7. **`Decimal`, jamais `float`** pour montants, prix, quantités et taux (stockés en TEXT dans SQLite).

## Stack
Python 3.12, `uv`, FastAPI, SQLite (module standard `sqlite3`, sans ORM), front statique HTML/CSS/JS + Chart.js. Qualité : `pytest`, `ruff`.
Environnement : Windows + WSL 2 (Ubuntu), projet dans `~/patrimoine`, app sur `http://localhost:8000` (port suivant si pris). L'app tourne aussi sans WSL (D-118) : lire et écrire tout texte en `encoding="utf-8"`.

## Commandes
```bash
uv sync                                        # installe l'environnement (.venv)
uv run python -m app                           # Windows/macOS/Linux : sauvegarde, port libre (8000 de préférence), fenêtre d'application sous Windows/macOS sinon navigateur (D-125), ramène l'instance déjà lancée (D-118) ; --navigateur, --sans-navigateur
./scripts/patrimoine.sh                        # idem depuis WSL (Patrimoine.bat : double-clic Windows via WSL ; Patrimoine-sans-WSL.bat : sans WSL)
uv run python -m app.demo                      # crée data/demo.db avec des données fictives
uv run python -m app.demo --grand              # crée data/demo-grand.db : 3 comptes, 30 lignes fictives
PATRIMOINE_DB=data/demo.db uv run python -m app   # même chose sur la base de démo
uv run python -m app.backup                     # sauvegarde la base dans data/sauvegardes/
uv run python -m app.backup --verifier          # relit la dernière copie de chaque base (D-114)
uv run python -m app.migrate                    # sauvegarde puis met à jour le schéma de chaque base (aussi fait au lancement, D-120)
uv run python -m app.profiles [sans-code <id>]  # liste les profils ; retire un code oublié
uv run python -m app.datadir [--choisir DOSSIER] # dossier des données ; copie vérifiée vers un autre (D-119)
uv run python -m app.version [--verifier-etiquette vX.Y.Z]  # version ; contrôle d'une étiquette de release (D-121)
uv run --group build pyinstaller packaging/patrimoine.spec   # Windows : dist/Patrimoine/Patrimoine.exe, sans console (D-126)
uv run pytest                                  # tests
uv run ruff check . && uv run ruff format .    # lint + formatage
for f in web/*.js; do node --check "$f"; done  # syntaxe du front (aussi en CI)
```

## Architecture
```text
app/version.py     __version__ (source lue par l'app, = pyproject.toml et uv.lock), notes d'une version tirées de CHANGELOG.md (D-121)
app/main.py        FastAPI, API JSON sous /api/..., sert web/
app/__main__.py    point d'entrée (options) ; app/launcher.py : verrou d'instance, port libre, navigateur, arrêt propre, sauvegarde horaire (D-118)
app/window.py      fenêtre d'application pywebview (Windows, macOS), repli sur le navigateur, taille gardée, actions du bouton « Fenêtre » (D-125, D-127)
app/console.py     Patrimoine.exe sans console : journal.txt dans le dossier de données par défaut, erreur de démarrage en boîte de dialogue (D-126)
app/config.py      lecture de .env (sans écraser l'environnement ; à côté de Patrimoine.exe une fois empaqueté, APP_DIR), PRICE_TTL_MINUTES, clé CoinGecko
app/datadir.py     dossier des données hors du code (D-119) : défaut par système, dossier choisi, copie vérifiée de l'ancien data/
app/db.py          connexion SQLite (PRAGMA foreign_keys=ON à chaque connexion), base du profil de la requête (ContextVar), Decimal, migrations (user_version)
app/profiles.py    profils (D-098) : data/profils.json, une base par profil, code en empreinte scrypt, jetons de session en mémoire
app/schema.sql     schéma de référence (tables account, asset, operation, price, price_history, fx_rate, benchmark, recurring, target, category_target, watchlist, watchlist_item, defi_line, defi_rate, defi_check, debt, debt_balance, defi_borrow, defi_borrow_flow, defi_borrow_check, defi_reserve)
app/migrations/    NNN_nom.sql : passage d'une version de schéma à la suivante (app/migrate.py : au lancement, copie vérifiée avant, refus d'une base plus récente, D-120)
app/models.py      dataclasses du domaine (Account, Asset, Operation, Position)
app/schemas.py     validation Pydantic des entrées de l'API ; Decimal renvoyés en chaînes
app/repository.py  SQL des comptes, actifs, opérations
app/portfolio.py   positions (PRU au prix moyen pondéré), conversion et total EUR, performance par période, fonctions pures
app/recurring.py   échéances des achats programmés, quantité proposée (fonctions pures)
app/analytics.py   TRI, volatilité, simulation d'indice, projection (fonctions pures)
app/defi.py        dépôts et emprunts Aave : intérêts estimés jour par jour au taux public, recalage par solde vérifié, facteur de santé (fonctions pures, D-095, D-108)
app/defi_sync.py   lignes suivies, taux en cache (un appel par jour), intérêts des mois clos enregistrés
app/debts.py       dettes : capital restant dû à mensualités constantes, recalage par capital saisi (fonctions pures, D-107)
app/custody.py     garde d'un compte crypto (plateforme / portefeuille personnel), comptes à l'étranger 3916 / 3916-bis (fonctions pures, D-104)
app/crypto.py      onglet Rendements crypto : récompenses par jeton, type, mois ; placements (compte, jeton), rendement réalisé en jetons, revenu estimé (fonctions pures, D-092, D-106)
app/watchlist.py   listes de suivi : rendement d'un cours sur 1 jour à 5 ans (fonctions pures, D-072)
app/income.py      dividendes reçus, frais payés, coût des frais courants (TER) ; fonctions pures
app/rebalance.py   allocation cible : répartition d'un versement sans vente (fonction pure)
app/tax.py         repères fiscaux FR en estimation : plafond et 5 ans PEA, 8 ans AV, PER (plafond, sortie), plus-values CTO, crypto (portefeuille global)
app/reconcile.py   comparaison d'un compte au relevé de la banque : écarts, causes, correction (D-063)
app/prices.py      cache des prix et taux dans SQLite, choix de la source, repli si panne
app/demo.py        remplit data/demo.db avec des données fictives
app/valuations.py  valeurs saisies à la main (actifs sans cours public), conversion montant -> parts
app/backup.py      sauvegarde de la base (API backup de SQLite), du jour au lancement puis chaque heure, relue, rotation 14 j + 12 mois + 1/an, profils.json, copie hors de WSL (BACKUP_COPY_DIR, D-114)
app/providers/     une source par fichier (yahoo, coingecko, frankfurter, eurostat, aave), get_price(client, id) -> Price
app/crypto_tickers.py ticker Yahoo d'une crypto (table vérifiée, D-111)
app/tickers.py     ticker Yahoo d'un ISIN : table locale, puis recherche Yahoo sur clic (/api/tickers, D-083)
app/importers/     import CSV : csv_generique (lecture, correspondance des colonnes), preparation (comptes, actifs, doublons), un profil par plateforme reconnu aux en-têtes (trade_republic D-071, binance D-082) ; /api/import/preview puis /api/import (D-053)
web/tutorial.js    tutoriel de premier lancement en 5 étapes (D-099), relançable par le « ? » de la barre du haut
web/               index.html (pages par onglets #apercu, #performance...), profiles.js (choix du profil, puis charge app.js, D-098), app.js, tips.js (infobulles « ? »), styles.css ; web/vendor/ : Chart.js, chartjs-chart-treemap et police Inter copiés (pas de CDN, D-117)
.github/workflows/ CI : tests sous Linux, Windows et macOS, lint et syntaxe du front à chaque PR ; release.yml : étiquette vX.Y.Z → contrôles, archive, Patrimoine.exe zippé, SHA256SUMS, page de release (actions figées par SHA, D-121) ; exe-windows.yml : construit et lance Patrimoine.exe (D-126) ; dependabot.yml : dépendances chaque semaine en une PR, 7 j de recul sauf alerte (D-123)
.claude/           réglages Claude Code (D-079, D-113) : hooks garde-fou données réelles (bases des profils comprises) et ruff après édition, sous-agents, skills /avant-pr et nouvelle-migration
packaging/         Patrimoine.exe (D-126) : recette PyInstaller patrimoine.spec (dossier zippé), icône dessinée par icone.py, essai de la fenêtre en CI, licences tierces relevées par licences.py (D-128)
scripts/           lancement en un clic : patrimoine.sh (WSL), Patrimoine.bat (Bureau Windows via WSL), Patrimoine-sans-WSL.bat
tests/fixtures/    données FICTIVES uniquement
data/              ignoré par git : demo.db, demo-grand.db (les vraies données sont dans le dossier des données, D-119)
LICENSE            GNU GPL v3 ou ultérieure (D-128) ; README.md pour l'utilisateur, SECURITY.md pour signaler une faille
docs/roadmap.md    jalons ; docs/decisions.md : décisions et pourquoi ; docs/releases.md : sortir une version ; CHANGELOG.md : journal, « Non publié » complété par chaque PR visible
```

## Modèle (résumé, détail dans app/schema.sql)
- Dossier des données (D-119) : hors du code (`%LOCALAPPDATA%\Patrimoine`, `~/Library/Application Support/Patrimoine`, `~/.local/share/patrimoine`), ou choisi (`--choisir`, `PATRIMOINE_DATA_DIR`) ; l'ancien `data/` du code est copié et vérifié au premier lancement, jamais effacé. Ci-dessous, `data/` désigne ce dossier.
- Fenêtre (D-125) : pywebview (WebView2 / WebKit), seulement Windows et macOS ; sous Windows réglages de la page et cookie de session dans `data/fenetre/` (jamais copié ; magasin WebKit par défaut sous macOS) ; API en `Cache-Control: no-store` ; aucun `js_api` ; SmartScreen coupé ; `POST /api/window/focus` ramène la fenêtre devant ; pas de barre de menu : bouton « Fenêtre » de la page (`web/fenetre.js`, adresse `/?fenetre`), routes fixes `/api/window/browser|folder|zoom` sans profil ni paramètre (D-127).
- Profils (D-098) : une base par personne (`data/profils/<id>.db`, liste `data/profils.json`), choisie à chaque requête par un jeton de session (cookie HttpOnly) ; sans profils.json, base unique `data/patrimoine.db` ; `PATRIMOINE_DB` force une base unique. Une mémoire de processus qui décrit une base se range par `db.conn_key(conn)`. Code facultatif (anti-curiosité, la base n'est pas chiffrée). Gérer : renommer, exporter (son profil), restaurer (base vérifiée, nouveau profil), supprimer vers `data/corbeille/` ; profil Démo (`app.demo.fill_demo`).
- Positions **calculées** à partir des opérations, jamais stockées.
- Espèces **calculées** (D-062) pour PEA, AV, PER ; **saisies** pour CTO, crypto, Autre (solde du jour + opérations suivantes, D-075). Calcul D-062 : champ « Versements » du compte + opérations rejouées dans l'ordre (versements, ventes, dividendes − achats, frais, retraits) ; un achat qui les rendrait négatives compte un versement supposé. Livret : solde saisi + opérations suivantes (D-041).
- Valorisation = quantité × dernier prix, convertie en EUR via `fx_rate` (1 EUR = `rate` devise) ; coût et plus-values en devise aux taux BCE du jour de chaque opération (`RateHistory`, D-081).
- Actif sans ticker ni id CoinGecko = **valeur saisie** (fonds euros, immobilier...) : saisi en montants (convertis en parts), valeur totale saisie à une date et rangée comme un prix `manuel`, aucun appel réseau (D-056). Livret = compte d'espèces seules, classe « Livrets ».
- Cache prix : rappeler une API seulement si le dernier prix a plus de 5 min (`PRICE_TTL_MINUTES`).
- Historique : clôtures quotidiennes dans `price_history`, complétées au plus une fois par jour (`/api/history`).
- Comparaison et calendrier : liste `benchmark` (indices Yahoo, livrets à taux saisi calculés en local), `/api/benchmark`, `/api/calendar` (D-044).
- Visualisations (D-072) : carte du portefeuille (treemap, suit le filtre), listes de suivi (`watchlist`, tickers Yahoo seuls, cryptos comprises via `app/crypto_tickers.py`, CoinGecko en repli, D-111 ; `/api/watchlists`, « Mes lignes » calculée), rendements mensuels. Allocation cible dans « Projection et objectifs ». Ordre et masquage des onglets réglés par « Réorganiser » (D-073), gardés dans le navigateur, Gérer en dernier. Positions et opérations : un bloc par compte (D-073), un tableau à la fois (D-117). Classe d'actif `fonds` (OPCVM, UC).
- Inflation : indice Eurostat (IPCH France, table ECOICOP 2) en mémoire, un appel par jour, prolongé au taux saisi après le dernier mois publié (D-067) ; `/api/history` : argent net placé (achats − ventes, cadeaux exclus) et ce montant moins l'inflation lissée par année (D-110).
- Performance : `/api/performance` découpe cette série par jour/semaine/mois/année, en TWR (achats le matin, ventes le soir ; dividendes et frais comptés ; titres déjà détenus à leur valeur de marché, D-029, D-081). Filtre de lignes réglé dans Vue d'ensemble (`exclude`, `exclude_line=compte-actif`, `exclude_account`, D-069), suivi par Vue d'ensemble, Performance, Calendrier, Comparaison et Projection.
- Achats programmés : table `recurring` ; mensuel, trimestriel, hebdomadaire ou toutes les deux semaines (`bimensuel`, D-071) ; chaque échéance passée est proposée dans « Gérer » (`/api/recurring`), validé avec le prix réel ou ignoré, jamais enregistré seul (D-055).
- Achat offert (Saveback, Stockperk, cadeau TR) : `operation.offered`, ne sort pas des espèces (pas de versement supposé) ; l'achat entre au prix réel, frais compris, comme un apport : seule la hausse qui suit est un gain ; frais couverts par la part offerte hors « frais payés » (D-076, D-078). Impôt retenu d'un dividende : `operation.tax_withheld`.
- Onglet Crypto (D-092, schéma v17) : récompense = achat offert d'une crypto, typée par `operation.reward_kind` (earn, staking, airdrop, cashback, parrainage, autre) ; `/api/crypto/rewards` : valeur à réception et actuelle, par jeton/type/mois, rendement en jetons sur 12 mois. Import Binance : un gain par jeton, mois et type. Frais crypto dans cet onglet, hors de « Revenus et frais » (bourse seule).
- Onglet « Rendements crypto » (D-106, ancre `#crypto`) : placement = (compte, jeton) avec Earn/staking sur 12 mois ou ligne Aave ; rendement réalisé en jetons (jusqu'au dernier versement de moins de 35 jours), revenu estimé = capital × taux Aave du jour sinon réalisé ; rendement moyen pondéré par le capital ; cadeaux (airdrop, cashback, parrainage) dans les revenus, jamais dans un taux ; carte « Ne rapportent rien ».
- Dépôts Aave (D-095, schéma v18) : ligne (compte, jeton) reliée à un marché (`defi_line`), taux de dépôt du jour en cache (`defi_rate`) ; chaque mois clos = achat offert `earn` créé et recalculé tout seul (`source='aave'`, non modifiable à la main), mois en cours estimé ; « Vérifier » (`defi_check`) recale sur le solde lu dans Aave. Suivre une ligne = compte (ou nouveau « Aave <chaîne> ») + premier dépôt ; Déposer/Retirer (transfert depuis/vers un compte de l'app, sinon `solde_initial` / transfert hors de l'app, D-096). `/api/aave/...`, tableau « Mes placements » de l'onglet Rendements crypto.
- Emprunts Aave (D-108, schéma v21) : `defi_borrow` + mouvements + dettes vérifiées, dette = même calcul que les dépôts au taux d'emprunt (`side='borrow'`) ; facteur de santé par marché = dépôts suivis × seuil (`defi_reserve`) ÷ dettes, alerte sous 1,5 ; dette comptée dans le patrimoine net ; `/api/aave/borrows`, carte « Emprunts Aave ».
- Comptes crypto (D-104, schéma v19) : `account.custody` = `exchange` (plateforme) ou `wallet` (portefeuille personnel, sans espèces), NULL à préciser ; `account.abroad` (tout compte) = à déclarer, liste 3916 / 3916-bis dans Fiscalité (jamais un portefeuille) ; devinés d'après nom et établissement (`app/custody.py`). Pas de suivi par adresse publique (règle 3). 
- Dettes (D-107, schéma v20) : `debt` (montant, taux, début, durée facultatifs) + `debt_balance` (capital saisi qui recale) ; hors positions, espèces et performance ; Patrimoine net = total (hors filtre) − capital restant dû, tuile de Vue d'ensemble (`debts_eur` de `/api/portfolio`), carte « Dettes » dans Gérer, `/api/debts`.
- Revenus et frais (`/api/income`, D-057, D-060, D-076 : barres par ligne, calendrier, frais courants par compte), allocation cible (`/api/targets`, D-058), repères fiscaux (`/api/tax`, D-059, PER D-064, crypto D-066) : calculés hors ligne à partir des opérations, prix et taux déjà en base.
- Catégorie d'un actif : `asset.category`, sinon sa classe (`category_of`, D-061) ; cibles par ligne ou par catégorie (`/api/targets?level=categorie`).
- Import par plateforme (D-071) : seules les opérations d'investissement, une « partie » du relevé (PEA, CTO...) par compte de l'app, empreinte = id de l'opération chez la plateforme, plans d'investissement proposés en achats programmés.
- Import Binance (D-082) : « Historique des transactions » ; stablecoins = actifs, échanges crypto-crypto marqués `crypto_swap`, frais BNB vendus, gains Earn/airdrops en achats offerts par mois, retraits en `transfert_sortant` (type d'opération : quantité en moins, ni vente ni espèces, schéma v15), cours estimés (dollar BCE, CoinGecko 365 j par id, prix du fichier).
- Import : compte d'une partie retenu seulement s'il est à la plateforme, sinon « Créer » proposé ; devise d'un actif modifiable, opérations converties au taux BCE de leur jour (D-083).
- Transfert sortant ou retrait vers un compte de l'app (`to_account_id`, schéma v16) : arrivée déduite (`with_transfers`) au prix de revient du départ, ou versement ; sinon « Hors de l'app » + texte (D-085). Actifs soldés (`sold_out`) rangés à part dans Gérer. Cryptos cotées en $ par défaut : import Binance converti au taux BCE du jour, espèces € d'un compte crypto débitées au même taux (D-085).
- Supprimer un compte ou un actif qui a des opérations : les déplacer (même devise) ou les supprimer aussi, sauvegarde de la base juste avant (D-074).
- Ordre des comptes et actifs : colonne `position`, réglée par glisser-déposer dans « Gérer » (`PUT /api/accounts/order`, `/api/assets/order`), suivie par toute l'API (D-070). Disposition des cartes et tri des tableaux : dans le navigateur.
- Répartition : `/api/portfolio` renvoie `groupings` (classe, catégorie, ligne, compte, devise ; 7 plus grosses parts + « Autres »), couleur d'une ligne = celle choisie (`asset.color`, D-052) sinon son rang (D-051).

## Façon de travailler
- Un jalon = une branche = une PR. Petites étapes testables, mode plan avant de coder.
- Expliquer en 2-3 phrases chaque notion nouvelle (l'utilisateur apprend).
- Demander avant : push, migration de base, suppression de données, dépendance lourde.
- Toute décision de conception va dans `docs/decisions.md`.
- Économie de tokens : ne jamais lire en entier `docs/decisions.md` (≈ 90 Ko) ni `web/app.js` (≈ 285 Ko) ; chercher avec `grep -n` puis lire la zone utile. Nouvelle décision : numéro suivant par `grep -n "^## D-" docs/decisions.md | tail -1`, ajout en fin de fichier.
- Schéma modifié : suivre la skill `nouvelle-migration`. Avant de pousser : `/avant-pr`.
- Sous-agents (`.claude/agents/`, D-084), à appeler sans redemander : `verificateur` (Haiku) pour
  tests + lint quand la sortie est longue ; `relecteur-calculs` (Opus) si un calcul financier
  change ; `gardien-confidentialite` (modèle du fil) si réseau, import, API ou fixtures changent ;
  `analyste-import` (modèle du fil) au début d'un profil de plateforme ; `capture-ui` (Haiku) pour
  montrer un changement de `web/` (il relève les erreurs de la console) ; `Explore` (Haiku,
  D-113) pour chercher dans le code. Un sous-agent part sans le contexte du fil : lui donner
  fichiers, décision et question précise. Pas de sous-agent pour une tâche de quelques lectures.

## Conventions du front (règles permanentes)
- **Tout tableau est triable par en-tête dès sa création** (demandé trois fois par l'utilisateur) : `<th data-sort="cle"><button class="sort">`, `bindSortable(id, colonnes numériques)` au chargement, puis `fillSortable(id, lignes, versLigne, texteVide)` au rendu (ordre par défaut dans `SORT_DEFAULTS`). Seules exceptions : listes à ordre choisi (Comptes, Actifs de « Gérer ») et formulaires en tableau.
- Toute nouvelle carte d'onglet porte `data-card="nom-stable"` (déplaçable par « Réorganiser », D-070, D-073) ; `class="card half"` pour une demi-largeur.
- Aucune bibliothèque chargée depuis un CDN (copie dans `web/vendor/`), aucune requête tierce à l'ouverture de la page.
- Formulaires d'actif et de compte : un champ propre à certaines classes ou types porte `data-for="etf action ..."` (D-072) ; un champ rempli en base reste visible.
- **Jetons seulement** (D-115) : dans `web/styles.css`, couleurs, espacements, tailles de texte et rayons passent par les variables du haut du fichier (`--surface --text --accent --ok --ko --warn` et leurs mélanges, `--s-1` à `--s-7`, `--t-xs` à `--t-2xl`, `--r-sm`, `--radius`) ; aucune valeur en dur, aucun dégradé, ombre réservée à ce qui flotte. Les `--series-*` sont la palette des graphiques.
- **Textes courts, détail en infobulle** (D-091) : une ligne visible au plus sous un titre ; le complément va dans un bouton `<button type="button" class="tip" data-tip="…" aria-label="Plus d'infos">?</button>` placé juste après le texte qu'il explique (après le libellé d'un champ, après le titre d'une carte), jamais dans un coin. Depuis `app.js` : `makeTip(texte)`. Les longues règles fiscales restent dans un `<details class="note">`. Espacements (padding, margin, gap) par les jetons `--s-*`.
