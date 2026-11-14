"""Import Binance, export « Historique des transactions » (D-082). Données FICTIVES."""

import json
from datetime import date
from decimal import Decimal as D

import httpx2
import pytest
from fastapi.testclient import TestClient

from app.importers import binance, csv_generique
from app.main import app, http_client
from app.models import Operation
from app.tax import crypto_sales
from tests.conftest import fake_client

ENTETE = "User_ID,UTC_Time,Account,Operation,Coin,Change,Remark"
UID = "999000111"


def csv(*lignes: str) -> str:
    return "\n".join([ENTETE, *(f"{UID},{ligne}" for ligne in lignes)])


FICHIER = csv(
    # Versement en euros, puis achat d'USDT en euros (frais en BNB).
    "2025-01-02 09:00:00,Spot,Deposit,EUR,500,",
    "2025-01-02 09:10:00,Spot,Transaction Spend,EUR,-250,",
    "2025-01-02 09:10:00,Spot,Transaction Buy,USDT,250,",
    "2025-01-02 09:10:00,Spot,Transaction Fee,BNB,-0.0005,",
    # BNB acheté en USDT (frais en BNB, pris sur l'achat).
    "2025-01-03 10:00:00,Spot,Buy,BNB,0.2,",
    "2025-01-03 10:00:00,Spot,Sell,USDT,-100,",
    "2025-01-03 10:00:00,Spot,Fee,BNB,-0.0002,",
    # BTC et ETH achetés à la même seconde contre USDT.
    "2025-01-04 11:00:00,Spot,Transaction Spend,USDT,-50,",
    "2025-01-04 11:00:00,Spot,Transaction Spend,USDT,-25,",
    "2025-01-04 11:00:00,Spot,Transaction Buy,BTC,0.0005,",
    "2025-01-04 11:00:00,Spot,Transaction Fee,BNB,-0.0001,",
    "2025-01-04 11:00:00,Spot,Transaction Buy,ETH,0.01,",
    "2025-01-04 11:00:00,Spot,Transaction Fee,BNB,-0.0001,",
    # Intérêts Earn de février (mois terminé), avec leurs souscriptions internes.
    "2025-02-10 01:00:00,Spot,Simple Earn Flexible Interest,BNB,0.001,Binance Earn",
    "2025-02-10 01:00:01,Spot,Simple Earn Flexible Subscription,BNB,-0.001,Binance Earn",
    "2025-02-11 01:00:00,Spot,Simple Earn Flexible Interest,BNB,0.001,Binance Earn",
    "2025-02-11 01:00:01,Spot,Simple Earn Flexible Subscription,BNB,-0.001,Binance Earn",
    # Airdrop d'un jeton fictif, converti en BNB (poussière).
    "2025-02-15 08:00:00,Spot,HODLer Airdrops Distribution,FICT,40,Binance Launchpool",
    "2025-02-20 08:00:00,Spot,Small Assets Exchange BNB,FICT,-40,FICT to BNB",
    "2025-02-20 08:00:00,Spot,Small Assets Exchange BNB,BNB,0.004,FICT to BNB",
    # BTC envoyé vers un wallet (transfert sortant) puis revenu (titres déjà détenus).
    "2025-02-22 12:00:00,Spot,Withdraw,BTC,-0.0002,Withdraw fee is included",
    "2025-02-25 12:00:00,Spot,Deposit,BTC,0.0002,",
    # Convert : deux jambes à une seconde d'écart ; BTC vendu contre des euros (imposable).
    "2025-03-01 14:00:00,Spot,Binance Convert,USDT,-20,",
    "2025-03-01 14:00:01,Spot,Binance Convert,USDC,19.9,",
    "2025-03-02 15:00:00,Spot,Transaction Sold,BTC,-0.0001,",
    "2025-03-02 15:00:00,Spot,Transaction Revenue,EUR,9,",
    "2025-03-02 15:00:00,Spot,Transaction Fee,EUR,-0.01,",
    # Intérêts du dernier mois du fichier : attendent l'export suivant.
    "2025-03-05 01:00:00,Spot,Simple Earn Flexible Interest,USDC,0.01,Binance Earn",
    "2025-03-06 09:00:00,Spot,Mystery Operation,BNB,1,",
)

