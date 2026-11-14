#!/usr/bin/env bash
# Lance Patrimoine et ouvre la page dans le navigateur de Windows (WSL) ou de Linux.
# Si l'app tourne déjà, l'app rouvre seulement la page. Fermer la fenêtre arrête l'app.
# Tout le travail (port libre, instance déjà ouverte, navigateur) est fait par `python -m app`
# (D-118) ; ce script ne fait que se placer dans le dossier et trouver uv.
# Usage : ~/patrimoine/scripts/patrimoine.sh (ou double-clic sur Patrimoine.bat côté Windows).
set -euo pipefail

cd "$(dirname "$0")/.."
export PATH="$HOME/.local/bin:$PATH" # uv, même si le profil du shell n'est pas chargé
exec uv run python -m app "$@"
