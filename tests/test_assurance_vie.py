"""Carte « Assurance-vie » de Fiscalité (D-142) : part de gains d'un rachat, abattement, seuil
de 150 000 €. Montants fictifs ; exemples chiffrés repris du BOFiP quand il en donne."""

from datetime import date
from decimal import Decimal as D

import pytest
from fastapi.testclient import TestClient

from app import life_insurance as av
from app.main import app

OLD = date(2010, 1, 1)  # contrat de plus de 8 ans
TODAY = date(2026, 6, 1)


def test_part_de_gains_d_un_rachat_partiel():
    # 100 000 versés, contrat à 150 000, rachat de 30 000 : 20 000 de versements, 10 000 de gains.
    rest, [g] = av.replay(
        [av.Premium(date(2018, 1, 1), D("100000"))],
        [av.Buyback(date(2026, 1, 5), D("30000"), D("150000"))],
        date(2018, 1, 1),
    )
    assert (g.gain, rest.total, g.estimated, g.eight_years) == (D("10000"), D("80000"), False, True)


def test_rachats_successifs_et_contrat_en_perte():
    premiums = [av.Premium(date(2019, 1, 1), D("100000"))]
    # Contrat en perte (80 000 pour 100 000 versés) : la part de versements est plafonnée au
    # rachat (tolérance du BOFiP), aucun gain.
    rest, [g1] = av.replay(premiums, [av.Buyback(date(2020, 1, 1), D("10000"), D("80000"))], None)
    assert (g1.gain, rest.total) == (D("0"), D("90000"))
    # Deuxième rachat : les versements restants (90 000) remplacent les versements d'origine.
    rest, [_, g2] = av.replay(
        premiums,
        [
            av.Buyback(date(2020, 1, 1), D("10000"), D("80000")),
            av.Buyback(date(2021, 1, 1), D("12000"), D("120000")),
        ],
        None,
    )
    assert (g2.gain, rest.total) == (D("3000"), D("81000"))  # 12 000 − 90 000 × 0,1
    # Valeur du jour inconnue : tout le rachat est compté en versements, signalé.
    _, [g3] = av.replay(premiums, [av.Buyback(date(2020, 1, 1), D("5000"), None)], None)
    assert (g3.gain, g3.estimated) == (D("0"), True)


def test_versements_avant_et_apres_le_27_septembre_2017():
    premiums = [
        av.Premium(None, D("30000")),  # champ du compte, sans date : ouvert avant → avant 2017
        av.Premium(date(2017, 9, 26), D("10000")),
        av.Premium(date(2017, 9, 27), D("40000")),
    ]
    rest, _ = av.replay(premiums, [], date(2012, 3, 1))
    assert (rest.pre, rest.post) == (D("40000"), D("40000"))
    rest, _ = av.replay(premiums[:1], [], date(2018, 3, 1))  # ouvert après : tout après 2017
    assert (rest.pre, rest.post) == (D("0"), D("30000"))
    # Situation au 31/12 de l'année passée : rien après cette date.
    rest, _ = av.replay(premiums, [], date(2012, 3, 1), until=date(2017, 12, 31))
    assert rest.total == D("80000")
    rest, _ = av.replay(premiums, [], date(2012, 3, 1), until=date(2016, 12, 31))
    assert rest.total == D("30000")


def test_seuil_de_150_000_exemple_du_bofip():
    # BOI-RPPM-RCM-20-15 § 230 : 100 000 € de versements d'avant 2017, 250 000 € d'après ;
    # 15 000 € de gains d'après 2017 → 15 000 × (150 000 − 100 000) / 250 000 = 3 000 € à 7,5 %.
    encours = av.Remaining(D("100000"), D("250000"))
    assert av.reduced_fraction(encours) == D("0.2")
    out = av.income_tax(D("20000"), D("5000"), OLD, TODAY, D("0"), encours)
    assert (out["at_reduced"], out["at_pfu"]) == (D("8000"), D("12000"))  # 5 000 + 3 000
    assert out["tax"] == D("8000") * D("0.075") + D("12000") * D("0.128")
    # Versements d'avant 2017 au-delà de 150 000 € : rien de ceux d'après à 7,5 %.
    assert av.reduced_fraction(av.Remaining(D("160000"), D("10000"))) == 0
    assert av.reduced_fraction(av.Remaining(D("50000"), D("100000"))) == 1


