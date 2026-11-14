"""Saisie via l'API : compte, actif, opérations, total. Données FICTIVES."""

import pytest
from fastapi.testclient import TestClient

from app import db
from app.main import app


@pytest.fixture
def client(db_file):
    with TestClient(app) as c:
        yield c


@pytest.fixture
def pea(client):
    """Un compte et un ETF fictifs."""
    account = client.post("/api/accounts", json={"name": "PEA test", "kind": "pea"}).json()
    asset = client.post(
        "/api/assets",
        json={
            "name": "ETF fictif",
            "asset_class": "etf",
            "isin": "fr0000000001",
            "ticker": "fake.pa",
        },
    ).json()
    return account["id"], asset["id"]


def achat(client, pea, date, qty, price, fees="0", op_type="achat"):
    account_id, asset_id = pea
    return client.post(
        "/api/operations",
        json={
            "account_id": account_id,
            "asset_id": asset_id,
            "op_date": date,
            "op_type": op_type,
            "quantity": qty,
            "unit_price": price,
            "fees": fees,
        },
    )


def test_actif_normalise(client, pea):
    [asset] = client.get("/api/assets").json()
    assert asset["isin"] == "FR0000000001"
    assert asset["ticker"] == "FAKE.PA"
    assert asset["currency"] == "EUR"


def test_compte_en_double_refuse(client, pea):
    res = client.post("/api/accounts", json={"name": "PEA test", "kind": "pea"})
    assert res.status_code == 409


def test_actif_sans_identifiant_de_prix_refuse(client):
    res = client.post("/api/assets", json={"name": "Sans ticker", "asset_class": "etf"})
    assert res.status_code == 422


def test_deux_achats_et_total(client, pea):
    assert achat(client, pea, "2025-01-02", "10", "5.00", "1.00").status_code == 201
    res = achat(client, pea, "2025-02-03", "10", "6.00", "1.00")
    assert res.status_code == 201
    assert res.json()["unit_price"] == "6.00"  # chaîne, jamais float

    data = client.get("/api/portfolio").json()
    [pos] = data["positions"]
    assert pos["quantity"] == "20"
    assert pos["pru"] == "5.6000"
    assert pos["value"] == "120.00"  # réseau coupé en test : prix de la dernière opération
    assert pos["price_source"] == "operation"
    # Aucun versement saisi : les achats supposent 112 € versés, espèces à zéro (D-062).
    assert data["totals"] == {
        "EUR": {"value": "120.00", "cost": "112.00", "gain": "8.00", "gain_pct": "7.14"}
    }
    assert pos["gain_pct"] == "7.14" and pos["day_change"] is None  # pas de cours en ligne


@pytest.mark.parametrize(
    "date, qty, price",
    [
        ("2025-01-02", "0", "5"),  # quantité nulle
        ("2025-01-02", "-1", "5"),  # quantité négative
        ("2025-01-02", "1", "abc"),  # prix non numérique
        ("2999-01-01", "1", "5"),  # date future
    ],
)
def test_saisie_invalide_refusee(client, pea, date, qty, price):
    assert achat(client, pea, date, qty, price).status_code == 422
    assert client.get("/api/operations").json() == []


def test_vente_impossible_annulee(client, pea):
    achat(client, pea, "2025-01-02", "1", "5")
    res = achat(client, pea, "2025-01-03", "2", "5", op_type="vente")
    assert res.status_code == 422
    assert len(client.get("/api/operations").json()) == 1  # rollback : rien d'enregistré


def test_suppression_operation(client, pea):
    op_id = achat(client, pea, "2025-01-02", "1", "5").json()["id"]
    assert client.delete(f"/api/operations/{op_id}").status_code == 204
    data = client.get("/api/portfolio").json()
    assert (data["positions"], data["totals"]) == ([], {})
    assert client.delete(f"/api/operations/{op_id}").status_code == 404


def test_suppression_qui_casserait_une_vente_refusee(client, pea):
    op_id = achat(client, pea, "2025-01-02", "2", "5").json()["id"]
    achat(client, pea, "2025-01-03", "1", "5", op_type="vente")
    assert client.delete(f"/api/operations/{op_id}").status_code == 422
    assert len(client.get("/api/operations").json()) == 2


