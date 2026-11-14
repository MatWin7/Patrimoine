"""Carte Réglages (D-130) : valeurs bornées, .env prioritaire, clé CoinGecko jamais renvoyée,
dossier de copie des sauvegardes jamais sur le réseau. Clé et chemins FICTIFS."""

import json
import sqlite3
from datetime import datetime
from pathlib import PurePosixPath

import pytest
from fastapi.testclient import TestClient

from app import backup, config, profiles, settings, updates
from app.main import app

CLE = "CG-cleFictiveDeTest1234"
ENV_NAMES = (
    "PRICE_TTL_MINUTES",
    "BACKUP_KEEP",
    "PROFILE_LOCK_MINUTES",
    "BACKUP_COPY_DIR",
    "COINGECKO_API_KEY",
)


@pytest.fixture(autouse=True)
def sans_env(monkeypatch, tmp_path):
    for name in ENV_NAMES:
        monkeypatch.delenv(name, raising=False)
    # Jamais le vrai .env : ni lu pour l'origine d'un réglage, ni recopié avec les sauvegardes.
    monkeypatch.setattr(settings, "dotenv_value", lambda name: None)
    monkeypatch.setattr(backup, "ENV_FILE", tmp_path / "absent.env")


@pytest.fixture
def client(db_file):
    with TestClient(app) as c:
        yield c


def _fichier() -> dict:
    return json.loads((profiles.DATA_DIR / "reglages.json").read_text(encoding="utf-8"))


def test_valeurs_par_defaut(client):
    fields = client.get("/api/settings").json()["fields"]
    assert fields["price_ttl_minutes"] == {
        "value": 5,
        "default": 5,
        "min": 1,
        "max": 1440,
        "locked": None,
    }
    assert fields["backup_keep"]["value"] == 14
    assert fields["profile_lock_minutes"]["value"] == 30
    assert fields["backup_copy_dir"] == {"value": None, "locked": None, "error": None}
    assert fields["coingecko_api_key"] == {"set": False, "locked": None}


def test_nombres_enregistres_et_appliques_sans_relancer(client):
    body = {"price_ttl_minutes": 15, "backup_keep": 30, "profile_lock_minutes": 60}
    assert client.put("/api/settings", json=body).status_code == 200
    assert _fichier()["reglages"] == body
    assert config.price_ttl().total_seconds() == 15 * 60
    assert config.backup_keep() == 30
    assert profiles.idle_limit().total_seconds() == 60 * 60
    # null : retour à la valeur par défaut ; un champ absent ne bouge pas.
    client.put("/api/settings", json={"price_ttl_minutes": None})
    assert _fichier()["reglages"] == {"backup_keep": 30, "profile_lock_minutes": 60}
    assert config.price_ttl().total_seconds() == 5 * 60


@pytest.mark.parametrize(
    "body",
    [
        {"price_ttl_minutes": 0},
        {"price_ttl_minutes": 1441},
        {"backup_keep": 1000},
        {"profile_lock_minutes": 0},  # 0 reviendrait à ne jamais demander le code
        {"profile_lock_minutes": 100000},
        {"price_ttl_minutes": 2.5},
        {"price_ttl_minutes": "10"},
        {"price_ttl_minutes": True},
        {"reglage_inconnu": 1},
    ],
)
def test_valeur_refusee_rien_ecrit(client, body):
    response = client.put("/api/settings", json=body)
    assert response.status_code in (400, 422)
    assert not (profiles.DATA_DIR / "reglages.json").exists()


def test_env_garde_la_main(client, monkeypatch):
    monkeypatch.setenv("PRICE_TTL_MINUTES", "9")
    field = client.get("/api/settings").json()["fields"]["price_ttl_minutes"]
    assert field["value"] == 9 and field["locked"] == "environnement"
    response = client.put("/api/settings", json={"price_ttl_minutes": 20})
    assert response.status_code == 400
    assert "PRICE_TTL_MINUTES" in response.json()["detail"]
    monkeypatch.setattr(settings, "dotenv_value", lambda name: "9")
    assert client.get("/api/settings").json()["fields"]["price_ttl_minutes"]["locked"] == ".env"


def test_valeur_abimee_du_fichier_ignoree(client):
    reglages = {"reglages": {"price_ttl_minutes": -3, "backup_keep": "beaucoup"}}
    (profiles.DATA_DIR / "reglages.json").write_text(json.dumps(reglages), encoding="utf-8")
    assert config.price_ttl().total_seconds() == 5 * 60
    assert config.backup_keep() == 14
    (profiles.DATA_DIR / "reglages.json").write_text("{pas du json", encoding="utf-8")
    assert client.get("/api/settings").status_code == 200


# --- Clé CoinGecko -----------------------------------------------------------------------------


