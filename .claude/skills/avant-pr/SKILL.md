---
name: avant-pr
description: Vérifications à faire avant de pousser une branche ou d'ouvrir une PR sur Patrimoine (tests, lint, règles du front, décisions, confidentialité). À lancer avec /avant-pr à la fin d'un jalon ou d'une série.
disable-model-invocation: true
---

# Avant de pousser ou d'ouvrir la PR

Passer chaque point et dire à l'utilisateur ceux qui ne sont pas remplis.

1. **Base** : la branche part du dernier `main` (`git fetch origin main` puis rebase si besoin, en
   gardant les textes que l'utilisateur a modifiés à la main en cas de conflit).
2. **Qualité** : `uv run pytest -q`, `uv run ruff check .`, `uv run ruff format --check .`.
3. **Front** (si `web/` change) :
   - tout nouveau tableau triable (`data-sort`, `bindSortable`, `fillSortable`, `SORT_DEFAULTS`) ;
   - toute nouvelle carte avec `data-card="nom-stable"` ;
   - aucune URL externe, aucune bibliothèque hors `web/vendor/` ;
   - vu en clair et en sombre, et à 390 px de large.
4. **Confidentialité** : `git diff origin/main --stat` puis relire le diff ; aucun montant,
   ISIN de position ou nom de compte réel de l'utilisateur, captures faites sur `data/demo.db`.
5. **Sécurité** : `/security-review` sur le diff si la PR touche `app/main.py`, `app/importers/`,
   `app/providers/` ou une route qui écrit ou supprime.
   Relecture indépendante : sous-agent `relecteur-calculs` si `app/portfolio.py`, `analytics.py`,
   `income.py`, `tax.py`, `rebalance.py`, `reconcile.py` ou `app/importers/` changent ;
   `gardien-confidentialite` si réseau, import, routes API, `web/` ou `tests/fixtures/` changent.
6. **Documentation** : une décision `D-0xx` dans `docs/decisions.md` (numéro suivant :
   `grep -n "^## D-" docs/decisions.md | tail -1`, ajout en fin de fichier) ; `CLAUDE.md` à jour
   si l'architecture, le modèle ou une commande change ; `docs/roadmap.md` si un jalon avance.
   Changement visible pour l'utilisateur : une ligne sous `## Non publié` de `CHANGELOG.md` (D-121).
7. **Migration** : si `app/schema.sql` change, la skill `nouvelle-migration` a été suivie.
8. **Feu vert** : ne pousser et n'ouvrir la PR qu'après l'accord de l'utilisateur, vers `main`.
