"""Lancement : `uv run python -m app`, sous Windows, macOS ou Linux (D-118).

S'ouvre dans une fenêtre d'application sous Windows et macOS (D-125), sinon dans le navigateur.
Options : `--navigateur` (toujours le navigateur), `--sans-navigateur` (ne rien ouvrir),
`--version` (D-121). Le serveur reste lié à 127.0.0.1.
Patrimoine.exe (D-126) lance ce même programme, sans console : messages dans un journal,
erreur de démarrage montrée dans une boîte de dialogue.
"""

import argparse
import sys
import traceback

from app import console, datadir, launcher
from app.datadir import DataDirError
from app.version import __version__


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="python -m app", description="Lance Patrimoine.")
    display = parser.add_mutually_exclusive_group()
    display.add_argument(
        "--navigateur",
        dest="display",
        action="store_const",
        const=launcher.BROWSER,
        help="ouvrir la page dans le navigateur plutôt que dans une fenêtre",
    )
    display.add_argument(
        "--sans-navigateur",
        dest="display",
        action="store_const",
        const=launcher.NOTHING,
        help="ne rien ouvrir (l'adresse est affichée)",
    )
    parser.add_argument("--version", action="version", version=f"Patrimoine {__version__}")
    # Avant tout message : sans console, ils iraient nulle part. Le journal est dans le dossier
    # de données par défaut, connu même quand le dossier choisi est introuvable.
    log = console.redirect_to_log(datadir.default_dir(), __version__)
    launcher.startup_errors.clear()
    args = parser.parse_args(argv)
    for stream in (sys.stdout, sys.stderr):
        # Console Windows redirigée vers un fichier (cp1252) : un caractère imprévu ne doit pas
        # arrêter l'app au milieu d'un message.
        if hasattr(stream, "reconfigure"):
            stream.reconfigure(errors="replace")
    try:
        code = launcher.run(display=args.display or launcher.WINDOW)
    except DataDirError as err:  # dossier des données refusé, introuvable ou copie ratée (D-119)
        code = launcher.fail(str(err))
    except SystemExit as err:  # message d'arrêt (aucun port libre...) : à montrer aussi
        if not isinstance(err.code, str):
            raise
        code = launcher.fail(err.code)
    except Exception:  # noqa: BLE001 : sans console, une erreur imprévue serait invisible
        if log is None:
            raise
        traceback.print_exc()
        code = launcher.fail("Erreur imprévue (détail dans le journal).")
    if code and log is not None:
        console.alert(console.startup_error_text(launcher.startup_errors, log))
    return code


if __name__ == "__main__":
    sys.exit(main())