def test_abattement_impute_dans_l_ordre_du_bofip():
    encours = av.Remaining(D("100000"), D("250000"))
    # 5 000 de gains d'avant 2017, 3 000 à 7,5 %, 12 000 à 12,8 % ; abattement de 9 200 :
    # 5 000 sur l'avant 2017, 3 000 sur la part à 7,5 %, 1 200 sur la part à 12,8 %.
    out = av.income_tax(D("20000"), D("5000"), OLD, TODAY, D("9200"), encours)
    assert out["allowance_used"] == D("9200")
    assert out["tax"] == D("10800") * D("0.128")
    # Gains sous l'abattement : pas d'impôt, abattement en partie gardé.
    out = av.income_tax(D("1000"), D("0"), OLD, TODAY, D("4600"), av.Remaining(D("0"), D("1")))
    assert (out["tax"], out["allowance_used"]) == (D("0"), D("1000"))


def test_avant_huit_ans_ni_abattement_ni_taux_reduit():
    small = av.Remaining(D("0"), D("10000"))
    out = av.income_tax(D("1000"), D("0"), date(2022, 1, 1), TODAY, D("4600"), small)
    assert (out["tax"], out["allowance_used"]) == (D("128"), D("0"))  # 12,8 %
    # Versements d'avant 2017 : 15 % de 4 à 8 ans, 35 % avant 4 ans.
    mid = av.income_tax(D("1000"), D("1000"), date(2017, 1, 1), date(2022, 1, 1), D("0"), small)
    assert mid["tax"] == D("150")
    young = av.income_tax(D("1000"), D("1000"), date(2017, 1, 1), date(2019, 1, 1), D("0"), small)
    assert young["tax"] == D("350")


def test_rachat_sans_impot():
    # 40 000 € pour 30 000 de versements : 25 % de gains, 4 600 € d'abattement → 18 400 €.
    assert av.tax_free_buyback(D("40000"), D("30000"), True, D("4600")) == D("18400")
    assert av.tax_free_buyback(D("40000"), D("30000"), False, D("4600")) == 0  # avant 8 ans
    assert av.tax_free_buyback(D("20000"), D("30000"), False, D("0")) == D("20000")  # en perte
    assert av.tax_free_buyback(D("40000"), D("39000"), True, D("4600")) == D("40000")


@pytest.fixture
def client(db_file):
    with TestClient(app) as c:
        yield c


def post(client, path, body, status=201):
    res = client.post(path, json=body)
    assert res.status_code == status, res.text
    return res.json()