USD = [(date(2025, 1, 1), D("1.25"))]  # 1 EUR = 1,25 $ (fictif)
CLOSES = {
    "BNB": [(date(2025, 1, 1), D("500")), (date(2025, 2, 28), D("600"))],
    "BTC": [(date(2025, 1, 4), D("80000"))],
    "ETH": [(date(2025, 1, 4), D("2000"))],
}


def lire(text: str = FICHIER, closes=CLOSES):
    rows = csv_generique.read_rows(text)
    return binance.parse(rows[0], rows[1:], 2, binance.Pricer(USD, closes))


def ops(lines):
    return [x for x in lines if x.op_type]


def test_reconnu_en_anglais_et_en_francais():
    assert binance.detect(ENTETE.split(","))
    fr = "Identifiant utilisateur,Durée,Compte,Opération,Jeton,Change,Remarque".split(",")
    assert binance.detect(fr)
    assert not binance.detect("date,type,symbol,shares".split(","))


def test_achat_en_euros_puis_echanges():
    lines = lire()
    by = {(x.line, x.op_type): x for x in ops(lines)}
    assert by[(2, "versement")].amount == D("500")
    # Achat d'USDT en euros : prix exact, frais BNB au cours du BNB de la veille.
    usdt = by[(4, "achat")]
    assert usdt.asset == "tether" and usdt.unit_price * usdt.quantity + usdt.fees == D("250.25")
    fee = by[(5, "vente")]
    assert fee.asset == "binancecoin" and fee.crypto_swap and fee.quantity == D("0.0005")
    assert fee.unit_price * fee.quantity == D("0.25")
    # BNB acheté en USDT : vente d'USDT marquée échange, frais pris sur le BNB reçu.
    sale, buy = by[(7, "vente")], by[(6, "achat")]
    assert sale.crypto_swap and sale.quantity == D("100") and sale.unit_price == D("0.8")
    assert buy.quantity == D("0.1998")
    assert buy.quantity * buy.unit_price + buy.fees == D("80")
    # Deux achats à la même seconde : chaque dépense va à l'achat au prix le plus proche.
    assert by[(11, "achat")].asset == "bitcoin" and by[(11, "achat")].unit_price == D("80000")
    assert by[(13, "achat")].asset == "ethereum" and by[(13, "achat")].unit_price == D("2000")


def test_gains_poussiere_envois_et_cessions():
    lines = lire()
    by_line = {x.line: x for x in lines}
    # Intérêts de février : un achat offert (sur la dernière ligne), souscriptions ignorées.
    earn = by_line[17]
    assert (
        earn.op_type == "achat" and earn.quantity == D("0.002") and earn.key == "gains|BNB|2025-02"
    )
    assert earn.offered == earn.quantity * earn.unit_price
    assert by_line[15].skip and "interne" in by_line[16].skip
    # Jeton gratuit converti : pas d'actif à créer, le BNB reçu est offert.
    assert "gratuitement" in by_line[19].skip and "gratuitement" in by_line[20].skip
    dust = by_line[21]
    assert dust.op_type == "achat" and dust.asset == "binancecoin" and dust.offered
    # Envoi : transfert sortant (ni vente ni espèces) ; retour : titres déjà détenus.
    (sent,) = [x for x in ops(lines) if x.line == 22]
    assert sent.op_type == "transfert_sortant" and sent.quantity == D("0.0002")
    back = next(x for x in ops(lines) if x.line == 23)
    assert back.op_type == "solde_initial" and back.quantity == D("0.0002")
    # Convert USDT -> USDC : échange non imposable.
    convert = [x for x in ops(lines) if x.line in (24, 25)]
    assert {(x.op_type, x.asset, x.crypto_swap) for x in convert} == {
        ("vente", "tether", True),
        ("achat", "usd-coin", False),
    }
    # BTC vendu contre des euros : cession imposable, frais en euros.
    sale = next(x for x in ops(lines) if x.line == 26)
    assert sale.op_type == "vente" and not sale.crypto_swap and sale.fees == D("0.01")
    assert sale.quantity * sale.unit_price - sale.fees == D("9") - D("0.01")
    assert "prochain export" in by_line[29].skip
    assert "non reconnue" in by_line[30].error


