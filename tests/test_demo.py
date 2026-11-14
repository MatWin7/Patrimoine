"""Démo « grand portefeuille » (D-051) : données FICTIVES, dans une base jetable."""

from fastapi.testclient import TestClient

from app import db, demo
from app.main import app


def test_demo_grand_portefeuille(db_file):
    db.init_db(db_file)
    with db.connect(db_file) as conn:
        demo.fill_grand(conn)
    conn.close()
    with TestClient(app) as client:
        data = client.get("/api/portfolio").json()
    # 30 lignes cotées, plus un fonds euros, un fonds de PER et un appartement à valeur saisie.
    assert len(data["positions"]) == len(demo.GRAND_LINES) + 3 == 33
    assert all(not c["cash"].startswith("-") for c in data["cash"])  # versements suffisants
    assert data["fx_error"] is None  # taux USD fictif enregistré : rien à chercher en ligne
    ligne = data["groupings"]["ligne"]
    # 33 lignes + le livret : 7 + « Autres » (27 lignes) + « Espèces »
    assert len(ligne["top"]) == 9 and ligne["others"] == 27
    assert [p["label"] for p in data["groupings"]["devise"]["all"]] == ["EUR", "USD"]
