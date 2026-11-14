"""Onglet Crypto (D-092) : récompenses reçues, type de récompense. Données FICTIVES."""

from datetime import date, timedelta
from decimal import Decimal as D

import pytest
from fastapi.testclient import TestClient

from app import crypto
from app.importers import binance, csv_generique
from app.main import app
from app.models import Account, Asset, Operation
from app.portfolio import RateHistory

ACCOUNTS = {
    1: Account(1, "Crypto fictif", "crypto", None, "EUR"),
    2: Account(2, "CTO", "cto", None, "EUR"),
}
ASSETS = {
    1: Asset(1, "Jeton fictif", "crypto", None, None, "fictif", "USD"),
    2: Asset(2, "Action fictive", "action", None, "A.PA", None, "EUR"),
}
FX = RateHistory({"USD": D("1.25")})  # 1 EUR = 1,25 $ (fictif)


def op(id_, op_type, quantity, price, day, *, account=1, asset=1, offered=None, kind=None):
    return Operation(
        id_, account, asset, day, op_type, D(quantity), D(price), None, D("0"),
        "USD" if asset == 1 else "EUR", None, offered=offered and D(offered), reward_kind=kind,
    )  # fmt: skip


def test_seule_la_part_offerte_d_une_crypto_compte():
    ops = [
        op(1, "achat", "10", "5", date(2025, 1, 2)),  # achat ordinaire : pas une récompense
        op(2, "achat", "1", "5", date(2025, 2, 1), offered="5", kind="earn"),
        op(3, "achat", "2", "5", date(2025, 3, 1), offered="2.5"),  # poussière : 1/4 offert
        op(4, "achat", "1", "12", date(2025, 3, 1), account=2, asset=2, offered="10"),  # Saveback
    ]
    items = crypto.rewards(ops, ACCOUNTS, ASSETS, FX)
    assert [(r.operation_id, r.kind, r.quantity, r.value) for r in items] == [
        (2, "earn", D("1"), D("4")),
        (3, "autre", D("0.5"), D("2")),
    ]


def test_rendement_en_jetons_sur_douze_mois():
    """100 jetons détenus toute l'année, 5 reçus en récompense : ≈ 5 % par an, quel que soit
    le cours ; moins de 30 jours détenus : pas de rendement."""
    today = date(2025, 12, 31)
    start = today - timedelta(days=364)
    ops = [op(1, "achat", "100", "1", start - timedelta(days=10))]
    total, days = crypto.held_days(ops, 1, {1}, start, today)
    assert days == 365 and total == D("36500")
    assert crypto.token_yield(D("5"), total, days) == D("0.05")
    assert crypto.token_yield(D("1"), D("100"), 10) is None


def test_synthese_par_jeton_type_et_mois():
    today = date(2025, 6, 30)
    ops = [
        op(1, "achat", "100", "1", date(2024, 1, 2)),
        op(2, "achat", "2", "1", date(2025, 1, 15), offered="2", kind="staking"),
        op(3, "achat", "1", "1", date(2025, 2, 15), offered="1", kind="airdrop"),
    ]
    items = crypto.rewards(ops, ACCOUNTS, ASSETS, FX)
    out = crypto.summary(items, ops, ACCOUNTS, ASSETS, {1: D("2")}, {1: D("103")}, today)
    assert (out["total"], out["ytd"], out["now"], out["now_partial"]) == (
        D("2.4"),
        D("2.4"),
        D("6"),  # 3 jetons à 2 € aujourd'hui
        False,
    )
    [row] = out["assets"]
    assert row["quantity"] == D("3") and row["share_held"] == D("3") / D("103")
    assert row["yield_12m"] is not None and D("0.02") < row["yield_12m"] < D("0.04")
    assert [k["kind"] for k in out["kinds"]] == ["staking", "airdrop"]
    assert out["months"]["2025-01"] == {1: D("1.6")}
    assert out["months_kind"]["2025-02"] == {"airdrop": D("0.8")}


def test_type_des_libelles_binance():
    assert binance.reward_kind("Simple Earn Flexible Interest") == "earn"
    assert binance.reward_kind("Launchpool Interest") == "airdrop"
    assert binance.reward_kind("ETH 2.0 Staking Rewards") == "staking"
    assert binance.reward_kind("HODLer Airdrops Distribution") == "airdrop"
    assert binance.reward_kind("Card Cashback") == "cashback"
    assert binance.reward_kind("Referral Commission") == "parrainage"
    assert binance.reward_kind("Crypto Box") == "autre"