def test_manque_compte_offert_et_identifiant_jamais_lu():
    text = csv(
        "2025-01-03 10:00:00,Spot,Buy,BNB,0.1,",
        "2025-01-03 10:00:00,Spot,Sell,USDT,-50,",
        "2025-01-02 10:00:00,Spot,Deposit,USDT,50,",
        "2025-02-01 10:00:00,Spot,Transaction Sold,BNB,-0.15,",
        "2025-02-01 10:00:00,Spot,Transaction Revenue,USDT,60,",
    )
    lines = lire(text)
    extra = [x for x in lines if x.key and x.key.startswith("manque|")]
    assert len(extra) == 1 and extra[0].quantity == D("0.05") and extra[0].offered
    assert UID not in repr(lines)


def _net(lines, symbol: str) -> D:
    """Quantité détenue d'un jeton à la fin, lignes rejouées dans l'ordre de l'application."""
    total = D(0)
    found = False
    for x in sorted(ops(lines), key=lambda x: (x.op_date, x.seq or 0, x.line)):
        if x.asset == binance.coin_id(symbol):
            found = True
            total += -x.quantity if x.op_type in ("vente", "transfert_sortant") else x.quantity
    assert found, f"aucune opération sur {symbol}"  # sinon le test ne vérifierait rien
    return total


def test_gains_du_mois_comptes_avant_le_retrait_du_meme_jour():
    """Retrait qui vide l'Earn le jour du dernier gain du mois : les gains ne sont pas comptés
    une deuxième fois dans le « reçu absent de l'export »."""
    text = csv(
        "2025-01-02 10:00:00,Spot,Deposit,USDC,100,",
        "2025-01-10 01:00:00,Spot,Simple Earn Flexible Interest,USDC,1,",
        "2025-01-10 01:00:01,Spot,Simple Earn Flexible Subscription,USDC,-1,",
        "2025-01-20 01:00:00,Spot,Simple Earn Flexible Interest,USDC,1,",
        "2025-01-20 01:00:01,Spot,Simple Earn Flexible Subscription,USDC,-1,",
        "2025-01-20 21:00:00,Spot,Withdraw,USDC,-102,",
        "2025-03-01 10:00:00,Spot,Deposit,EUR,1,",
    )
    lines = lire(text)
    assert not [x for x in lines if x.key and x.key.startswith("manque|")]
    assert _net(lines, "USDC") == 0


def test_gains_du_mois_avances_avant_un_retrait_en_milieu_de_mois():
    text = csv(
        "2025-01-02 10:00:00,Spot,Deposit,USDC,100,",
        "2025-01-10 01:00:00,Spot,Simple Earn Flexible Interest,USDC,1,",
        "2025-01-10 01:00:01,Spot,Simple Earn Flexible Subscription,USDC,-1,",
        "2025-01-12 21:00:00,Spot,Withdraw,USDC,-101,",
        "2025-01-20 01:00:00,Spot,Simple Earn Flexible Interest,USDC,2,",
        "2025-01-20 01:00:01,Spot,Simple Earn Flexible Subscription,USDC,-2,",
        "2025-01-25 21:00:00,Spot,Withdraw,USDC,-2,",
        "2025-03-01 10:00:00,Spot,Deposit,EUR,1,",
    )
    lines = lire(text)
    assert _net(lines, "USDC") == 0