def test_compte_inconnu(client, pea):
    res = client.post(
        "/api/operations",
        json={
            "account_id": 99,
            "asset_id": pea[1],
            "op_date": "2025-01-02",
            "op_type": "achat",
            "quantity": "1",
            "unit_price": "5",
        },
    )
    assert res.status_code == 404


ASSET = {"name": "ETF fictif", "asset_class": "etf", "isin": "FR0000000001", "ticker": "FAKE.PA"}


def test_modification_actif_et_cache(client, pea, db_file):
    from app import db

    _, asset_id = pea
    with db.connect() as conn:
        conn.execute(
            "INSERT INTO price VALUES (?, '2025-01-02T10:00:00Z', '9.99', 'EUR', 'yahoo')",
            (asset_id,),
        )
    conn.close()

    def prices_in_cache():
        with db.connect() as conn:
            n = conn.execute("SELECT COUNT(*) FROM price").fetchone()[0]
        conn.close()
        return n

    res = client.patch(f"/api/assets/{asset_id}", json={**ASSET, "name": "ETF renommé"})
    assert res.status_code == 200 and res.json()["name"] == "ETF renommé"
    assert prices_in_cache() == 1  # même ticker : cache conservé
    res = client.patch(f"/api/assets/{asset_id}", json={**ASSET, "ticker": " cw8.pa "})
    assert res.json()["ticker"] == "CW8.PA"
    assert prices_in_cache() == 0  # nouveau ticker : cache vidé


def test_modification_actif_refusee(client, pea):
    _, asset_id = pea
    achat(client, pea, "2025-01-02", "1", "5")
    url = f"/api/assets/{asset_id}"
    assert client.patch(url, json={**ASSET, "ticker": None}).status_code == 422
    # Devise changée sans taux de change connu (hors ligne) : rien n'est modifié.
    assert client.patch(url, json={**ASSET, "currency": "USD"}).status_code == 503
    assert client.get("/api/assets").json()[0]["currency"] == "EUR"
    assert client.patch("/api/assets/999", json=ASSET).status_code == 404


def test_devise_actif_modifiee_avec_operations(client, pea, db_file):
    """D-083 : opérations converties au taux BCE de leur jour, cours en cache effacés."""
    _, asset_id = pea
    achat(client, pea, "2025-01-02", "2", "5", fees="1")
    achat(client, pea, "2025-01-06", "1", "8")
    conn = db.connect(db_file)
    with conn:
        conn.executemany(
            "INSERT INTO fx_rate (rate_date, currency, rate) VALUES (?, 'USD', ?)",
            [("2025-01-02", "1.25"), ("2025-01-03", "1.5")],
        )
    conn.close()
    res = client.patch(f"/api/assets/{asset_id}", json={**ASSET, "currency": "USD"})
    assert res.status_code == 200 and res.json()["currency"] == "USD"
    ops = sorted(client.get("/api/operations").json(), key=lambda o: o["op_date"])
    assert [(o["unit_price"], o["fees"], o["currency"]) for o in ops] == [
        ("6.25", "1.25", "USD"),  # taux du 2 janvier
        ("12", "0", "USD"),  # dernier taux connu avant le 6 janvier
    ]
    # Retour à l'euro : les montants d'origine reviennent.
    client.patch(f"/api/assets/{asset_id}", json={**ASSET, "currency": "EUR"})
    ops = sorted(client.get("/api/operations").json(), key=lambda o: o["op_date"])
    assert [(o["unit_price"], o["fees"]) for o in ops] == [("5", "1"), ("8", "0")]


def test_modification_compte(client, pea):
    account_id, _ = pea
    body = {"name": "PEA renommé", "kind": "pea", "institution": "Banque fictive"}
    res = client.patch(f"/api/accounts/{account_id}", json=body)
    assert res.status_code == 200
    assert res.json()["institution"] == "Banque fictive"
    client.post("/api/accounts", json={"name": "Autre", "kind": "cto"})
    assert (
        client.patch(f"/api/accounts/{account_id}", json={**body, "name": "Autre"}).status_code
        == 409
    )


