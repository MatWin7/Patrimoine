"""Numéro de version de Patrimoine et notes d'une release (D-121).

Version sémantique `majeur.mineur.correctif` : le correctif monte pour une réparation, le mineur
pour une nouveauté, le majeur pour un changement qui casse quelque chose (0.x tant que l'app
n'est pas publiée). Ce fichier est la source lue par l'app ; `pyproject.toml` et `uv.lock` doivent
porter le même numéro (vérifié par les tests et par le workflow de release).

`uv run python -m app.version` affiche la version ;
`--verifier-etiquette v0.2.0` refuse une étiquette qui ne correspond pas à cette version ou
dont `CHANGELOG.md` n'a pas de section ; `--notes FICHIER` y écrit les notes de la version.
"""

import argparse
import re
import sys
from pathlib import Path

__version__ = "0.1.0"

ROOT = Path(__file__).resolve().parent.parent
CHANGELOG = ROOT / "CHANGELOG.md"

# Étiquette de release : v + version sémantique, avec un suffixe de préversion facultatif
# (v0.2.0-rc.1). Rien d'autre : le nom finit dans un nom de fichier et un titre de page.
TAG_RE = re.compile(r"^v(\d+\.\d+\.\d+(?:-[0-9A-Za-z.]+)?)$")
# Titre de section du journal : « ## 0.2.0 » ou « ## 0.2.0 - 2026-12-01 ».
SECTION_RE = re.compile(r"^## \[?([^\]\s]+)\]?(?:\s.*)?$")


class ReleaseError(ValueError):
    """Étiquette ou journal des changements qui ne permet pas de publier."""


def version_from_tag(tag: str) -> str:
    """« v0.2.0 » -> « 0.2.0 » ; refuse tout autre format."""
    match = TAG_RE.fullmatch(tag)
    if not match:
        raise ReleaseError(f"Étiquette « {tag} » invalide : attendu vX.Y.Z (ex. v0.2.0).")
    return match.group(1)


def is_prerelease(version: str) -> bool:
    """Une préversion (0.2.0-rc.1) n'est pas proposée comme « dernière version »."""
    return "-" in version


def release_notes(changelog: str, version: str) -> str:
    """Texte de la section « ## <version> » du journal, sans son titre."""
    lines: list[str] | None = None
    for line in changelog.splitlines():
        match = SECTION_RE.match(line)
        if match:
            if lines is not None:
                break
            if match.group(1) == version:
                lines = []
            continue
        if lines is not None:
            lines.append(line)
    if lines is None:
        raise ReleaseError(f"CHANGELOG.md n'a pas de section « ## {version} ».")
    notes = "\n".join(lines).strip()
    if not notes:
        raise ReleaseError(f"La section « ## {version} » de CHANGELOG.md est vide.")
    return notes


def check_tag(tag: str, changelog: str, version: str | None = None) -> str:
    """Notes de la release si l'étiquette correspond à la version du code, sinon ReleaseError."""
    version = version or __version__
    tagged = version_from_tag(tag)
    if tagged != version:
        raise ReleaseError(
            f"L'étiquette {tag} ne correspond pas à la version du code ({version}) : "
            "changer app/version.py, pyproject.toml et uv.lock dans une PR, puis étiqueter."
        )
    return release_notes(changelog, version)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="python -m app.version")
    parser.add_argument("--verifier-etiquette", metavar="ETIQUETTE")
    parser.add_argument("--notes", metavar="FICHIER", help="écrit les notes de la version")
    args = parser.parse_args(argv)
    if not args.verifier_etiquette:
        print(__version__)
        return 0
    try:
        notes = check_tag(args.verifier_etiquette, CHANGELOG.read_text(encoding="utf-8"))
    except ReleaseError as err:
        print(err, file=sys.stderr)
        return 1
    if args.notes:
        Path(args.notes).write_text(notes + "\n", encoding="utf-8")
    print(f"{args.verifier_etiquette} : version {__version__}, notes trouvées.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
