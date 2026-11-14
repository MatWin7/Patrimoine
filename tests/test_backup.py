"""Sauvegarde de la base (base jetable, données FICTIVES)."""

import sqlite3
from datetime import datetime

import pytest

from app import backup as backup_module
from app import db, profiles
from app.backup import (
    ENV_COPY,
    BackupError,
    auto_backup,
    backup,
    check,
    copy_out,
    daily,
    kept_names,
    registry_backup,
    rotate,
    verify,
)


def test_sauvegarde_copie_complete(db_file, tmp_path):
    db.init_db()
    with db.connect() as conn:
        conn.execute("INSERT INTO account (name, kind) VALUES ('Compte fictif', 'pea')")
    conn.close()
    target = backup(target_dir=tmp_path / "sauvegardes")
    assert target.name.startswith("test-") and target.suffix == ".db"
    copy = sqlite3.connect(target)
    assert copy.execute("SELECT name FROM account").fetchall() == [("Compte fictif",)]
    copy.close()


def test_sauvegarde_base_absente(tmp_path):
    with pytest.raises(FileNotFoundError):
        backup(tmp_path / "absente.db", tmp_path)


def _base_fictive(db_file):
    db.init_db()
    return db_file


def test_sauvegarde_auto_une_par_jour(db_file, tmp_path):
    source = _base_fictive(db_file)
    dossier = tmp_path / "sauvegardes"
    matin = datetime(2030, 1, 15, 9, 0, 0)
    premiere = auto_backup(source, dossier, keep=14, now=matin)
    assert premiere is not None and premiere.name == "test-auto-2030-01-15-090000.db"
    # Deuxième lancement le même jour : pas de nouvelle copie.
    assert auto_backup(source, dossier, keep=14, now=matin.replace(hour=18)) is None
    assert auto_backup(source, dossier, keep=14, now=datetime(2030, 1, 16, 8)) is not None


def test_sauvegarde_auto_rotation_garde_les_manuelles(db_file, tmp_path):
    source = _base_fictive(db_file)
    dossier = tmp_path / "sauvegardes"
    manuelle = backup(source, dossier, now=datetime(2030, 1, 1, 12))
    for jour in range(1, 6):
        auto_backup(source, dossier, keep=3, now=datetime(2030, 1, jour, 9))
    restantes = sorted(p.name for p in dossier.glob("test-auto-*.db"))
    assert restantes == [f"test-auto-2030-01-0{j}-090000.db" for j in (3, 4, 5)]
    assert manuelle.is_file()


def test_sauvegarde_auto_sans_base(tmp_path):
    assert auto_backup(tmp_path / "absente.db", tmp_path / "sauvegardes") is None
    assert not (tmp_path / "sauvegardes").exists()


# --- D-114 : rotation « grand-père, père, fils », vérification, copie hors de WSL -----------


def _nom(jour: datetime) -> str:
    return f"base-auto-{jour:%Y-%m-%d-%H%M%S}.db"


def test_rotation_garde_jours_mois_et_annees():
    # Une copie par jour, du 1er janvier 2028 au 15 mars 2030 : environ 800 copies.
    debut = datetime(2028, 1, 1, 9).toordinal()
    jours = [datetime.fromordinal(o).replace(hour=9) for o in range(debut, debut + 805)]
    gardees = kept_names([_nom(j) for j in jours], keep=14)
    dernier = jours[-1]
    # Les 14 derniers jours.
    assert {_nom(j) for j in jours[-14:]} <= gardees
    # La dernière copie de chacun des 12 derniers mois (mars 2030 compris).
    fins_de_mois = {}
    for j in jours:
        fins_de_mois[(j.year, j.month)] = _nom(j)
    douze = sorted(fins_de_mois)[-12:]
    assert {fins_de_mois[m] for m in douze} <= gardees
    # La dernière de chaque année, sans limite.
    assert {_nom(datetime(2028, 12, 31, 9)), _nom(datetime(2029, 12, 31, 9))} <= gardees
    # Rien d'autre : 14 jours + 11 autres fins de mois + fin 2028 (fin 2029 est un des 12 mois).
    assert len(gardees) == 14 + 11 + 1
    assert _nom(datetime(2028, 6, 30, 9)) not in gardees
    assert dernier.year == 2030


