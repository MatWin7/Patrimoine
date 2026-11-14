"""Audit de sécurité (D-080) : requêtes venues d'un autre site, en-têtes de sécurité, date du
premier achat jamais envoyée à Yahoo."""

from datetime import date

import pytest
from fastapi.testclient import TestClient

from app import prices
from app.main import app

PEA = {"name": "PEA test", "kind": "pea"}


@pytest.mark.parametrize("site", ["cross-site", "same-site"])
def test_api_refuse_une_requete_venue_d_un_autre_site(db_file, site):
    """Un site piégé ouvert dans un autre onglet (ou une autre app sur localhost:3000)."""
    with TestClient(app) as client:
        res = client.post("/api/accounts", json=PEA, headers={"Sec-Fetch-Site": site})
        assert res.status_code == 403
        lecture = client.get("/api/accounts", headers={"Sec-Fetch-Site": site})
        assert lecture.status_code == 403
        assert client.get("/api/accounts").json() == []


def test_ecriture_refusee_si_l_origine_est_etrangere(db_file):
    """Navigateur ancien sans Sec-Fetch-Site : l'en-tête Origin suffit pour les écritures."""
    with TestClient(app) as client:
        res = client.post("/api/accounts", json=PEA, headers={"Origin": "https://piege.example"})
        assert res.status_code == 403
        assert client.get("/api/accounts").json() == []


def test_deplacement_sans_corps_refuse_depuis_un_autre_site(db_file):
    """Seule écriture possible par un simple formulaire (pas de JSON) : elle est bloquée aussi."""
    with TestClient(app) as client:
        res = client.post("/api/benchmarks/1/move?step=1", headers={"Sec-Fetch-Site": "cross-site"})
        assert res.status_code == 403


@pytest.mark.parametrize("site", ["same-origin", "none"])
def test_la_page_de_l_app_reste_acceptee(db_file, site):
    with TestClient(app, base_url="http://localhost:8000") as client:
        headers = {"Sec-Fetch-Site": site, "Origin": "http://localhost:8000"}
        assert client.post("/api/accounts", json=PEA, headers=headers).status_code == 201


def test_page_ouverte_depuis_un_lien_externe(db_file):
    """Seule l'API est protégée : un lien vers http://localhost:8000 ouvre bien la page."""
    with TestClient(app) as client:
        assert client.get("/", headers={"Sec-Fetch-Site": "cross-site"}).status_code == 200


@pytest.mark.parametrize("path", ["/", "/app.js", "/api/health"])
def test_en_tetes_de_securite(db_file, path):
    with TestClient(app) as client:
        headers = client.get(path).headers
    csp = headers["content-security-policy"]
    assert "connect-src 'self'" in csp
    assert "frame-ancestors 'none'" in csp
    assert headers["x-frame-options"] == "DENY"
    assert headers["x-content-type-options"] == "nosniff"


@pytest.mark.parametrize(
    ("first_buy", "begin"),
    [(date(2024, 6, 17), date(2024, 1, 1)), (date(2025, 1, 3), date(2024, 1, 1))],
)
def test_debut_d_historique_arrondi_a_l_annee(first_buy, begin):
    assert prices.history_begin(first_buy) == begin
