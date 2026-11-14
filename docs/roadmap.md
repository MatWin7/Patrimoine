# Roadmap

Un jalon = une branche = une PR. On ne commence pas le suivant tant que le précédent ne sert pas.

- [x] **1. Cadrage** : brief validé, `CLAUDE.md`, roadmap, décisions, schéma SQL (`app/schema.sql`), `.gitignore`.
- [x] **2. Squelette** : `pyproject.toml` (uv), FastAPI `/api/health` lié à 127.0.0.1, `app/db.py` crée la base depuis le schéma, un test vert, ruff configuré.
- [x] **3. Saisie manuelle** : formulaires compte / actif / opération (achat, vente, solde initial), suppression d'une opération, positions et PRU calculés, total au dernier prix saisi, base de démo fictive (`python -m app.demo`).
- [x] **4. Prix automatiques** : appel HTTP direct à Yahoo (D-019) + CoinGecko + Frankfurter, cache 5 min, conversion EUR, bouton « Actualiser les cours », actualisation automatique, modification et suppression des comptes et actifs, plus-value en %, variation du jour, test du ticker, sauvegarde de la base (avancée du jalon 8).
- [x] **5. Import CSV** : CSV générique avec correspondance des colonnes, aperçu, actifs reconnus, doublons évités, tout ou rien (D-053). Profils par plateforme : Trade Republic (D-071) ; Binance à venir. Modèle de tableur à remplir et collage depuis Excel (D-054).
- [x] **6. Graphiques** : répartition par classe (espèces comprises), évolution de la valeur des titres et du montant investi depuis le premier achat (clôtures Yahoo stockées, schéma v3), plus-values latentes par position. Chart.js copié dans `web/vendor/`.
- [ ] **7. Personnalisation** : en grande partie couvert par les pages par onglets et les réglages gardés dans le navigateur (D-030, D-031). Reste possible : masquer ou réordonner les blocs de la vue d'ensemble, page d'accueil au choix. Gain jugé faible (D-035).
- [x] **8. Confort** : sauvegarde automatique quotidienne au lancement avec rotation (D-032), lancement en un clic depuis Windows (D-033), alerte de variation du jour (D-034). Sauvegarde manuelle déjà livrée au jalon 4.

- [x] **Performance par période** (hors jalon) : jour, semaine, mois, année, dates de début et de fin réglables, gain, performance pondérée par le temps, cumul, baisse max (D-029). « Gérer » en colonne à gauche (D-030).
- [x] **Analyses** (hors jalon) : pages par onglets, TRI, volatilité, comparaison à un indice, calendrier mois × années, apports / gains, projection vers un objectif, export CSV (D-031).
- [x] **Espèces** (hors jalon) : solde espèces par compte, première migration de schéma (v2).
- [x] **Versements et retraits** (hors jalon) : espèces calculées à partir des opérations, « Modifier » une opération, page « Gérer » en cartes, tableaux triables (schéma v4).
- [x] **Inflation** (hors jalon) : courbe « Investi moins l'inflation » sur l'évolution, indice des prix Eurostat (D-045).
- [x] **Performance par ligne** (hors jalon) : puces pour retirer une ligne du calcul de la performance (D-046).
- [x] **Beaucoup de lignes** (hors jalon) : répartition par classe, ligne, compte ou devise, 7 plus grosses parts + « Autres », poids et recherche dans les positions, démo de 30 lignes (D-051), couleur choisie par actif (schéma v5, D-052).
- [x] **Achats programmés** (hors jalon) : investissement régulier (mois, trimestre ou semaine), chaque échéance proposée à valider avec le prix réel ou à ignorer, rien d'enregistré tout seul (schéma v6, D-055).
- [x] **Enveloppes** (hors jalon) : assurance-vie, PER, livrets, immobilier ; actifs sans cours public à valeur saisie, saisis en montants (schéma v7, D-056).
- [x] **Revenus, cible, fiscalité, frais** (hors jalon) : dividendes et frais saisis, rendement sur PRU (D-057) ; allocation cible par ligne et répartition du prochain versement (D-058) ; plafond et 5 ans du PEA, 8 ans de l'assurance-vie, plus-values du compte-titres (D-059) ; frais courants des fonds (D-060). Schéma v8.
- [x] **Catégories** (hors jalon) : une catégorie personnelle par actif (la classe à défaut), répartition par catégorie, cibles par catégorie avec achat sur la plus grosse ligne (schéma v9, D-061).
- [x] **Versements** (hors jalon) : espèces déduites des versements, un achat sans versement fait monter les versements, Fiscalité en trois cartes identiques (D-062).
- [x] **Vérifier un compte** (hors jalon) : espèces et quantités comparées au relevé de la banque, correction proposée, rien d'enregistré (D-063).
- [x] **Fiscalité du PER** (hors jalon) : versements de l'année face au plafond de l'avis d'impôt, impôt économisé, impôt et net si sortie en capital, mise en garde à faible tranche marginale (D-064) ; carte PEA en plus-values latentes et impôt au retrait (D-065).
- [x] **Fiscalité crypto** (hors jalon) : ventes en euros au portefeuille global (aide au formulaire 2086), échanges crypto contre crypto non imposables, seuil de 305 €, taux du prélèvement forfaitaire par année (31,4 % dès 2026) (D-066). Schéma v10.

## Plus tard (hors jalons)
- Dettes (crédit immobilier) et patrimoine net.
- Clé API plateforme crypto en lecture seule (jamais de droit trading ou retrait).
