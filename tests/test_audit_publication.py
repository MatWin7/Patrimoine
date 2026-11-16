"""Audit avant publication (D-138) : un test par défaut corrigé. Données FICTIVES, aucun réseau.

Nombres en notation scientifique, saisies sans bornes, table des cours qui grossit, transferts
oubliés par la réconciliation et par les plus-values d'un PEA, cours en pence, cryptos sans
historique, dividendes en devise, essais de code sans limite à la suppression d'un profil.
"""

import os
from datetime import UTC, date, datetime, timedelta
from decimal import Decimal as D

import httpx2
import pytest
from fastapi.testclient import TestClient

from app import db, debts, income, prices, profiles, repository, settings
from app.analytics import xirr
from app.importers.csv_generique import parse_date, parse_decimal
from app.main import app
from app.models import Operation
from app.portfolio import RateHistory, compute_positions, eur_costs
from app.providers import Unreachable, yahoo
from app.schemas import money, plain
from app.tax import cto_estimate
from tests.conftest import fake_client

EUR = {"EUR": D("1")}
TTL = timedelta(minutes=5)


def op(
    i,
    day,
    kind,
    qty=None,
    price=None,
    amount=None,
    cur="EUR",
    asset=1,
    account=1,
    to=None,
    fees="0",
):
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
        to_account_id=to,
    )


@pytest.fixture
def client(db_file):
    with TestClient(app) as c:
        yield c


@pytest.fixture
def conn(db_file):
    db.init_db()
    with db.connect() as c:
        yield c
    c.close()


def new_line(client, kind="crypto", asset_class="crypto", **asset):
    account = client.post("/api/accounts", json={"name": f"Compte {kind}", "kind": kind}).json()
    body = {"name": "Jeton fictif", "asset_class": asset_class} | asset
    created = client.post("/api/assets", json=body).json()
    return account, created, {"account_id": account["id"], "asset_id": created["id"]}


# --- Nombres : jamais de notation scientifique, jamais hors bornes --------------------------


def test_plain_ecrit_toujours_en_decimal():
    assert str(D("0.00000012")) == "1.2E-7"  # ce que renvoyait l'API
    assert plain(D("0.00000012")) == "0.00000012"
    assert plain(D("0E-8")) == "0.00000000"
    assert plain(D("1E+1")) == "10"


def test_api_poussiere_de_crypto_sans_notation_scientifique(client):
    _, _, line = new_line(client, coingecko_id="jeton-fictif")
    trade = {"op_date": "2026-01-02", "op_type": "achat", "unit_price": "50000"}
    created = client.post("/api/operations", json=line | trade | {"quantity": "0.00000012"})
    assert created.status_code == 201
    assert created.json()["quantity"] == "0.00000012"
    assert client.get("/api/operations").json()[0]["quantity"] == "0.00000012"
    assert client.get("/api/portfolio").json()["positions"][0]["quantity"] == "0.00000012"


def test_taux_d_une_dette_a_dix_pour_cent(client):
    body = {"name": "Prêt fictif", "kind": "conso", "principal": "1000", "rate_pct": "10"}
    debt = client.post("/api/debts", json=body | {"start_date": "2025-01-01", "months": 12})
    assert debt.status_code == 201
    (listed,) = client.get("/api/debts").json()["debts"]
    assert listed["rate_pct"] == "10"  # avant : « 1E+1 », que le formulaire ne savait pas relire


def test_nombre_demesure_refuse_a_la_saisie(client):
    """Avant : l'opération était enregistrée, puis chaque page répondait par une erreur 500."""
    _, _, line = new_line(client, coingecko_id="jeton-fictif")
    trade = line | {"op_date": "2026-01-02", "op_type": "achat"}
    huge = client.post("/api/operations", json=trade | {"quantity": "1E+20", "unit_price": "1"})
    assert huge.status_code == 422
    dust = client.post("/api/operations", json=trade | {"quantity": "1E-19", "unit_price": "1"})
    assert dust.status_code == 422  # plus de 18 décimales
    edge = client.post("/api/operations", json=trade | {"quantity": "1E+15", "unit_price": "1E+15"})
    assert edge.status_code == 201  # aux bornes : accepté, et les pages répondent toujours
    assert client.get("/api/portfolio").status_code == 200
    assert client.get("/api/operations").status_code == 200


