"""Patrimoine.exe sans console (D-126) : journal local et message d'erreur à l'écran.

Lancé par Patrimoine.exe, le programme n'a pas de fenêtre noire : sys.stdout et sys.stderr
valent None. Les messages du lanceur (dossier des données, bases, sauvegardes) et ceux du
serveur vont alors dans un journal, un simple fichier texte dans le dossier de données par
défaut de l'utilisateur. Il n'en sort jamais (règle 2) et ne contient aucun montant : les
messages n'affichent que des chemins, des nombres d'opérations et des erreurs.

Une erreur qui empêche le démarrage (dossier des données introuvable, base plus récente que
l'app...) serait sinon invisible : elle est montrée dans une boîte de dialogue Windows.
"""

from __future__ import annotations

import sys
from datetime import datetime
from pathlib import Path
from typing import IO

LOG_NAME = "journal.txt"
OLD_LOG_NAME = "journal-precedent.txt"
MAX_LOG_BYTES = 1_000_000  # au-delà, le journal repart de zéro (l'ancien est gardé à côté)


def needs_log() -> bool:
    """Vrai sans console (Patrimoine.exe) : print() n'afficherait rien nulle part."""
    return sys.stdout is None or sys.stderr is None


def open_log(folder: Path) -> IO[str]:
    """Journal ouvert en ajout. Trop gros, il est d'abord mis de côté ; s'il est ouvert par une
    instance déjà lancée, Windows refuse le renommage et l'on écrit simplement à la suite."""
    folder.mkdir(parents=True, exist_ok=True)
    if sys.platform != "win32":
        folder.chmod(0o700)  # comme le dossier des données : lisible par le seul utilisateur
    path = folder / LOG_NAME
    try:
        if path.stat().st_size > MAX_LOG_BYTES:
            path.replace(folder / OLD_LOG_NAME)
    except OSError:
        pass
    # Ligne par ligne (buffering=1) : un arrêt brutal ne perd pas les derniers messages.
    return open(path, "a", encoding="utf-8", errors="replace", buffering=1)


def redirect_to_log(folder: Path, version: str) -> Path | None:
    """Sans console, envoie stdout et stderr dans le journal. Renvoie son chemin (None si une
    console existe, ou si le journal ne peut pas être ouvert : l'app démarre quand même)."""
    if not needs_log():
        return None
    try:
        stream = open_log(folder)
    except OSError:
        return None
    sys.stdout = sys.stderr = stream
    print(f"--- {datetime.now():%Y-%m-%d %H:%M:%S} · Patrimoine {version} ---")
    return folder / LOG_NAME


def startup_error_text(errors: list[str], log: Path | None) -> str:
    """Texte de la boîte de dialogue : les erreurs, puis où lire le journal."""
    lines = ["Patrimoine n'a pas pu démarrer.", "", *errors]
    if log is not None:
        lines += ["", f"Détails : {log}"]
    return "\n".join(lines)


def alert(text: str) -> bool:
    """Boîte de dialogue « erreur » de Windows (rien ailleurs). False si elle n'a pas pu
    s'afficher. MessageBoxW est une fonction du système : aucune dépendance ajoutée."""
    if sys.platform != "win32":
        return False
    try:
        import ctypes

        style = 0x10 | 0x40000  # MB_ICONERROR | MB_TOPMOST : icône d'erreur, au premier plan
        ctypes.windll.user32.MessageBoxW(None, text, "Patrimoine", style)
    except (AttributeError, OSError):
        return False
    return True
