"""Dossier des données hors du code (D-119) : emplacement par système, choix d'un autre dossier,
copie vérifiée de l'ancien data/ sans jamais le modifier. Données FICTIVES uniquement."""

import hashlib
import sqlite3
from contextlib import closing
from pathlib import Path

import pytest

from app import config, datadir, db, launcher, profiles

BASE = profiles.LEGACY_FILE


def test_meme_nom_de_base_que_les_profils():
    assert datadir.LIVE_BASE == profiles.LEGACY_FILE
    assert datadir.REGISTRY == profiles.REGISTRY_NAME
    assert datadir.PROFILES_SUBDIR == profiles.PROFILES_SUBDIR


# --- Emplacement par défaut -------------------------------------------------------------------


def test_dossier_par_defaut_selon_le_systeme(tmp_path):
    home = tmp_path / "alice"
    local = str(tmp_path / "Local")
    assert datadir.default_dir("win32", {"LOCALAPPDATA": local}, home) == Path(local, "Patrimoine")
    assert datadir.default_dir("win32", {}, home) == home / "AppData" / "Local" / "Patrimoine"
    mac = home / "Library" / "Application Support" / "Patrimoine"
    assert datadir.default_dir("darwin", {}, home) == mac
    assert datadir.default_dir("linux", {}, home) == home / ".local" / "share" / "patrimoine"
    xdg = str(tmp_path / "xdg")
    assert datadir.default_dir("linux", {"XDG_DATA_HOME": xdg}, home) == Path(xdg, "patrimoine")
    # Chemin relatif : ignoré, comme le veut la spécification XDG.
    relatif = datadir.default_dir("linux", {"XDG_DATA_HOME": "xdg"}, home)
    assert relatif == home / ".local" / "share" / "patrimoine"


def test_dossier_par_defaut_hors_du_code():
    assert (
        not datadir.default_dir("linux", {}, Path.home())
        .resolve()
        .is_relative_to(config.ROOT.resolve())
    )


# --- Dossier choisi ---------------------------------------------------------------------------


@pytest.fixture
def defaut(tmp_path, monkeypatch):
    """Dossier « par défaut » fictif, sans .env lu."""
    folder = tmp_path / "defaut"
    monkeypatch.setattr(datadir, "default_dir", lambda: folder)
    monkeypatch.setattr(datadir, "dotenv_value", lambda name: None)
    return folder


def test_sans_reglage_dossier_par_defaut(defaut):
    assert datadir.resolve() == (defaut, "par défaut")


def test_variable_d_environnement(tmp_path, defaut, monkeypatch):
    monkeypatch.setenv("PATRIMOINE_DATA_DIR", str(tmp_path / "ailleurs"))
    assert datadir.resolve() == (tmp_path / "ailleurs", "PATRIMOINE_DATA_DIR")


@pytest.mark.parametrize(
    "valeur",
    [
        "donnees",  # relatif
        "/home/alice/OneDrive/Patrimoine",  # synchronisé vers un cloud (règle 2)
        str(config.ROOT / "data"),  # dans le code : une mise à jour l'emporterait
    ],
)
def test_dossier_refuse(valeur, defaut, monkeypatch):
    monkeypatch.setenv("PATRIMOINE_DATA_DIR", valeur)
    with pytest.raises(datadir.DataDirError):
        datadir.resolve()


def test_dossier_choisi_absent_rien_n_est_cree(tmp_path):
    absent = tmp_path / "cle-usb" / "Patrimoine"
    with pytest.raises(datadir.DataDirError, match="introuvable"):
        datadir.ensure(absent, "choisi")
    assert not absent.exists()


def test_dossier_par_defaut_cree(defaut):
    datadir.ensure(defaut, "par défaut")
    assert defaut.is_dir()


# --- Copie vérifiée ---------------------------------------------------------------------------


