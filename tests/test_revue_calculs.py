"""Revue des calculs financiers (D-081) : performance le jour d'une vente, dividendes et frais,
titres déjà détenus, volatilité, taux de change du jour de l'opération, prix moyen pondéré
commun aux comptes-titres. Données FICTIVES, aucun réseau."""

from datetime import date
from decimal import Decimal as D

import httpx2
import pytest

from app import db, prices
from app.analytics import points_per_year, volatility
from app.models import Operation, Position
from app.portfolio import (
    RateHistory,
    daily_flows,
    daily_income,
    eur_costs,
    performance,
    totals_in_eur,
    value_history,
)
from app.providers import frankfurter
from app.tax import cto_estimate, realized_sales
from tests.conftest import fake_client

EUR = {"EUR": D("1")}
D1, D2, D3 = date(2026, 1, 5), date(2026, 1, 6), date(2026, 1, 7)


def op(i, day, kind, qty=None, price=None, amount=None, fees="0", cur="EUR", asset=1, account=1):
    return Operation(
        i,
        account,
        asset,
        day,
        kind,
        D(qty) if qty else None,
        D(price) if price else None,
        D(amount) if amount else None,
        D(fees),
        cur,
        None,
    )


def daily(ops, closes, rates=EUR, fx=None):
    points = [(d, v) for d, v, _ in value_history(ops, closes, rates, D3, history=fx)]
    flows = daily_flows(ops, rates, fx, closes)
    return performance(points, flows, "day", D1, D3, daily_income(ops, rates, fx))


UP = {1: [(D1, D("10"), "EUR"), (D2, D("11"), "EUR"), (D3, D("11"), "EUR")]}


def test_vente_partielle_un_jour_de_hausse():
    """90 % vendus un jour à +10 % : la journée vaut +10 %, plus +1 000 %."""
    res = daily([op(1, D1, "achat", "100", "10"), op(2, D2, "vente", "90", "11")], UP)
    assert [r.performance for r in res] == [D("0"), D("0.1"), D("0")]
    assert res[1].gain == D("100")


def test_vente_totale_un_jour_de_hausse():
    res = daily([op(1, D1, "achat", "100", "10"), op(2, D2, "vente", "100", "11")], UP)
    assert res[1].performance == D("0.1")  # avant : 0 %, le gain du jour était perdu
    assert res[1].gain == D("100")


def test_dividende_detache_ne_coute_rien():
    """Le cours baisse du dividende détaché : ni perte ni gain ce jour-là."""
    closes = {1: [(D1, D("10"), "EUR"), (D2, D("9"), "EUR"), (D3, D("9"), "EUR")]}
    res = daily([op(1, D1, "achat", "100", "10"), op(2, D2, "dividende", amount="100")], closes)
    assert res[1].performance == D("0")
    assert (res[1].income, res[1].gain, res[1].cumulative_gain) == (D("100"), D("0"), D("0"))


def test_droits_de_garde_reduisent_la_performance():
    flat = {1: [(D1, D("10"), "EUR"), (D2, D("10"), "EUR"), (D3, D("10"), "EUR")]}
    res = daily([op(1, D1, "achat", "100", "10"), op(2, D2, "frais", amount="10")], flat)
    assert res[1].performance == D("-0.01")
    assert res[-1].cumulative_gain == D("-10")


def test_titres_deja_detenus_a_leur_valeur_de_marche():
    """Saisis au PRU de 5 € un jour où ils cotent 10 € : la plus-value d'avant n'est pas la
    performance du jour."""
    res = daily([op(1, D2, "solde_initial", "100", "5")], UP)
    assert res[0].performance == D("0")
    assert res[0].flows == D("1100")  # 100 × 11 € de clôture
    # Sans clôture connue : repli sur le PRU.
    assert daily_flows([op(1, D2, "solde_initial", "100", "5")], EUR) == {D2: D("500")}


def test_volatilite_au_rythme_de_la_serie():
    factors = [D("1.01"), D("0.99")] * 50
    assert points_per_year(100, date(2026, 1, 1), date(2026, 4, 10)) == D("365")
    assert points_per_year(10, date(2026, 1, 1), date(2026, 1, 10)) == D("252")  # trop court
    stocks = volatility(factors)
    crypto = volatility(factors, D("365"))
    assert crypto / stocks == (D("365") / D("252")).sqrt()


USD = RateHistory(
    {"EUR": D("1"), "USD": D("1.25")},
    {"USD": [(date(2025, 1, 2), D("1.04")), (date(2026, 3, 2), D("1.17"))]},
)


def test_historique_des_taux():
    assert USD.on(date(2024, 6, 1))["USD"] == D("1.04")  # avant l'historique : premier taux
    assert USD.on(date(2025, 6, 1))["USD"] == D("1.04")
    assert USD.on(date(2026, 3, 2))["USD"] == D("1.17")
    assert RateHistory({"EUR": D("1"), "USD": D("1.25")}).on(D1)["USD"] == D("1.25")


def test_plus_value_en_dollars_compte_le_change():
    """Cours inchangé à 100 $, mais 1 € vaut 1,04 $ à l'achat et 1,17 $ à la vente."""
    ops = [
        op(1, date(2025, 1, 2), "achat", "10", "100", cur="USD"),
        op(2, date(2026, 3, 2), "vente", "10", "100", cur="USD"),
    ]
    [sale] = realized_sales(ops, USD)
    assert sale.gain == D("0")  # en dollars
    expected = D("1000") / D("1.17") - D("1000") / D("1.04")  # le dollar a baissé : moins-value
    assert sale.gain_eur == expected
    est = cto_estimate(ops, {1}, 2026, USD.latest, D("31.4"), USD)
    assert est["net"] == expected
    # Sans historique : taux du jour, plus-value nulle (ancien calcul).
    assert cto_estimate(ops, {1}, 2026, USD.latest, D("31.4"))["net"] == D("0")