def test_fiscalite_crypto_offert_sans_prix_d_acquisition():
    def op(i, op_type, qty, price, offered=None, swap=False):
        return Operation(
            i,
            1,
            1,
            date(2025, 1, i),
            op_type,
            D(qty),
            D(price),
            None,
            D("0"),
            "EUR",
            None,
            crypto_swap=swap,
            offered=None if offered is None else D(offered),
        )

    ops_ = [op(1, "achat", "1", "100"), op(2, "achat", "1", "100", offered="100")]
    _, remaining = crypto_sales(ops_, {}, lambda a, d: None, {})
    assert remaining == D("100")  # seule la crypto payée compte


def handler(request: httpx2.Request) -> httpx2.Response:
    """Faux Frankfurter (dollar) et faux CoinGecko (BNB, BTC, ETH), données fictives."""
    if "frankfurter" in request.url.host:
        rates = {"2025-01-02": {"USD": 1.25}, "2025-03-03": {"USD": 1.25}}
        return httpx2.Response(200, text=json.dumps({"rates": rates}))
    if "coingecko" in request.url.host:
        coin = request.url.path.split("/")[-2]
        price = {"binancecoin": 500, "bitcoin": 80000, "ethereum": 2000}.get(coin, 1)
        stamp = 1735689600000  # 1er janvier 2025
        points = [[stamp + i * 86400000, price] for i in range(120)]
        return httpx2.Response(200, text=json.dumps({"prices": points}))
    return httpx2.Response(404)


@pytest.fixture
def client(db_file):
    app.dependency_overrides[http_client] = lambda: fake_client(handler)
    with TestClient(app) as c:
        yield c


def test_import_complet(client):
    body = {"text": FICHIER}
    preview = client.post("/api/import/preview", json=body).json()
    assert preview["profile"]["id"] == "binance"
    assert [p["suggested_name"] for p in preview["parts"]] == ["Binance"]
    assert UID not in json.dumps(preview)
    unknown = {
        r["asset"]: r["asset_name"] for r in preview["rows"] if r["status"] == "actif_inconnu"
    }
    assert unknown == {
        "tether": "USDT",
        "binancecoin": "BNB",
        "bitcoin": "BTC",
        "ethereum": "ETH",
        "usd-coin": "USDC",
    }
    # Un autre compte crypto, même choisi dans le formulaire, n'est pas pris d'office (D-083).
    other = {"name": "Wallet fictif", "kind": "crypto", "institution": "Autre plateforme"}
    other_id = client.post("/api/accounts", json=other).json()["id"]
    parts = client.post("/api/import/preview", json={**body, "account_id": other_id}).json()
    assert parts["parts"][0]["account_id"] is None
    account = {"name": "Binance", "kind": "crypto", "institution": "Binance"}
    created = client.post("/api/accounts", json=account)
    assert created.status_code == 201
    parts = client.post("/api/import/preview", json={**body, "account_id": other_id}).json()
    assert parts["parts"][0]["account_id"] == created.json()["id"]
    for coin, name in unknown.items():
        asset = {"name": name, "asset_class": "crypto", "coingecko_id": coin, "currency": "EUR"}
        assert client.post("/api/assets", json=asset).status_code == 201
    done = client.post("/api/import", json=body)
    assert done.status_code == 201, done.text
    assert done.json()["counts"]["erreur"] == 1  # l'opération inconnue
    again = client.post("/api/import", json=body).json()
    assert again["imported"] == 0 and again["counts"]["deja_importee"] == done.json()["imported"]
    stored = client.get("/api/operations").json()
    swaps = [o for o in stored if o.get("crypto_swap")]
    assert swaps and all(o["op_type"] == "vente" for o in swaps)


def test_ordre_du_fichier_inverse(client):
    """Un export du plus récent au plus ancien s'importe dans l'ordre des dates."""
    rows = FICHIER.split("\n")
    text = "\n".join([rows[0], *reversed(rows[1:])])
    client.post(
        "/api/accounts", json={"name": "Binance", "kind": "crypto", "institution": "Binance"}
    )
    for coin, name in [
        ("tether", "USDT"),
        ("binancecoin", "BNB"),
        ("bitcoin", "BTC"),
        ("ethereum", "ETH"),
        ("usd-coin", "USDC"),
    ]:
        client.post(
            "/api/assets",
            json={"name": name, "asset_class": "crypto", "coingecko_id": coin, "currency": "EUR"},
        )
    assert client.post("/api/import", json={"text": text}).status_code == 201


