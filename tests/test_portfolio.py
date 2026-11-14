"""Calcul des positions, sur des opérations FICTIVES."""

from dataclasses import replace
from datetime import date
from decimal import Decimal as D

import pytest

from app.models import Operation
from app.portfolio import (
    PositionError,
    allocation,
    annualized,
    compute_positions,
    convert,
    daily_flows,
    day_change,
    max_drawdown,
    performance,
    period_key,
    ratio,
    sum_by_currency,
    top_parts,
    totals_in_eur,
    value_history,
    with_market_prices,
)


def op(id, jour, op_type, qty, price, fees="0", asset_id=1, currency="EUR"):
    return Operation(
        id=id,
        account_id=1,
        asset_id=asset_id,
        op_date=date(2025, 1, jour),
        op_type=op_type,
        quantity=D(qty),
        unit_price=D(price),
        amount=None,
        fees=D(fees),
        currency=currency,
        note=None,
    )


def test_deux_achats_pru_avec_frais():
    ops = [op(1, 2, "achat", "10", "5.00", "1.00"), op(2, 9, "achat", "10", "6.00", "1.00")]
    [p] = compute_positions(ops)
    assert p.quantity == D("20")
    assert p.cost == D("112.00")  # 50 + 1 + 60 + 1
    assert p.pru == D("5.6")
    assert p.last_price == D("6.00")
    assert p.value == D("120.00")
    assert p.gain == D("8.00")


def test_ordre_chronologique_et_non_ordre_de_saisie():
    ops = [op(2, 9, "achat", "1", "7"), op(1, 2, "achat", "1", "5")]
    [p] = compute_positions(ops)
    assert p.last_price == D("7")  # le prix le plus récent, pas le dernier saisi


def test_vente_garde_le_pru():
    ops = [op(1, 2, "achat", "10", "4"), op(2, 3, "vente", "4", "5", "0.50")]
    [p] = compute_positions(ops)
    assert p.quantity == D("6")
    assert p.pru == D("4")
    assert p.cost == D("24")


def test_vente_totale_supprime_la_position():
    ops = [op(1, 2, "achat", "3", "4"), op(2, 3, "vente", "3", "5")]
    assert compute_positions(ops) == []


def test_vente_superieure_a_la_quantite_refusee():
    ops = [op(1, 2, "achat", "3", "4"), op(2, 3, "vente", "5", "5")]
    with pytest.raises(PositionError, match="5 parts vendues, 3 détenues"):
        compute_positions(ops)


def test_vente_avant_achat_refusee():
    ops = [op(1, 5, "achat", "3", "4"), op(2, 1, "vente", "1", "5")]
    with pytest.raises(PositionError):
        compute_positions(ops)


def test_totaux_en_eur_et_devise_sans_taux():
    ops = [
        op(1, 2, "achat", "2", "10.005"),
        op(2, 2, "achat", "1", "3", asset_id=2),
        op(3, 2, "achat", "1", "100", asset_id=3, currency="USD"),
        op(4, 2, "achat", "1", "50", asset_id=4, currency="CHF"),
    ]
    rates = {"EUR": D("1"), "USD": D("1.25")}  # pas de taux CHF
    totals = totals_in_eur(compute_positions(ops), rates)
    assert totals["EUR"] == {"value": D("103.010"), "cost": D("103.010"), "gain": D("0")}
    assert totals["CHF"]["value"] == D("50")  # laissé à part plutôt que mal converti


def test_conversion_via_euro():
    rates = {"EUR": D("1"), "USD": D("1.25"), "GBP": D("0.8")}
    assert convert(D("125"), "USD", "EUR", rates) == D("100")
    assert convert(D("125"), "USD", "GBP", rates) == D("80")
    assert convert(D("7"), "EUR", "EUR", {}) == D("7")
    assert convert(D("7"), "JPY", "EUR", rates) is None


def test_cours_en_ligne_remplace_le_dernier_prix_saisi():
    ops = [op(1, 2, "achat", "10", "5.00"), op(2, 2, "achat", "4", "20", asset_id=2)]
    rates = {"EUR": D("1")}
    p1, p2 = with_market_prices(compute_positions(ops), {1: (D("5.5"), "EUR")}, rates)
    assert (p1.last_price, p1.value, p1.gain) == (D("5.5"), D("55.0"), D("5.0"))
    assert p2.last_price == D("20")  # sans cours en ligne : prix de la dernière opération


