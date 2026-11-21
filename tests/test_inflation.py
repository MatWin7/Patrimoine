"""Inflation : indice Eurostat, investi moins l'inflation (D-045). Données FICTIVES, sans réseau."""

from datetime import UTC, date, datetime, timedelta
from decimal import Decimal as D

import httpx2
import pytest
from fastapi.testclient import TestClient

from app import prices
from app.analytics import eroded_invested, extend_index, index_on
from app.main import app, http_client
from app.providers import ProviderError, eurostat
from tests.conftest import fake_client

# Indice fictif : +1 % par mois de mars à mai 2025.
MONTHLY = {date(2025, 3, 1): D("100"), date(2025, 4, 1): D("101"), date(2025, 5, 1): D("102.01")}


def json_stat(values: dict[str, float]) -> dict:
    """Réponse au format JSON-stat d'Eurostat : valeurs rangées par position du mois."""
    months = sorted(values)
    return {
        "dimension": {"time": {"category": {"index": {m: i for i, m in enumerate(months)}}}},
        "value": {str(i): values[m] for i, m in enumerate(months) if values[m] is not None},
    }


class FakeEurostat:
    def __init__(self, status=200):
        self.status = status
        self.requests = []

    def __call__(self, request: httpx2.Request) -> httpx2.Response:
        self.requests.append(request)
        if self.status != 200:
            return httpx2.Response(self.status)
        if request.url.host == "ec.europa.eu":
            return httpx2.Response(
                200, json=json_stat({"2025-03": 100.0, "2025-04": 101.0, "2025-05": None})
            )
        return httpx2.Response(404)  # Yahoo : pas d'historique, courbe au prix des opérations


def test_lecture_json_stat_en_decimal():
    server = FakeEurostat()
    values = eurostat.get_hicp(fake_client(server))
    assert values == {date(2025, 3, 1): D("100.0"), date(2025, 4, 1): D("101.0")}  # mai absent
    [req] = server.requests
    # Règle 3 : le nom de la série publique, le pays et l'unité, rien d'autre.
    # Nouvelle table ECOICOP 2 d'abord : l'ancienne est figée à décembre 2025 (D-067).
    assert req.url.path.endswith("/prc_hicp_minr")
    # ECOICOP 2 : dimension « coicop18 », ensemble des produits « TOTAL ».
    params = {"format": "JSON", "geo": "FR", "unit": "I25", "coicop18": "TOTAL"}
    assert dict(req.url.params) == params
    assert req.content == b""


def test_variations_mensuelles_enchainees_en_indice():
    """Si l'indice manque dans la nouvelle table, ses variations mensuelles suffisent."""

    def handler(request):
        if request.url.params.get("unit") == "RCH_M":
            return httpx2.Response(200, json=json_stat({"2026-01": -0.5, "2026-02": 1.0}))
        return httpx2.Response(404)

    values = eurostat.get_hicp(fake_client(handler))
    assert values == {date(2026, 1, 1): D("99.5"), date(2026, 2, 1): D("100.495")}


def test_repli_sur_l_ancienne_table_si_la_nouvelle_manque():
    def handler(request):
        if request.url.path.endswith("/prc_hicp_minr"):
            return httpx2.Response(404)
        return httpx2.Response(200, json=json_stat({"2025-11": 100.0, "2025-12": 100.5}))

    values = eurostat.get_hicp(fake_client(handler))
    assert max(values) == date(2025, 12, 1)


def test_eurostat_essaie_l_autre_base_puis_echoue():
    tried = "minr I25 TOTAL.*minr RCH_M TOTAL.*midx I25 CP00.*midx I15 CP00"
    with pytest.raises(ProviderError, match=tried):
        eurostat.get_hicp(fake_client(FakeEurostat(status=404)))
    with pytest.raises(ProviderError, match="inattendue"):
        eurostat._parse({"value": {}})


def test_indice_interpole_dans_le_mois():
    assert index_on(MONTHLY, date(2025, 3, 1)) == D("100")
    assert index_on(MONTHLY, date(2025, 3, 17)) == D("100") + D(16) / D(31)  # 16 jours sur 31
    assert index_on(MONTHLY, date(2025, 1, 5)) == D("100")  # avant la série : premier indice
    assert index_on(MONTHLY, date(2025, 9, 1)) == D("102.01")  # après : dernier indice connu
    assert index_on({}, date(2025, 3, 1)) is None


