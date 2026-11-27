# Journal des changements

Les changements visibles de Patrimoine, version par version (D-121). Numéros en version
sémantique `majeur.mineur.correctif` : correctif pour une réparation, mineur pour une nouveauté,
majeur pour un changement qui casse quelque chose. 0.x tant que l'app n'est pas publiée.

Chaque PR qui change ce que voit l'utilisateur ajoute une ligne sous « Non publié ». Pour sortir
une version, ce titre devient `## X.Y.Z` (voir `docs/releases.md`) ; le texte de la section devient
la page de la release sur GitHub.

## Non publié

### Application

- Mac : une nouvelle version remplace l'ancienne sans bloquer (l'icône rebondissait dans le Dock sans fenêtre) : chaque build porte son propre numéro ; parade dans le README pour les anciennes versions (D-152).
- Retouches : messages d'erreur en français, qui disent quoi faire ; Entrée passe au champ suivant et valide le formulaire au dernier ; cadenas sur la ligne du profil protégé dans le menu du profil ; boutons de la fenêtre dans la carte Aide ; les douze couleurs dans la démo, sur ses douze plus grosses lignes (D-151) ; Mac : double flèche et croix plus grandes, sans contour bleu, listes de même hauteur que les champs (D-150).
- Graphiques : au survol, le carré de couleur de chaque valeur est plein, sans contour, sur tous les graphiques ; un clic sur le graphique Plus/moins-values l'agrandit ; le titre du total indique « (filtre actif) » au lieu de « des lignes cochées » (D-149).
- Corrections : le total au centre du camembert suit le choix Financier / Brut / Net ; les dates de Performance et de Comparaison se limitent à la période qui a des données, et une date impossible revient à la dernière valide ; un bouton bloqué ne montre plus le sablier (« Importer ») ; les champs d'un formulaire restent alignés et de même hauteur (D-148).
- Vue d'ensemble : le sélecteur Financier / Brut / Net est en haut à droite de la carte, le montant principal est centré en hauteur (D-147).
- Filtre des lignes : l'icône de la barre du haut n'a plus de pastille ; le détail reste dans son infobulle (D-146).
- Évolution : la courbe « Placé moins l'inflation » suit chaque mois l'inflation officielle sur 12 mois, et l'infobulle affiche ce taux, qui change donc d'un mois à l'autre (D-145).
- Retouches de l'interface : nouvelle phrase d'accroche et cadenas sur les profils à code, anneau de répartition avec le total au centre, raccourcis de période en noir et blanc (6 mois ajouté), légendes gains/pertes, boutons d'action en bleu plein, type d'une liste de suivi choisi à sa création, « Ajouter des opérations » en deux parties comme Comptes, actions de la fenêtre déplacées dans Réglages > Général, douze couleurs au choix pour un actif (schéma v22).
- Vue d'ensemble : le chiffre principal s'appelle « Patrimoine financier » et passe d'un clic à
  « Brut » (tout ce qui est détenu) ou « Net » (brut moins les dettes). Le financier laisse de
  côté l'immobilier, les SCPI, les livrets et l'argent des comptes bancaires ; les tuiles et la
  répartition suivent le choix, gardé d'une visite à l'autre (D-143).
- Fiscalité, carte Assurance-vie plus complète : gains de chaque contrat, abattement restant de
  l'année (4 600 € ou 9 200 € en couple), plus grand rachat sans impôt sur le revenu, impôt si
  tout était racheté (versements d'avant ou d'après le 27/09/2017, seuil de 150 000 €), rachats
  de l'année avec leur part de gains, repère de transmission (D-144).
- Profil Démo complet : sept ans d'opérations fictives sur toutes les enveloppes (PEA, compte-titres
  en dollars, assurance-vie, PER, livrets, immobilier, or, prêts), achats programmés à valider,
  dividendes, ventes, cibles, listes de suivi, cryptos sur une plateforme et un portefeuille,
  récompenses, dépôts et emprunt Aave. « Recréer » dans Réglages, carte Profils, pour
  l'obtenir. Les lignes de la démo ne sont plus demandées en ligne (D-140).
- Windows : « Recréer » le profil Démo, ou supprimer un profil juste après l'avoir utilisé,
  pouvait échouer (« accès refusé ») ; corrigé.
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
- `Patrimoine.app` pour Mac à puce Apple : une image disque `.dmg`, glisser l'app dans
  Applications. Pas encore notarisée par Apple : au premier lancement, « Ouvrir quand même » dans
  Réglages Système > Confidentialité et sécurité (README). Menus de macOS en français ; quitter
  par Cmd+Q arrête proprement le serveur et la sauvegarde en cours (D-137).

- Avis de mise à jour : au premier lancement, Patrimoine demande s'il doit prévenir quand une
  nouvelle version sort. Si oui, il demande à GitHub le numéro de la dernière version une fois par
  jour, sans rien envoyer d'autre, et affiche un bandeau avec le lien vers la page de
  téléchargement. Rien n'est installé tout seul. Carte « Mises à jour » dans Gérer : changer
  d'avis, vérifier maintenant (D-129).
- Carte « Réglages » dans Gérer : cache des prix, sauvegardes gardées, verrouillage des profils,
  dossier où recopier les sauvegardes (autre disque ou clé USB, jamais un dossier réseau ni
  synchronisé) et clé CoinGecko, sans fichier `.env` ; un réglage écrit dans `.env` passe avant
  (D-130).
