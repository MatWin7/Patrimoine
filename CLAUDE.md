# Patrimoine

Tableau de bord **local** pour suivre un patrimoine financier (actions/ETF, crypto ; assurance-vie, PER, livrets, immobilier ; valeur saisie à la main pour ce qui n'a pas de cours). Usage personnel, une seule machine.

## Règles non négociables
1. **Lecture seule** : aucun ordre, virement ni appel en écriture vers une banque, un courtier ou une plateforme.
2. **Données locales** : montants et quantités ne quittent jamais la machine (ni GitHub, ni cloud, ni télémétrie, ni logs envoyés ailleurs).
3. **Seules requêtes sortantes** : prix publics par ticker/id, taux de change et indices publics (inflation Eurostat, D-045), taux de dépôt et d'emprunt publics d'Aave par chaîne et jeton (au plus une fois par jour, D-095), numéro de la dernière version publiée sur GitHub (au plus une fois par jour, seulement si l'utilisateur l'a accepté, D-129), sans montant, quantité ni adresse de portefeuille.
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
uv run python -m app.demo                      # crée data/demo.db : démo complète fictive, celle du profil Démo (D-140)
uv run python -m app.demo --grand              # crée data/demo-grand.db : 3 comptes, 30 lignes fictives
PATRIMOINE_DB=data/demo.db uv run python -m app   # même chose sur la base de démo
uv run python -m app.backup                     # sauvegarde la base dans data/sauvegardes/
uv run python -m app.backup --verifier          # relit la dernière copie de chaque base (D-114)
uv run python -m app.migrate                    # sauvegarde puis met à jour le schéma de chaque base (aussi fait au lancement, D-120)
uv run python -m app.profiles [sans-code <id>]  # liste les profils ; retire un code oublié
uv run python -m app.datadir [--choisir DOSSIER] # dossier des données ; copie vérifiée vers un autre (D-119)
uv run python -m app.version [--verifier-etiquette vX.Y.Z]  # version ; contrôle d'une étiquette de release (D-121)
uv run python -m app.diagnostic                 # diagnostic sans montant ni chemin, quand l'app ne démarre pas (D-132)
uv run --group build pyinstaller packaging/patrimoine.spec   # Windows : dist/Patrimoine/Patrimoine.exe, sans console (D-126) ; macOS : dist/Patrimoine.app (D-137)
uv run pytest                                  # tests
uv run ruff check . && uv run ruff format .    # lint + formatage
for f in web/*.js web/app/*.js; do node --check "$f"; done  # syntaxe du front (aussi en CI)
```

## Architecture
```text
app/updates.py     avis de mise à jour (D-129) : dernière release GitHub une fois par jour si accepté, choix dans data/reglages.json, rien n'est téléchargé
app/diagnostic.py  diagnostic à copier (D-132) : texte sans montant, nom, ticker ni chemin (tailles par tranche, erreurs réduites à type + lignes de app/), erreurs inattendues en mémoire ; GET /api/diagnostic (aussi sans profil), `python -m app.diagnostic`
app/outbound.py    ce qui sort de la machine (D-131) : liste des services joints (fait foi), crochet du client HTTP qui refuse toute autre adresse, compteur en mémoire ; GET /api/about
app/settings.py    carte Réglages (D-130) : reglages.json du dossier des données (verrou commun avec updates), .env prioritaire, clé CoinGecko jamais renvoyée, dossier de copie sans réseau
app/startpage.py   page de démarrage réglable (D-134) : fond pris dans une liste, mot d'accueil borné, image personnelle JPEG vérifiée dans data/accueil/fond.jpg ; clé `accueil` de reglages.json ; /api/start (lecture sans profil)
app/version.py     __version__ (source lue par l'app, = pyproject.toml et uv.lock), notes d'une version tirées de CHANGELOG.md (D-121)
app/main.py        FastAPI : application, contrôles de chaque requête (profil, nom de site, origine, en-têtes, cache), relevé des erreurs inattendues, branche les routeurs de app/api/ dans l'ordre, sert web/ ; aucune route ici
app/api/           routes de l'API, une famille par fichier (D-139), jamais d'import de app.main : deps.py (client HTTP, cookie de session), common.py (mises en forme communes, état hors ligne `offline_state`, ordre, sauvegarde avant suppression), machine.py (état, mises à jour, réglages, page de démarrage, Ce qui sort, diagnostic, fenêtre), profiles.py, accounts.py (comptes, actifs, valeurs saisies, vérification d'un relevé), operations.py, recurring.py, income.py, crypto.py, debts.py, aave.py (dépôts et emprunts), targets.py, tax.py, imports.py (import CSV), quotes.py (/api/quote, /api/tickers), portfolio.py (/api/portfolio, /api/history, filtre de lignes `Filter`, série de valeur `value_series`), performance.py (performance, comparaison, calendrier), benchmarks.py (liste de comparaison), watchlists.py, projection.py ; nouvelle route : dans le fichier de sa famille, nouvelle famille : un fichier + une ligne dans la liste de main.py
app/__main__.py    point d'entrée (options) ; app/launcher.py : verrou d'instance, port libre, navigateur, arrêt propre, sauvegarde horaire (D-118)
app/window.py      fenêtre d'application pywebview (Windows, macOS), repli sur le navigateur, taille gardée, actions du bouton « Fenêtre » (D-125, D-127)
app/console.py     Patrimoine.exe / .app sans console : journal.txt dans le dossier de données par défaut, erreur de démarrage en boîte de dialogue (D-126, D-137)
app/config.py      lecture de .env (sans écraser l'environnement ; à côté de Patrimoine.exe une fois empaqueté, APP_DIR) ; PRICE_TTL_MINUTES, clé CoinGecko... lus par app/settings.py (.env, puis carte Réglages)
app/datadir.py     dossier des données hors du code (D-119) : défaut par système, dossier choisi, copie vérifiée de l'ancien data/
app/db.py          connexion SQLite (PRAGMA foreign_keys=ON à chaque connexion ; `with db.connection() as conn` la ferme en sortant, jamais `with db.connect()`, D-141), base du profil de la requête (ContextVar), Decimal, migrations (user_version)
app/profiles.py    profils (D-098) : data/profils.json, une base par profil, code en empreinte scrypt, jetons de session en mémoire
app/schema.sql     schéma de référence (tables account, asset, operation, price, price_history, fx_rate, benchmark, recurring, target, category_target, watchlist, watchlist_item, defi_line, defi_rate, defi_check, debt, debt_balance, defi_borrow, defi_borrow_flow, defi_borrow_check, defi_reserve)
app/migrations/    NNN_nom.sql : passage d'une version de schéma à la suivante (app/migrate.py : au lancement, copie vérifiée avant, refus d'une base plus récente, D-120)
app/models.py      dataclasses du domaine (Account, Asset, Operation, Position)
app/schemas.py     validation Pydantic des entrées de l'API (tout format hérite de `Entree` : nombres et dates bornés, D-138) ; Decimal renvoyés en chaînes par `plain` ou `money`, jamais `str` (notation scientifique)
app/repository.py  SQL des comptes, actifs, opérations
app/portfolio.py   positions (PRU au prix moyen pondéré), conversion et total EUR, performance par période, fonctions pures
app/recurring.py   échéances des achats programmés, quantité proposée (fonctions pures)
app/analytics.py   TRI, volatilité, simulation d'indice, projection (fonctions pures)
app/defi.py        dépôts et emprunts Aave : intérêts estimés jour par jour au taux public, recalage par solde vérifié, facteur de santé (fonctions pures, D-095, D-108)
app/defi_sync.py   lignes suivies, taux en cache (un appel par jour), intérêts des mois clos enregistrés
app/debts.py       dettes : capital restant dû à mensualités constantes, recalage par capital saisi (fonctions pures, D-107)
app/custody.py     garde d'un compte crypto (plateforme / portefeuille personnel), comptes à l'étranger 3916 / 3916-bis (fonctions pures, D-104)
app/crypto.py      onglet Crypto : récompenses par jeton, type, mois ; placements (compte, jeton), rendement réalisé en jetons, revenu estimé (fonctions pures, D-092, D-106)
app/watchlist.py   listes de suivi : rendement d'un cours sur 1 jour à 5 ans (fonctions pures, D-072)
app/income.py      dividendes reçus, frais payés, coût des frais courants (TER) ; fonctions pures
app/rebalance.py   allocation cible : répartition d'un versement sans vente (fonction pure)
app/tax.py         repères fiscaux FR en estimation : plafond et 5 ans PEA, 8 ans AV, PER (plafond, sortie), plus-values CTO, crypto (portefeuille global)
app/life_insurance.py assurance-vie (D-144) : part de gains d'un rachat, versements d'avant ou d'après le 27/09/2017, seuil de 150 000 €, abattement après 8 ans (fonctions pures)
app/reconcile.py   comparaison d'un compte au relevé de la banque : écarts, causes, correction (D-063)
app/prices.py      cache des prix et taux dans SQLite (un seul cours en ligne gardé par actif, D-138), choix de la source, repli si panne ; clôtures quotidiennes Yahoo, cryptos comprises par leur ticker Yahoo
app/demo.py        remplit data/demo.db (démo complète) et data/demo-grand.db ; app/demo_complet.py : sept ans fictifs sur toutes les enveloppes, cours générés, tickers `DEMO~` jamais demandés en ligne (D-140)
app/valuations.py  valeurs saisies à la main (actifs sans cours public), conversion montant -> parts
app/backup.py      sauvegarde de la base (API backup de SQLite), du jour au lancement puis chaque heure, relue, rotation 14 j + 12 mois + 1/an, profils.json, copie hors de WSL (BACKUP_COPY_DIR, D-114)
app/providers/     une source par fichier (yahoo, coingecko, frankfurter, eurostat, aave), get_price(client, id) -> Price
app/crypto_tickers.py ticker Yahoo d'une crypto (table vérifiée, D-111)
app/tickers.py     ticker Yahoo d'un ISIN : table locale, puis recherche Yahoo sur clic (/api/tickers, D-083)
app/importers/     import CSV : csv_generique (lecture, correspondance des colonnes), preparation (comptes, actifs, doublons), un profil par plateforme reconnu aux en-têtes (trade_republic D-071, binance D-082) ; /api/import/preview puis /api/import (D-053)
web/diagnostic.js  fenêtre « Diagnostic » (copie, lien de signalement) et erreurs de la page relevées dès le chargement (D-132)
web/accueil.js     page de démarrage réglable (D-134) : pose le fond et le mot d'accueil avant profiles.js, tient la carte Apparence, réduit l'image choisie en JPEG dans la page
web/tutorial.js    tutoriel de premier lancement en 6 étapes (D-099), relançable depuis Réglages, carte Aide ; « Comment ça marche » et fenêtre « Ce qui sort de cet ordinateur » (D-131)
web/app/           la page de l'app, un fichier par partie de l'écran (D-139), scripts classiques qui partagent leurs fonctions : positions.js (listes de Gérer, positions, glisser-déposer), graphiques.js, performance.js, filtre.js (filtre de lignes, alerte du jour), comparaison.js (liste de comparaison, calendrier), visualisations.js, projection.js (comparaison à un indice, projection), pages.js (onglets, disposition), saisie.js (opérations, comptes, actifs), verification.js (relevé de la banque, suppression, état du serveur), reglages.js, import.js, programmes.js (achats programmés, valeurs saisies), revenus.js, crypto.js, cibles.js, fiscalite.js, dettes.js (dettes, emprunts Aave), demarrage.js (premier affichage, toujours en dernier)
web/               index.html (pages par onglets #apercu, #performance...), theme.js (thème auto, clair ou sombre posé sur `<html>` avant l'affichage, D-133), profiles.js (page de démarrage et choix du profil, puis charge les fichiers de l'app dans l'ordre de sa liste `APP_FILES`, D-098, D-139), app.js (socle : formats, API, messages, tri, questions, préférences), tips.js (infobulles « ? »), styles.css, logo.svg ; icônes Lucide en symboles SVG dans index.html (`<use href="#i-nom">`, D-133) ; web/vendor/ : Chart.js, chartjs-chart-treemap, police Inter et licence Lucide copiés (pas de CDN, D-117)
.github/ISSUE_TEMPLATE/ formulaires « Signaler un problème » (diagnostic obligatoire) et « Proposer une idée », faille en privé (D-132)
.github/workflows/ CI : tests sous Linux, Windows et macOS, lint et syntaxe du front à chaque PR ; release.yml : étiquette vX.Y.Z → contrôles, archive, Patrimoine.exe zippé, SHA256SUMS, page de release (actions figées par SHA, D-121) ; exe-windows.yml : construit et lance Patrimoine.exe (D-126) ; app-macos.yml : Patrimoine.app et son .dmg (D-137) ; dependabot.yml : dépendances chaque semaine en une PR, 7 j de recul sauf alerte (D-123)
.claude/           réglages Claude Code (D-079, D-113) : hooks garde-fou données réelles (bases des profils comprises) et ruff après édition, sous-agents, skills /avant-pr et nouvelle-migration
packaging/         Patrimoine.exe (D-126) et Patrimoine.app (D-137, signée ad hoc, .dmg) : recette PyInstaller patrimoine.spec, icônes .ico/.icns dessinées par icone.py, essai de la fenêtre en CI, licences tierces relevées par licences.py (D-128)
scripts/           lancement en un clic : patrimoine.sh (WSL), Patrimoine.bat (Bureau Windows via WSL), Patrimoine-sans-WSL.bat ; instantane_api.py : routes et réponses de l'API sur les bases de démo, à comparer avant et après une refonte (D-139)
tests/fixtures/    données FICTIVES uniquement
data/              ignoré par git : demo.db, demo-grand.db (les vraies données sont dans le dossier des données, D-119)
LICENSE            GNU GPL v3 ou ultérieure (D-128) ; README.md pour l'utilisateur, SECURITY.md pour signaler une faille
docs/roadmap.md    jalons ; docs/decisions.md : décisions et pourquoi ; docs/releases.md : sortir une version ; CHANGELOG.md : journal, « Non publié » complété par chaque PR visible
```

## Modèle (résumé, détail dans app/schema.sql)
- Dossier des données (D-119) : hors du code (`%LOCALAPPDATA%\Patrimoine`, `~/Library/Application Support/Patrimoine`, `~/.local/share/patrimoine`), ou choisi (`--choisir`, `PATRIMOINE_DATA_DIR`) ; l'ancien `data/` du code est copié et vérifié au premier lancement, jamais effacé. Ci-dessous, `data/` désigne ce dossier.
- Fenêtre (D-125) : pywebview (WebView2 / WebKit), seulement Windows et macOS (menus macOS en français, Cmd+Q range tout via `window.watch_app_quit`, D-137) ; sous Windows réglages de la page et cookie de session dans `data/fenetre/` (jamais copié ; magasin WebKit par défaut sous macOS) ; API en `Cache-Control: no-store` ; aucun `js_api` ; SmartScreen coupé ; `POST /api/window/focus` ramène la fenêtre devant ; pas de barre de menu : bouton « Fenêtre » de la page (`web/fenetre.js`, adresse `/?fenetre`), routes fixes `/api/window/browser|folder|zoom` sans profil ni paramètre (D-127).
- Avis de mise à jour (D-129) : question au premier lancement (bandeau), aucune requête avant la réponse ; `GET /api/updates` vérifie au plus une fois par jour (échec compris), `PUT` le choix, `POST /api/updates/check` « Vérifier maintenant » ; bandeau « Version X disponible » + lien vers la page de la release construit à partir du numéro validé ; carte « Alertes et mises à jour » de la page Réglages ; réglage de la machine, pas du profil.
- Page Réglages (D-133, roue dentée, `#reglages`, hors des onglets) : cartes Profils, Apparence (thème ; fond et mot d'accueil de la page de démarrage, D-134), Général, Alertes et mises à jour, Aide (Comment ça marche, tutoriel, Ce qui sort, diagnostic).
- Page de démarrage (D-134) : réglage de la machine dans `data/reglages.json` (clé `accueil`) ; `GET /api/start` et `GET /api/start/image` répondent sans profil ouvert, `PUT /api/start` (fond, texte), `PUT` et `DELETE /api/start/image` avec un profil ; image réduite et réencodée en JPEG par la page, 3 Mo au plus, `data/accueil/fond.jpg`, hors sauvegardes.
- Gérer (D-135) : quatre cartes. « Ajouter des opérations » (`data-card="nouvelle-operation"`) : sélecteur « Une par une » / « Importer un relevé », un panneau à la fois (`data-operation-mode`), choix gardé dans le navigateur ; ouverture d'un panneau depuis ailleurs par l'événement `operationmode` ou l'attribut `data-operation-open`. Puis Achats programmés, Comptes (avec la partie Dettes), Actifs.
- Réglages (D-130, carte « Général ») : `GET/PUT /api/settings`, réglage de la machine dans `data/reglages.json` (clé `reglages`) ; ordre : environnement/.env, carte, défaut ; un réglage de .env est grisé dans la carte.
- Profils (D-098) : une base par personne (`data/profils/<id>.db`, liste `data/profils.json`), choisie à chaque requête par un jeton de session (cookie HttpOnly) ; sans profils.json, base unique `data/patrimoine.db` ; `PATRIMOINE_DB` force une base unique. Une mémoire de processus qui décrit une base se range par `db.conn_key(conn)`. Code facultatif (anti-curiosité, la base n'est pas chiffrée). Réglages, carte Profils : renommer, exporter (son profil), restaurer (base vérifiée, nouveau profil), supprimer vers `data/corbeille/` ; profil Démo (`app.demo.fill_demo`, démo complète D-140 : une fonctionnalité nouvelle s'y ajoute, avec une ligne dans `tests/test_demo_complet.py`).
- Positions **calculées** à partir des opérations, jamais stockées.
- Espèces **calculées** (D-062) pour PEA, AV, PER ; **saisies** pour CTO, crypto, Autre (solde du jour + opérations suivantes, D-075). Calcul D-062 : champ « Versements » du compte + opérations rejouées dans l'ordre (versements, ventes, dividendes − achats, frais, retraits) ; un achat qui les rendrait négatives compte un versement supposé. Livret : solde saisi + opérations suivantes (D-041).
- Valorisation = quantité × dernier prix, convertie en EUR via `fx_rate` (1 EUR = `rate` devise) ; coût et plus-values en devise aux taux BCE du jour de chaque opération (`RateHistory`, D-081).
- Actif sans ticker ni id CoinGecko = **valeur saisie** (fonds euros, immobilier...) : saisi en montants (convertis en parts), valeur totale saisie à une date et rangée comme un prix `manuel`, aucun appel réseau (D-056). Livret = compte d'espèces seules, classe « Livrets ».
- Cache prix : rappeler une API seulement si le dernier prix a plus de 5 min (`PRICE_TTL_MINUTES`).
- Historique : clôtures quotidiennes dans `price_history`, complétées au plus une fois par jour (`/api/history`).
- Comparaison (carte de l'onglet Performance, D-133) et calendrier : liste `benchmark` (indices Yahoo, livrets à taux saisi calculés en local), `/api/benchmark`, `/api/calendar` (D-044).
- Visualisations (D-072) : carte du portefeuille (treemap, suit le filtre), listes de suivi (`watchlist`, tickers Yahoo seuls, cryptos comprises via `app/crypto_tickers.py`, CoinGecko en repli, D-111 ; `/api/watchlists`, « Mes lignes » calculée), rendements mensuels. Allocation cible dans « Projection ». Neuf onglets (D-133) : Vue d'ensemble, Performance, Visualisations, Projection et objectifs, Revenus et frais, Fiscalité, Rendements crypto, Positions et opérations, Gérer (sous 1 500 px de large, forme courte : la partie en `.tab-more` est masquée) ; ordre et masquage réglés par « Réorganiser » (D-073), gardés dans le navigateur, Gérer en dernier. Positions et opérations : un bloc par compte (D-073), un tableau à la fois (D-117). Classe d'actif `fonds` (OPCVM, UC).
- Inflation : indice Eurostat (IPCH France, table ECOICOP 2) en mémoire, un appel par jour, prolongé au taux saisi après le dernier mois publié (D-067) ; `/api/history` : argent net placé (achats − ventes, cadeaux exclus) et ce montant moins l'inflation lissée par année (D-110).
- Performance : `/api/performance` découpe cette série par jour/semaine/mois/année, en TWR (achats le matin, ventes le soir ; dividendes et frais comptés ; titres déjà détenus à leur valeur de marché, D-029, D-081). Filtre de lignes réglé dans Vue d'ensemble (`exclude`, `exclude_line=compte-actif`, `exclude_account`, D-069), suivi par Vue d'ensemble, Performance, Calendrier, Comparaison et Projection.
- Achats programmés : table `recurring` ; mensuel, trimestriel, hebdomadaire ou toutes les deux semaines (`bimensuel`, D-071) ; chaque échéance passée est proposée dans « Gérer » (`/api/recurring`), validé avec le prix réel ou ignoré, jamais enregistré seul (D-055).
- Achat offert (Saveback, Stockperk, cadeau TR) : `operation.offered`, ne sort pas des espèces (pas de versement supposé) ; l'achat entre au prix réel, frais compris, comme un apport : seule la hausse qui suit est un gain ; frais couverts par la part offerte hors « frais payés » (D-076, D-078). Impôt retenu d'un dividende : `operation.tax_withheld`.
- Onglet Crypto (D-092, schéma v17) : récompense = achat offert d'une crypto, typée par `operation.reward_kind` (earn, staking, airdrop, cashback, parrainage, autre) ; `/api/crypto/rewards` : valeur à réception et actuelle, par jeton/type/mois, rendement en jetons sur 12 mois. Import Binance : un gain par jeton, mois et type. Frais crypto dans cet onglet, hors de « Revenus et frais » (bourse seule).
- Onglet « Rendements crypto » (D-106, ancre `#crypto`) : placement = (compte, jeton) avec Earn/staking sur 12 mois ou ligne Aave ; rendement réalisé en jetons (jusqu'au dernier versement de moins de 35 jours), revenu estimé = capital × taux Aave du jour sinon réalisé ; rendement moyen pondéré par le capital ; cadeaux (airdrop, cashback, parrainage) dans les revenus, jamais dans un taux ; carte « Ne rapportent rien ».
- Dépôts Aave (D-095, schéma v18) : ligne (compte, jeton) reliée à un marché (`defi_line`), taux de dépôt du jour en cache (`defi_rate`) ; chaque mois clos = achat offert `earn` créé et recalculé tout seul (`source='aave'`, non modifiable à la main), mois en cours estimé ; « Vérifier » (`defi_check`) recale sur le solde lu dans Aave. Suivre une ligne = compte (ou nouveau « Aave <chaîne> ») + premier dépôt ; Déposer/Retirer (transfert depuis/vers un compte de l'app, sinon `solde_initial` / transfert hors de l'app, D-096). `/api/aave/...`, tableau « Mes placements » de l'onglet Crypto.
- Emprunts Aave (D-108, schéma v21) : `defi_borrow` + mouvements + dettes vérifiées, dette = même calcul que les dépôts au taux d'emprunt (`side='borrow'`) ; facteur de santé par marché = dépôts suivis × seuil (`defi_reserve`) ÷ dettes, alerte sous 1,5 ; dette comptée dans le patrimoine net ; `/api/aave/borrows`, carte « Emprunts Aave ».
- Comptes crypto (D-104, schéma v19) : `account.custody` = `exchange` (plateforme) ou `wallet` (portefeuille personnel, sans espèces), NULL à préciser ; `account.abroad` (tout compte) = à déclarer, liste 3916 / 3916-bis dans Fiscalité (jamais un portefeuille) ; devinés d'après nom et établissement (`app/custody.py`). Pas de suivi par adresse publique (règle 3). 
- Dettes (D-107, schéma v20) : `debt` (montant, taux, début, durée facultatifs) + `debt_balance` (capital saisi qui recale) ; hors positions, espèces et performance ; Patrimoine net = total (hors filtre) − capital restant dû, tuile de Vue d'ensemble (`debts_eur` de `/api/portfolio`), partie « Dettes » de la carte Comptes dans Gérer (D-135), `/api/debts`.
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
- Économie de tokens : ne jamais lire en entier `docs/decisions.md` (≈ 90 Ko) ni tous les fichiers de `web/app/` (≈ 285 Ko à eux tous) ; chercher avec `grep -n` puis lire la zone utile. Nouvelle décision : numéro suivant par `grep -n "^## D-" docs/decisions.md | tail -1`, ajout en fin de fichier.
- Schéma modifié : suivre la skill `nouvelle-migration`. Avant de pousser : `/avant-pr`.
- Sous-agents (`.claude/agents/`, D-084), à appeler sans redemander : `verificateur` (Haiku) pour
  tests + lint quand la sortie est longue ; `relecteur-calculs` (Opus) si un calcul financier
  change ; `gardien-confidentialite` (modèle du fil) si réseau, import, API ou fixtures changent ;
  `analyste-import` (modèle du fil) au début d'un profil de plateforme ; `capture-ui` (Haiku) pour
  montrer un changement de `web/` (il relève les erreurs de la console) ; `Explore` (Haiku,
  D-113) pour chercher dans le code. Un sous-agent part sans le contexte du fil : lui donner
  fichiers, décision et question précise. Pas de sous-agent pour une tâche de quelques lectures.

## Conventions du front (règles permanentes)
- **Fichiers de la page** (D-139) : `web/app.js` puis `web/app/*.js`, chargés dans l'ordre de `APP_FILES` (`web/profiles.js`). À son chargement, un fichier ne se sert que de ce que les fichiers précédents définissent ; une fonction dont d'autres fichiers ont besoin dès leur chargement va dans `web/app.js` ; tout appel qui lance un chargement de données au démarrage va dans `web/app/demarrage.js`. Nouveau fichier : l'ajouter à `APP_FILES` avant `demarrage.js` (vérifié par `tests/test_regles_projet.py`).
- **Tout tableau est triable par en-tête dès sa création** (demandé trois fois par l'utilisateur) : `<th data-sort="cle"><button class="sort">`, `bindSortable(id, colonnes numériques)` au chargement, puis `fillSortable(id, lignes, versLigne, texteVide)` au rendu (ordre par défaut dans `SORT_DEFAULTS`). Seules exceptions : listes à ordre choisi (Comptes, Actifs de « Gérer ») et formulaires en tableau.
- Toute nouvelle carte d'onglet porte `data-card="nom-stable"` (déplaçable par « Réorganiser », D-070, D-073) ; `class="card half"` pour une demi-largeur.
- Aucune bibliothèque chargée depuis un CDN (copie dans `web/vendor/`), aucune requête tierce à l'ouverture de la page.
- Formulaires d'actif et de compte : un champ propre à certaines classes ou types porte `data-for="etf action ..."` (D-072) ; un champ rempli en base reste visible.
- **Jetons seulement** (D-115, revus en D-133) : dans `web/styles.css`, couleurs, espacements, tailles de texte, rayons et ombres passent par les variables du haut du fichier (fond `--bg`, surfaces `--surface --surface-2 --raised --floating --field`, textes `--text --text-2 --text-3`, `--accent --ok --ko --warn` et leurs mélanges, `--s-1` à `--s-7`, `--t-xs` à `--t-3xl`, `--r-xs --r-sm --r-md --radius --r-lg --r-xl`, `--control`) ; aucune valeur en dur, aucun dégradé (sauf les fonds de la page de démarrage, jetons `--fond-*`, et le masque d'opacité de la barre du haut). Choix actif (onglet, segment) : pastille `--inverse` / `--on-inverse`. Boutons : un `<button>` sans classe est une action, bleu plein ; `class="secondary"` (ou `data-cancel`, `data-close`, `.chip`, bouton non-envoi dans un `<form>`) pour ce qui l'accompagne, bleu teinté ; jamais deux boutons pleins côte à côte. Ombres : `--shadow-sm` pour une carte, `--shadow-button` pour un bouton, `--shadow` et `--shadow-lg` pour ce qui flotte. Thème : `:root[data-theme="dark"]`, jamais `prefers-color-scheme` dans la feuille de style. Les `--series-*` sont la palette des graphiques.
- **Textes courts, détail en infobulle** (D-091) : une ligne visible au plus sous un titre ; le complément va dans un bouton `<button type="button" class="tip" data-tip="…" aria-label="Plus d'infos">?</button>` placé juste après le texte qu'il explique (après le libellé d'un champ, après le titre d'une carte), jamais dans un coin. Depuis `app.js` : `makeTip(texte)`. Les longues règles fiscales restent dans un `<details class="note">`. Espacements (padding, margin, gap) par les jetons `--s-*`.
