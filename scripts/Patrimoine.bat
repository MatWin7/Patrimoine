@echo off
rem Double-clic : lance Patrimoine dans WSL et ouvre le navigateur.
rem A copier sur le Bureau Windows. Fermer cette fenetre arrete l application.
title Patrimoine
wsl.exe --cd ~ -e bash -lc "~/patrimoine/scripts/patrimoine.sh"
if errorlevel 1 pause
