"""Patrimoine.exe (D-126) : journal sans console, erreur de démarrage montrée, chemins du
paquet, icône et recette PyInstaller. L'exécutable lui-même est construit et lancé en CI
Windows (.github/workflows/exe-windows.yml)."""

import io
import re
import struct
import subprocess
import sys
from pathlib import Path

import pytest

from app import __main__ as entree
from app import console, datadir, launcher

RACINE = Path(__file__).parent.parent
RECETTE = (RACINE / "packaging" / "patrimoine.spec").read_text(encoding="utf-8")


@pytest.fixture
def couper_console(monkeypatch, request):
    """Comme Patrimoine.exe : aucune sortie standard, rendue à la fin du test, journaux ouverts
    refermés (Windows ne laisserait pas effacer le dossier). À appeler dans le test lui-même :
    pytest repose sa propre capture entre la préparation et le test."""
    ouverts = []
    ouvrir = console.open_log
    monkeypatch.setattr(
        console, "open_log", lambda dossier: ouverts.append(ouvrir(dossier)) or ouverts[-1]
    )
    request.addfinalizer(lambda: [flux.close() for flux in ouverts])

    def couper():
        monkeypatch.setattr(sys, "stdout", None)
        monkeypatch.setattr(sys, "stderr", None)

    return couper


def test_avec_console_pas_de_journal(tmp_path):
    assert console.redirect_to_log(tmp_path, "9.9.9") is None
    assert not (tmp_path / console.LOG_NAME).exists()


def test_sans_console_messages_dans_le_journal(tmp_path, monkeypatch, couper_console):
    couper_console()
    journal = console.redirect_to_log(tmp_path / "donnees", "9.9.9")
    print("Base utilisée : demo.db (3 opérations)")
    sys.stdout.close()
    texte = journal.read_text(encoding="utf-8")
    assert "Patrimoine 9.9.9" in texte
    assert "Base utilisée : demo.db (3 opérations)" in texte


def test_journal_trop_gros_mis_de_cote(tmp_path):
    (tmp_path / console.LOG_NAME).write_text("x" * (console.MAX_LOG_BYTES + 1), encoding="utf-8")
    with console.open_log(tmp_path) as flux:
        flux.write("neuf\n")
    assert (tmp_path / console.LOG_NAME).read_text(encoding="utf-8") == "neuf\n"
    assert (tmp_path / console.OLD_LOG_NAME).stat().st_size == console.MAX_LOG_BYTES + 1


def test_journal_impossible_l_app_demarre_quand_meme(tmp_path, monkeypatch, couper_console):
    couper_console()
    (tmp_path / "fichier").write_text("", encoding="utf-8")  # un fichier là où irait le dossier
    assert console.redirect_to_log(tmp_path / "fichier", "9.9.9") is None


def test_texte_de_l_erreur_de_demarrage(tmp_path):
    texte = console.startup_error_text(["Dossier introuvable."], tmp_path / "journal.txt")
    assert texte.startswith("Patrimoine n'a pas pu démarrer.")
    assert "Dossier introuvable." in texte
    assert str(tmp_path / "journal.txt") in texte


@pytest.mark.skipif(sys.platform == "win32", reason="ouvrirait une vraie boîte de dialogue")
def test_boite_de_dialogue_seulement_sous_windows():
    assert console.alert("essai") is False


def test_erreur_de_demarrage_montree_sans_console(tmp_path, monkeypatch, couper_console):
    couper_console()
    montre = []
    monkeypatch.setattr(datadir, "default_dir", lambda: tmp_path)
    monkeypatch.setattr(console, "alert", montre.append)

    def dossier_introuvable(display):
        raise datadir.DataDirError("Dossier des données introuvable : X:\\Patrimoine.")

    monkeypatch.setattr(launcher, "run", dossier_introuvable)
    assert entree.main([]) == 1
    assert len(montre) == 1
    assert "Dossier des données introuvable : X:\\Patrimoine." in montre[0]
    assert str(tmp_path / console.LOG_NAME) in montre[0]


def test_erreur_imprevue_dans_le_journal(tmp_path, monkeypatch, couper_console):
    couper_console()
    montre = []
    monkeypatch.setattr(datadir, "default_dir", lambda: tmp_path)
    monkeypatch.setattr(console, "alert", montre.append)

    def panne(display):
        raise ZeroDivisionError("panne simulée")

    monkeypatch.setattr(launcher, "run", panne)
    assert entree.main([]) == 1
    sys.stdout.flush()
    assert "ZeroDivisionError: panne simulée" in (tmp_path / console.LOG_NAME).read_text(
        encoding="utf-8"
    )
    assert "Erreur imprévue" in montre[0]


def test_aucun_port_libre_montre(tmp_path, monkeypatch, couper_console):
    couper_console()
    montre = []
    monkeypatch.setattr(datadir, "default_dir", lambda: tmp_path)
    monkeypatch.setattr(console, "alert", montre.append)

    def sans_port(display):
        raise SystemExit("Aucun port libre entre 8000 et 8019.")

    monkeypatch.setattr(launcher, "run", sans_port)
    assert entree.main([]) == 1
    assert "Aucun port libre" in montre[0]