def test_suppression_compte_et_actif_seulement_sans_operation(client, pea):
    account_id, asset_id = pea
    op_id = achat(client, pea, "2025-01-02", "1", "5").json()["id"]
    assert client.delete(f"/api/accounts/{account_id}").status_code == 409
    assert client.delete(f"/api/assets/{asset_id}").status_code == 409
    client.delete(f"/api/operations/{op_id}")
    assert client.delete(f"/api/accounts/{account_id}").status_code == 204
    assert client.delete(f"/api/assets/{asset_id}").status_code == 204
    assert client.get("/api/accounts").json() == client.get("/api/assets").json() == []
    assert client.delete(f"/api/assets/{asset_id}").status_code == 404


def test_especes_ajoutees_au_total(client, pea):
    """Espèces = champ « Versements » − achats (D-062), ajoutées au total."""
    account_id, _ = pea
    achat(client, pea, "2025-01-02", "10", "5.00", "1.00")
    body = {"name": "PEA test", "kind": "pea", "deposits_before": "63.5"}
    res = client.patch(f"/api/accounts/{account_id}", json=body)
    assert res.json()["cash"] == "12.50" and res.json()["cash_date"] is None
    data = client.get("/api/portfolio").json()
    totals = data["totals"]["EUR"]
    assert (totals["value"], totals["cash"], totals["total"]) == ("50.00", "12.50", "62.50")
    assert totals["gain"] == "-1.00"  # les espèces ne changent pas la plus-value
    assert data["cash"] == [
        {
            "account": "PEA test",
            "kind": "pea",
            "cash": "12.50",
            "currency": "EUR",
            "cash_date": None,
        }
    ]
    bad = {**body, "deposits_before": "-1"}
    assert client.patch(f"/api/accounts/{account_id}", json=bad).status_code == 422


def test_especes_seules_sans_position(client):
    client.post("/api/accounts", json={"name": "Livret fictif", "kind": "livret", "cash": "100"})
    totals = client.get("/api/portfolio").json()["totals"]
    assert totals["EUR"]["total"] == "100.00" and totals["EUR"]["value"] == "0.00"


def titres_detenus(client, pea, **prix):
    account_id, asset_id = pea
    body = {"account_id": account_id, "asset_id": asset_id, "op_date": "2025-01-02"}
    body |= {"op_type": "solde_initial", "quantity": "3", **prix}
    return client.post("/api/operations", json=body)


def test_titres_deja_detenus_avec_le_montant_actuel(client, pea):
    # 3 titres valant 100 € en tout : PRU = 100 / 3, arrondi à 8 décimales.
    res = titres_detenus(client, pea, amount="100")
    assert res.status_code == 201
    assert res.json()["unit_price"] == "33.33333333"
    [pos] = client.get("/api/portfolio").json()["positions"]
    assert pos["cost"] == "100.00"  # la plus-value part de zéro


def test_titres_deja_detenus_le_pru_l_emporte(client, pea):
    res = titres_detenus(client, pea, unit_price="20", amount="100")
    assert res.json()["unit_price"] == "20"


def test_prix_obligatoire_hors_titres_deja_detenus(client, pea):
    assert titres_detenus(client, pea).status_code == 422  # ni PRU ni montant
    account_id, asset_id = pea
    body = {"account_id": account_id, "asset_id": asset_id, "op_date": "2025-01-02"}
    body |= {"op_type": "achat", "quantity": "3", "amount": "100"}
    assert client.post("/api/operations", json=body).status_code == 422


def test_titres_deja_detenus_quantite_fractionnaire(client, pea):
    # Crypto fictive : une fraction de titre, PRU élevé ; quantité × PRU retombe sur le montant.
    account_id, asset_id = pea
    body = {"account_id": account_id, "asset_id": asset_id, "op_date": "2025-01-02"}
    body |= {"op_type": "solde_initial", "quantity": "0.0031517", "amount": "256.83"}
    res = client.post("/api/operations", json=body)
    assert res.json()["unit_price"] == "81489.35495130"
    [pos] = client.get("/api/portfolio").json()["positions"]
    assert pos["cost"] == "256.83"