def test_cours_coingecko_redemande_apres_refus(monkeypatch):
    from app import prices

    calls = []

    def busy(request):
        calls.append(1)
        if len(calls) == 1:
            return httpx2.Response(429)
        return httpx2.Response(200, text=json.dumps({"prices": [[1735689600000, 3]]}))

    waits = []
    series = prices.coin_history(fake_client(busy), "bitcoin", date(2025, 1, 2), sleep=waits.append)
    assert series == [(date(2025, 1, 1), D("3"))] and waits == [2]
    assert prices.coin_history(fake_client(), "bitcoin", date(2025, 1, 2)) == series  # en cache


def test_transfert_sortant_ni_vente_ni_especes():
    """Type « transfert_sortant » (D-082) : la quantité baisse, le prix de revient restant aussi,
    sans espèces ni cession ; la crypto emporte sa part du prix d'acquisition."""
    from app.portfolio import cash_movement, compute_positions, daily_flows
    from app.tax import realized_sales

    def op(i, op_type, qty, price):
        return Operation(
            i, 1, 1, date(2025, 1, i), op_type, D(qty), D(price), None, D("0"), "EUR", None
        )

    history = [op(1, "achat", "4", "100"), op(2, "transfert_sortant", "1", "150")]
    (pos,) = compute_positions(history)
    assert pos.quantity == D("3") and pos.cost == D("300")
    assert cash_movement(history[1]) == 0
    assert daily_flows(history, {"EUR": D("1")})[date(2025, 1, 2)] == D("-150")
    (sale,) = realized_sales([*history, op(3, "vente", "1", "200")])
    assert sale.op_date == date(2025, 1, 3) and round(sale.cost, 6) == 100  # aucune cession le 2
    _, remaining = crypto_sales(history, {}, lambda a, d: None, {})
    assert remaining == D("300")  # 400 payés, un quart de la valeur est parti


def test_cours_coingecko_en_panne_pas_redemande_tout_de_suite():
    from app import prices

    calls = []

    def down(request):
        calls.append(1)
        return httpx2.Response(500)

    assert prices.coin_history(fake_client(down), "dogecoin", date(2025, 1, 2)) == []
    assert prices.coin_history(fake_client(down), "dogecoin", date(2025, 1, 2)) == []
    assert len(calls) == 1  # le 2e aperçu n'attend pas une nouvelle panne


