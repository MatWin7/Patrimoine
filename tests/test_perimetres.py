"""Patrimoine brut, net ou financier dans Vue d'ensemble (D-143). Données FICTIVES ; actifs à
valeur saisie seulement : aucune requête ne part."""

from datetime import date
from decimal import Decimal as D

import pytest
from fastapi.testclient import TestClient

from app import db, repository
from app.main import app


@pytest.fixture
def client(db_file):
    with TestClient(app) as c:
        yield c


def _fill() -> None:
    """Un contrat d'assurance-vie (fonds euros 1 000 €, SCPI 2 000 €), un bien immobilier
    (200 000 €), un livret (5 000 €), un compte « Autre » (1 500 € d'or achetés sur 3 000 €
    d'espèces saisies : il en reste 1 500 €)."""
    with db.connection() as conn:

        def buy(account, asset, quantity, price):
            repository.create_operation(
                conn, account.id, asset.id, date(2024, 3, 1), "achat",
                D(quantity), D(price), None, D("0"), "EUR", None,
            )  # fmt: skip

        av = repository.create_account(conn, "AV fictive", "assurance_vie", None, "EUR")
        immo = repository.create_account(conn, "Maison fictive", "immobilier", None, "EUR")
        repository.create_account(conn, "Livret fictif", "livret", None, "EUR", D("5000"))
        banque = repository.create_account(conn, "Banque fictive", "autre", None, "EUR", D("3000"))

        def new(name, asset_class):
            return repository.create_asset(conn, name, asset_class, None, None, None, "EUR")

        buy(av, new("Fonds euros fictif", "fonds_euros"), "1000", "1")
        buy(av, new("SCPI fictive", "scpi"), "10", "200")
        buy(immo, new("Appartement fictif", "immobilier"), "1", "200000")
        buy(banque, new("Pièces fictives", "or"), "1", "1500")
        conn.commit()


def test_trois_perimetres(client):
    _fill()
    debt = {"name": "Prêt fictif", "kind": "immobilier", "principal": "50000",
            "start_date": "2024-01-01"}  # fmt: skip
    assert client.post("/api/debts", json=debt).status_code == 201

    brut = client.get("/api/portfolio").json()
    assert brut["scope"] == "brut"
    assert brut["scopes"] == {"brut": "211000.00", "net": "161000.00", "financier": "2500.00"}
    assert brut["totals"]["EUR"]["total"] == "211000.00"
    net = client.get("/api/portfolio?scope=net").json()
    assert (net["scope"], net["totals"], net["scopes"]) == ("net", brut["totals"], brut["scopes"])

    fin = client.get("/api/portfolio?scope=financier").json()
    assert fin["scopes"] == brut["scopes"]  # les trois chiffres, quel que soit le périmètre
    t = fin["totals"]["EUR"]
    # Investi et plus-value sur le même périmètre ; ni livret ni espèces du compte « Autre ».
    assert (t["value"], t["cost"], t["gain"]) == ("2500.00", "2500.00", "0.00")
    assert "cash" not in t and fin["cash"] == []
    assert sorted(p["asset"] for p in fin["positions"]) == ["Fonds euros fictif", "Pièces fictives"]
    labels = {part["label"] for part in fin["allocation"]}
    assert labels == {"Fonds euros", "Or"}
    assert sum(D(p["pct"]) for p in fin["allocation"]) == 100
    assert client.get("/api/portfolio?scope=autre").status_code == 422


def test_sans_dette_net_egal_brut_et_filtre_suivi(client):
    _fill()
    out = client.get("/api/portfolio").json()["scopes"]
    assert out["net"] == out["brut"] == "211000.00"
    with db.connection() as conn:
        banque = next(a for a in repository.list_accounts(conn) if a.kind == "autre")
    # Le filtre de lignes s'applique avant le périmètre : compte « Autre » exclu en entier.
    fin = client.get(f"/api/portfolio?scope=financier&exclude_account={banque.id}").json()
    assert fin["scopes"]["financier"] == "1000.00" and fin["filtered"] is True
    assert fin["scopes"]["brut"] == "208000.00"
