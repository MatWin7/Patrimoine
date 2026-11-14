"""Retouches série 6 (D-076, D-078) : achats offerts, impôt retenu, Revenus, Fiscalité. FICTIF."""

from datetime import date, timedelta
from decimal import Decimal as D

import pytest
from fastapi.testclient import TestClient

from app.income import fees_by_year, fees_paid
from app.main import app
from app.models import Operation
from app.portfolio import compute_positions, daily_flows, replay_cash


def op(id_, op_type, quantity=None, price=None, offered=None, fees="0", day=date(2025, 1, 2)):
    return Operation(
        id_, 1, 1, day, op_type, quantity and D(quantity), price and D(price),
        None, D(fees), "EUR", None, offered=offered and D(offered),
    )  # fmt: skip


def test_achat_offert_au_prix_reel():
    """1 part à 12 € dont 10 € offerts (D-078) : elle entre à son prix réel, seule la hausse
    qui suit est un gain ; les espèces de l'utilisateur ne paient que 2 €."""
    ops = [op(1, "achat", "1", "12", offered="10"), op(2, "achat", "1", "14")]
    [pos] = compute_positions(ops)
    assert (pos.cost, pos.pru, pos.gain) == (D("26"), D("13"), D("2"))  # dernier prix 14 €
    # Espèces : seuls 2 € + 14 € sortent (versement supposé de 16 €) ; la performance compte
    # l'achat entier comme un apport, donc pas de gain le jour du cadeau.
    assert replay_cash(D("0"), "EUR", ops) == (D("0"), D("16"))
    assert daily_flows(ops, {})[date(2025, 1, 2)] == D("26")


def test_frais_payes_par_la_plateforme():
    """Cadeau qui couvre titres et frais : le frais compte dans le prix de revient, pas dans
    les frais payés ; s'il ne couvre qu'une partie des frais, le reste est payé."""
    gift = op(1, "achat", "1", "30", offered="31", fees="1")
    partial = op(2, "achat", "1", "30", offered="30.40", fees="1")
    plain = op(3, "achat", "1", "30", fees="1")
    assert [fees_paid(o) for o in (gift, partial, plain)] == [D("0"), D("0.60"), D("1")]
    assert fees_by_year([gift, partial, plain], {}) == {2025: D("1.60")}
    assert compute_positions([gift])[0].cost == D("31")


@pytest.fixture
def client(db_file):
    with TestClient(app) as c:
        yield c


def post(client, path, body, status=201):
    res = client.post(path, json=body)
    assert res.status_code == status, res.text
    return res.json()


@pytest.fixture
def cto(client):
    account = post(client, "/api/accounts", {"name": "CTO fictif", "kind": "cto"})
    action = post(
        client, "/api/assets", {"name": "Action fictive", "asset_class": "action", "ticker": "A.PA"}
    )
    return account["id"], action["id"]


def test_saisie_part_offerte_et_impot_retenu(client, cto):
    account, asset = cto
    base = {"account_id": account, "asset_id": asset, "op_date": "2025-01-02"}
    buy = post(
        client,
        "/api/operations",
        {**base, "op_type": "achat", "quantity": "1", "unit_price": "12", "offered": "10"},
    )
    assert buy["offered"] == "10"
    # Plus que le total de l'achat : refusé, rien d'enregistré.
    too_much = {**base, "op_type": "achat", "quantity": "1", "unit_price": "12", "offered": "13"}
    post(client, "/api/operations", too_much, status=422)
    # Sans objet sur une vente : ignoré.
    sale = {**base, "op_type": "vente", "quantity": "1", "unit_price": "15", "offered": "5"}
    assert post(client, "/api/operations", sale)["offered"] is None
    div = post(
        client,
        "/api/operations",
        {**base, "op_type": "dividende", "amount": "8.50", "tax_withheld": "1.50"},
    )
    assert div["tax_withheld"] == "1.50"
    # Correction : la part offerte retirée disparaît.
    res = client.patch(
        f"/api/operations/{buy['id']}",
        json={**base, "op_type": "achat", "quantity": "1", "unit_price": "12"},
    )
    assert res.status_code == 200 and res.json()["offered"] is None
    lines = client.get("/api/income").json()["lines"]
    assert [(line["asset"], line["withheld"]) for line in lines] == [("Action fictive", "1.50")]