def test_cours_dans_une_devise_sans_taux_ignore():
    [p] = with_market_prices(
        compute_positions([op(1, 2, "achat", "1", "5")]), {1: (D("9"), "USD")}, {"EUR": D("1")}
    )
    assert p.last_price == D("5")


def test_variation_du_jour_et_pourcentages():
    [p] = compute_positions([op(1, 2, "achat", "10", "5.00")])
    rates = {"EUR": D("1"), "USD": D("1.25")}
    assert day_change(p, D("5.5"), D("5.0"), "EUR", rates) == D("5.0")
    assert day_change(p, D("5"), D("4"), "USD", rates) == D("8.0")  # (4 € - 3,2 €) × 10
    assert day_change(p, D("5"), D("4"), "JPY", rates) is None
    assert ratio(D("8"), D("112")).quantize(D("0.0001")) == D("0.0714")
    assert ratio(D("1"), D("0")) is None


def test_somme_par_devise():
    rates = {"EUR": D("1"), "USD": D("2")}
    amounts = [(D("10"), "EUR"), (D("4"), "USD"), (D("3"), "CHF")]
    assert sum_by_currency(amounts, rates) == {"EUR": D("12"), "CHF": D("3")}


# --- Graphiques (jalon 6) ---------------------------------------------------


def jour(n):
    return date(2025, 1, n)


def test_evolution_jour_par_jour():
    ops = [op(1, 2, "achat", "10", "5.00", "1.00"), op(2, 8, "achat", "10", "6.00")]
    closes = {
        1: [(jour(1), D("4.90"), "EUR"), (jour(3), D("5.20"), "EUR"), (jour(9), D("6.10"), "EUR")]
    }
    points = value_history(ops, closes, {"EUR": D("1")}, jour(10))
    assert points == [
        (jour(2), D("49.00"), D("51.00")),  # clôture du 1er (veille de l'achat) faute de mieux
        (jour(3), D("52.00"), D("51.00")),
        (jour(8), D("104.00"), D("111.00")),  # 20 parts à la clôture du 3 (pas de cotation depuis)
        (jour(9), D("122.00"), D("111.00")),
        (jour(10), D("122.00"), D("111.00")),
    ]


def test_evolution_sans_cloture_et_cours_du_jour():
    """Sans clôture, prix de la dernière opération ; le dernier point prend le cours en ligne."""
    ops = [op(1, 2, "achat", "10", "5.00"), op(2, 5, "vente", "4", "5.50")]
    points = value_history(ops, {}, {"EUR": D("1")}, jour(6), {1: (D("6.00"), "EUR")})
    assert points == [
        (jour(2), D("50.00"), D("50.00")),
        (jour(5), D("33.00"), D("30.00")),  # 6 parts à 5,50 ; coût réduit au prorata
        (jour(6), D("36.00"), D("30.00")),
    ]


def test_evolution_convertie_en_euros():
    ops = [op(1, 2, "achat", "10", "5.00", currency="USD")]
    closes = {1: [(jour(2), D("5.00"), "USD")]}
    assert value_history(ops, closes, {"EUR": D("1"), "USD": D("1.25")}, jour(2)) == [
        (jour(2), D("40"), D("40"))
    ]
    assert value_history(ops, closes, {"EUR": D("1")}, jour(2)) == [(jour(2), D("0"), D("0"))]


def test_evolution_vide_sans_achat():
    assert value_history([], {}, {"EUR": D("1")}, jour(2)) == []


def test_repartition_regroupee_en_euros():
    slices = [
        ("ETF", D("100"), "EUR"),
        ("Espèces", D("30"), "EUR"),
        ("ETF", D("25"), "USD"),
        ("Crypto", D("5"), "JPY"),  # pas de taux : laissé de côté
        ("Espèces", D("0"), "EUR"),
    ]
    assert allocation(slices, {"EUR": D("1"), "USD": D("1.25")}) == [
        ("ETF", D("120")),
        ("Espèces", D("30")),
    ]


def test_top_parts_regroupe_les_petites_lignes():
    parts = [(f"L{i}", D(10 - i)) for i in range(6)] + [("Espèces", D("1"))]
    top, others = top_parts(parts, 3, keep_last=("Espèces",))
    # 3 plus grosses, puis le reste additionné (7 + 6 + 5), puis les espèces en dernier.
    assert top == [("L0", D(10)), ("L1", D(9)), ("L2", D(8)), ("Autres", D(18)), ("Espèces", D(1))]
    assert others == 3
    # Une seule part en trop : affichée telle quelle plutôt qu'un « Autres » d'une ligne.
    assert top_parts(parts[:4], 3) == (parts[:4], 0)