def test_carte_assurance_vie(client):
    """Contrat fictif ouvert en 2015 : fonds euros et unité de compte à valeur saisie, un rachat
    partiel en 2025 (vente d'unités, puis retrait)."""
    acc = post(
        client,
        "/api/accounts",
        {"name": "AV fictive", "kind": "assurance_vie", "opened_on": "2015-01-10"},
    )["id"]
    fonds = post(
        client, "/api/assets", {"name": "Fonds euros fictif", "asset_class": "fonds_euros"}
    )
    uc = post(client, "/api/assets", {"name": "UC fictive", "asset_class": "fonds"})

    def op(body):
        return post(client, "/api/operations", {"account_id": acc, **body})

    op({"op_date": "2015-01-10", "op_type": "versement", "amount": "10000"})
    op({"asset_id": fonds["id"], "op_date": "2015-01-11", "op_type": "achat",
        "quantity": "10000", "unit_price": "1"})  # fmt: skip
    op({"op_date": "2019-02-01", "op_type": "versement", "amount": "20000"})
    op({"asset_id": uc["id"], "op_date": "2019-02-02", "op_type": "achat",
        "quantity": "20000", "unit_price": "1"})  # fmt: skip
    for asset, value in ((fonds, "12000"), (uc, "26000")):
        path = f"/api/assets/{asset['id']}/valuations"
        post(client, path, {"value_date": "2024-12-31", "value": value})
    op({"asset_id": uc["id"], "op_date": "2025-02-28", "op_type": "vente",
        "quantity": "2000", "unit_price": "1.5"})  # fmt: skip
    op({"op_date": "2025-03-01", "op_type": "retrait", "amount": "3000"})

    data = client.get("/api/tax?year=2025").json()
    [row] = data["assurance_vie"]
    life = data["life_insurance"]
    # Valeur avant le rachat : 12 000 + 18 000 × 1,30 + 3 000 d'espèces = 38 400 ;
    # part de versements = 30 000 × 3 000 / 38 400 = 2 343,75 ; gains = 656,25.
    [b] = life["buybacks"]
    assert (b["amount"], b["gain"], b["eight_years"], b["estimated"]) == (
        "3000.00",
        "656.25",
        True,
        False,
    )
    # Aujourd'hui : 35 400 € pour 27 656,25 de versements restants, gains 7 743,75 dont 1/3
    # d'avant 2017 ; abattement de 4 600 : 2 581,25 sur l'avant 2017, 2 018,75 sur le reste.
    assert (row["value"], row["remaining"], row["gains"]) == ("35400.00", "27656.25", "7743.75")
    assert row["exit_ir"] == "235.78"  # (5 162,50 − 2 018,75) × 7,5 %
    # Prélèvements sociaux : pas sur les intérêts du fonds euros, déjà prélevés chaque année.
    assert row["exit_social"] == "987.93"  # (7 743,75 − 2 000 d'intérêts du fonds euros) × 17,2 %
    assert row["tax_free"] == "21028.57"  # 4 600 / (7 743,75 / 35 400)
    assert (life["allowance_left"], life["over_cap"], life["encours"]) == (
        "4600.00",
        False,
        "27656.25",
    )
    couple = client.get("/api/tax?year=2025&couple=true").json()
    assert couple["assurance_vie"][0]["exit_ir"] == "0.00"
    assert couple["life_insurance"]["allowance"] == "9200.00"
    assert client.get("/api/tax?year=2024").json()["life_insurance"]["buybacks"] == []


def test_rachat_ou_frais_sans_espece_ne_sont_pas_des_versements(client):
    """Retrait saisi sans la vente d'unités qui l'a payé, frais de gestion pris sur les unités :
    pas de versement supposé (D-062 ne vaut que pour les achats)."""
    acc = post(
        client,
        "/api/accounts",
        {"name": "AV fictive", "kind": "assurance_vie", "opened_on": "2015-01-10"},
    )["id"]
    uc = post(client, "/api/assets", {"name": "UC fictive", "asset_class": "fonds"})

    def op(body):
        return post(client, "/api/operations", {"account_id": acc, **body})

    op({"op_date": "2015-01-10", "op_type": "versement", "amount": "10000"})
    op({"asset_id": uc["id"], "op_date": "2015-01-11", "op_type": "achat",
        "quantity": "100", "unit_price": "100"})  # fmt: skip
    post(
        client, f"/api/assets/{uc['id']}/valuations", {"value_date": "2024-12-31", "value": "15000"}
    )
    op({"op_date": "2025-03-01", "op_type": "frais", "amount": "80"})
    op({"op_date": "2025-03-03", "op_type": "retrait", "amount": "3000"})
    life = client.get("/api/tax?year=2025").json()
    [row] = life["assurance_vie"]
    [b] = life["life_insurance"]["buybacks"]
    # Valeur avant le rachat : 15 000 (les frais ont laissé les espèces à zéro) ;
    # part de gains = 3 000 − 10 000 × 3 000 / 15 000 = 1 000 ; versements restants 8 000.
    assert (b["gain"], row["paid_in"], row["remaining"]) == ("1000.00", "10000.00", "8000.00")