def test_revenus_par_mois_calendrier_et_frais_courants(client, cto):
    account, asset = cto
    pea = post(client, "/api/accounts", {"name": "PEA fictif", "kind": "pea"})["id"]
    etf = post(
        client,
        "/api/assets",
        {"name": "ETF fictif", "asset_class": "etf", "ticker": "E.PA", "ter": "0.2"},
    )["id"]
    today = date.today()
    recent, old = today - timedelta(days=40), today - timedelta(days=400)
    for day, amount in [(recent, "30"), (old, "20")]:
        post(
            client,
            "/api/operations",
            {
                "account_id": account,
                "asset_id": asset,
                "op_date": day.isoformat(),
                "op_type": "dividende",
                "amount": amount,
            },
        )
    for where in (account, pea):  # le même ETF sur deux comptes
        post(
            client,
            "/api/operations",
            {
                "account_id": where,
                "asset_id": etf,
                "op_date": "2025-01-02",
                "op_type": "achat",
                "quantity": "10",
                "unit_price": "100",
            },
        )
    data = client.get("/api/income").json()
    d = data["dividends"]
    assert (d["last_12m"], d["prev_12m"], d["change_pct"], d["monthly"]) == (
        "30.00",
        "20.00",
        "50.00",
        "2.50",
    )
    month = next(m for m in data["months"] if m["month"] == f"{recent:%Y-%m}")
    assert month["lines"] == [{"asset": "Action fictive", "amount": "30.00"}]
    cal = data["calendar"]
    assert len(cal["months"]) == 12 and cal["months"][-1] == f"{today:%Y-%m}"
    [row] = cal["rows"]
    assert (
        row["total"] == "30.00" and row["cells"][cal["months"].index(f"{recent:%Y-%m}")] == "30.00"
    )
    # Frais courants : un bloc par compte, dans l'ordre des comptes, avec sous-total.
    ter = data["ter"]
    assert [(a["account"], a["first_year"], len(a["lines"])) for a in ter["accounts"]] == [
        ("CTO fictif", "2.00", 1),
        ("PEA fictif", "2.00", 1),
    ]
    assert ter["first_year"] == "4.00"


def test_fiscalite_latentes_et_realisees(client, cto):
    account, asset = cto
    pea = post(client, "/api/accounts", {"name": "PEA fictif", "kind": "pea"})["id"]
    base = {"asset_id": asset, "op_type": "achat", "quantity": "2", "unit_price": "10"}
    post(client, "/api/operations", {**base, "account_id": account, "op_date": "2025-01-02"})
    # Action offerte : 1 part à 10 €, entièrement offerte ; prix de revient fiscal = 10 €.
    offered = {**base, "quantity": "1", "offered": "10"}
    post(client, "/api/operations", {**offered, "account_id": account, "op_date": "2025-01-03"})
    post(client, "/api/operations", {**base, "account_id": pea, "op_date": "2025-01-02"})
    last = {**base, "quantity": "1", "unit_price": "15"}  # dernier prix connu au CTO : 15 €
    post(client, "/api/operations", {**last, "account_id": account, "op_date": "2025-01-04"})
    year = date.today().year
    sale = {**base, "op_type": "vente", "quantity": "1", "unit_price": "15"}
    post(client, "/api/operations", {**sale, "account_id": pea, "op_date": f"{year}-01-05"})
    data = client.get(f"/api/tax?year={year}&pfu=30").json()
    # 4 parts à 15 € = 60 € ; prix de revient réel 20 + 10 (offert) + 15 = 45 €.
    [row] = data["cto"]["rows"]
    assert (row["latent_gains"], row["latent_losses"], row["latent_tax"]) == (
        "15.00",
        "0.00",
        "4.50",
    )
    assert data["cto"]["latent_tax"] == "4.50"
    assert data["pea_realized"] == {"gains": "5.00", "losses": "0.00", "net": "5.00"}
    assert [s["gain_eur"] for s in data["pea_sales"]] == ["5.00"]
    assert data["pea"][0]["realized_gains"] == "5.00"