def test_date_absurde_refusee_a_la_saisie(client):
    _, _, line = new_line(client, coingecko_id="jeton-fictif")
    trade = line | {"op_type": "achat", "quantity": "1", "unit_price": "1"}
    assert client.post("/api/operations", json=trade | {"op_date": "0202-01-02"}).status_code == 422
    assert client.post("/api/operations", json=trade | {"op_date": "1900-01-01"}).status_code == 201
    account = {"name": "PEA fictif", "kind": "pea", "opened_on": "0001-01-01"}
    assert client.post("/api/accounts", json=account).status_code == 422


def test_arrondi_d_un_total_de_plus_de_26_chiffres():
    """La précision par défaut (28 chiffres) refusait cet arrondi : InvalidOperation."""
    assert money(D("123456789012345678901234567.895")) == "123456789012345678901234567.90"
    assert money(D("1E+30")) == "1" + "0" * 30 + ".00"
    assert money(D("1234.565")) == "1234.57"  # l'arrondi habituel ne change pas


def test_import_bornes_des_nombres_et_des_dates():
    assert parse_decimal("1 234,56 €") == D("1234.56")
    assert parse_date("02/01/2025") == date(2025, 1, 2)
    with pytest.raises(ValueError, match="hors bornes"):
        parse_decimal("1E+40")
    with pytest.raises(ValueError, match="date illisible"):
        parse_date("0202-01-02")


def test_id_coingecko_a_alphabet_ferme(client):
    bad = {"name": "Jeton fictif", "asset_class": "crypto", "coingecko_id": "jeton/../fictif"}
    assert client.post("/api/assets", json=bad).status_code == 422
    good = bad | {"coingecko_id": " Usd-Coin "}
    assert client.post("/api/assets", json=good).json()["coingecko_id"] == "usd-coin"


# --- Table des cours : un seul cours en ligne par actif ------------------------------------


def test_un_seul_cours_en_ligne_garde_par_actif(conn):
    etf = repository.create_asset(conn, "ETF fictif", "etf", None, "FAKE.PA", None, "EUR")
    t0 = datetime(2026, 1, 5, 10, 0, tzinfo=UTC)
    for minutes in range(0, 60, 5):  # une heure de page ouverte : 12 rafraîchissements
        price = prices.Price(D("5") + minutes, "EUR", "yahoo", t0 + timedelta(minutes=minutes))
        prices.save_price(conn, etf.id, price)
    assert conn.execute("SELECT count(*) FROM price").fetchone()[0] == 1  # avant : 12
    assert prices.latest_price(conn, etf.id).price == D("60")


def test_le_menage_des_cours_garde_la_valeur_saisie(conn):
    etf = repository.create_asset(conn, "ETF fictif", "etf", None, "FAKE.PA", None, "EUR")
    conn.execute(
        "INSERT INTO price (asset_id, fetched_at, price, currency, source)"
        " VALUES (?, '2025-12-31T00:00:00Z', '100', 'EUR', 'manuel')",
        (etf.id,),
    )
    now = datetime(2026, 1, 5, 10, 0, tzinfo=UTC)
    prices.save_price(conn, etf.id, prices.Price(D("5"), "EUR", "yahoo", now))
    sources = [r[0] for r in conn.execute("SELECT source FROM price ORDER BY source")]
    assert sources == ["manuel", "yahoo"]


# --- Transferts entre comptes de l'app ------------------------------------------------------