def test_cout_en_euros_et_total():
    ops = [op(1, date(2025, 1, 2), "achat", "10", "104", cur="USD")]
    costs = eur_costs(ops, USD)
    assert costs == {(1, 1): D("1000")}  # 1 040 $ à 1,04
    pos = Position(1, 1, D("10"), D("1040"), D("125"), "USD")  # vaut 1 250 $ = 1 000 €
    totals = totals_in_eur([pos], USD.latest, costs)
    assert totals["EUR"]["gain"] == D("0")  # hausse du cours mangée par le change
    assert totals_in_eur([pos], USD.latest)["EUR"]["gain"] == D("168")  # ancien calcul


def test_courbe_au_taux_de_chaque_jour():
    ops = [op(1, date(2025, 1, 2), "achat", "1", "104", cur="USD")]
    closes = {1: [(date(2025, 1, 2), D("104"), "USD"), (date(2026, 3, 2), D("117"), "USD")]}
    points = value_history(ops, closes, USD.latest, date(2026, 3, 2), history=USD)
    assert points[0][1:] == (D("100"), D("100"))
    assert points[-1][1:] == (D("100"), D("100"))  # +12,5 % en dollars, 0 % en euros


def test_prix_moyen_commun_aux_comptes_titres():
    """Même titre sur deux comptes-titres : un seul prix moyen, comme le veut l'impôt."""
    ops = [
        op(1, date(2025, 1, 2), "achat", "10", "10", account=1),
        op(2, date(2025, 2, 3), "achat", "10", "20", account=2),
        op(3, date(2025, 6, 2), "vente", "10", "20", account=2),
    ]
    [alone] = realized_sales(ops)
    [pooled] = realized_sales(ops, pooled=True)
    assert (alone.gain, pooled.gain) == (D("0"), D("50"))  # prix moyen 15 € et non 20 €
    est = cto_estimate(ops, {1, 2}, 2025, EUR, D("31.4"), pooled=True, only=1)
    assert est["sales"] == []  # la vente est sur l'autre compte
    assert eur_costs(ops, RateHistory(EUR), pooled=True) == {(1, 1): D("150")}


def test_frankfurter_historique():
    seen = []

    def server(request):
        seen.append(request)
        rates = {"2025-01-02": {"USD": 1.04}, "2025-01-03": {"USD": 1.03}}
        return httpx2.Response(200, json={"base": "EUR", "rates": rates})

    out = frankfurter.get_history(fake_client(server), {"USD"}, date(2025, 1, 1), D1)
    assert out == {"USD": [(date(2025, 1, 2), D("1.04")), (date(2025, 1, 3), D("1.03"))]}
    [req] = seen
    assert req.url.path == "/v1/2025-01-01..2026-01-05"
    assert dict(req.url.params) == {"base": "EUR", "symbols": "USD"}  # règle 3


@pytest.fixture
def conn(db_file):
    db.init_db()
    with db.connect() as c:
        yield c
    c.close()


def test_historique_des_taux_complete_une_fois_par_jour(conn):
    seen = []

    def server(request):
        seen.append(request)
        return httpx2.Response(200, json={"rates": {"2025-03-03": {"USD": 1.05}}})

    client = fake_client(server)
    first_op = date(2025, 3, 14)  # jamais envoyée : on demande depuis le 1er janvier
    assert prices.update_fx_history(conn, {"USD": first_op}, client, today=D1) is None
    assert seen[0].url.path == "/v1/2025-01-01..2026-01-05"
    prices.update_fx_history(conn, {"USD": first_op}, client, today=D1)
    assert len(seen) == 1  # déjà fait aujourd'hui
    assert prices.rate_series(conn, {"USD", "EUR"}) == {"USD": [(date(2025, 3, 3), D("1.05"))]}
    # Réseau coupé : l'erreur est rendue, rien n'est cassé.
    prices._fx_checked.clear()
    error = prices.update_fx_history(conn, {"USD": date(2024, 5, 1)}, fake_client(), today=D1)
    assert error.startswith("historique des taux de change")


def test_api_performance_compte_les_dividendes(db_file):
    """Dividende de 2 € reçu le 3 juin, cours de l'ETF fictif de 5 € à 5,5 € : +10 % de cours,
    plus 2 € sur 50 € investis, soit +14 % ; le gain compte les deux."""
    from fastapi.testclient import TestClient

    from app.main import app
    from tests.test_performance_filtre import deux_lignes

    with TestClient(app) as client:
        etf, action = deux_lignes(client)
        account = client.get("/api/accounts").json()[0]["id"]
        client.post(
            "/api/operations",
            json={
                "account_id": account,
                "asset_id": etf,
                "op_date": "2025-06-03",
                "op_type": "dividende",
                "amount": "2",
            },
        )
        data = client.get(f"/api/performance?period=day&exclude={action}&end=2025-06-03").json()
    s = data["summary"]
    assert (s["income"], s["gain"], s["performance"]) == ("2.00", "7.00", "14.00")