def test_cle_enregistree_jamais_renvoyee(client):
    response = client.put("/api/settings", json={"coingecko_api_key": f"  {CLE} "})
    assert response.status_code == 200
    assert CLE not in response.text
    assert response.json()["fields"]["coingecko_api_key"]["set"] is True
    assert CLE not in client.get("/api/settings").text
    assert config.coingecko_api_key() == CLE
    # Retirer la clé.
    client.put("/api/settings", json={"coingecko_api_key": ""})
    assert config.coingecko_api_key() is None
    assert "coingecko_api_key" not in _fichier()["reglages"]


@pytest.mark.parametrize("cle", ["CG-abc\r\nX-Autre: 1", "CG abc def ghij", "court", "é" * 20])
def test_cle_invalide_refusee_sans_etre_reprise(client, cle):
    response = client.put("/api/settings", json={"coingecko_api_key": cle})
    assert response.status_code == 400
    assert cle not in response.text
    assert config.coingecko_api_key() is None


def test_cle_de_l_env_prioritaire(client, monkeypatch):
    client.put("/api/settings", json={"coingecko_api_key": CLE})
    monkeypatch.setenv("COINGECKO_API_KEY", "CG-autreCleFictive99")
    assert config.coingecko_api_key() == "CG-autreCleFictive99"
    assert "CG-autreCleFictive99" not in client.get("/api/settings").text


# --- Dossier de copie des sauvegardes ----------------------------------------------------------


def _une_sauvegarde(db_file):
    """Une copie automatique fictive dans les sauvegardes du dossier des données."""
    with sqlite3.connect(db_file) as conn:
        conn.executescript(config.ROOT.joinpath("app", "schema.sql").read_text(encoding="utf-8"))
    conn.close()
    backup.auto_backup(db_file, backup.backup_dir(), keep=14, now=datetime(2030, 1, 1, 9))


def test_dossier_de_copie_premiere_copie_tout_de_suite(client, db_file, tmp_path):
    _une_sauvegarde(db_file)
    dehors = tmp_path / "disque-d"
    dehors.mkdir()
    response = client.put("/api/settings", json={"backup_copy_dir": str(dehors)})
    assert response.status_code == 200, response.text
    assert response.json()["message"] == "1 sauvegarde(s) recopiée(s)."
    assert (dehors / "test-auto-2030-01-01-090000.db").is_file()
    assert config.backup_copy_dir() == dehors
    assert client.get("/api/settings").json()["fields"]["backup_copy_dir"]["value"] == str(dehors)
    # Vide : plus de copie.
    client.put("/api/settings", json={"backup_copy_dir": ""})
    assert config.backup_copy_dir() is None


@pytest.mark.parametrize(
    "chemin",
    [
        "sauvegardes",  # relatif
        "\\\\serveur\\partage\\Patrimoine",  # partage Windows
        "//serveur/partage/Patrimoine",
        "/mnt/c/Users/Moi/OneDrive/Patrimoine",  # synchronisé
        "/chemin/qui/n/existe/pas",
        "/tmp/a\nb",
    ],
)
def test_dossier_de_copie_refuse(client, chemin):
    response = client.put("/api/settings", json={"backup_copy_dir": chemin})
    assert response.status_code == 400
    assert not (profiles.DATA_DIR / "reglages.json").exists()


def test_dossier_de_copie_dans_les_donnees_refuse(client):
    dedans = profiles.DATA_DIR / "copie"
    dedans.mkdir()
    assert client.put("/api/settings", json={"backup_copy_dir": str(dedans)}).status_code == 400


def test_dossier_de_copie_non_inscriptible_pas_retenu(client, tmp_path, monkeypatch):
    dehors = tmp_path / "lecture-seule"
    dehors.mkdir()

    def refus(*args, **kwargs):
        raise PermissionError("accès refusé")

    monkeypatch.setattr(backup, "copy_out", refus)
    response = client.put("/api/settings", json={"backup_copy_dir": str(dehors)})
    assert response.status_code == 400
    assert "Copie impossible" in response.json()["detail"]
    assert config.backup_copy_dir() is None


def test_dossier_devenu_reseau_refuse_a_l_usage(tmp_path, monkeypatch):
    dehors = tmp_path / "nas"
    dehors.mkdir()
    settings.save({"backup_copy_dir": str(dehors)})
    assert config.backup_copy_dir() == dehors
    monkeypatch.setattr(settings, "is_network_path", lambda path: True)
    with pytest.raises(RuntimeError, match="réseau"):
        config.backup_copy_dir()


MOUNTS = """\
/dev/sdc / ext4 rw,relatime 0 0
drvfs /mnt/c 9p rw,noatime,aname=drvfs;path=C:\\134;uid=1000 0 0
drvfs /mnt/nas 9p rw,noatime,aname=drvfs;path=\\134\\134serveur\\134partage;uid=1000 0 0
//serveur/partage /mnt/smb cifs rw,vers=3.0 0 0
serveur:/export /mnt/nfs nfs4 rw 0 0
distant: /home/moi/rclone fuse.rclone rw 0 0
gvfsd-fuse /run/user/1000/gvfs fuse.gvfsd-fuse rw 0 0
/dev/sde /media/moi/USB fuseblk rw 0 0
/dev/sdd /mnt/mon\\040disque ext4 rw 0 0
"""