- « Comment ça marche » : quatre repères (où sont les données, sauvegardes, mises à jour, ce que
  l'on peut changer), lus sur l'ordinateur, dans le tutoriel (nouvelle étape 2) et dans une carte
  de Gérer. Fenêtre « Ce qui sort de cet ordinateur » : chaque service joint par l'app, ce qui lui
  est envoyé, quand, comment s'en passer, et le nombre de requêtes depuis le lancement (D-131).
- « Signaler un problème » (Gérer, et l'écran de choix du profil) : « Copier le diagnostic » montre
  puis copie un texte à coller dans le signalement (version, système, état des bases, sauvegardes,
  réglages, services joints, dernières erreurs), sans montant, nom, ticker ni chemin. Une erreur
  inattendue du serveur l'indique dans son message. `uv run python -m app.diagnostic` donne la
  même chose quand l'app ne démarre pas (D-132).
- Nouvelle interface (D-133) : cartes blanches sur fond gris, barre du haut en une ligne (logo,
  onglets au milieu, filtre, actualiser, réglages et profil à droite), icônes, boutons bleus,
  page plus large (jusqu'à 1 840 px), page de démarrage avec un mot d'accueil. Thème au choix : automatique, clair ou sombre. En thème sombre, le halo
  blanc autour des infobulles et des panneaux a disparu.
- Neuf onglets : « Comparaison » rejoint Performance ; dans une fenêtre étroite, quatre noms
  d'onglets se raccourcissent pour rester sur une ligne. Nouvelle page Réglages (roue
  dentée) : Profils, Apparence, Général, Alertes et mises à jour, Aide ; les cartes citées plus
  haut (Réglages, Mises à jour, Comment ça marche, Signaler un problème) s'y trouvent. « Gérer »
  ne garde que ce qui se saisit : opérations, import, achats programmés, comptes, actifs, dettes.
- Page de démarrage réglable (Réglages, carte Apparence, D-134) : fond au choix parmi sept, ou
  une image à toi, réduite et gardée dans le dossier des données sans ses métadonnées ; mot
  d'accueil modifiable. Commun à tous les profils de l'ordinateur.
- Gérer en quatre cartes (D-135) : « Ajouter des opérations » réunit la saisie une par une et
  l'import d'un relevé, au choix par un sélecteur ; les dettes se rangent dans la carte Comptes.
- Textes des onglets allégés (D-136) : une ligne visible au plus sous un graphique ou un tableau,
  le détail dans l'infobulle « ? » ; les mentions de prudence (pas un conseil fiscal, simulation
  et non prévision, aucun ordre passé) restent visibles.
- Comparaison à l'Euro Stoxx 50 réparée : Yahoo ne donne plus la devise de cet indice, l'app la
  connaît désormais (euro). Un autre indice sans devise affiche une erreur claire.
- Audit avant publication (D-138). Chiffres corrigés : un titre coté à Londres en pence valait
  cent fois trop ; une crypto connue de Yahoo a maintenant un cours par jour (Évolution et
  Performance ne mettent plus tout son gain sur le dernier jour, et l'aide au formulaire 2086
  estime la valeur du portefeuille au cours du jour de la cession) ; dividendes et frais en
  devise convertis au taux du jour de l'opération, comme dans Fiscalité ; plus-values d'un PEA
  reçu d'un autre compte ; plus-value latente de titres transférés entre deux comptes-titres
  (leur prix de revient était compté pour zéro) ; dette dont le capital restant dû est saisi
  après la fin prévue.
- Bugs corrigés : très petites quantités de crypto affichées « 1.2E-7 » ; un nombre démesuré ou
  une date de l'an 202 saisis par erreur bloquaient toutes les pages (refusés désormais, à la
  saisie comme à l'import) ; la base grossissait d'un cours par actif toutes les 5 minutes ; la
  comparaison au relevé oubliait les titres reçus par transfert ; quand Yahoo ne répond pas, la
  page n'attend plus dix secondes par titre.

### Publication

- Code ouvert sous licence GNU GPL v3 ou ultérieure ; `Patrimoine.exe` livré avec sa licence et
  celles des composants qu'il embarque (`LICENCES-TIERCES.txt`). README pour un nouvel
  utilisateur (avertissement, installation, données, ce qui sort de la machine) et `SECURITY.md`
  pour signaler une faille en privé. Un « ? » à côté de la version rappelle la licence et que les
  chiffres sont des estimations, ni conseil en investissement ni conseil fiscal (D-128).
- Modèles de signalement sur GitHub : « Signaler un problème » (avec le diagnostic) et « Proposer
  une idée », qui rappellent de ne jamais joindre de vraies données ; une faille se signale en
  privé (D-132).

### Sécurité

- L'app refuse toute requête vers une adresse qui n'est pas déclarée dans la liste « Ce qui sort
  de cet ordinateur », ou qui n'est pas en https (D-131).
- Code d'un profil : cinq essais ratés ferment le profil une minute, aussi pour le supprimer ou
  changer son code (avant, seulement pour l'ouvrir) ; fichier des réglages lisible par son seul
  propriétaire (D-138).
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
