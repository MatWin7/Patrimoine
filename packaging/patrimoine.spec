# Recette PyInstaller de Patrimoine.exe (D-126) et de Patrimoine.app (macOS, D-137) :
# `uv run --group build pyinstaller packaging/patrimoine.spec`, sur le système visé.
#
# macOS : dist/Patrimoine.app, une application ordinaire à glisser dans Applications. Signée
# « ad hoc » (signature sans identité, gratuite, exigée par les Mac à puce Apple), pas notarisée
# par Apple : au premier lancement, macOS demande une confirmation (« Ouvrir quand même »).
# Les licences sont rangées dans Patrimoine.app/Contents/Resources (et à côté de l'app dans le
# .dmg) : un fichier ajouté après la construction casserait la signature.
#
# Résultat : dist/Patrimoine/ avec Patrimoine.exe et son sous-dossier _internal (Python, les
# bibliothèques, web/, le schéma et les migrations). Ce dossier est livré tel quel, zippé :
# rien à installer, aucun droit d'administrateur. Les données ne sont jamais dedans : elles
# restent dans le dossier des données (D-119), qu'une nouvelle version ne touche pas.
# Sans console (console=False) : messages dans un journal, erreurs dans une boîte de dialogue
# (app/console.py). Fichier Python lu par PyInstaller, qui fournit Analysis, PYZ, EXE et COLLECT.
import re
import sys
from pathlib import Path

ROOT = Path(SPECPATH).parent  # noqa: F821 : SPECPATH est fourni par PyInstaller
sys.path.insert(0, str(ROOT / "packaging"))
import icone  # noqa: E402
import licences  # noqa: E402

VERSION = re.search(
    r'^__version__ = "(\d+)\.(\d+)\.(\d+)"', (ROOT / "app" / "version.py").read_text("utf-8"), re.M
)
if VERSION is None:
    raise SystemExit("Version introuvable dans app/version.py")

MAC = sys.platform == "darwin"
WORK = Path(workpath)  # noqa: F821 : workpath est fourni par PyInstaller
WORK.mkdir(parents=True, exist_ok=True)
# Icône dessinée à chaque construction (packaging/icone.py), dans le dossier de travail.
ICON = WORK / ("patrimoine.icns" if MAC else "patrimoine.ico")
ICON.write_bytes(icone.icns() if MAC else icone.ico())
# Licences (D-128) : celle de l'app (GPL v3 ou ultérieure) et celles des composants embarqués,
# relevées sur la machine qui construit.
LICENCE = WORK / "LICENCE.txt"
LICENCE.write_text((ROOT / "LICENSE").read_text(encoding="utf-8"), encoding="utf-8")
TIERCES = WORK / "LICENCES-TIERCES.txt"
TIERCES.write_text(licences.texte(), encoding="utf-8")


def version_info():
    """Propriétés de Patrimoine.exe (clic droit > Propriétés > Détails), Windows seulement."""
    if sys.platform != "win32":
        return None
    from PyInstaller.utils.win32 import versioninfo as vi

    numbers = (*(int(n) for n in VERSION.groups()), 0)
    text = ".".join(VERSION.groups())
    strings = [
        vi.StringStruct("CompanyName", "Patrimoine"),
        vi.StringStruct("FileDescription", "Patrimoine"),
        vi.StringStruct("FileVersion", text),
        vi.StringStruct("InternalName", "Patrimoine"),
        vi.StringStruct("OriginalFilename", "Patrimoine.exe"),
        vi.StringStruct("ProductName", "Patrimoine"),
        vi.StringStruct("ProductVersion", text),
    ]
    return vi.VSVersionInfo(
        ffi=vi.FixedFileInfo(filevers=numbers, prodvers=numbers),
        kids=[
            vi.StringFileInfo([vi.StringTable("040C04B0", strings)]),  # français, Unicode
            vi.VarFileInfo([vi.VarStruct("Translation", [0x040C, 1200])]),
        ],
    )


# Fichiers lus par l'app à l'exécution, rangés comme dans le dépôt (chemins tirés de __file__).
datas = [
    (str(ROOT / "web"), "web"),
    (str(ROOT / "app" / "schema.sql"), "app"),
    (str(ROOT / "app" / "migrations"), "app/migrations"),
    (str(ROOT / "CHANGELOG.md"), "."),  # notes de version (app/version.py)
]
if MAC:  # dans Patrimoine.app, avant la signature
    datas += [(str(LICENCE), "."), (str(TIERCES), ".")]

a = Analysis(  # noqa: F821
    [str(ROOT / "app" / "__main__.py")],
    pathex=[str(ROOT)],
    datas=datas,
    # uvicorn charge l'app par son nom (« app.main:app ») : PyInstaller ne le verrait pas.
    hiddenimports=["app.main"],
    # Outils de développement jamais utilisés par l'app : gardés hors du paquet.
    excludes=["pytest", "ruff", "tkinter", "PyInstaller"],
    noarchive=False,
    optimize=0,  # garder les assert et les docstrings : l'app ne doit pas changer de comportement
)
pyz = PYZ(a.pure)  # noqa: F821

exe = EXE(  # noqa: F821
    pyz,
    a.scripts,
    [],
    exclude_binaries=True,
    name="Patrimoine",
    console=False,  # pas de fenêtre noire derrière l'app (macOS : une app, pas un terminal)
    icon=str(ICON),
    version=version_info(),
    upx=False,  # binaires compressés par UPX : souvent pris pour un virus
    # PyInstaller retire les symboles des binaires sous Linux et macOS (sans effet sous Windows).
    strip=False,
    # macOS : processeur de la machine qui construit (puce Apple sur le runner macos-latest) ;
    # signature ad hoc (aucune identité Apple) ; pas d'émulation d'arguments du Finder.
    target_arch=None,
    codesign_identity=None,
    argv_emulation=False,
)
coll = COLLECT(  # noqa: F821
    exe,
    a.binaries,
    a.datas,
    strip=False,
    upx=False,
    name="Patrimoine",
)

if MAC:
    # Réglages lus par macOS dans Contents/Info.plist.
    app = BUNDLE(  # noqa: F821
        coll,
        name="Patrimoine.app",
        icon=str(ICON),
        bundle_identifier="io.github.matwin7.patrimoine",
        version=".".join(VERSION.groups()),
        info_plist={
            "CFBundleName": "Patrimoine",
            "CFBundleDisplayName": "Patrimoine",
            "CFBundleShortVersionString": ".".join(VERSION.groups()),
            "CFBundleVersion": ".".join(VERSION.groups()),
            "CFBundleDevelopmentRegion": "fr",
            "LSApplicationCategoryType": "public.app-category.finance",
            "LSMinimumSystemVersion": "12.0",
            "NSHighResolutionCapable": True,
            "NSHumanReadableCopyright": "Licence GNU GPL v3 ou ultérieure",
            # La page est servie en http sur 127.0.0.1 : seules les adresses locales sont
            # permises sans https, rien n'est ouvert vers Internet (règle 6).
            "NSAppTransportSecurity": {"NSAllowsLocalNetworking": True},
        },
    )
else:
    # Licences à côté de Patrimoine.exe (D-128).
    DOSSIER = Path(DISTPATH) / "Patrimoine"  # noqa: F821 : DISTPATH est fourni par PyInstaller
    for fichier in (LICENCE, TIERCES):
        (DOSSIER / fichier.name).write_text(fichier.read_text(encoding="utf-8"), encoding="utf-8")