def test_reconciliation_d_un_compte_alimente_par_transfert(client):
    """Avant : la ligne reçue manquait, et sa vente faisait répondre 500 à la comparaison."""
    old = client.post("/api/accounts", json={"name": "CTO fictif A", "kind": "cto"}).json()
    new = client.post("/api/accounts", json={"name": "CTO fictif B", "kind": "cto"}).json()
    etf = client.post(
        "/api/assets", json={"name": "ETF fictif", "asset_class": "etf", "ticker": "fake.pa"}
    ).json()
    base = {"asset_id": etf["id"], "unit_price": "20"}
    buy = base | {"account_id": old["id"], "op_date": "2025-01-02", "op_type": "achat"}
    assert client.post("/api/operations", json=buy | {"quantity": "10"}).status_code == 201
    move = base | {"account_id": old["id"], "op_date": "2025-02-03", "quantity": "10"}
    move |= {"op_type": "transfert_sortant", "to_account_id": new["id"]}
    assert client.post("/api/operations", json=move).status_code == 201
    sale = base | {"account_id": new["id"], "op_date": "2025-03-03", "op_type": "vente"}
    assert client.post("/api/operations", json=sale | {"quantity": "4"}).status_code == 201

    res = client.post(
        f"/api/accounts/{new['id']}/reconcile", json={"quantities": {str(etf["id"]): "6"}}
    )
    assert res.status_code == 200
    (line,) = res.json()["lines"]
    assert (line["app"], line["bank"], line["ok"]) == ("6", "6", True)


def test_plus_value_d_un_pea_recu_d_un_autre_pea():
    """PEA transféré d'une banque à l'autre : 10 titres achetés 100 €, vendus 120 € dans le
    nouveau plan. Plus-value 200 € ; avant, tout le prix de vente (1 200 €)."""
    day = date(2026, 1, 5)
    ops = [
        op(1, day, "achat", "10", "100", account=1),
        op(2, day + timedelta(days=30), "transfert_sortant", "10", "110", account=1, to=2),
        op(3, day + timedelta(days=60), "vente", "10", "120", account=2),
    ]
    one = cto_estimate(ops, {2}, 2026, EUR)
    assert (one["gains"], one["losses"]) == (D("200"), D("0"))
    assert cto_estimate(ops, {1, 2}, 2026, EUR, only=2)["gains"] == D("200")
    assert cto_estimate(ops, {1}, 2026, EUR)["sales"] == []  # la vente n'est pas dans l'ancien


def test_titres_sortis_des_comptes_titres_quittent_le_prix_moyen_commun():
    """Deux comptes-titres (1, 2) au prix moyen commun, un compte « autre » (3). Les titres
    envoyés vers 3 emportent leur prix de revient et sortent du prix moyen commun : avant, ils
    y restaient et faussaient le prix moyen des achats suivants (200 € au lieu de 225 €)."""
    day = date(2026, 1, 5)
    ops = [
        op(1, day, "achat", "10", "100", account=1),
        op(2, day, "achat", "10", "200", account=2),
        op(3, day + timedelta(days=1), "transfert_sortant", "10", "150", account=1, to=3),
        op(4, day + timedelta(days=2), "vente", "10", "300", account=3),
        op(5, day + timedelta(days=3), "achat", "10", "300", account=2),
        op(6, day + timedelta(days=4), "vente", "10", "300", account=2),
    ]
    est = cto_estimate(ops, {1, 2}, 2026, EUR, pooled=True)
    assert [s.op_id for s, _ in est["sales"]] == [6]  # la vente faite dans 3 n'en est pas une
    assert est["gains"] == D("750")  # 3 000 − 10 × 225 € (10 à 150 € + 10 à 300 €)


def test_resultats_inchanges_sans_transfert():
    """La correction ne change rien quand aucun titre ne vient d'ailleurs."""
    day = date(2026, 1, 5)
    ops = [
        op(1, day, "achat", "10", "100", account=1),
        op(2, day, "achat", "5", "50", account=9),  # un autre compte, hors du calcul
        op(3, day + timedelta(days=1), "vente", "4", "130", account=1),
        op(4, day + timedelta(days=1), "vente", "5", "80", account=9),
    ]
    est = cto_estimate(ops, {1}, 2026, EUR)
    assert (est["gains"], [s.op_id for s, _ in est["sales"]]) == (D("120"), [3])


