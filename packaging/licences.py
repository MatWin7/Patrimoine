"""Licences des composants livrés avec Patrimoine.exe (étape 8 de la distribution, D-128).

`texte()` réunit, pour chaque bibliothèque Python que l'app charge (dépendances de
pyproject.toml et leurs propres dépendances, celles installées sur la machine qui construit),
son nom, sa version, sa licence déclarée et le texte de ses fichiers de licence ; puis la licence
de Python et celles des fichiers copiés dans web/vendor/. Les outils de développement et de
construction (pytest, ruff, PyInstaller) n'y figurent pas : ils ne sont pas livrés.
Lancé par packaging/patrimoine.spec, ou à la main : `uv run python packaging/licences.py`.
Bibliothèque standard uniquement.
"""

import importlib.metadata as metadata
import os
import re
import sys
import tomllib
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
VENDOR = ROOT / "web" / "vendor"
# Fichiers de web/vendor/ et leur licence (MIT pour Chart.js, OFL pour la police Inter).
VENDOR_LICENCES = [
    ("Chart.js", VENDOR / "chart.js-LICENSE.md"),
    ("chartjs-chart-treemap", VENDOR / "chartjs-chart-treemap-LICENSE.md"),
    ("Police Inter", VENDOR / "inter" / "LICENSE"),
    ("Icônes Lucide", VENDOR / "lucide-LICENSE"),
]
NOTE_EXE = (
    "Patrimoine.exe est construit avec PyInstaller, dont le chargeur est sous GPL 2 avec une\n"
    "exception qui permet de le livrer avec un programme sous n'importe quelle licence.\n"
    "Sous Windows, la fenêtre utilise le moteur WebView2 de Microsoft déjà installé avec\n"
    "Windows ; les fichiers d'interface WebView2 livrés par pywebview gardent leur licence\n"
    "(Microsoft, redistribution permise)."
)


def _nom(nom: str) -> str:
    """Nom normalisé d'un paquet (PEP 503) : « Typing_Extensions » = « typing-extensions »."""
    return re.sub(r"[-_.]+", "-", nom).lower()


def _exigence(ligne: str) -> str | None:
    """Nom du paquet d'une exigence, ou None si elle ne vaut que pour une option (« extra »)."""
    texte, _, marqueur = ligne.partition(";")
    if "extra" in marqueur:
        return None
    trouve = re.match(r"\s*([A-Za-z0-9][A-Za-z0-9._-]*)", texte)
    return _nom(trouve.group(1)) if trouve else None


def paquets_livres() -> list[metadata.Distribution]:
    """Dépendances de l'app installées ici, de proche en proche, triées par nom.

    Un paquet absent (marqueur d'un autre système, par ex. pywebview sous Linux) est ignoré : la
    liste décrit ce qui est réellement embarqué par la machine qui construit."""
    projet = tomllib.loads((ROOT / "pyproject.toml").read_text(encoding="utf-8"))
    a_voir = [n for n in map(_exigence, projet["project"]["dependencies"]) if n]
    vus: dict[str, metadata.Distribution] = {}
    while a_voir:
        nom = a_voir.pop()
        if nom in vus:
            continue
        try:
            dist = metadata.distribution(nom)
        except metadata.PackageNotFoundError:
            continue
        vus[nom] = dist
        a_voir.extend(n for n in map(_exigence, dist.requires or []) if n)
    return [vus[n] for n in sorted(vus)]


def _licence_declaree(dist: metadata.Distribution) -> str:
    meta = dist.metadata
    expression = meta.get("License-Expression")
    if expression:
        return expression
    classeurs = [
        c.split("::")[-1].strip()
        for c in meta.get_all("Classifier") or []
        if c.startswith("License ::")
    ]
    licence = (meta.get("License") or "").strip()
    if licence and "\n" not in licence and len(licence) < 80:
        return licence
    return ", ".join(classeurs) or "voir le texte ci-dessous"


def _fichiers_licence(dist: metadata.Distribution) -> list[str]:
    textes = []
    for fichier in dist.files or []:
        nom = fichier.name.upper()
        if ".dist-info" in str(fichier) and nom.startswith(("LICEN", "COPYING", "NOTICE")):
            try:
                textes.append(fichier.read_text(encoding="utf-8").strip())
            except (OSError, UnicodeDecodeError):
                continue
    return textes


def _licence_python() -> str:
    """Texte que montre `license()` dans Python : LICENSE.txt à côté de la bibliothèque standard
    (Linux, macOS) ou un dossier au-dessus (Windows, où il cite aussi OpenSSL, SQLite, libffi...
    livrés avec Python). Même recherche que le module site."""
    ici = Path(os.__file__).resolve().parent
    for dossier in (ici, ici.parent, Path(sys.base_prefix)):
        for nom in ("LICENSE.txt", "LICENSE"):
            if (dossier / nom).is_file():
                return (dossier / nom).read_text(encoding="utf-8").strip()
    return "Python Software Foundation License : https://docs.python.org/3/license.html"


def _bloc(titre: str, corps: str) -> str:
    return f"{'=' * 78}\n{titre}\n{'=' * 78}\n\n{corps.strip()}\n"


def texte() -> str:
    paquets = paquets_livres()
    blocs = [
        "Patrimoine, copyright © 2026 les auteurs de Patrimoine.\n"
        "Patrimoine est un logiciel libre sous GNU GPL v3 ou ultérieure (fichier LICENCE.txt).\n"
        "Il est livré avec les composants ci-dessous, chacun sous sa propre licence.\n\n"
        + NOTE_EXE
        + "\n\nBibliothèques Python :\n"
        + "\n".join(f"- {d.metadata['Name']} {d.version} : {_licence_declaree(d)}" for d in paquets)
        + f"\n- Python {sys.version.split()[0]} : PSF-2.0\n"
        + "\nFichiers de la page (web/vendor/) :\n"
        + "\n".join(f"- {nom}" for nom, _ in VENDOR_LICENCES)
        + "\n"
    ]
    for dist in paquets:
        textes = _fichiers_licence(dist) or [f"Licence déclarée : {_licence_declaree(dist)}"]
        blocs.append(_bloc(f"{dist.metadata['Name']} {dist.version}", "\n\n".join(textes)))
    blocs.append(_bloc(f"Python {sys.version.split()[0]}", _licence_python()))
    for nom, chemin in VENDOR_LICENCES:
        blocs.append(_bloc(nom, chemin.read_text(encoding="utf-8")))
    return "\n\n".join(blocs)


if __name__ == "__main__":
    sys.stdout.reconfigure(encoding="utf-8")
    print(texte())