def test_import_binance_un_gain_par_type_dans_le_mois():
    """Intérêts Earn et airdrop du même jeton le même mois : deux achats offerts typés ; un
    seul type dans le mois garde la clé d'avant D-092 (pas de doublon au réimport)."""
    entete = "User_ID,UTC_Time,Account,Operation,Coin,Change,Remark"
    lignes = [
        "1,2025-01-02 10:00:00,Spot,Deposit,BNB,1,",
        "1,2025-02-10 01:00:00,Spot,Simple Earn Flexible Interest,BNB,0.001,",
        "1,2025-02-12 01:00:00,Spot,Airdrop Assets,BNB,0.002,",
        "1,2025-03-10 01:00:00,Spot,Simple Earn Flexible Interest,BNB,0.001,",
        "1,2025-04-01 10:00:00,Spot,Deposit,EUR,1,",
    ]
    rows = csv_generique.read_rows("\n".join([entete, *lignes]))
    closes = {"BNB": [(date(2025, 1, 1), D("500"))]}
    pricer = binance.Pricer([(date(2025, 1, 1), D("1.25"))], closes)
    lines = binance.parse(rows[0], rows[1:], 2, pricer)
    gains = {x.key: x.reward_kind for x in lines if x.key and x.key.startswith("gains|")}
    assert gains == {
        "gains|BNB|2025-02|earn": "earn",
        "gains|BNB|2025-02|airdrop": "airdrop",
        "gains|BNB|2025-03": "earn",
    }


@pytest.fixture
def client(db_file):
    with TestClient(app) as c:
        yield c


def post(client, path, body, status=201):
    res = client.post(path, json=body)
    assert res.status_code == status, res.text
    return res.json()


def test_api_recompenses_et_saisie_du_type(client):
    account = post(client, "/api/accounts", {"name": "Crypto fictif", "kind": "crypto"})
    asset = post(
        client,
        "/api/assets",
        {
            "name": "Jeton fictif",
            "asset_class": "crypto",
            "coingecko_id": "fictif",
            "currency": "EUR",
        },
    )
    base = {"account_id": account["id"], "asset_id": asset["id"], "op_type": "achat"}
    buy = {**base, "op_date": "2025-01-02", "quantity": "1", "unit_price": "10"}
    assert post(client, "/api/operations", {**buy, "reward_kind": "earn"})["reward_kind"] is None
    gift = {**buy, "op_date": "2025-02-01", "offered": "10", "reward_kind": "staking"}
    assert post(client, "/api/operations", gift)["reward_kind"] == "staking"
    post(client, "/api/operations", {**gift, "reward_kind": "inconnu"}, status=422)
    out = client.get("/api/crypto/rewards").json()
    assert out["total"] == "10.00" and out["kinds"][0]["kind"] == "staking"
    [row] = out["assets"]
    assert (row["asset"], row["quantity"], row["held"]) == ("Jeton fictif", "1", "2")
    assert out["recent"][0]["kind"] == "staking"
    assert len(out["months"]) == 24


def test_frais_crypto_dans_l_onglet_crypto_pas_dans_revenus(client):
    """Revenus et frais se concentre sur la bourse : les frais crypto vont dans l'onglet Crypto."""
    account = post(client, "/api/accounts", {"name": "Crypto fictif", "kind": "crypto"})
    cto = post(client, "/api/accounts", {"name": "CTO fictif", "kind": "cto"})
    jeton = post(
        client,
        "/api/assets",
        {
            "name": "Jeton fictif",
            "asset_class": "crypto",
            "coingecko_id": "fictif",
            "currency": "EUR",
        },
    )
    action = post(
        client, "/api/assets", {"name": "Action fictive", "asset_class": "action", "ticker": "A.PA"}
    )
    buy = {"op_type": "achat", "op_date": "2025-01-02", "quantity": "1", "unit_price": "10"}
    post(
        client,
        "/api/operations",
        {**buy, "account_id": account["id"], "asset_id": jeton["id"], "fees": "2"},
    )
    post(
        client,
        "/api/operations",
        {**buy, "account_id": cto["id"], "asset_id": action["id"], "fees": "1"},
    )
    assert client.get("/api/income").json()["fees_total"] == "1.00"
    out = client.get("/api/crypto/rewards").json()
    assert out["fees"] == {
        "total": "2.00",
        "years": [{"year": 2025, "fees": "2.00"}],
        "other": "0.00",
    }
    # Un jeton acheté sans rendement : « ne rapporte rien » (D-106).
    assert [(i["asset"], i["value"]) for i in out["idle"]] == [("Jeton fictif", "10.00")]