# --- Calculs --------------------------------------------------------------------------------


def test_vente_totale_ne_laisse_aucune_poussiere_de_cout():
    """Vente partielle puis vente du reste : la division laissait −2E-24 € de prix de revient,
    gardés par le rachat suivant. Quantités et prix tirés au hasard (aucune opération réelle)
    jusqu'à en trouver qui laissent ce résidu."""
    day = date(2026, 1, 5)
    ops = [
        op(1, day, "achat", "0.0028267", "74723.58", fees="6.05"),
        op(2, day, "achat", "0.35746283", "38379.94"),
        op(3, day + timedelta(days=1), "vente", "0.13720697", "40000"),
        op(4, day + timedelta(days=2), "vente", "0.22308256", "40000"),
        op(5, day + timedelta(days=3), "achat", "2", "10"),
    ]
    (position,) = compute_positions(ops)
    assert position.cost == D("20")  # exactement le rachat


def test_prix_de_revient_latent_apres_un_transfert_entre_comptes_titres():
    """10 titres achetés 100 € dans un compte-titres puis transférés dans un autre : leur prix
    de revient reste 1 000 €. Avant, le prix moyen commun le retirait sans le rendre : 0 €, et
    toute la valeur de la ligne passait pour une plus-value latente."""
    day = date(2026, 1, 5)
    fx = RateHistory(EUR)
    ops = [
        op(1, day, "achat", "10", "100", account=1),
        op(2, day + timedelta(days=1), "transfert_sortant", "10", "110", account=1, to=2),
    ]
    assert eur_costs(ops, fx, pooled=True, pool={1, 2}) == {(2, 1): D("1000")}
    assert eur_costs(ops, fx, pooled=True) == {(2, 1): D("1000")}


def test_prix_de_revient_latent_de_titres_venus_d_un_autre_type_de_compte():
    """Compte 9 (hors comptes-titres) : 10 titres à 100 €, envoyés au compte-titres 2, où 10
    autres ont coûté 200 €. Prix moyen commun des comptes-titres : 150 €."""
    day = date(2026, 1, 5)
    fx = RateHistory(EUR)
    ops = [
        op(1, day, "achat", "10", "100", account=9),
        op(2, day, "achat", "10", "200", account=2),
        op(3, day + timedelta(days=1), "transfert_sortant", "10", "110", account=9, to=2),
        op(4, day + timedelta(days=2), "vente", "5", "300", account=2),
    ]
    costs = eur_costs(ops, fx, pooled=True, pool={1, 2})
    assert costs == {(2, 1): D("2250")}  # 15 titres restants à 150 €
    # Le même prix moyen que celui de la plus-value réalisée : 5 × (300 − 150).
    assert cto_estimate(ops, {1, 2}, 2026, EUR, pooled=True)["gains"] == D("750")


def test_prix_de_revient_par_compte_inchange():
    day = date(2026, 1, 5)
    fx = RateHistory(EUR)
    ops = [
        op(1, day, "achat", "10", "100", account=1),
        op(2, day, "achat", "10", "200", account=2),
        op(3, day + timedelta(days=1), "vente", "4", "300", account=1),
    ]
    assert eur_costs(ops, fx) == {(1, 1): D("600"), (2, 1): D("2000")}
    assert eur_costs(ops, fx, pooled=True, pool={1, 2}) == {(1, 1): D("900"), (2, 1): D("1500")}
    assert eur_costs(ops, fx, pooled=True) == eur_costs(ops, fx, pooled=True, pool={1, 2})


def test_tri_quel_que_soit_le_sens_des_flux():
    start, end = date(2025, 1, 1), date(2026, 1, 1)
    usual = xirr([(start, D("-100")), (end, D("110"))])
    assert abs(usual - D("0.1")) < D("0.000001")
    # Argent sorti d'abord, remis ensuite : même taux, et non une borne de la recherche.
    mirrored = xirr([(start, D("100")), (end, D("-110"))])
    assert abs(mirrored - D("0.1")) < D("0.000001")
    # Presque tout perdu en un an : la solution est la borne basse elle-même.
    assert xirr([(start, D("-1000")), (end, D("10"))]) == D("-0.99")