def test_rotation_ne_touche_que_les_copies_automatiques(tmp_path):
    for jour in range(1, 21):
        (tmp_path / f"base-auto-2030-01-{jour:02d}-090000.db").write_bytes(b"")
    autres = [
        "base-2030-01-01-120000.db",  # sauvegarde manuelle
        "base-avant-suppression-2030-01-02-120000.db",
        "base-auto-2030-01-03-090000.db.partiel",
        "notes.txt",
    ]
    for nom in autres:
        (tmp_path / nom).write_bytes(b"")
    supprimees = rotate(tmp_path, keep=5)
    assert len(supprimees) == 15
    restantes = sorted(p.name for p in tmp_path.iterdir())
    assert restantes == sorted(autres + [f"base-auto-2030-01-{j}-090000.db" for j in range(16, 21)])


def test_rotation_par_base(tmp_path):
    # Deux bases dans le même dossier : chacune garde ses propres copies.
    for jour in range(1, 6):
        for base in ("alice", "bob"):
            (tmp_path / f"{base}-auto-2030-01-0{jour}-090000.db").write_bytes(b"")
    (tmp_path / "profils-auto-2030-01-01-090000.json").write_bytes(b"{}")
    rotate(tmp_path, keep=2)
    noms = sorted(p.name for p in tmp_path.iterdir())
    assert noms == [
        "alice-auto-2030-01-04-090000.db",
        "alice-auto-2030-01-05-090000.db",
        "bob-auto-2030-01-04-090000.db",
        "bob-auto-2030-01-05-090000.db",
        "profils-auto-2030-01-01-090000.json",
    ]


def test_copie_abimee_signalee(tmp_path):
    abimee = tmp_path / "base-auto-2030-01-01-090000.db"
    abimee.write_bytes(b"ceci n'est pas une base SQLite" * 100)
    with pytest.raises(BackupError):
        verify(abimee)


def test_sauvegarde_auto_relue(db_file, tmp_path, monkeypatch):
    source = _base_fictive(db_file)
    dossier = tmp_path / "sauvegardes"

    def copie_abimee(*args, **kwargs):
        dossier.mkdir(exist_ok=True)
        cible = dossier / "test-auto-2030-01-01-090000.db"
        cible.write_bytes(b"x" * 4096)
        return cible

    monkeypatch.setattr(backup_module, "backup", copie_abimee)
    with pytest.raises(BackupError):
        auto_backup(source, dossier, keep=14, now=datetime(2030, 1, 1, 9))
    assert not list(dossier.glob("*.db"))  # la copie abîmée n'est pas gardée


def _profils_fictifs(tmp_path, monkeypatch):
    """Dossier data/ fictif avec un profils.json (aucun montant)."""
    data = tmp_path / "data"
    data.mkdir()
    (data / "profils.json").write_text('{"profils": []}', encoding="utf-8")
    monkeypatch.setattr(profiles, "DATA_DIR", data)
    monkeypatch.delenv("PATRIMOINE_DB", raising=False)
    return data


def test_copie_de_profils_json(tmp_path, monkeypatch):
    _profils_fictifs(tmp_path, monkeypatch)
    dossier = tmp_path / "sauvegardes"
    copie = registry_backup(dossier, keep=14, now=datetime(2030, 1, 1, 9))
    assert copie is not None and copie.name == "profils-auto-2030-01-01-090000.json"
    assert copie.read_text(encoding="utf-8") == '{"profils": []}'
    assert registry_backup(dossier, keep=14, now=datetime(2030, 1, 1, 18)) is None


def test_pas_de_copie_de_profils_json_sans_profils(tmp_path, monkeypatch):
    _profils_fictifs(tmp_path, monkeypatch)
    monkeypatch.setenv("PATRIMOINE_DB", str(tmp_path / "demo.db"))  # base unique forcée
    assert registry_backup(tmp_path / "sauvegardes") is None


