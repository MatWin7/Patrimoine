# Journal des changements

Les changements visibles de Patrimoine, version par version (D-121). Numéros en version
sémantique `majeur.mineur.correctif` : correctif pour une réparation, mineur pour une nouveauté,
majeur pour un changement qui casse quelque chose. 0.x tant que l'app n'est pas publiée.

Chaque PR qui change ce que voit l'utilisateur ajoute une ligne sous « Non publié ». Pour sortir
une version, ce titre devient `## X.Y.Z` (voir `docs/releases.md`) ; le texte de la section devient
la page de la release sur GitHub.

## Non publié

### Application

- Patrimoine s'ouvre dans sa propre fenêtre sous Windows et macOS, et non plus dans un onglet du
  navigateur ; fermer la fenêtre arrête l'app. Un second lancement ramène la fenêtre devant.
  Taille, préférences de la page et profil ouvert sont gardés d'un lancement à l'autre. Bouton
  « Fenêtre » (⋯) de la barre du haut, sans barre de menu : recharger la page, l'ouvrir dans le
  navigateur, zoom à 100 %, montrer le dossier des données ; sous Windows, le zoom s'affiche dans
  le titre de la fenêtre (D-125, D-127).
- `--navigateur` pour garder l'ancien fonctionnement ; sous Linux, WSL ou sans moteur web
  (WebView2), le navigateur s'ouvre comme avant.
- `Patrimoine.exe` pour Windows : un dossier zippé à dézipper puis lancer par double-clic, sans
  installer Python ni ouvrir de console. Les données restent dans leur dossier, d'une version à
  l'autre ; messages dans un journal, erreur de démarrage montrée à l'écran (D-126).

### Publication

- Code ouvert sous licence GNU GPL v3 ou ultérieure ; `Patrimoine.exe` livré avec sa licence et
  celles des composants qu'il embarque (`LICENCES-TIERCES.txt`). README pour un nouvel
  utilisateur (avertissement, installation, données, ce qui sort de la machine) et `SECURITY.md`
  pour signaler une faille en privé. Un « ? » à côté de la version rappelle la licence et que les
  chiffres sont des estimations, ni conseil en investissement ni conseil fiscal (D-128).

### Sécurité

- `opentelemetry-api` 1.45.2 (alerte Dependabot) ; télémétrie native de FastAPI coupée d'office
  (D-122).
- Dépendances à jour (FastAPI 0.162, Starlette 1.10, httpx2 2.16...) et revues chaque semaine
  par Dependabot, avec 7 jours de recul sauf alerte de sécurité (D-123).

## 0.1.0

Première version numérotée : tout ce qui a été construit jusqu'ici.

### Suivi

- Comptes (PEA, CTO, assurance-vie, PER, livrets, crypto, autres), actifs cotés, valeurs saisies
  à la main (fonds euros, immobilier) et dettes ; positions et prix de revient calculés à partir
  des opérations.
- Cours publics actualisés (Yahoo, CoinGecko, taux de change BCE), avec cache et repli en cas de
  panne ; aucun montant ne quitte la machine.
- Vue d'ensemble, positions, opérations, performance par période, calendrier, comparaison à un
  indice, projection et allocation cible, revenus et frais, repères fiscaux, listes de suivi,
  rendements crypto et dépôts ou emprunts Aave.
- Import CSV générique et profils Trade Republic et Binance ; achats programmés.

### Application

- Profils séparés, avec code facultatif ; tutoriel de premier lancement.
- Lancement sous Windows, macOS ou Linux, avec ou sans WSL ; port libre, une seule instance.
- Données dans un dossier hors du code ; sauvegardes automatiques relues et vérifiables.
- Mise à jour du schéma de chaque base au lancement, après une copie vérifiée.
- Numéro de version affiché dans la barre du haut et renvoyé par `/api/health`.
