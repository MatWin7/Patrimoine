"""Licences (D-128) : celle de l'app et celles des composants livrés avec Patrimoine.exe."""

import sys
from pathlib import Path

RACINE = Path(__file__).parent.parent
sys.path.insert(0, str(RACINE / "packaging"))
import licences  # noqa: E402


def test_licence_de_l_app_gpl3():
    texte = (RACINE / "LICENSE").read_text(encoding="utf-8")
    assert texte.startswith("                    GNU GENERAL PUBLIC LICENSE")
    assert "Version 3, 29 June 2007" in texte
    pyproject = (RACINE / "pyproject.toml").read_text(encoding="utf-8")
    assert 'license = "GPL-3.0-or-later"' in pyproject


def test_paquets_livres_sans_outils_de_developpement():
    noms = {licences._nom(d.metadata["Name"]) for d in licences.paquets_livres()}
    assert {"fastapi", "uvicorn", "httpx2", "starlette", "pydantic"} <= noms
    assert not noms & {"pytest", "ruff", "pyinstaller", "pluggy", "iniconfig", "pygments"}


def test_exigences_lues():
    assert licences._exigence("pywebview>=6.2.1; sys_platform == 'win32'") == "pywebview"
    assert licences._exigence("Typing_Extensions>=4") == "typing-extensions"
    assert licences._exigence("email-validator; extra == 'email'") is None


def test_texte_complet():
    texte = licences.texte()
    for morceau in ("GNU GPL v3", "fastapi", "Python ", "Chart.js", "Police Inter", "PyInstaller"):
        assert morceau in texte
    # Chaque fichier de web/vendor/ a sa licence relevée.
    for _, chemin in licences.VENDOR_LICENCES:
        assert chemin.is_file()


def test_recette_pose_les_licences():
    recette = (RACINE / "packaging" / "patrimoine.spec").read_text(encoding="utf-8")
    assert '"LICENCE.txt"' in recette and '"LICENCES-TIERCES.txt"' in recette