def test_avec_console_erreur_imprevue_inchangee(monkeypatch):
    def panne(display):
        raise ZeroDivisionError("panne simulée")

    monkeypatch.setattr(launcher, "run", panne)
    with pytest.raises(ZeroDivisionError):
        entree.main([])


def test_dossier_de_l_exe_pour_env_et_bases_relatives(tmp_path):
    """Empaqueté, .env et PATRIMOINE_DB relatif se lisent à côté de Patrimoine.exe, pas dans
    _internal ; un dossier de données dans celui de l'exe est refusé (une mise à jour le
    remplace). Vérifié dans un processus à part : APP_DIR est fixé à l'import."""
    exe = tmp_path / "Patrimoine" / "Patrimoine.exe"
    exe.parent.mkdir()
    script = (
        "import sys; sys.frozen = True; sys.executable = sys.argv[1]\n"
        "from pathlib import Path\n"
        "from app import config, datadir, db, backup\n"
        "import os; os.environ['PATRIMOINE_DB'] = 'essai.db'\n"
        "assert config.APP_DIR == Path(sys.argv[1]).resolve().parent, config.APP_DIR\n"
        "assert backup.ENV_FILE == config.APP_DIR / '.env'\n"
        "assert db.db_path() == config.APP_DIR / 'essai.db'\n"
        "try:\n"
        "    datadir.check_choice(config.APP_DIR / 'donnees')\n"
        "except datadir.DataDirError:\n"
        "    print('REFUS')\n"  # ASCII : la console Windows n'est pas en UTF-8
    )
    sortie = subprocess.run(
        [sys.executable, "-c", script, str(exe)],
        cwd=RACINE,
        capture_output=True,
        text=True,
        encoding="utf-8",
        check=True,
    )
    assert sortie.stdout.strip() == "REFUS"


def test_icone_valide():
    sys.path.insert(0, str(RACINE / "packaging"))
    try:
        import icone
    finally:
        sys.path.pop(0)
    donnees = icone.ico((16, 256))
    reserve, kind, nombre = struct.unpack("<HHH", donnees[:6])
    assert (reserve, kind, nombre) == (0, 1, 2)
    for i, cote in enumerate((16, 256)):
        largeur, _, _, _, _, bits, taille, debut = struct.unpack(
            "<BBBBHHII", donnees[6 + 16 * i : 22 + 16 * i]
        )
        assert largeur == cote % 256 and bits == 32
        image = donnees[debut : debut + taille]
        assert image[:8] == b"\x89PNG\r\n\x1a\n"
        assert struct.unpack(">II", image[16:24]) == (cote, cote)


def test_recette_emporte_les_fichiers_lus_par_l_app():
    """Un fichier non Python ajouté sous app/ (table, modèle...) doit aussi entrer dans
    Patrimoine.exe : sinon l'app empaquetée planterait là où les tests passent."""
    autres = {
        p.relative_to(RACINE / "app").as_posix()
        for p in (RACINE / "app").rglob("*")
        if p.is_file() and p.suffix not in (".py", ".pyc")
    }
    attendus = {"schema.sql"} | {
        f"migrations/{p.name}" for p in (RACINE / "app/migrations").iterdir()
    }
    assert autres == attendus, "ajouter le fichier aux datas de packaging/patrimoine.spec"
    for morceau in ('"web"', '"schema.sql"', '"migrations"', '"CHANGELOG.md"', '"app.main"'):
        assert morceau in RECETTE
    assert re.search(r"console=False", RECETTE)


def test_journal_ouvert_en_utf8(tmp_path):
    with console.open_log(tmp_path) as flux:
        assert isinstance(flux, io.TextIOWrapper) and flux.encoding == "utf-8"


def test_marque_internet_retiree_des_seules_dll(tmp_path):
    """Sous Windows, « a.dll:Zone.Identifier » est un flux caché du fichier ; ailleurs c'est un
    fichier ordinaire de ce nom, ce qui suffit à vérifier ce qui est retiré ou gardé."""
    if sys.platform == "win32":
        pytest.skip("flux NTFS : essayé en vrai par exe-windows.yml")
    from app import window

    (tmp_path / "lib").mkdir()
    for nom in ("a.dll", "lib/b.dll", "c.pyd"):
        (tmp_path / nom).write_bytes(b"")
        (tmp_path / f"{nom}:Zone.Identifier").write_text(
            "[ZoneTransfer]\nZoneId=3", encoding="utf-8"
        )
    assert window.unblock_bundled_dlls(tmp_path) == 2
    assert not (tmp_path / "a.dll:Zone.Identifier").exists()
    assert not (tmp_path / "lib/b.dll:Zone.Identifier").exists()
    assert (tmp_path / "c.pyd:Zone.Identifier").exists()  # seules les DLL sont touchées
    assert window.unblock_bundled_dlls(tmp_path) == 0
