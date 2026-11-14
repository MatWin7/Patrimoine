"""Durcissement (D-039) : noms de site acceptés, pas de page /docs, jour local, base affichée."""

import os
import subprocess
import sys
import time
from datetime import UTC, date, datetime

import pytest
from fastapi.testclient import TestClient

from app import db
from app.main import app


@pytest.mark.parametrize("base_url", ["http://localhost:8000", "http://127.0.0.1:8000"])
def test_noms_de_site_locaux_acceptes(db_file, base_url):
    with TestClient(app, base_url=base_url) as client:
        assert client.get("/api/health").status_code == 200


@pytest.mark.parametrize("path", ["/api/accounts", "/api/portfolio", "/"])
def test_nom_de_site_etranger_refuse(db_file, path):
    """Une page piégée qui ferait pointer son domaine vers 127.0.0.1 (DNS rebinding)."""
    with TestClient(app) as client:
        res = client.get(path, headers={"Host": "attaquant.example:8000"})
    assert res.status_code == 400


def test_suppression_refusee_depuis_un_nom_etranger(db_file):
    with TestClient(app) as client:
        account = client.post("/api/accounts", json={"name": "PEA test", "kind": "pea"}).json()
        res = client.delete(f"/api/accounts/{account['id']}", headers={"Host": "attaquant.example"})
        assert res.status_code == 400
        assert len(client.get("/api/accounts").json()) == 1


@pytest.mark.parametrize("path", ["/docs", "/redoc"])
def test_pas_de_page_docs_qui_charge_un_cdn(db_file, path):
    with TestClient(app) as client:
        res = client.get(path)
    assert "cdn.jsdelivr.net" not in res.text
    assert res.status_code == 404


ENFANT = "PATRIMOINE_TEST_FUSEAU"  # posé dans le processus de test relancé sous Windows


@pytest.fixture
def fuseau_decale(request):
    """Fuseau où la date locale diffère de la date UTC à l'instant du test.

    Linux et macOS changent de fuseau en cours de route (time.tzset). Windows ne le sait pas :
    le test y est relancé dans un processus Python neuf, qui lit la variable TZ au démarrage
    (D-118). Renvoie False quand ce processus enfant a déjà tout vérifié.
    """
    # Nom POSIX : « AAA-14 » = UTC+14 (en avance), « AAA+12 » = UTC-12 (en retard), signe
    # inversé. Syntaxe comprise par Linux, macOS et Windows.
    tz = "AAA-14" if datetime.now(UTC).hour >= 10 else "AAA+12"
    if os.environ.get(ENFANT):  # processus enfant : TZ lu au démarrage
        assert date.today() != datetime.now(UTC).date(), "TZ ignoré par ce système"
        yield True
        return
    if not hasattr(time, "tzset"):  # Windows
        enfant = subprocess.run(
            [sys.executable, "-m", "pytest", "-q", "-p", "no:cacheprovider", request.node.nodeid],
            env={**os.environ, "TZ": tz, ENFANT: "1"},
            cwd=request.config.rootpath,
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            check=False,
        )
        assert enfant.returncode == 0, enfant.stdout[-3000:]
        yield False
        return
    old = os.environ.get("TZ")
    os.environ["TZ"] = tz
    time.tzset()
    assert date.today() != datetime.now(UTC).date()
    yield True
    if old is None:
        del os.environ["TZ"]
    else:
        os.environ["TZ"] = old
    time.tzset()


def test_historique_finit_au_jour_local(db_file, fuseau_decale):
    """Une opération datée d'aujourd'hui (heure locale) apparaît dans la courbe."""
    if not fuseau_decale:
        return  # Windows : vérifié dans le processus enfant
    today = date.today()
    with TestClient(app) as client:
        account = client.post("/api/accounts", json={"name": "PEA test", "kind": "pea"}).json()
        asset = client.post(
            "/api/assets", json={"name": "ETF fictif", "asset_class": "etf", "ticker": "FAKE.PA"}
        ).json()
        res = client.post(
            "/api/operations",
            json={
                "account_id": account["id"],
                "asset_id": asset["id"],
                "op_date": today.isoformat(),
                "op_type": "achat",
                "quantity": "2",
                "unit_price": "10",
            },
        )
        assert res.status_code == 201
        points = client.get("/api/history").json()["points"]
    assert points[-1]["date"] == today.isoformat()
    assert points[-1]["invested"] == "20.00"


def test_describe_base_absente(tmp_path):
    assert "absente" in db.describe(tmp_path / "nulle-part.db")


def test_describe_compte_les_operations_sans_montant(db_file):
    db.init_db()
    with db.connect() as conn:
        conn.execute("INSERT INTO account (name, kind) VALUES ('Compte fictif', 'pea')")
        conn.execute(
            "INSERT INTO asset (name, asset_class, ticker, currency)"
            " VALUES ('ETF fictif', 'etf', 'FAKE.PA', 'EUR')"
        )
        for day in ("2025-01-02", "2025-02-03"):
            conn.execute(
                "INSERT INTO operation (account_id, asset_id, op_date, op_type, quantity,"
                " unit_price, currency) VALUES (1, 1, ?, 'achat', '3', '12.34', 'EUR')",
                (day,),
            )
    conn.close()
    text = db.describe()
    assert str(db_file) in text and "2 opérations" in text
    assert "12.34" not in text


def test_describe_fichier_qui_n_est_pas_une_base(tmp_path):
    path = tmp_path / "faux.db"
    path.write_text("pas une base SQLite", encoding="utf-8")
    assert "illisible" in db.describe(path)