def test_capital_restant_du_saisi_apres_la_fin_prevue():
    debt = debts.Debt(
        1, "Prêt fictif", "conso", None, "EUR", D("1200"), D("0"), date(2025, 1, 1), 12
    )
    assert debts.balance_at(debt, {}, date(2026, 3, 1)) == D("0")  # fini comme prévu
    later = {date(2026, 2, 1): D("500")}  # échéances reportées : il reste 500 €
    assert debts.balance_at(debt, later, date(2026, 3, 1)) == D("500")  # avant : 0
    during = {date(2025, 7, 1): D("900")}  # un recalage en cours de prêt ne change pas la fin
    assert debts.balance_at(debt, during, date(2026, 3, 1)) == D("0")


def test_dividende_en_devise_au_taux_de_son_jour():
    """100 $ reçus un jour à 1 € = 1,25 $ : 80 €, même si le dollar vaut autre chose aujourd'hui.
    Les mêmes euros que dans l'estimation fiscale."""
    paid = date(2025, 3, 3)
    ops = [op(1, paid, "dividende", amount="100", cur="USD")]
    today_rates = {"EUR": D("1"), "USD": D("1")}
    fx = RateHistory(today_rates, {"USD": [(paid, D("1.25"))]})
    assert income.income_summary(ops, today_rates, date(2026, 1, 5))["total"] == D("100")
    assert income.income_summary(ops, today_rates, date(2026, 1, 5), fx)["total"] == D("80")
    assert cto_estimate(ops, {1}, 2025, today_rates, history=fx)["dividends"] == D("80")
    fee = [op(2, paid, "frais", amount="10", cur="USD")]
    assert income.fees_by_year(fee, today_rates, fx) == {2025: D("8")}
    assert income.fees_by_asset(fee, today_rates, fx) == {1: D("8")}


# --- Yahoo : cotations en pence, cryptos, source muette -------------------------------------


def chart(currency, closes, first=date(2026, 1, 5)):
    stamps = [
        int(datetime.combine(first + timedelta(days=i), datetime.min.time(), UTC).timestamp())
        + 43200
        for i in range(len(closes))
    ]
    meta = {"currency": currency, "regularMarketPrice": closes[-1], "chartPreviousClose": closes[0]}
    quote = {"close": closes}
    result = {"meta": meta, "timestamp": stamps, "indicators": {"quote": [quote]}}
    return {"chart": {"result": [result]}}


class Yahoo:
    def __init__(self, currency="EUR", closes=(10.0, 11.0)):
        self.currency, self.closes, self.requests = currency, list(closes), []

    def __call__(self, request: httpx2.Request) -> httpx2.Response:
        self.requests.append(request)
        return httpx2.Response(200, json=chart(self.currency, self.closes))


def test_cotation_de_londres_en_pence():
    """Yahoo donne SHEL.L en « GBp » : 3 732,5 pence font 37,325 livres, pas 3 732,5 livres."""
    server = Yahoo("GBp", [3701.0, 3732.5])
    price = yahoo.get_price(fake_client(server), "FAKE.L")
    assert (price.price, price.currency, price.previous_close) == (D("37.325"), "GBP", D("37.01"))
    currency, closes = yahoo.get_history(
        fake_client(server), "FAKE.L", date(2026, 1, 1), date(2026, 1, 9)
    )
    assert currency == "GBP"
    assert [c for _, c in closes] == [D("37.01"), D("37.325")]
    usd = yahoo.get_price(fake_client(Yahoo("USD", [10.0, 12.5])), "FAKE")
    assert (usd.price, usd.currency) == (D("12.5"), "USD")  # une devise ordinaire ne bouge pas


