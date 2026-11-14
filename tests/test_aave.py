"""Dépôts Aave suivis (D-095) : taux publics, intérêts estimés, soldes vérifiés.

Données FICTIVES ; le réseau est remplacé par un faux serveur Aave (aucune requête ne sort).
"""

import json
from datetime import date, timedelta
from decimal import Decimal as D

import httpx2
import pytest
from fastapi.testclient import TestClient

from app import defi, defi_sync
from app.main import app, http_client
from app.providers import ProviderError, aave
from tests.conftest import fake_client

MARKET = "0x" + "1" * 40
TOKEN = "0x" + "2" * 40
APY = D("0.0365")  # 3,65 % par an (fictif)
BORROW_APY = D("0.05")  # taux d'emprunt fictif
TODAY = date.today()
_M = TODAY.year * 12 + TODAY.month - 1 - 3
START = date(_M // 12, _M % 12 + 1, 1)  # premier jour d'il y a trois mois : trois mois clos


def aave_server(requests: list):
    def handler(request: httpx2.Request) -> httpx2.Response:
        if request.url.host == "api.coingecko.com":  # cours d'un jeton : id et devise seuls
            requests.append({"coingecko": str(request.url)})
            if "market_chart" in request.url.path:
                return httpx2.Response(404)
            return httpx2.Response(200, json={"fictif": {"usd": 1.0}})
        assert request.url.host == "api.v3.aave.com" and request.method == "POST"
        body = json.loads(request.content)
        requests.append(body)
        query = body["query"]
        if "chains" in query:
            data = {"chains": [{"chainId": 42161, "name": "Arbitrum"}]}
        elif "markets" in query:
            reserve = {
                "underlyingToken": {"symbol": "USDC", "address": TOKEN},
                "supplyInfo": {
                    "apy": {"value": str(APY)},
                    "liquidationThreshold": {"value": "0.78"},
                },
                "borrowInfo": {"apy": {"value": str(BORROW_APY)}, "borrowingState": "ENABLED"},
                "isFrozen": False,
                "isPaused": False,
            }
            market = {"name": "AaveV3Arbitrum", "address": MARKET, "reserves": [reserve]}
            data = {"markets": [market]}
        else:
            days = [TODAY - timedelta(days=i) for i in range(365)]
            borrow = "borrowAPYHistory" in query
            field = "borrowAPYHistory" if borrow else "supplyAPYHistory"
            rate = BORROW_APY if borrow else APY
            data = {
                field: [
                    {"avgRate": {"value": str(rate)}, "date": f"{d}T00:00:00+00:00"} for d in days
                ]
            }
        return httpx2.Response(200, json={"data": data})

    return handler


# --- Source ------------------------------------------------------------------------------


def test_requetes_aave_sans_adresse_de_portefeuille_ni_montant():
    """Règle 3 élargie : chaîne, marché et jeton seulement ; jamais le champ `user`."""
    sent: list = []
    client = fake_client(aave_server(sent))
    assert aave.get_chains(client) == [(42161, "Arbitrum")]
    [reserve] = aave.get_reserves(client, 42161)
    assert (reserve["symbol"], reserve["apy"], reserve["frozen"]) == ("USDC", APY, False)
    history = aave.get_rate_history(client, 42161, MARKET, TOKEN)
    assert len(history) == 365 and history[TODAY] == APY
    for body in sent:
        assert body["query"].lstrip().startswith("query")  # lecture, jamais de mutation
        assert "user" not in json.dumps(body["variables"])
        assert "mutation" not in body["query"]
    assert sent[-1]["variables"]["r"] == {
        "market": MARKET,
        "underlyingToken": TOKEN,
        "chainId": 42161,
        "window": "LAST_YEAR",
    }


def test_adresse_de_contrat_invalide_refusee_avant_tout_appel():
    with pytest.raises(ProviderError):
        aave.get_rate_history(fake_client(), 1, "0xabc", TOKEN)


def test_reponse_aave_en_erreur():
    def handler(request):
        return httpx2.Response(200, json={"errors": [{"message": "boom"}]})

    with pytest.raises(ProviderError):
        aave.get_chains(fake_client(handler))


# --- Calcul ------------------------------------------------------------------------------


def test_taux_du_jour_equivalent_a_l_apy():
    factor = defi.daily_factor(APY)
    assert abs((1 + factor) ** 365 - (1 + APY)) < D("1E-20")


def test_interets_composes_par_mois_clos_et_mois_en_cours():
    today = date(2025, 3, 10)
    rates = {date(2025, 1, 1) + timedelta(days=i): APY for i in range(80)}
    est = defi.estimate({date(2025, 1, 1): D("1000")}, rates, {}, today)
    f = 1 + defi.daily_factor(APY)
    jan = D("1000") * (f**31 - 1)
    feb = D("1000") * (f**59 - f**31)
    assert abs(est.months["2025-01"] - jan) < D("1E-9")
    assert abs(est.months["2025-02"] - feb) < D("1E-9")
    assert abs(est.current - D("1000") * (f**69 - f**59)) < D("1E-9")
    assert abs(est.balance - D("1000") * f**69) < D("1E-9")
    assert not est.extrapolated and est.unexplained == 0


def test_taux_le_plus_ancien_applique_avant_le_premier_connu():
    rates = {date(2025, 2, 1): APY}
    est = defi.estimate({date(2025, 1, 1): D("100")}, rates, {}, date(2025, 2, 5))
    assert est.extrapolated and est.months["2025-01"] > 0


def test_solde_verifie_recale_et_ecart_negatif_reduit_les_mois_precedents():
    rates = {date(2025, 1, 1): APY}
    flows = {date(2025, 1, 1): D("1000")}
    # Solde vérifié le 2 mars à 1001 : moins que l'estimation (≈ 1005,8).
    est = defi.estimate(flows, rates, {date(2025, 3, 2): D("1001")}, date(2025, 3, 2))
    # Janvier + février + début mars = 1 jeton : mars, négatif, est ramené à zéro.
    assert sum(est.months.values()) == D("1") and est.current == 0 and est.balance == D("1001")
    est = defi.estimate(flows, rates, {date(2025, 3, 2): D("1001")}, date(2025, 3, 31))
    total = sum(est.months.values()) + est.current
    assert abs(D("1000") + total - est.balance) < D("1E-8")
    assert "2025-03" in est.verified and est.unexplained == 0
    # Solde vérifié inférieur aux dépôts : écart que les intérêts ne couvrent pas.
    est = defi.estimate(flows, rates, {date(2025, 2, 1): D("900")}, date(2025, 2, 10))
    assert est.months == {} and est.unexplained > 0


def test_retrait_du_depot_laisse_les_interets_deja_gagnes():
    rates = {date(2025, 1, 1): APY}
    flows = {date(2025, 1, 1): D("10"), date(2025, 1, 2): D("-10")}
    est = defi.estimate(flows, rates, {}, date(2025, 2, 10))
    first_day = D("10") * defi.daily_factor(APY)
    assert abs(est.balance - first_day * (1 + defi.daily_factor(APY)) ** 40) < D("1E-15")


# --- API ---------------------------------------------------------------------------------


@pytest.fixture
def client(db_file):
    sent: list = []
    app.dependency_overrides[http_client] = lambda: fake_client(aave_server(sent))
    defi_sync.reset_memory()
    with TestClient(app) as c:
        c.sent = sent
        yield c
    defi_sync.reset_memory()


def post(client, path, body, status=201):
    res = client.post(path, json=body)
    assert res.status_code == status, res.text
    return res.json()


def setup_line(client):
    account = post(client, "/api/accounts", {"name": "Aave fictif", "kind": "crypto"})
    asset = post(
        client,
        "/api/assets",
        {
            "name": "USDC fictif",
            "asset_class": "crypto",
            "coingecko_id": "fictif",
            "currency": "USD",
        },
    )
    buy = {
        "account_id": account["id"],
        "asset_id": asset["id"],
        "op_type": "achat",
        "op_date": START.isoformat(),
        "quantity": "1000",
        "unit_price": "1",
        "currency": "USD",
    }
    post(client, "/api/operations", buy)
    line = {"account_id": account["id"], "asset_id": asset["id"], "chain_id": 42161}
    lines = post(client, "/api/aave/lines", line | {"market": MARKET, "token": TOKEN})
    return account, asset, lines


def auto_ops(client):
    return [o for o in client.get("/api/operations").json() if o["source"] == "aave"]


def test_api_suivi_interets_automatiques_et_verification(client):
    account, asset, [line] = setup_line(client)
    assert (line["symbol"], line["chain"], line["apy_today_pct"]) == ("USDC", "Arbitrum", "3.65")
    ops = auto_ops(client)
    closed = {o["op_date"][:7] for o in ops}
    assert len(closed) >= 2 and f"{TODAY:%Y-%m}" not in closed
    assert all(o["reward_kind"] == "earn" and o["offered"] for o in ops)
    assert D(line["interest"]) > 0 and D(line["current"]) >= 0

    # Intérêts calculés : ni modifiés ni supprimés à la main.
    assert client.patch(f"/api/operations/{ops[0]['id']}", json={}).status_code in (409, 422)
    assert client.delete(f"/api/operations/{ops[0]['id']}").status_code == 409

    # Un dépôt ajouté après coup recalcule les mois clos.
    before = sum(D(o["quantity"]) for o in ops)
    extra = {
        "account_id": account["id"],
        "asset_id": asset["id"],
        "op_type": "achat",
        "op_date": START.isoformat(),
        "quantity": "1000",
        "unit_price": "1",
        "currency": "USD",
    }
    post(client, "/api/operations", extra)
    out = client.get("/api/crypto/rewards").json()
    # Rendements crypto (D-106) : la ligne Aave est un placement, au taux public du jour.
    [place] = out["placements"]
    assert (place["aave_line_id"], place["rate_today_pct"]) == (line["id"], "3.65")
    assert place["kinds"] == ["earn"]
    after = sum(D(o["quantity"]) for o in auto_ops(client))
    assert abs(after - 2 * before) < D("1E-8")

    # Solde vérifié aujourd'hui : l'écart est renvoyé et l'estimation repart de ce solde.
    out = post(
        client,
        f"/api/aave/lines/{line['id']}/checks",
        {"check_date": TODAY.isoformat(), "quantity": "2000.5"},
        status=200,
    )
    assert D(out["estimated"]) > D("2000.5") and D(out["gap"]) < 0
    [line] = out["lines"]
    assert D(line["estimated"]) == D("2000.5")
    held_recorded = D("2000") + sum(D(o["quantity"]) for o in auto_ops(client))
    assert held_recorded <= D("2000.5")

    # Fin du suivi : les intérêts estimés disparaissent.
    assert client.delete(f"/api/aave/lines/{line['id']}").status_code == 204
    assert auto_ops(client) == [] and client.get("/api/aave/lines").json() == []


def test_api_taux_demandes_une_fois_par_jour(client):
    setup_line(client)
    count = sum("supplyAPYHistory" in b["query"] for b in client.sent)
    client.get("/api/aave/lines")
    client.get("/api/aave/lines")
    assert sum("supplyAPYHistory" in b["query"] for b in client.sent) == count == 1


def test_api_ligne_non_crypto_ou_deja_suivie(client):
    setup_line(client)
    account = post(client, "/api/accounts", {"name": "CTO fictif", "kind": "cto"})
    action = post(
        client, "/api/assets", {"name": "Action fictive", "asset_class": "action", "ticker": "A.PA"}
    )
    body = {"chain_id": 42161, "market": MARKET, "token": TOKEN}
    post(
        client,
        "/api/aave/lines",
        body | {"account_id": account["id"], "asset_id": action["id"]},
        status=422,
    )
    lines = client.get("/api/aave/lines").json()
    again = {"account_id": lines[0]["account_id"], "asset_id": lines[0]["asset_id"]}
    post(client, "/api/aave/lines", body | again, status=409)
    post(client, "/api/aave/lines", body | again | {"market": "0x" + "3" * 40}, status=422)


def test_api_suivi_avec_premier_depot_puis_depot_et_retrait(client):
    """Choix du compte et dépôt à une date (D-096) : hors de l'app = titres déjà détenus, sans
    toucher aux espèces ; depuis un compte de l'app = transfert ; retrait = transfert sortant."""
    aave_account = post(client, "/api/accounts", {"name": "Aave Arbitrum", "kind": "crypto"})
    wallet = post(client, "/api/accounts", {"name": "Plateforme fictive", "kind": "crypto"})
    asset = post(
        client,
        "/api/assets",
        {"name": "USDC", "asset_class": "crypto", "coingecko_id": "fictif", "currency": "USD"},
    )
    deposit = {"quantity": "1000", "op_date": START.isoformat(), "unit_price": "1"}
    body = {"account_id": aave_account["id"], "asset_id": asset["id"], "chain_id": 42161}
    [line] = post(
        client, "/api/aave/lines", body | {"market": MARKET, "token": TOKEN, "deposit": deposit}
    )
    ops = [o for o in client.get("/api/operations").json() if o["source"] == "manuel"]
    assert [(o["op_type"], o["account_id"], o["quantity"]) for o in ops] == [
        ("solde_initial", aave_account["id"], "1000")
    ]
    assert D(line["interest"]) > 0

    # Dépôt depuis un compte de l'app : transfert sortant vers le compte Aave.
    buy = {"account_id": wallet["id"], "asset_id": asset["id"], "op_type": "achat"}
    post(
        client,
        "/api/operations",
        buy | {"op_date": START.isoformat(), "quantity": "500", "unit_price": "1"},
    )
    move = {
        "quantity": "500",
        "op_date": TODAY.isoformat(),
        "other_account_id": wallet["id"],
        "unit_price": "1",
    }
    post(client, f"/api/aave/lines/{line['id']}/moves", move)
    transfer = [
        o for o in client.get("/api/operations").json() if o["op_type"] == "transfert_sortant"
    ]
    assert [(o["account_id"], o["to_account_id"]) for o in transfer] == [
        (wallet["id"], aave_account["id"])
    ]

    # Retrait hors de l'app, puis retrait trop grand refusé.
    out = {
        "direction": "retrait",
        "quantity": "200",
        "op_date": TODAY.isoformat(),
        "unit_price": "1",
    }
    [line] = post(client, f"/api/aave/lines/{line['id']}/moves", out)
    assert D(line["held"]) > D("1300") and D(line["held"]) < D("1320")
    post(client, f"/api/aave/lines/{line['id']}/moves", out | {"quantity": "5000"}, status=422)

    # Dépôt depuis le compte Aave lui-même : refusé.
    post(
        client,
        f"/api/aave/lines/{line['id']}/moves",
        move | {"other_account_id": aave_account["id"]},
        status=422,
    )


def test_api_nouveau_compte_aave_cree_avec_la_ligne(client):
    asset = post(
        client,
        "/api/assets",
        {"name": "USDC", "asset_class": "crypto", "coingecko_id": "fictif", "currency": "USD"},
    )
    body = {"account_id": None, "asset_id": asset["id"], "chain_id": 42161}
    deposit = {"quantity": "10", "op_date": TODAY.isoformat()}  # prix trouvé par l'app
    [line] = post(
        client, "/api/aave/lines", body | {"market": MARKET, "token": TOKEN, "deposit": deposit}
    )
    assert line["account"] == "Aave Arbitrum" and line["held"] == "10"
    [op] = [o for o in client.get("/api/operations").json() if o["op_type"] == "solde_initial"]
    assert op["unit_price"] == "1.0"
    gecko = [b["coingecko"] for b in client.sent if "coingecko" in b]
    assert gecko and all("10" not in g.split("?")[1] for g in gecko)  # jamais la quantité


def test_api_cours_introuvable_rien_n_est_cree(client):
    def offline_gecko(request):
        if request.url.host == "api.coingecko.com":
            raise httpx2.ConnectError("coupé", request=request)
        return aave_server(client.sent)(request)

    app.dependency_overrides[http_client] = lambda: fake_client(offline_gecko)
    asset = post(
        client,
        "/api/assets",
        {"name": "USDC", "asset_class": "crypto", "coingecko_id": "fictif", "currency": "USD"},
    )
    body = {"account_id": None, "asset_id": asset["id"], "chain_id": 42161}
    deposit = {"quantity": "10", "op_date": TODAY.isoformat()}
    post(
        client,
        "/api/aave/lines",
        body | {"market": MARKET, "token": TOKEN, "deposit": deposit},
        502,
    )
    assert client.get("/api/accounts").json() == []  # rien n'est resté de l'essai