# --- Performance par période --------------------------------------------------


def operation(id, day, op_type, qty, price, fees="0"):
    return replace(op(id, 1, op_type, qty, price, fees), op_date=day)


D2, J31, F3, F28, M3 = (
    date(2025, 1, 2),
    date(2025, 1, 31),
    date(2025, 2, 3),
    date(2025, 2, 28),
    date(2025, 3, 3),
)
OPS = [
    operation(1, D2, "achat", "10", "5"),
    operation(2, F3, "achat", "10", "5.5"),
    operation(3, M3, "vente", "10", "6.05", "1"),
]
POINTS = [(D2, D("50")), (J31, D("55")), (F3, D("110")), (F28, D("121")), (M3, D("60.5"))]


def test_apports_et_retraits():
    assert daily_flows(OPS, {"EUR": D("1")}) == {D2: D("50"), F3: D("55.0"), M3: D("-59.50")}


def test_performance_mensuelle_sans_compter_les_achats():
    flows = daily_flows(OPS, {"EUR": D("1")})
    jan, feb, mar = performance(POINTS, flows, "month", D2, M3)
    assert (jan.label, jan.gain, jan.performance) == ("2025-01", D("5"), D("0.1"))
    # Février : l'achat de 55 € n'est pas un gain ; le cours a pris 10 %.
    assert (feb.start_value, feb.flows, feb.end_value) == (D("55"), D("55.0"), D("121"))
    assert (feb.gain, feb.performance) == (D("11.0"), D("0.1"))
    assert (feb.cumulative_gain, feb.cumulative_factor) == (D("16.0"), D("1.21"))
    # Mars : vente au cours du jour, seuls les frais (1 €) sont perdus.
    assert mar.gain == D("-1.00")
    assert mar.first == date(2025, 3, 1) and mar.last == M3  # bornée par la fin de l'analyse


def test_performance_depuis_une_date_choisie():
    flows = daily_flows(OPS, {"EUR": D("1")})
    [feb] = performance(POINTS, flows, "year", date(2025, 2, 1), F28)
    assert (feb.label, feb.start_value, feb.gain, feb.performance) == (
        "2025",
        D("55"),
        D("11.0"),
        D("0.1"),
    )


def test_periodes():
    jeudi = date(2025, 1, 2)
    assert period_key(jeudi, "week") == ("2025-S01", date(2024, 12, 30), date(2025, 1, 5))
    assert period_key(date(2024, 2, 10), "month") == (
        "2024-02",
        date(2024, 2, 1),
        date(2024, 2, 29),
    )
    assert period_key(jeudi, "year")[0] == "2025"
    with pytest.raises(ValueError):
        period_key(jeudi, "siecle")


def test_baisse_max_et_annualisation():
    flows = {D2: D("100")}
    points = [(D2, D("100")), (J31, D("110")), (F28, D("99")), (M3, D("105"))]
    results = performance(points, flows, "day", D2, M3)
    assert max_drawdown(results) == D("-0.1")  # de 110 à 99
    assert annualized(D("1.21"), 730).quantize(D("0.0001")) == D("0.1000")
    assert annualized(D("1.21"), 200) is None  # moins d'un an : pas d'extrapolation


def test_argent_net_place_arbitrage_et_cadeau():
    """Argent net placé (D-110) : achats moins ventes au montant payé ou reçu ; une vente puis
    un rachat avec la plus-value ne le change pas, une part offerte n'y entre pas."""
    from dataclasses import replace

    gift = replace(op(3, 6, "achat", "1", "8.00"), offered=D("8.00"))  # récompense
    ops = [
        op(1, 2, "achat", "10", "5.00", "1.00"),  # 51 € placés
        op(2, 4, "vente", "10", "8.00", "1.00"),  # 79 € reçus
        op(4, 5, "achat", "10", "7.90", asset_id=2),  # rachat avec la plus-value
        gift,
    ]
    rates = {"EUR": D("1")}
    placed = value_history(ops, {}, rates, jour(6), placed=True)
    assert [p[2] for p in placed] == [D("51.00"), D("-28.00"), D("51.00"), D("51.00")]
    cost = value_history(ops, {}, rates, jour(6))
    assert [p[2] for p in cost] == [D("51.00"), D("0"), D("79.00"), D("87.00")]