def test_indice_prolonge_au_taux_saisi():
    index = extend_index(MONTHLY, D("2"), date(2025, 3, 10), date(2025, 6, 15))
    # Mai publié, puis juin et juillet à 2 % par an (juillet pour interpoler juin).
    assert sorted(index) == [date(2025, m, 1) for m in range(3, 8)]
    assert index[date(2025, 5, 1)] == D("102.01")
    assert (index[date(2025, 7, 1)] / index[date(2025, 5, 1)]) ** 6 == pytest.approx(D("1.02"))
    # Rien de publié (Eurostat injoignable) : le taux saisi sert dès le premier mois.
    alone = extend_index({}, D("2"), date(2025, 3, 10), date(2026, 3, 1))
    assert alone[date(2025, 3, 1)] == D("100")
    assert alone[date(2026, 3, 1)] == pytest.approx(D("102"))


def test_investi_erode_achats_et_vente():
    points = [
        (date(2025, 3, 1), D("1000")),  # achat
        (date(2025, 4, 1), D("1000")),  # +1 % d'inflation
        (date(2025, 4, 1), D("1500")),  # nouvel achat, même jour
        (date(2025, 5, 1), D("750")),  # vente de la moitié (au coût)
    ]
    after_april = D("1000") * 100 / 101  # 1 000 € n'achètent plus que 990,10 € d'alors
    after_may = (after_april + 500) * D("101") / D("102.01")
    cents = [v.quantize(D("0.01")) for v in (after_april, after_april + 500, after_may / 2)]
    assert cents == [D("990.10"), D("1490.10"), D("737.67")]
    eroded = eroded_invested(points, MONTHLY)
    assert [v.quantize(D("0.01")) for v in eroded] == [D("1000.00"), *cents]
    # Le nouvel achat d'avril n'a encore rien perdu ; la vente de mai retire la moitié.
    assert eroded_invested(points, {}) == [None] * 4


def test_cache_un_appel_par_jour_et_nouvel_essai_apres_panne():
    server = FakeEurostat(status=500)
    t0 = datetime(2025, 6, 2, 8, tzinfo=UTC)
    assert prices.inflation_index(fake_client(server), t0)[0] == {}
    assert len(server.requests) == 4  # les 4 séries essayées
    prices.inflation_index(fake_client(server), t0 + timedelta(minutes=30))
    assert len(server.requests) == 4  # panne récente : pas de nouvel essai
    server.status = 200
    values, error = prices.inflation_index(fake_client(server), t0 + timedelta(hours=2))
    assert error is None and len(values) == 2
    prices.inflation_index(fake_client(server), t0 + timedelta(hours=5))
    assert len(server.requests) == 5  # déjà récupéré aujourd'hui
    # Le lendemain, Eurostat est en panne : on garde la série de la veille.
    server.status = 500
    values, error = prices.inflation_index(fake_client(server), t0 + timedelta(days=1))
    assert len(values) == 2 and "500" in error


def test_api_historique_avec_inflation(db_file):
    server = FakeEurostat()
    with TestClient(app) as client:
        account = client.post(
            "/api/accounts", json={"name": "PEA test", "kind": "pea", "cash": "100"}
        ).json()
        asset = client.post(
            "/api/assets", json={"name": "ETF fictif", "asset_class": "etf", "ticker": "fake.pa"}
        ).json()
        client.post(
            "/api/operations",
            json={
                "account_id": account["id"],
                "asset_id": asset["id"],
                "op_date": "2025-03-01",
                "op_type": "achat",
                "quantity": "10",
                "unit_price": "10.00",
            },
        )
        app.dependency_overrides[http_client] = lambda: fake_client(server)
        # Taux saisi à 0 % : au-delà d'avril, rien de plus n'est perdu.
        data = client.get("/api/history?inflation_rate=0").json()
        assert client.get("/api/history?inflation_rate=50").status_code == 422
    first, last = data["points"][0], data["points"][-1]
    assert (first["invested_eroded"], first["inflation_loss"]) == ("100.00", "0.00")
    # Dernier indice d'avril (101) : 100 € de mars n'achètent plus que 99,01 € d'alors.
    assert (last["invested"], last["invested_eroded"], last["inflation_loss"]) == (
        "100.00",
        "99.01",
        "-0.99",
    )
    # Taux de la courbe au jour (D-112) : +1 % de mars à avril, soit 12,7 % par an ;
    # au-delà d'avril, le taux saisi (0 %).
    assert (first["inflation_pct"], last["inflation_pct"]) == ("12.7", "0.0")
    assert data["inflation"] == {
        "source": "Eurostat, IPCH France",
        "last_month": "2025-04",
        "rate": "0",
        "error": None,
    }
    # Règle 3 : ni quantité ni montant, seulement des noms de série, un ticker et des dates.
    allowed = {"format", "geo", "unit", "coicop", "coicop18"}
    allowed |= {"period1", "period2", "interval", "range"}
    for req in server.requests:
        assert req.content == b"" and set(req.url.params) <= allowed


