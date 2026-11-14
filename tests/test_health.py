from fastapi.testclient import TestClient

from app import db
from app.main import app
from app.version import __version__


def test_health_ok(db_file):
    with TestClient(app) as client:  # le "with" déclenche le démarrage (création de la base)
        res = client.get("/api/health")
    assert res.status_code == 200
    assert res.json() == {
        "status": "ok",
        "app": "patrimoine",
        "version": __version__,
        "schema_version": db.SCHEMA_VERSION,
    }
    assert db_file.exists()


def test_page_web_servie(db_file):
    with TestClient(app) as client:
        res = client.get("/")
    assert res.status_code == 200
    assert "Patrimoine" in res.text


def test_fichiers_web_revalides(db_file):
    """Après une mise à jour, le navigateur ne doit pas garder un ancien app.js en cache."""
    with TestClient(app) as client:
        assert client.get("/app.js").headers["cache-control"] == "no-cache"
        # API : montants jamais gardés dans le cache disque du navigateur ou de la fenêtre (D-125).
        assert client.get("/api/health").headers["cache-control"] == "no-store"