def test_historique_lu_en_pence_avant_la_correction_est_redemande(conn):
    stock = repository.create_asset(conn, "Action fictive", "action", None, "FAKE.L", None, "GBP")
    for day, close in ((date(2025, 12, 1), "3600"), (date(2026, 1, 5), "3701")):  # cent fois trop
        conn.execute(
            "INSERT INTO price_history (asset_id, price_date, close, currency, source)"
            " VALUES (?, ?, ?, 'GBP', 'yahoo')",
            (stock.id, day.isoformat(), close),
        )
    server = Yahoo("GBp", [3701.0, 3732.5])
    start = date(2025, 12, 1)
    prices.update_history(conn, [(stock, start)], fake_client(server), today=date(2026, 1, 9))
    assert len(server.requests) == 2  # les derniers jours, puis tout l'historique
    assert "period1=" + str(int(datetime(2025, 1, 1, tzinfo=UTC).timestamp())) in str(
        server.requests[1].url
    )
    assert dict((d, c) for d, c, _ in prices.load_history(conn, stock.id))[date(2026, 1, 5)] == D(
        "37.01"
    )


def test_crypto_avec_ses_cloutures_yahoo(conn):
    """Une crypto connue de Yahoo a maintenant un cours par jour : sa courbe ne reste plus au
    prix des opérations. Seul le ticker public part (règle 3)."""
    coin = repository.create_asset(conn, "Bitcoin fictif", "crypto", None, None, "bitcoin", "USD")
    unknown = repository.create_asset(
        conn, "Jeton fictif", "crypto", None, None, "jeton-fictif", "USD"
    )
    server = Yahoo("EUR", [50000.0, 51000.0])
    start = date(2026, 1, 5)
    errors = prices.update_history(
        conn, [(coin, start), (unknown, start)], fake_client(server), today=date(2026, 1, 9)
    )
    assert errors == {}
    assert [r.url.path for r in server.requests] == ["/v8/finance/chart/BTC-EUR"]
    assert [(c, cur) for _, c, cur in prices.load_history(conn, coin.id)] == [
        (D("50000.0"), "EUR"),
        (D("51000.0"), "EUR"),
    ]
    assert prices.load_history(conn, unknown.id) == []  # inconnue de Yahoo : comme avant


def test_source_muette_demandee_une_seule_fois(conn):
    """Yahoo ne répond pas : un seul essai par passage, pas un délai de 10 s par titre."""
    assets = [
        repository.create_asset(conn, f"ETF fictif {i}", "etf", None, f"FAKE{i}.PA", None, "EUR")
        for i in range(4)
    ]
    calls = []

    def silent(request: httpx2.Request) -> httpx2.Response:
        calls.append(request)
        raise httpx2.ConnectTimeout("délai dépassé", request=request)

    start = date(2026, 1, 5)
    errors = prices.update_history(
        conn, [(a, start) for a in assets], fake_client(silent), today=date(2026, 1, 9)
    )
    assert len(calls) == 1
    assert set(errors) == {a.id for a in assets}  # chaque titre reste signalé

    calls.clear()
    quotes = prices.get_quotes(conn, assets, fake_client(silent), ttl=TTL, now=datetime.now(UTC))
    assert len(calls) == 1
    assert all(q.error == "source injoignable (ConnectTimeout)" for q in quotes.values())


def test_ticker_inconnu_n_arrete_pas_les_suivants(conn):
    assets = [
        repository.create_asset(conn, f"ETF fictif {i}", "etf", None, f"FAKE{i}.PA", None, "EUR")
        for i in range(3)
    ]
    calls = []

    def missing(request: httpx2.Request) -> httpx2.Response:
        calls.append(request)
        return httpx2.Response(404)

    quotes = prices.get_quotes(conn, assets, fake_client(missing), ttl=TTL, now=datetime.now(UTC))
    assert len(calls) == 3  # une réponse, même d'erreur : la source est joignable
    assert all(q.error == "réponse HTTP 404" for q in quotes.values())
    assert not issubclass(type(quotes), Unreachable)


# --- Sécurité -------------------------------------------------------------------------------


@pytest.fixture
def data_dir(tmp_path, monkeypatch):
    monkeypatch.delenv("PATRIMOINE_DB", raising=False)
    monkeypatch.setattr(profiles, "DATA_DIR", tmp_path)
    profiles.reset_sessions()
    yield tmp_path
    profiles.reset_sessions()