def test_crypto_revendue_creee_d_office_et_cotee_en_dollars(client):
    """D-085 : une crypto achetée puis revendue dans le relevé est créée sans demander (en $) ;
    une crypto cotée en $ reçoit ses montants convertis au taux BCE du jour ; un retrait part
    vers le compte choisi dans l'aperçu."""
    text = csv(
        "2025-01-02 09:00:00,Spot,Deposit,EUR,500,",
        "2025-01-02 09:10:00,Spot,Transaction Spend,EUR,-100,",
        "2025-01-02 09:10:00,Spot,Transaction Buy,SOL,1,",
        "2025-01-03 09:10:00,Spot,Transaction Sold,SOL,-1,",
        "2025-01-03 09:10:00,Spot,Transaction Revenue,EUR,120,",
        "2025-01-03 10:00:00,Spot,Transaction Spend,EUR,-100,",
        "2025-01-03 10:00:00,Spot,Transaction Buy,BTC,0.001,",
        "2025-01-04 10:00:00,Spot,Withdraw,BTC,-0.0004,",
    )
    account = {"name": "Binance", "kind": "crypto", "institution": "Binance"}
    binance_id = client.post("/api/accounts", json=account).json()["id"]
    wallet = client.post("/api/accounts", json={"name": "Wallet fictif", "kind": "crypto"})
    wallet = wallet.json()["id"]
    btc = {"name": "BTC", "asset_class": "crypto", "coingecko_id": "bitcoin", "currency": "USD"}
    client.post("/api/assets", json=btc)
    preview = client.post("/api/import/preview", json={"text": text}).json()
    assert preview["auto_assets"] == ["SOL"]
    assert not any(r["status"] == "actif_inconnu" for r in preview["rows"])
    assert [a["name"] for a in client.get("/api/assets").json()] == ["BTC"]  # rien créé encore
    withdraw = next(r for r in preview["rows"] if r["op_type"] == "transfert_sortant")
    body = {"text": text, "destinations": {str(withdraw["line"]): wallet}}
    done = client.post("/api/import", json=body)
    assert done.status_code == 201, done.text
    assert done.json()["auto_assets"] == ["SOL"]
    assets = {a["name"]: a for a in client.get("/api/assets").json()}
    assert assets["SOL"]["currency"] == "USD" and assets["SOL"]["sold_out"]
    assert not assets["BTC"]["sold_out"]
    ops = client.get("/api/operations").json()
    buy = next(o for o in ops if o["op_type"] == "achat" and o["asset_id"] == assets["BTC"]["id"])
    assert (buy["currency"], buy["unit_price"]) == ("USD", "125000")  # 100 000 € × 1,25
    out = next(o for o in ops if o["op_type"] == "transfert_sortant")
    assert (out["account_id"], out["to_account_id"]) == (binance_id, wallet)
    held = {(p["account"], p["quantity"]) for p in client.get("/api/portfolio").json()["positions"]}
    assert held == {("Binance", "0.0006"), ("Wallet fictif", "0.0004")}
    # Espèces en euros : l'achat de BTC coté en $ les débite au même taux (500 - 100 + 120 - 100).
    cash = {a["id"]: D(a["cash"]) for a in client.get("/api/accounts").json()}
    assert cash[binance_id] == D("420")


def test_destination_texte_et_choix_avant_la_creation_du_compte(client):
    """D-086 : sans compte Binance (première utilisation), un retrait garde le compte ou le texte
    choisi dans l'aperçu ; le texte est enregistré tel quel à l'import."""
    text = csv(
        "2025-01-02 09:00:00,Spot,Deposit,EUR,500,",
        "2025-01-02 09:10:00,Spot,Transaction Spend,EUR,-100,",
        "2025-01-02 09:10:00,Spot,Transaction Buy,BTC,0.001,",
        "2025-01-04 10:00:00,Spot,Withdraw,BTC,-0.0004,",
    )
    wallet = client.post("/api/accounts", json={"name": "Wallet fictif", "kind": "crypto"})
    wallet = wallet.json()["id"]
    first = client.post("/api/import/preview", json={"text": text}).json()
    withdraw = next(r for r in first["rows"] if r["op_type"] == "transfert_sortant")
    assert withdraw["status"] in ("sans_compte", "actif_inconnu")
    line = str(withdraw["line"])
    chosen = client.post(
        "/api/import/preview", json={"text": text, "destinations": {line: wallet}}
    ).json()
    row = next(r for r in chosen["rows"] if r["op_type"] == "transfert_sortant")
    assert row["to_account_id"] == wallet and row["destination"] is None
    typed = client.post(
        "/api/import/preview", json={"text": text, "destination_texts": {line: "Ledger"}}
    ).json()
    row = next(r for r in typed["rows"] if r["op_type"] == "transfert_sortant")
    assert row["to_account_id"] is None and row["destination"] == "Ledger"


