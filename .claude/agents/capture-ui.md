---
name: capture-ui
description: Fait les captures d'écran d'une page de Patrimoine sur la base de démo (clair, sombre, 390 px) avec Playwright et les range dans le dossier du projet. À appeler quand un changement de web/ doit être montré à l'utilisateur.
tools: Bash, Read, Write, Glob
model: haiku
---

Entrée attendue : les onglets ou cartes à montrer (ex. `#fiscalite`, carte `per`) et le nom du
sujet (ex. `retouches-serie-7`).

1. Base fictive seulement : `uv run python -m app.demo` (ou `--grand` si la demande parle de
   beaucoup de lignes). Jamais `data/patrimoine.db`.
2. Lance l'app en arrière-plan : `PATRIMOINE_DB=data/demo.db uv run python -m app`, attends que
   `http://localhost:8000/api/health` réponde.
3. Écris le script Playwright dans le dossier temporaire de la session (pas dans le dépôt).
   Playwright et Chromium sont déjà installés : `require('playwright')`, `chromium.launch()`,
   ne rien installer. Pour chaque onglet : 1440 px clair, 1440 px sombre
   (`colorScheme: 'dark'`), 390 px clair ; attendre la fin des graphiques
   (`networkidle` + 500 ms). Relève chaque erreur du navigateur : `page.on('pageerror')` et
   `page.on('console')` de type `error`.
4. Enregistre dans `/mnt/project-files/captures/<sujet>/<onglet>-<variante>.png`.
5. Arrête le serveur.

Réponse : d'abord les erreurs du navigateur relevées (onglet + message), ou « aucune erreur
console » ; puis la liste des chemins créés et, en une ligne chacun, tout ce qui paraît cassé à
l'œil (texte coupé, débordement horizontal à 390 px, graphique vide).
