"""Chaque requête ferme les connexions SQLite qu'elle ouvre, même quand elle échoue.

Une connexion non fermée n'est libérée qu'au passage du ramasse-miettes ; sous Windows, son
fichier reste verrouillé jusque-là (« Recréer » la démo, supprimer un profil). Le test vaut pour
tous les systèmes : il relève les connexions ouvertes par `db.connect` pendant une requête et
vérifie, juste après la réponse, qu'aucune n'est restée ouverte.
"""

import re
import sqlite3
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from app import db, demo
from app.main import app

RACINE = Path(__file__).resolve().parent.parent


def ouverte(conn: sqlite3.Connection) -> bool:
    try:
        conn.total_changes  # noqa: B018 : lève ProgrammingError sur une connexion fermée
    except sqlite3.ProgrammingError:
        return False
    return True


@pytest.fixture
def suivi(db_file, monkeypatch):
    """Base de démo complète (fictive) et liste des connexions ouvertes depuis le dernier appel."""
    db.init_db(db_file)
    with db.connection(db_file) as conn:
        demo.fill_demo(conn)
    vues: list[sqlite3.Connection] = []
    vrai = db.connect

    def connect(path=None):
        conn = vrai(path)
        vues.append(conn)
        return conn

    monkeypatch.setattr(db, "connect", connect)
    with TestClient(app) as client:
        yield client, vues


def _a_plat(routes) -> list:
    out = []
    for route in routes:
        inclus = getattr(route, "original_router", None)
        out.extend(_a_plat(inclus.routes) if inclus is not None else [route])
    return out


def _verifie(client, vues, methode: str, url: str, **kwargs) -> int:
    vues.clear()
    reponse = client.request(methode, url, **kwargs)
    restees = [c for c in vues if ouverte(c)]
    assert not restees, f"{methode} {url} ({reponse.status_code}) : {len(restees)} restée(s)"
    return reponse.status_code


def test_les_lectures_ferment_leurs_connexions(suivi):
    """Toutes les routes GET sans paramètre de chemin, sur la démo complète."""
    client, vues = suivi
    chemins = [
        r.path
        for r in _a_plat(app.routes)
        if "GET" in (getattr(r, "methods", None) or ()) and r.path.startswith("/api/")
    ]
    lues = 0
    for chemin in chemins:
        if "{" in chemin:
            continue
        _verifie(client, vues, "GET", chemin)
        lues += 1
    assert lues > 30
    for asset in client.get("/api/assets").json():
        _verifie(client, vues, "GET", f"/api/assets/{asset['id']}/valuations")
    for liste in client.get("/api/watchlists").json():
        _verifie(client, vues, "GET", f"/api/watchlists/{liste['id']}/performance")


@pytest.mark.parametrize(
    ("methode", "url"),
    [
        ("DELETE", "/api/accounts/999999"),
        ("DELETE", "/api/assets/999999"),
        ("DELETE", "/api/operations/999999"),
        ("DELETE", "/api/debts/999999"),
        ("DELETE", "/api/recurring/999999"),
        ("DELETE", "/api/watchlists/999999"),
        ("DELETE", "/api/aave/lines/999999"),
        ("DELETE", "/api/aave/borrows/999999"),
        ("GET", "/api/assets/999999/valuations"),
        ("GET", "/api/watchlists/999999/performance"),
        ("POST", "/api/accounts/999999/reconcile"),
    ],
)
def test_une_requete_en_erreur_ferme_aussi_ses_connexions(suivi, methode, url):
    """Une route qui lève une erreur au milieu de son bloc (objet inconnu) laissait sa connexion
    au ramasse-miettes : c'est le cas que `db.connection` règle."""
    client, vues = suivi
    statut = _verifie(client, vues, methode, url, json={} if methode == "POST" else None)
    assert statut >= 400


def test_le_code_ouvre_la_base_par_db_connection():
    """`with db.connect() as conn` valide la transaction sans fermer la connexion : la forme
    permise dans app/ est `with db.connection() as conn` (D-141)."""
    fautes = [
        f"{f.relative_to(RACINE)}:{n}"
        for f in sorted((RACINE / "app").rglob("*.py"))
        for n, ligne in enumerate(f.read_text(encoding="utf-8").splitlines(), 1)
        if re.search(r"\bwith (db\.)?connect\(", ligne)
    ]
    assert fautes == []