def test_placements_et_chiffres_cles():
    """Rendements crypto (D-106) : un placement par (compte, jeton) ; taux Aave du jour s'il
    existe, sinon rendement réalisé en jetons ; airdrops hors taux ; données FICTIVES."""
    today = date(2025, 12, 31)
    start = today - timedelta(days=364)
    ops = [
        op(1, "achat", "100", "1", start - timedelta(days=10)),
        op(2, "achat", "5", "2", today, offered="10", kind="staking"),
        op(3, "achat", "1", "2", today, offered="2", kind="airdrop"),
        op(4, "achat", "1", "2", today, account=4, offered="2", kind="earn"),
    ]
    accounts = {**ACCOUNTS, 4: Account(4, "Autre fictif", "crypto", None, "EUR")}
    items = crypto.rewards(ops, accounts, ASSETS, FX)
    held = {(1, 1): (D("106"), D("212")), (3, 1): (D("1000"), D("1000")), (4, 1): (D("1"), None)}
    found = crypto.placements(items, ops, held, {(3, 1): D("0.03")}, today)
    by_key = {(p.account_id, p.asset_id): p for p in found}
    stake, aave, unpriced = by_key[(1, 1)], by_key[(3, 1)], by_key[(4, 1)]
    # 5 jetons de staking (l'airdrop n'est pas un rendement) sur ≈ 100 détenus : ≈ 5 %/an.
    assert stake.kinds == ("staking",) and stake.received_12m == D("5")
    assert round(stake.realized, 4) == D("0.0500") and stake.rate == stake.realized
    assert stake.estimated == D("212") * stake.realized
    # Ligne Aave sans intérêt encore reçu : taux du jour, pas de rendement réalisé.
    assert (aave.realized, aave.rate_today, aave.estimated) == (None, D("0.03"), D("30.00"))
    assert (unpriced.capital, unpriced.estimated) == (None, None)
    totals = crypto.overview(found, [(5, 1, D("100"))], items, today)
    assert totals["average_rate"] == (stake.estimated + D("30")) / D("1212")
    assert totals["placed_share"] == D("1212") / D("1312")
    assert totals["gifts_12m"] == D("1.6")  # 2 $ à 1,25 $ l'euro
    assert totals["income_12m"] == D("8") + D("1.6") + D("1.6")


def test_rendement_realise_s_arrete_au_dernier_versement():
    """Mois en cours pas encore versé : le rendement court jusqu'au dernier versement (moins de
    35 jours) ; placement arrêté depuis plus longtemps : jusqu'à aujourd'hui. Données FICTIVES."""
    today = date(2025, 12, 31)
    start = today - timedelta(days=364)
    base = [op(1, "achat", "100", "1", start - timedelta(days=10))]

    def realized(days_ago):
        reward = op(
            2, "achat", "5", "0", today - timedelta(days=days_ago), offered="0", kind="earn"
        )
        reward = Operation(**{**reward.__dict__, "offered": D("1")})
        items = crypto.rewards([*base, reward], ACCOUNTS, ASSETS, FX)
        [p] = crypto.placements(items, [*base, reward], {(1, 1): (D("105"), None)}, {}, today)
        return round(p.realized, 12)

    # Rendement = reçus ÷ (jetons-jours ÷ jours) × 365 ÷ jours = reçus × 365 ÷ jetons-jours.
    # 5 jetons reçus il y a 20 jours : 345 jours, dont le dernier à 105 jetons.
    assert realized(20) == round(D("5") * 365 / (D("100") * 344 + D("105")), 12)
    # Dernier versement il y a 100 jours : jusqu'à aujourd'hui, 101 jours à 105 jetons.
    assert realized(100) == round(D("5") * 365 / (D("100") * 264 + D("105") * 101), 12)