@pytest.mark.parametrize(
    ("chemin", "distant"),
    [
        ("/home/moi/Patrimoine", False),
        ("/mnt/c/Users/Moi/Sauvegardes", False),  # disque C: vu de WSL
        ("/mnt/nas/Patrimoine", True),  # partage Windows monté par drvfs
        ("/mnt/smb/Patrimoine", True),
        ("/mnt/nfs", True),
        ("/home/moi/rclone/x", True),
        ("/mnt/mon disque/Patrimoine", False),
        ("/run/user/1000/gvfs/smb-share:server=nas/x", True),  # partage ouvert depuis GNOME
        ("/media/moi/USB/Patrimoine", False),  # clé USB NTFS
    ],
)
def test_montages_reseau_reconnus(tmp_path, chemin, distant):
    mounts = tmp_path / "mounts"
    mounts.write_text(MOUNTS, encoding="utf-8")
    assert settings._linux_mount_is_remote(PurePosixPath(chemin), str(mounts)) is distant


# --- Fichier partagé avec l'avis de mise à jour ------------------------------------------------


def test_reglages_et_mises_a_jour_ne_s_effacent_pas(client):
    updates.set_enabled(True)
    client.put("/api/settings", json={"backup_keep": 20})
    updates.set_enabled(False)
    data = _fichier()
    assert data["reglages"] == {"backup_keep": 20}
    assert data["updates"]["enabled"] is False


def test_ecriture_refusee_depuis_un_autre_site(client):
    response = client.put(
        "/api/settings",
        json={"backup_keep": 20},
        headers={"Origin": "https://exemple.invalid", "Sec-Fetch-Site": "cross-site"},
    )
    assert response.status_code == 403
    assert not (profiles.DATA_DIR / "reglages.json").exists()


@pytest.mark.parametrize("chemin", ["\\\\serveur\\partage\\Patrimoine", "//serveur/partage/x"])
def test_partage_refuse_sans_contacter_le_serveur(monkeypatch, chemin):
    """Sous Windows, résoudre un chemin \\\\serveur ouvrirait une connexion vers ce serveur."""
    import pathlib

    def interdit(self, *args, **kwargs):
        raise AssertionError("le disque a été lu avant le contrôle du réseau")

    monkeypatch.setattr(pathlib.Path, "resolve", interdit)
    monkeypatch.setattr(pathlib.Path, "is_dir", interdit)
    with pytest.raises(RuntimeError, match="réseau|chemin complet"):
        settings.check_copy_dir(chemin)


MAC = """\
/dev/disk3s1s1 on / (apfs, sealed, local, read-only, journaled)
/dev/disk3s5 on /System/Volumes/Data (apfs, local, journaled, nobrowse)
//moi@nas/partage on /Volumes/NAS (smbfs, nodev, nosuid, mounted by moi)
/dev/disk4s1 on /Volumes/Cle USB (exfat, local, nodev, nosuid, noowners)
"""


@pytest.mark.parametrize(
    ("chemin", "distant"),
    [
        ("/Users/moi/Sauvegardes", False),
        ("/Volumes/NAS/Patrimoine", True),
        ("/Volumes/Cle USB/Patrimoine", False),
    ],
)
def test_montages_reseau_macos(chemin, distant):
    assert settings._mac_mount_is_remote(PurePosixPath(chemin), MAC) is distant
    assert settings._mac_mount_is_remote(PurePosixPath(chemin), "sortie illisible") is True


def test_lien_vers_un_dossier_synchronise_refuse(tmp_path):
    cloud = tmp_path / "OneDrive" / "Patrimoine"
    cloud.mkdir(parents=True)
    lien = tmp_path / "Sauvegardes"
    try:
        lien.symlink_to(cloud, target_is_directory=True)
    except OSError:
        pytest.skip("liens symboliques non permis sur ce poste")
    with pytest.raises(RuntimeError, match="synchronisé"):
        settings.check_copy_dir(str(lien))


def test_tilde_developpe_avant_la_copie(client, monkeypatch, tmp_path):
    maison = tmp_path / "maison"
    (maison / "Sauvegardes").mkdir(parents=True)
    monkeypatch.setenv("HOME", str(maison))
    monkeypatch.setenv("USERPROFILE", str(maison))
    vers = []
    monkeypatch.setattr(backup, "copy_out", lambda dest: vers.append(dest) or [])
    response = client.put("/api/settings", json={"backup_copy_dir": "~/Sauvegardes"})
    assert response.status_code == 200, response.text
    assert vers == [maison / "Sauvegardes"]
    assert _fichier()["reglages"]["backup_copy_dir"] == str(maison / "Sauvegardes")
