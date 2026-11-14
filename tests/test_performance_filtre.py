"""Performance limitée à certaines lignes (D-046). Données FICTIVES, aucun réseau."""

from datetime import date
from decimal import Decimal

import httpx2
from fastapi.testclient import TestClient

from app.main import app, http_client
from tests.conftest import fake_client
from tests.test_historique import FakeHistory

# Deux lignes fictives : l'ETF gagne 10 % le 3 juin, l'action en perd 20 %.
YAHOO = {
    "/v8/finance/chart/ETF.PA": FakeHistory({date(2025, 6, 2): 5.0, date(2025, 6, 3): 5.5}),
    "/v8/finance/chart/ACT.PA": FakeHistory({date(2025, 6, 2): 10.0, date(2025, 6, 3): 8.0}),
}


def yahoo(request: httpx2.Request) -> httpx2.Response:
    server = YAHOO.get(request.url.path)
    return server(request) if server else httpx2.Response(404)


def deux_lignes(client) -> list[int]:
    """Un compte, l'ETF et l'action fictifs, achetés le 2 juin 2025 ; renvoie leurs id."""
    account = client.post("/api/accounts", json={"name": "PEA test", "kind": "pea"}).json()
    ids = []
    for name, ticker, price in [
        ("ETF fictif", "etf.pa", "5"),
        ("Action fictive", "act.pa", "10"),
    ]:
        asset = client.post(
            "/api/assets", json={"name": name, "asset_class": "etf", "ticker": ticker}
        ).json()
        ids.append(asset["id"])
        client.post(
            "/api/operations",
            json={
                "account_id": account["id"],
                "asset_id": asset["id"],
                "op_date": "2025-06-02",
                "op_type": "achat",
                "quantity": "10",
                "unit_price": price,
            },
        )
    app.dependency_overrides[http_client] = lambda: fake_client(yahoo)
    return ids


def test_performance_par_ligne(db_file):
    with TestClient(app) as client:
        etf, action = deux_lignes(client)
        params = {"period": "day", "end": "2025-06-03"}

        def perf(**extra):
            return client.get("/api/performance", params={**params, **extra}).json()

        both, etf_only = perf(), perf(exclude=action)
        nothing = perf(exclude=[etf, action])
        csv = client.get("/api/performance.csv", params={**params, "exclude": etf}).text

    # Toutes les lignes : 150 € investis, 55 + 80 = 135 € le 3 juin, soit -10 %.
    assert both["assets"] == [
        {"id": etf, "name": "ETF fictif"},
        {"id": action, "name": "Action fictive"},
    ]
    assert (both["summary"]["gain"], both["summary"]["performance"]) == ("-15.00", "-10.00")
    # Sans l'action : l'ETF seul, +10 %. La liste des lignes reste complète pour les puces.
    assert (etf_only["summary"]["gain"], etf_only["summary"]["performance"]) == ("5.00", "10.00")
    assert etf_only["assets"] == both["assets"]
    # Tout décoché : rien à calculer, mais les puces restent affichables.
    assert (nothing["summary"], nothing["periods"], len(nothing["assets"])) == (None, [], 2)
    # Le CSV suit le même filtre : l'action seule perd 20 % le 3 juin.
    assert csv.splitlines()[-1].split(";")[7] == "-20,00"


def test_filtre_partage_calendrier_comparaison_projection(db_file):
    """Le même filtre vaut pour le calendrier, la comparaison et la projection (D-050)."""
    with TestClient(app) as client:
        _, action = deux_lignes(client)

        def get(path, **params):
            both = client.get(path, params=params).json()
            alone = client.get(path, params={**params, "exclude": action}).json()
            return both, alone

        cal_both, cal_etf = get("/api/calendar")
        bench_both, bench_etf = get("/api/benchmark", rate="2", end="2025-06-03")
        proj_both, proj_etf = get("/api/projection", years=1, rates="0")

    # Juin 2025 : -10 % avec les deux lignes, +10 % pour l'ETF seul.
    assert (cal_both["months"]["2025-06"], cal_etf["months"]["2025-06"]) == ("-10.00", "10.00")
    assert bench_both["summary"]["portfolio_value"] == "135.00"
    assert bench_etf["summary"]["portfolio_value"] == "55.00"
    # Projection à 0 % : l'action (80 € au dernier cours) disparaît, les espèces restent.
    gap = Decimal(proj_both["start_value"]) - Decimal(proj_etf["start_value"])
    assert gap == Decimal("80")


def test_filtre_par_compte_et_par_ligne(db_file):
    """Même ETF dans deux comptes : on peut retirer celui d'un compte seulement (D-069)."""
    with TestClient(app) as client:
        etf, _ = deux_lignes(client)
        pea = client.get("/api/accounts").json()[0]["id"]
        cto = client.post(
            "/api/accounts", json={"name": "CTO test", "kind": "cto", "deposits": "200"}
        ).json()["id"]
        op = {"account_id": cto, "asset_id": etf, "op_date": "2025-06-02", "op_type": "achat"}
        client.post("/api/operations", json={**op, "quantity": "20", "unit_price": "5"})

        def portfolio(**params):
            return client.get("/api/portfolio", params=params).json()

        def accounts(data):
            return sorted((p["account"], p["asset"]) for p in data["positions"])

        full = portfolio()
        assert not full["filtered"] and len(full["positions"]) == 3
        # L'ETF du CTO seulement : celui du PEA reste.
        data = portfolio(exclude_line=f"{cto}-{etf}")
        assert data["filtered"]
        assert accounts(data) == [("PEA test", "Action fictive"), ("PEA test", "ETF fictif")]
        # Compte entier : ses lignes et ses espèces disparaissent.
        data = portfolio(exclude_account=cto)
        assert {c["account"] for c in data["cash"]} <= {"PEA test"}
        assert all(p["account"] == "PEA test" for p in data["positions"])
        # Même filtre sur la courbe : 50 € d'ETF au PEA + 100 € d'action le 2 juin.
        params = {"exclude_line": f"{cto}-{etf}"}
        first = client.get("/api/history", params=params).json()["points"][0]
        assert first["invested"] == "150.00"
        first = client.get("/api/history", params={"exclude_account": pea}).json()["points"][0]
        assert first["invested"] == "100.00"
        assert client.get("/api/portfolio", params={"exclude_line": "1;2"}).status_code == 422