def test_json_stat_en_liste_et_mois_a_l_ancienne():
    data = {
        "dimension": {"time": {"category": {"index": {"2025M01": 0, "2025M02": 1}}}},
        "value": [D("99.5"), None],
    }
    assert eurostat._parse(data) == {date(2025, 1, 1): D("99.5")}


def test_indice_au_glissement_sur_12_mois():
    """Chaque mois, la courbe monte au rythme de l'inflation sur 12 mois de ce mois : les mois
    de soldes ne la font pas remonter, et sa pente change d'un mois à l'autre (D-145)."""
    from app.analytics import smooth_index, yearly_inflation

    raw = {date(2024, month, 1): D("100") + D(month) / 10 for month in range(1, 13)}
    for month, change in enumerate(["-0.4", "0.7", "1.1", "0.2", "0.1", "-0.3"], start=1):
        raw[date(2025, month, 1)] = raw[date(2024, month, 1)] * (1 + D(change) / 100 + D("0.02"))
    smooth = smooth_index(raw)
    # Les 12 premiers mois, sans glissement : indice exact.
    for month in range(1, 13):
        assert smooth[date(2024, month, 1)] == raw[date(2024, month, 1)]
    # Ensuite, du 1er de M au 1er du mois suivant : glissement de M ramené au mois.
    for month in range(1, 7):
        start, nxt = date(2025, month, 1), date(2025, month + 1, 1)
        year = raw[start] / raw[date(2024, month, 1)]
        assert smooth[nxt] / smooth[start] == pytest.approx(year ** (D(1) / 12))
        assert yearly_inflation(smooth, date(2025, month, 15), D("2")) == pytest.approx(
            (year - 1) * 100
        )
    # Jamais de baisse tant que les prix montent sur un an, même en janvier (soldes).
    months = sorted(smooth)
    assert all(smooth[b] >= smooth[a] for a, b in zip(months, months[1:], strict=False))
    # Les taux changent d'un mois à l'autre.
    assert len({yearly_inflation(smooth, date(2025, m, 1), D("2")) for m in range(1, 7)}) > 1
    # Jusqu'au mois qui suit le dernier publié, puis le taux saisi prend le relais.
    assert max(smooth) == date(2025, 7, 1)
    # Série trop courte : rendue telle quelle.
    assert smooth_index({date(2025, 1, 1): D("100")}) == {date(2025, 1, 1): D("100")}


def test_argent_place_erode_retrait_au_montant_du_jour():
    """Avec `flows`, un retrait ôte des euros du jour, sans réduire au prorata (D-110)."""
    points = [(date(2025, 3, 1), D("1000")), (date(2025, 4, 1), D("400"))]
    eroded = eroded_invested(points, MONTHLY, flows=True)
    assert eroded[1].quantize(D("0.01")) == (D("1000") * 100 / 101 - 600).quantize(D("0.01"))
    # Un retrait plus grand que l'apport érodé : la série passe sous zéro.
    assert (
        eroded_invested(
            [(date(2025, 3, 1), D("1000")), (date(2025, 4, 1), D("-5"))], MONTHLY, flows=True
        )[1]
        < 0
    )


def test_inflation_au_jour_pente_de_la_courbe():
    """Pente annuelle de l'indice de la courbe au jour survolé, sinon taux saisi (D-112)."""
    from app.analytics import yearly_inflation

    index = {date(2025, 9, 1): D("100"), date(2025, 10, 1): D("100.1")}
    assert yearly_inflation(index, date(2025, 9, 17), D("2")) == pytest.approx(
        (D("1.001") ** 12 - 1) * 100
    )
    assert yearly_inflation(index, date(2025, 10, 1), D("2")) == D("2")