def test_copie_hors_wsl(db_file, tmp_path):
    source = _base_fictive(db_file)
    local = tmp_path / "sauvegardes"
    for jour in (1, 2):
        auto_backup(source, local, keep=14, now=datetime(2030, 1, jour, 9))
    backup(source, local, now=datetime(2030, 1, 2, 12))  # manuelle : reste locale
    reglages = tmp_path / "reglages-fictifs"
    reglages.write_text("PRICE_TTL_MINUTES=5\n", encoding="utf-8")
    dehors = tmp_path / "disque-d" / "Patrimoine-sauvegardes"
    dehors.parent.mkdir()

    copiees = copy_out(dehors, local, env_file=reglages, keep=14)
    assert sorted(p.name for p in copiees) == [
        "test-auto-2030-01-01-090000.db",
        "test-auto-2030-01-02-090000.db",
    ]
    assert (dehors / ENV_COPY).read_text(encoding="utf-8") == "PRICE_TTL_MINUTES=5\n"
    # Deuxième passage : rien de nouveau à copier.
    assert copy_out(dehors, local, env_file=reglages, keep=14) == []
    # La copie hors de WSL est une base lisible.
    copie = sqlite3.connect(dehors / "test-auto-2030-01-02-090000.db")
    assert copie.execute("SELECT count(*) FROM account").fetchone() == (0,)
    copie.close()


def test_copie_hors_wsl_rotation(db_file, tmp_path):
    source = _base_fictive(db_file)
    local = tmp_path / "sauvegardes"
    dehors = tmp_path / "Patrimoine-sauvegardes"
    for jour in range(1, 6):
        auto_backup(source, local, keep=3, now=datetime(2030, 1, jour, 9))
        copy_out(dehors, local, env_file=tmp_path / "absent", keep=3)
    assert sorted(p.name for p in dehors.iterdir()) == [
        f"test-auto-2030-01-0{j}-090000.db" for j in (3, 4, 5)
    ]


def test_copie_hors_wsl_disque_debranche(tmp_path):
    with pytest.raises(BackupError, match="débranché"):
        copy_out(tmp_path / "absent" / "Patrimoine-sauvegardes", tmp_path)


def test_sauvegarde_du_jour_complete(db_file, tmp_path, monkeypatch):
    source = _base_fictive(db_file)
    dehors = tmp_path / "Patrimoine-sauvegardes"
    monkeypatch.setenv("BACKUP_COPY_DIR", str(dehors))
    monkeypatch.setattr(backup_module, "ENV_FILE", tmp_path / "absent")  # jamais le vrai
    messages = daily([source], tmp_path / "sauvegardes", now=datetime(2030, 1, 1, 9))
    assert messages[0].startswith("Sauvegarde du jour")
    assert messages[1].startswith("Copie hors de WSL : 1 fichier")
    assert (dehors / "test-auto-2030-01-01-090000.db").is_file()
    # Une heure plus tard : rien à refaire, rien à dire.
    assert daily([source], tmp_path / "sauvegardes", now=datetime(2030, 1, 1, 10)) == []


def test_sauvegarde_du_jour_disque_absent_sans_planter(db_file, tmp_path, monkeypatch):
    source = _base_fictive(db_file)
    monkeypatch.setenv("BACKUP_COPY_DIR", str(tmp_path / "absent" / "Patrimoine-sauvegardes"))
    messages = daily([source], tmp_path / "sauvegardes", now=datetime(2030, 1, 1, 9))
    assert messages[0].startswith("Sauvegarde du jour")
    assert messages[1].startswith("Attention, copie hors de WSL impossible")


def test_verifier(db_file, tmp_path):
    source = _base_fictive(db_file)
    local = tmp_path / "sauvegardes"
    auto_backup(source, local, keep=14, now=datetime(2030, 1, 1, 9))
    vide = tmp_path / "vide"
    lignes = check([source], [local, vide])
    assert lignes[1].startswith("  OK ") and "schéma v" in lignes[1]
    assert "0 compte(s), 0 opération(s)" in lignes[1]
    assert lignes[3] == "  AUCUNE copie de test.db"
