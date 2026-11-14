"""Garde-fou Claude Code (.claude/hooks/garde_donnees.py) : vraies bases hors de portée."""

import json
import subprocess
import sys
from pathlib import Path

import pytest

HOOK = Path(__file__).parent.parent / ".claude" / "hooks" / "garde_donnees.py"


def code_retour(outil: str, entree: dict) -> int:
    appel = json.dumps({"tool_name": outil, "tool_input": entree})
    fini = subprocess.run(
        [sys.executable, str(HOOK)], input=appel, capture_output=True, text=True, check=False
    )
    return fini.returncode


@pytest.mark.parametrize(
    "chemin",
    [
        "data/patrimoine.db",
        "data/sauvegardes/patrimoine-2026-01-01.db",
        "data/profils/alice.db",
        "data/profils.json",
        "data/corbeille/alice-2026-01-01.db",
        "/mnt/d/Patrimoine-sauvegardes/alice-auto-2026-01-01-090000.db",
        "/home/alice/.local/share/patrimoine/profils.json",
        "C:\\Users\\Alice\\AppData\\Local\\Patrimoine\\profils.json",
        "/Users/alice/Library/Application Support/Patrimoine/sauvegardes",
        "~/Library/Application\\ Support/Patrimoine/profils.json",
        "%LOCALAPPDATA%\\Patrimoine\\profils.json",
        "$XDG_DATA_HOME/patrimoine/profils.json",
        ".env",
    ],
)
def test_vraies_donnees_refusees(chemin):
    assert code_retour("Read", {"file_path": chemin}) == 2
    assert code_retour("Bash", {"command": f"sqlite3 {chemin} .tables"}) == 2


@pytest.mark.parametrize("chemin", ["data/demo.db", "app/profiles.py", "tests/test_profiles.py"])
def test_fichiers_libres(chemin):
    assert code_retour("Read", {"file_path": chemin}) == 0


def decision(outil: str, entree: dict) -> str:
    appel = json.dumps({"tool_name": outil, "tool_input": entree})
    fini = subprocess.run(
        [sys.executable, str(HOOK)], input=appel, capture_output=True, text=True, check=False
    )
    if not fini.stdout:
        return ""
    return json.loads(fini.stdout)["hookSpecificOutput"]["permissionDecision"]


@pytest.mark.parametrize(
    "entree",
    [
        {"file_path": "/mnt/project-files/Exportation de transactions.csv"},
        {"file_path": "/mnt/project-files/releve.txt"},
        {"file_path": "/mnt/project-files/notes/../releve compte.pdf"},
        {"file_path": "/home/alice/Téléchargements/export (2).CSV"},
        {"file_path": "/tmp/operations.ofx"},
    ],
)
def test_releves_reels_demandent_accord(entree):
    assert decision("Read", entree) == "ask"


@pytest.mark.parametrize(
    "entree",
    [
        {"file_path": "tests/fixtures/binance_exemple.csv"},
        {"file_path": "/mnt/project-files/captures/apercu/clair.png"},
        {"file_path": "/mnt/project-files/notes/plan.md"},
        {"file_path": "app/importers/csv_generique.py"},
        {"pattern": "**/*.csv"},
    ],
)
def test_fichiers_du_code_sans_accord(entree):
    outil = "Glob" if "pattern" in entree else "Read"
    assert decision(outil, entree) == ""