def test_retrait_reparti_entre_plusieurs_comptes(client):
    """D-087 : un retrait réparti donne une opération par compte d'arrivée plus le reste hors de
    l'app ; une répartition trop grande est refusée sans rien enregistrer."""
    text = csv(
        "2025-01-02 09:00:00,Spot,Deposit,EUR,500,",
        "2025-01-02 09:10:00,Spot,Transaction Spend,EUR,-100,",
        "2025-01-02 09:10:00,Spot,Transaction Buy,BTC,0.001,",
        "2025-01-04 10:00:00,Spot,Withdraw,BTC,-0.0004,",
    )
    binance_id = client.post(
        "/api/accounts", json={"name": "Binance", "kind": "crypto", "institution": "Binance"}
    ).json()["id"]
    one = client.post("/api/accounts", json={"name": "Ledger fictif", "kind": "crypto"}).json()[
        "id"
    ]
    two = client.post("/api/accounts", json={"name": "Autre fictif", "kind": "crypto"}).json()["id"]
    btc = {"name": "BTC", "asset_class": "crypto", "coingecko_id": "bitcoin", "currency": "USD"}
    client.post("/api/assets", json=btc)
    preview = client.post("/api/import/preview", json={"text": text}).json()
    line = str(next(r for r in preview["rows"] if r["op_type"] == "transfert_sortant")["line"])
    split = {
        "splits": {
            line: [{"account_id": one, "value": "0.0001"}, {"account_id": two, "value": "0.0002"}]
        }
    }
    body = {"text": text, "destination_texts": {line: "Banque fictive"}, **split}
    flow = client.post("/api/import/preview", json=body).json()["rows"]
    flow = next(r for r in flow if r["op_type"] == "transfert_sortant")["outflow"]
    assert (flow["total"], flow["rest"], flow["over"], flow["text"]) == (
        "0.0004",
        "0.0001",
        False,
        "Banque fictive",
    )
    too_big = {"text": text, "splits": {line: [{"account_id": one, "value": "0.0005"}]}}
    refused = client.post("/api/import", json=too_big)
    assert refused.status_code == 422 and not client.get("/api/operations").json()
    done = client.post("/api/import", json=body)
    assert done.status_code == 201, done.text
    outs = [o for o in client.get("/api/operations").json() if o["op_type"] == "transfert_sortant"]
    got = {(o["to_account_id"], o["destination"], D(o["quantity"])) for o in outs}
    assert got == {
        (one, None, D("0.0001")),
        (two, None, D("0.0002")),
        (None, "Banque fictive", D("0.0001")),
    }
    assert {o["account_id"] for o in outs} == {binance_id}
    again = client.post("/api/import/preview", json=body).json()
    assert not [r for r in again["rows"] if r["status"] == "ok"]  # déjà importé : doublons


def test_apercu_en_flux_donne_la_progression_puis_le_resultat(client):
    """D-089 : une ligne JSON par événement, progression croissante, résultat identique à
    l'aperçu ordinaire ; une erreur arrive aussi en ligne, sans trace."""
    text = csv(
        "2025-01-02 09:00:00,Spot,Deposit,EUR,500,",
        "2025-01-04 10:00:00,Spot,Withdraw,BTC,-0.0004,",
    )
    stream = client.post("/api/import/preview/stream", json={"text": text})
    assert stream.status_code == 200
    assert stream.headers["content-type"].startswith("application/x-ndjson")
    events = [json.loads(line) for line in stream.text.splitlines()]
    steps = [e["progress"] for e in events if "progress" in e]
    assert steps == sorted(steps) and steps[0] <= 5 and steps[-1] >= 90
    assert events[-1]["result"] == client.post("/api/import/preview", json={"text": text}).json()
    bad = client.post("/api/import/preview/stream", json={"text": "a;b\n", "profile": "binance"})
    assert [list(json.loads(line)) for line in bad.text.splitlines()][-1] == ["error"]


def test_apercu_en_flux_identique_avec_des_plans_et_des_decimaux(client):
    """D-089 : un relevé à plans d'investissement (montants Decimal) passe aussi par le flux."""
    from pathlib import Path

    text = (Path(__file__).parent / "fixtures" / "trade_republic_fictif.csv").read_text(
        encoding="utf-8"
    )
    stream = client.post("/api/import/preview/stream", json={"text": text})
    events = [json.loads(line) for line in stream.text.splitlines()]
    assert "result" in events[-1], events[-1]
    assert events[-1]["result"] == client.post("/api/import/preview", json={"text": text}).json()