def test_essais_de_code_comptes_a_la_suppression_d_un_profil(data_dir):
    """Avant : la suppression laissait essayer des codes sans limite sur le profil d'un autre."""
    profiles.create("Camille")
    profiles.create("Marie", code="1357")
    for _ in range(profiles.MAX_FAILURES):
        with pytest.raises(profiles.ProfileError, match="Code incorrect"):
            profiles.delete("marie", "Marie", "0000")
    with pytest.raises(profiles.LockedOut):
        profiles.delete("marie", "Marie", "1357")  # même le bon code attend la fin du délai
    with pytest.raises(profiles.LockedOut):
        profiles.open_session("marie", "1357")  # toutes les portes sont fermées ensemble
    assert profiles.get("marie") is not None


def test_essais_de_code_comptes_au_changement_de_code(data_dir):
    profiles.create("Camille")
    profiles.create("Marie", code="1357")
    for _ in range(profiles.MAX_FAILURES):
        with pytest.raises(profiles.ProfileError, match="Code actuel incorrect"):
            profiles.set_code("marie", "2468", "0000")
    with pytest.raises(profiles.LockedOut):
        profiles.set_code("marie", "2468", "1357")


def test_bon_code_remet_le_compteur_a_zero(data_dir):
    profiles.create("Camille")
    profiles.create("Marie", code="1357")
    for _ in range(profiles.MAX_FAILURES - 1):
        with pytest.raises(profiles.ProfileError):
            profiles.delete("marie", "Marie", "0000")
    profiles.open_session("marie", "1357")
    with pytest.raises(profiles.ProfileError, match="Code incorrect"):
        profiles.delete("marie", "Marie", "0000")  # un seul essai raté : pas de délai
    profiles.delete("marie", "Marie", "1357")
    assert profiles.get("marie") is None


@pytest.mark.skipif(os.name == "nt", reason="droits Unix")
def test_fichier_des_reglages_lisible_par_son_seul_proprietaire(data_dir):
    settings.write_section("reglages", {"coingecko_api_key": "CG-cle-fictive-123"})
    assert (data_dir / "reglages.json").stat().st_mode & 0o777 == 0o600


def test_cle_coingecko_de_l_environnement_verifiee(data_dir, monkeypatch):
    monkeypatch.setenv("COINGECKO_API_KEY", "CG-cle-fictive-123")
    assert settings.coingecko_key() == "CG-cle-fictive-123"
    monkeypatch.setenv("COINGECKO_API_KEY", "clé avec espace\r\nX-Autre: 1")
    assert settings.coingecko_key() is None  # jamais posée telle quelle dans un en-tête HTTP


def test_essais_lances_en_meme_temps_comptes_un_par_un(data_dir):
    """La comparaison d'un code est lente exprès : vingt essais lancés ensemble passaient presque
    tous avant que le premier échec soit compté. Seuls cinq sont comparés désormais."""
    from concurrent.futures import ThreadPoolExecutor

    profiles.create("Camille")
    profiles.create("Marie", code="1357")

    def attempt(_):
        try:
            profiles.open_session("marie", "0000")
        except profiles.LockedOut:
            return "verrou"
        except profiles.ProfileError:
            return "essai"

    with ThreadPoolExecutor(max_workers=20) as pool:
        results = list(pool.map(attempt, range(20)))
    assert results.count("essai") == profiles.MAX_FAILURES
    assert results.count("verrou") == 20 - profiles.MAX_FAILURES


def test_essai_d_un_identifiant_de_cours_a_alphabet_ferme(client):
    assert client.get("/api/quote", params={"coingecko_id": "jeton/../fictif"}).status_code == 422
    bad_currency = {"coingecko_id": "jeton-fictif", "currency": "EUR&x=1"}
    assert client.get("/api/quote", params=bad_currency).status_code == 422
    assert client.get("/api/quote", params={"coingecko_id": "jeton-fictif"}).status_code == 404
