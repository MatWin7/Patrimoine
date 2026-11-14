"""TRI, volatilité, indice et projection, sur des chiffres FICTIFS calculables à la main."""

from datetime import date
from decimal import Decimal as D

from app.analytics import project, simulate_benchmark, volatility, xirr


def arrondi(value, places="0.0001"):
    return value.quantize(D(places))


def test_tri_d_un_placement_simple():
    # 100 € versés, 110 € récupérés un an (365 jours) plus tard : 10 % par an.
    flows = [(date(2025, 1, 1), D("-100")), (date(2026, 1, 1), D("110"))]
    assert arrondi(xirr(flows)) == D("0.1000")


def test_tri_tient_compte_du_moment_des_versements():
    # Le 2e versement n'a travaillé que 6 mois : le TRI dépasse le gain / total versé.
    flows = [
        (date(2025, 1, 1), D("-100")),
        (date(2025, 7, 2), D("-100")),
        (date(2026, 1, 1), D("215")),
    ]
    rate = xirr(flows)
    assert D("0.09") < rate < D("0.11")  # 15 € de gain sur 200 € versés, mais 1,5 an-argent


def test_tri_impossible():
    assert xirr([(date(2025, 1, 1), D("-100"))]) is None  # aucun retour
    assert xirr([(date(2025, 1, 1), D("-100")), (date(2025, 1, 1), D("100"))]) is None


def test_volatilite():
    assert volatility([D("1.01")]) is None
    assert volatility([D("1.01"), D("1.01"), D("1.01")]) == D("0")  # aucune variation
    # Rendements +1 % et -1 % : écart type 1,414 %, annualisé × racine(252).
    vol = volatility([D("1.01"), D("0.99")])
    assert arrondi(vol) == D("0.2245")


def test_indice_meme_argent_memes_dates():
    days = [date(2025, 1, 2), date(2025, 1, 3), date(2025, 1, 6)]
    closes = [(date(2025, 1, 2), D("10")), (date(2025, 1, 3), D("11")), (date(2025, 1, 6), D("12"))]
    flows = {date(2025, 1, 2): D("100"), date(2025, 1, 6): D("60")}
    points = simulate_benchmark(days, flows, closes, D("0"), date(2025, 1, 2))
    # 10 parts achetées à 10, puis 5 parts à 12 : 15 parts × 12.
    assert points == [
        (date(2025, 1, 2), D("100")),
        (date(2025, 1, 3), D("110")),
        (date(2025, 1, 6), D("180")),
    ]
    # Un retrait revend des parts pour le même montant.
    flows[date(2025, 1, 6)] = D("-60")
    assert simulate_benchmark(days, flows, closes, D("0"), date(2025, 1, 2))[-1][1] == D("60")
    assert simulate_benchmark(days, flows, [], D("0"), date(2025, 1, 2)) == []


def test_projection():
    assert project(D("1000"), D("100"), D("0"), 3) == [D("1100"), D("1200"), D("1300")]
    twelve = project(D("1000"), D("0"), D("0.06"), 12)
    assert arrondi(twelve[-1], "0.01") == D("1060.00")  # 12 mois composés = 6 % sur l'année
