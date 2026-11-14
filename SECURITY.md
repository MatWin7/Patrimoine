# Sécurité

Patrimoine garde des données financières personnelles : une faille compte plus qu'une nouveauté.

## Signaler une faille

Utilisez le **signalement privé** de GitHub : onglet **Security** du dépôt, puis
**Report a vulnerability**. N'ouvrez pas d'issue publique et ne joignez jamais de vraies données
(relevé, base, capture avec des montants) : une base de démo (`uv run python -m app.demo`) suffit à
reproduire.

Décrivez ce qui est touché (version, système), comment le reproduire et ce qu'un attaquant
pourrait obtenir. Vous recevrez une réponse dès que possible ; c'est un projet tenu sur du temps
libre, sans délai garanti.

## Ce que l'app promet

- Lecture seule : aucun ordre, virement ni appel en écriture vers une banque, un courtier ou une
  plateforme.
- Aucun montant, aucune quantité, aucune adresse de portefeuille envoyés sur le réseau ; seules
  sorties : cours, taux et indices publics, et, si vous l'acceptez, le numéro de la dernière
  version publiée (voir le README).
- L'app ne se met jamais à jour toute seule : elle prévient qu'une version existe, vous la
  téléchargez et vérifiez son empreinte vous-même.
- Serveur lié à `127.0.0.1`, qui ne répond qu'aux noms `localhost` et `127.0.0.1` ; l'API refuse
  les requêtes venues d'un autre site ; politique de sécurité du contenu (CSP) stricte.

Tout écart à ces règles est une faille, à signaler comme ci-dessus.

## Limites connues

- La base n'est pas chiffrée : quiconque lit vos fichiers lit vos données. Le code d'un profil
  n'est qu'un anti-curiosité dans l'app. Chiffrement prévu.
- Sous Windows, le moteur de la fenêtre (WebView2, de Microsoft) a sa propre télémétrie, réglée par
  les « Diagnostics » de Windows ; elle ne contient aucune donnée de l'app. SmartScreen y est coupé.
- `Patrimoine.exe` n'est pas encore signé : vérifiez l'empreinte SHA-256 publiée avec chaque
  release.

Seule la dernière version reçoit des correctifs.