def _base_fictive(path: Path, compte: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    db.init_db(path)
    with closing(sqlite3.connect(path)) as conn:
        conn.execute("INSERT INTO account (name, kind) VALUES (?, 'pea')", (compte,))
        conn.commit()


def _ancien_dossier(tmp_path: Path) -> Path:
    """data/ d'avant D-119, fictif : base unique, profils, sauvegardes, corbeille, démo, verrou."""
    old = tmp_path / "code" / "data"
    _base_fictive(old / BASE, "PEA fictif")
    _base_fictive(old / "profils" / "alice.db", "CTO fictif")
    (old / "profils.json").write_text('{"version": 1, "profiles": []}', encoding="utf-8")
    (old / "sauvegardes").mkdir()
    (old / "sauvegardes" / "alice-auto-2030-01-01-090000.db").write_bytes(b"copie fermee")
    (old / "corbeille").mkdir()
    (old / "corbeille" / "bob-2030-01-01.db").write_bytes(b"profil retire")
    (old / "imports").mkdir()
    (old / "imports" / "releve-fictif.csv").write_text("date;montant\n", encoding="utf-8")
    _base_fictive(old / "demo.db", "Démo")
    (old / "lancement.lock").write_bytes(b"")
    (old / "lancement.lock.port").write_text('{"port": 8000}', encoding="utf-8")
    (old / "fenetre").mkdir()  # cache du moteur web de la fenêtre (D-125) : jamais copié
    (old / "fenetre" / "taille.json").write_text('{"width": 900}', encoding="utf-8")
    return old


def _empreintes(folder: Path) -> dict[str, str]:
    return {
        p.relative_to(folder).as_posix(): hashlib.sha256(p.read_bytes()).hexdigest()
        for p in sorted(folder.rglob("*"))
        if p.is_file() and p.name != "lancement.lock"  # verrou du lanceur, pas une donnée
    }


def _comptes(path: Path) -> list[str]:
    with closing(sqlite3.connect(path)) as conn:
        return [row[0] for row in conn.execute("SELECT name FROM account")]


def test_copie_complete_et_original_intact(tmp_path):
    old = _ancien_dossier(tmp_path)
    avant = _empreintes(old)
    new = tmp_path / "neuf"
    new.mkdir()
    assert datadir.copy_data(old, new) == 6
    assert _empreintes(old) == avant  # rien modifié, rien supprimé
    assert not (new / "fenetre").exists()
    assert _comptes(new / BASE) == ["PEA fictif"]
    assert _comptes(new / "profils" / "alice.db") == ["CTO fictif"]
    copie = _empreintes(new)
    for nom in ("profils.json", "sauvegardes/alice-auto-2030-01-01-090000.db"):
        assert copie[nom] == avant[nom]  # fichiers fermés copiés à l'octet près
    assert "imports/releve-fictif.csv" in copie  # fichier inconnu : gardé aussi
    # Ni démo (recréée par app.demo), ni verrou, ni reste de la copie provisoire.
    assert not {"demo.db", "lancement.lock.port"} & set(copie)
    assert not (new / "lancement.lock").exists()
    assert not (new / datadir.STAGING_NAME).exists()


def test_rien_n_est_ecrase(tmp_path):
    old = _ancien_dossier(tmp_path)
    new = tmp_path / "neuf"
    _base_fictive(new / BASE, "Déjà là")
    with pytest.raises(datadir.DataDirError, match="déjà des données"):
        datadir.copy_data(old, new)
    assert _comptes(new / BASE) == ["Déjà là"]


def test_dossier_de_meme_nom_jamais_ecrase(tmp_path):
    old = _ancien_dossier(tmp_path)
    new = tmp_path / "neuf"
    (new / "sauvegardes").mkdir(parents=True)
    (new / "sauvegardes" / "a-moi.txt").write_text("x", encoding="utf-8")
    with pytest.raises(datadir.DataDirError, match="sauvegardes"):
        datadir.copy_data(old, new)
    assert [p.name for p in new.iterdir()] == ["sauvegardes"]


def test_copie_ratee_ne_laisse_rien(tmp_path, monkeypatch):
    """Une copie qui échoue au milieu ne laisse aucune donnée que l'app prendrait pour bonne."""
    old = _ancien_dossier(tmp_path)
    avant = _empreintes(old)
    new = tmp_path / "neuf"
    new.mkdir()
    vrai = datadir._copy_base
    appels = []

    def copie_qui_casse(source, target):
        appels.append(source)
        if len(appels) == 2:
            raise datadir.DataDirError("Copie incomplète (simulée)")
        vrai(source, target)

    monkeypatch.setattr(datadir, "_copy_base", copie_qui_casse)
    with pytest.raises(datadir.DataDirError):
        datadir.copy_data(old, new)
    assert list(new.iterdir()) == []
    assert _empreintes(old) == avant


def test_copie_abimee_detectee(tmp_path, monkeypatch):
    old = _ancien_dossier(tmp_path)
    new = tmp_path / "neuf"
    new.mkdir()
    monkeypatch.setattr(datadir, "_content", lambda path: (0, {"account": id(path)}))
    with pytest.raises(datadir.DataDirError, match="même contenu"):
        datadir.copy_data(old, new)
    assert not datadir.has_data(new)


# --- Premier lancement après la mise à jour ---------------------------------------------------


def test_ancien_dossier_recopie_une_fois(tmp_path):
    old = _ancien_dossier(tmp_path)
    new = tmp_path / "neuf"
    new.mkdir()
    messages = datadir.adopt_legacy(new, old)
    assert "copiées et vérifiées" in messages[0] and "pas été touché" in messages[1]
    assert _comptes(new / BASE) == ["PEA fictif"]
    # Lancement suivant : plus de copie, un simple rappel que l'ancien dossier existe encore.
    (new / BASE).unlink()
    _base_fictive(new / BASE, "Modifié depuis")
    assert "encore une copie" in datadir.adopt_legacy(new, old)[0]
    assert _comptes(new / BASE) == ["Modifié depuis"]


def test_rien_a_recopier(tmp_path):
    vide = tmp_path / "code" / "data"
    vide.mkdir(parents=True)
    _base_fictive(vide / "demo.db", "Démo")  # la démo seule ne compte pas
    assert datadir.adopt_legacy(tmp_path / "neuf", vide) == []


def test_ancienne_version_encore_lancee(tmp_path):
    old = _ancien_dossier(tmp_path)
    new = tmp_path / "neuf"
    new.mkdir()
    ancienne = launcher.InstanceLock(old / "lancement.lock")
    assert ancienne.acquire()
    try:
        with pytest.raises(datadir.DataDirError, match="tourne encore"):
            datadir.adopt_legacy(new, old)
    finally:
        ancienne.release()
    assert list(new.iterdir()) == []


def test_preparer_avant_une_commande(tmp_path, monkeypatch):
    old = _ancien_dossier(tmp_path)
    monkeypatch.setattr(datadir, "LEGACY_DIR", old)
    monkeypatch.delenv("PATRIMOINE_DB", raising=False)
    new = tmp_path / "neuf"
    assert datadir.prepare(new, "par défaut")
    assert datadir.has_data(new)


def test_base_forcee_ne_recopie_rien(tmp_path, monkeypatch):
    monkeypatch.setattr(datadir, "LEGACY_DIR", _ancien_dossier(tmp_path))
    monkeypatch.setenv("PATRIMOINE_DB", str(tmp_path / "demo.db"))
    assert datadir.prepare(tmp_path / "neuf", "par défaut") == []
    assert not (tmp_path / "neuf").exists()


# --- Choisir un autre dossier -----------------------------------------------------------------


def test_choisir_un_dossier(tmp_path, defaut):
    actuel = tmp_path / "actuel"
    _base_fictive(actuel / BASE, "PEA fictif")
    avant = _empreintes(actuel)
    cle = tmp_path / "cle-usb" / "Patrimoine"
    cle.parent.mkdir()
    messages = datadir.choose(cle, actuel)
    assert "1 fichier(s)" in messages[0]
    assert _empreintes(actuel) == avant  # l'ancien reste intact
    assert datadir.resolve() == (cle.resolve(), "choisi")
    assert (defaut / datadir.POINTER_NAME).read_text(encoding="utf-8") == str(cle.resolve())
    # Retour au dossier par défaut : copie, puis le choix est oublié.
    datadir.choose(defaut, cle)
    assert datadir.resolve() == (defaut, "par défaut")
    assert _comptes(defaut / BASE) == ["PEA fictif"]


def test_choisir_refuse_si_l_app_tourne(tmp_path, defaut):
    actuel = tmp_path / "actuel"
    _base_fictive(actuel / BASE, "PEA fictif")
    lancee = launcher.InstanceLock(actuel / "lancement.lock")
    assert lancee.acquire()
    try:
        with pytest.raises(datadir.DataDirError, match="lancé"):
            datadir.choose(tmp_path / "ailleurs", actuel)
    finally:
        lancee.release()
    assert not (tmp_path / "ailleurs").exists()


def test_choisir_refuse_si_variable_reglee(tmp_path, defaut, monkeypatch):
    monkeypatch.setenv("PATRIMOINE_DATA_DIR", str(tmp_path / "x"))
    with pytest.raises(datadir.DataDirError, match="PATRIMOINE_DATA_DIR"):
        datadir.choose(tmp_path / "ailleurs", tmp_path / "x")


def test_choisir_refuse_disque_absent(tmp_path, defaut):
    with pytest.raises(datadir.DataDirError, match="introuvable"):
        datadir.choose(tmp_path / "absent" / "Patrimoine", tmp_path / "actuel")


# --- Sauvegardes ------------------------------------------------------------------------------


def test_copie_des_sauvegardes_hors_du_dossier_des_donnees(monkeypatch):
    monkeypatch.setenv("BACKUP_COPY_DIR", str(profiles.DATA_DIR / "copie"))
    with pytest.raises(RuntimeError, match="dossier des données"):
        config.backup_copy_dir()


def test_dotenv_value_sans_toucher_l_environnement(tmp_path, monkeypatch):
    env = tmp_path / "reglages.txt"
    env.write_text("PATRIMOINE_DATA_DIR=/srv/patrimoine\nVIDE=\n", encoding="utf-8")
    monkeypatch.delenv("PATRIMOINE_DATA_DIR", raising=False)
    assert config.dotenv_value("PATRIMOINE_DATA_DIR", env) == "/srv/patrimoine"
    assert config.dotenv_value("VIDE", env) is None
    import os

    assert "PATRIMOINE_DATA_DIR" not in os.environ


def test_lien_vers_un_dossier_synchronise_refuse(tmp_path):
    cloud = tmp_path / "OneDrive" / "Patrimoine"
    cloud.mkdir(parents=True)
    lien = tmp_path / "donnees"
    try:
        lien.symlink_to(cloud, target_is_directory=True)
    except OSError:
        pytest.skip("liens symboliques non permis ici (Windows sans mode développeur)")
    with pytest.raises(datadir.DataDirError, match="onedrive"):
        datadir.check_choice(lien)


def test_ancien_dossier_intact_apres_la_copie(tmp_path):
    """Même le fichier de port périmé du lanceur reste là : rien n'est retiré de l'ancien."""
    old = _ancien_dossier(tmp_path)
    avant = _empreintes(old)
    new = tmp_path / "neuf"
    new.mkdir()
    datadir.adopt_legacy(new, old)
    assert _empreintes(old) == avant
    assert (old / "lancement.lock.port").is_file()


def test_rangement_coupe_puis_repris(tmp_path, monkeypatch):
    """Coupure pendant le rangement (antivirus, Ctrl+C) : le dossier n'est jamais pris pour des
    données, et la tentative suivante défait la copie partielle puis la refait en entier."""
    old = _ancien_dossier(tmp_path)
    avant = _empreintes(old)
    new = tmp_path / "neuf"
    new.mkdir()
    vrai = datadir.os.replace
    ranges = []

    def coupure(src, dst):
        if Path(src).parent.name == datadir.STAGING_NAME and len(ranges) == 3:
            raise KeyboardInterrupt  # pas rattrapé : comme une vraie coupure
        ranges.append(dst)
        vrai(src, dst)

    monkeypatch.setattr(datadir.os, "replace", coupure)
    with pytest.raises(KeyboardInterrupt):
        datadir.adopt_legacy(new, old)
    monkeypatch.setattr(datadir.os, "replace", vrai)
    assert (new / "profils").is_dir()  # déjà rangé...
    assert not datadir.has_data(new)  # ...mais jamais pris pour des données
    messages = datadir.adopt_legacy(new, old)
    assert "copiées et vérifiées" in messages[0]
    assert _comptes(new / BASE) == ["PEA fictif"]
    assert (new / "profils.json").is_file()
    assert not (new / datadir.MARKER_NAME).exists()
    assert _empreintes(old) == avant


def test_rangement_rate_defait_tout_de_suite(tmp_path, monkeypatch):
    old = _ancien_dossier(tmp_path)
    new = tmp_path / "neuf"
    new.mkdir()
    vrai = datadir.os.replace
    appels = []

    def refus(src, dst):
        appels.append(dst)
        if len(appels) == 4:
            raise PermissionError("fichier ouvert par l'antivirus (simulé)")
        vrai(src, dst)

    monkeypatch.setattr(datadir.os, "replace", refus)
    with pytest.raises(PermissionError):
        datadir.copy_data(old, new)
    assert list(new.iterdir()) == []


def test_copie_inachevee_sans_original(tmp_path):
    new = tmp_path / "neuf"
    new.mkdir()
    (new / datadir.MARKER_NAME).write_text('["profils"]', encoding="utf-8")
    vide = tmp_path / "vide"
    vide.mkdir()
    with pytest.raises(datadir.DataDirError, match="inachevée"):
        datadir.adopt_legacy(new, vide)


def test_marqueur_abime_ne_retire_rien(tmp_path):
    old = _ancien_dossier(tmp_path)
    new = tmp_path / "neuf"
    (new / "a-moi").mkdir(parents=True)
    (new / datadir.MARKER_NAME).write_text('["../a-moi"]', encoding="utf-8")
    with pytest.raises(datadir.DataDirError, match="illisible"):
        datadir.copy_data(old, new)
    assert (new / "a-moi").is_dir()
