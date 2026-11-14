@echo off
rem Double-clic : lance Patrimoine directement sous Windows (sans WSL) dans sa fenetre (D-125).
rem Il faut uv installe sous Windows (https://docs.astral.sh/uv/). Fermer la fenetre de l app l arrete.
rem A placer dans le dossier scripts\ du projet ; un raccourci sur le Bureau suffit ensuite.
title Patrimoine
cd /d "%~dp0.."
uv run python -m app
if errorlevel 1 pause
