"""Lecture du fichier .env et des réglages (valeurs FICTIVES)."""

import os
from datetime import timedelta

import pytest

from app import config


def test_dotenv_sans_ecraser_l_existant(tmp_path, monkeypatch):
    env = tmp_path / ".env"
    env.write_text(
        "# commentaire\nPRICE_TTL_MINUTES=7\nCOINGECKO_API_KEY=\nPATRIMOINE_DB='x.db'\n",
        encoding="utf-8",
    )
    monkeypatch.setenv("PATRIMOINE_DB", "deja-defini.db")
    config.load_dotenv(env)
    assert os.environ["PRICE_TTL_MINUTES"] == "7"
    assert os.environ["PATRIMOINE_DB"] == "deja-defini.db"  # la variable du shell gagne
    assert config.coingecko_api_key() is None  # ligne vide : pas de clé
    assert config.price_ttl() == timedelta(minutes=7)


def test_ttl_par_defaut_et_invalide(monkeypatch):
    assert config.price_ttl() == timedelta(minutes=5)
    monkeypatch.setenv("PRICE_TTL_MINUTES", "cinq")
    with pytest.raises(RuntimeError):
        config.price_ttl()


def test_backup_keep(monkeypatch):
    monkeypatch.delenv("BACKUP_KEEP", raising=False)
    assert config.backup_keep() == 14
    monkeypatch.setenv("BACKUP_KEEP", "0")
    assert config.backup_keep() == 1  # on garde toujours au moins la dernière copie
    monkeypatch.setenv("BACKUP_KEEP", "beaucoup")
    with pytest.raises(RuntimeError):
        config.backup_keep()


def test_backup_copy_dir(monkeypatch, tmp_path):
    monkeypatch.delenv("BACKUP_COPY_DIR", raising=False)
    assert config.backup_copy_dir() is None
    monkeypatch.setenv("BACKUP_COPY_DIR", str(tmp_path / "Patrimoine-sauvegardes"))
    assert config.backup_copy_dir() == tmp_path / "Patrimoine-sauvegardes"
    for refuse in (
        "sauvegardes",  # chemin relatif
        "/mnt/c/Users/Moi/OneDrive/Patrimoine",  # cloud
        "/mnt/c/Users/Moi/Dropbox/Patrimoine",
        str(config.ROOT / "data" / "copie"),  # même disque que la base
    ):
        monkeypatch.setenv("BACKUP_COPY_DIR", refuse)
        with pytest.raises(RuntimeError):
            config.backup_copy_dir()
