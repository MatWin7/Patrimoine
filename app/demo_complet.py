"""Démo complète (D-140) : un patrimoine FICTIF qui passe par chaque partie de l'app.

Profil Démo et `data/demo.db`. Sept ans d'historique (assurance-vie ouverte en 2017, PEA en
2019) pour que courbes, performance et repères fiscaux aient du sens. Tout est inventé : noms,
montants, cours (tickers DEMO~*, jamais demandés en ligne : générés ici, identiques d'un
lancement à l'autre), taux de change. Seules les parties en ligne par nature gardent de vrais
identifiants publics : indices de la comparaison et listes de suivi (règle 3 : rien de
personnel ne sort).

Les cours suivent un indice de marché fictif (`MARKET`, avec ses creux de 2018, 2020, 2022 et
2025), propre à chaque ligne par son bêta (sensibilité au marché), sa dérive annuelle et un bruit
quotidien reproductible. Calcul en Decimal, aucun float (règle 7).
"""

from dataclasses import dataclass
from datetime import UTC, date, datetime, timedelta
from decimal import ROUND_DOWN, Decimal

from app import defi_sync, prices, repository, valuations
from app.models import Account, Asset
from app.portfolio import compute_positions
from app.providers import Price

D = Decimal
ZERO = D("0")
NOTE = "fictif"
FIRST_DAY = date(2016, 12, 1)  # début des cours et des taux générés

# Indice de marché FICTIF (jour, niveau), interpolé jour après jour entre deux repères.
MARKET = [
    (date(2016, 12, 1), D("100")),
    (date(2018, 1, 26), D("112")),
    (date(2018, 12, 24), D("97")),
    (date(2019, 12, 31), D("121")),
    (date(2020, 2, 19), D("127")),
    (date(2020, 3, 23), D("84")),
    (date(2020, 12, 31), D("126")),
    (date(2021, 12, 31), D("157")),
    (date(2022, 10, 12), D("125")),
    (date(2022, 12, 31), D("134")),
    (date(2023, 12, 31), D("157")),
    (date(2024, 12, 31), D("186")),
    (date(2025, 2, 19), D("195")),
    (date(2025, 4, 8), D("166")),
    (date(2025, 12, 31), D("203")),
    (date(2026, 10, 31), D("214")),
]
# 1 EUR = x USD, FICTIF (même forme que la vraie courbe, chiffres arrondis).
USD = [
    (date(2016, 12, 1), D("1.06")),
    (date(2018, 2, 15), D("1.24")),
    (date(2019, 12, 31), D("1.11")),
    (date(2020, 3, 20), D("1.07")),
    (date(2021, 1, 6), D("1.23")),
    (date(2022, 9, 27), D("0.96")),
    (date(2023, 7, 18), D("1.12")),
    (date(2024, 10, 1), D("1.11")),
    (date(2025, 1, 13), D("1.02")),
    (date(2025, 9, 16), D("1.18")),
    (date(2026, 10, 31), D("1.16")),
]
GROWTH_AFTER = D("1.05")  # au-delà du dernier repère : +5 % par an, sans à-coup


def _business(day: date) -> date:
    """Jour ouvré le plus proche, au plus tard aujourd'hui (aucune opération dans le futur)."""
    while day.weekday() >= 5:
        day += timedelta(days=1)
    while day > date.today() or day.weekday() >= 5:
        day -= timedelta(days=1)
    return day


def _weekdays(start: date, end: date):
    day = start
    while day <= end:
        if day.weekday() < 5:
            yield day
        day += timedelta(days=1)


def _curve(anchors: list[tuple[date, Decimal]], days) -> dict[date, Decimal]:
    """Niveau de chaque jour, interpolé géométriquement entre deux repères (hausse régulière en
    pourcentage), puis +5 % par an après le dernier."""
    out = {}
    for day in days:
        if day >= anchors[-1][0]:
            last_day, last = anchors[-1]
            out[day] = last * GROWTH_AFTER ** (D((day - last_day).days) / 365)
            continue
        for (d0, v0), (d1, v1) in zip(anchors, anchors[1:], strict=False):
            if d0 <= day < d1:
                out[day] = v0 * (v1 / v0) ** (D((day - d0).days) / D((d1 - d0).days))
                break
    return out


def _noise(n: int, seed: int) -> Decimal:
    """Bruit quotidien reproductible, de -1 % à +1 %."""
    return D((n * 7919 + seed * 104729) % 2001 - 1000) / 100000


@dataclass
class Line:
    asset: Asset
    closes: dict[date, Decimal]


class Builder:
    """Écrit cours et opérations ; positions vérifiées une seule fois à la fin (`finish`)."""

    def __init__(self, conn):
        self.conn = conn
        self.today = date.today()
        self.days = list(_weekdays(FIRST_DAY, self.today))
        self.market = _curve(MARKET, self.days)
        self.usd = {d: v.quantize(D("0.0001")) for d, v in _curve(USD, self.days).items()}
        conn.executemany(
            "INSERT OR REPLACE INTO fx_rate (rate_date, currency, rate) VALUES (?, 'USD', ?)",
            [(d.isoformat(), str(v)) for d, v in self.usd.items()],
        )
        # Taux du jour même un week-end : l'app ne cherche rien en ligne pour le dollar.
        conn.execute(
            "INSERT OR REPLACE INTO fx_rate (rate_date, currency, rate) VALUES (?, 'USD', ?)",
            (self.today.isoformat(), str(self.usd[self.days[-1]])),
        )
        self.lines: dict[str, Line] = {}

    # --- Actifs -----------------------------------------------------------------------------

    def quoted(
        self,
        name: str,
        cls: str,
        ticker: str,
        currency: str,
        start: date,
        price: str,
        beta: str = "1",
        drift: str = "0",
        seed: int = 1,
        *,
        coingecko_id: str | None = None,
        wobble: str = "1",
    ) -> Asset:
        """Actif coté FICTIF et ses clôtures depuis `start` : prix de départ × marché^bêta ×
        (1 + dérive)^années × (1 + bruit × `wobble`) ; `wobble` faible pour un stablecoin."""
        conn = self.conn
        asset = repository.create_asset(conn, name, cls, None, ticker, coingecko_id, currency)
        p0, b, g = D(price), D(beta), D("1") + D(drift)
        m0 = self.market[_business(start)]
        step = D("0.01") if p0 >= 10 else D("0.0001")
        closes = {}
        for n, day in enumerate(d for d in self.days if d >= start):
            level = (self.market[day] / m0) ** b * g ** (D((day - start).days) / 365)
            closes[day] = (p0 * level * (1 + _noise(n, seed) * D(wobble))).quantize(step)
        conn.executemany(
            "INSERT OR REPLACE INTO price_history (asset_id, price_date, close, currency, source)"
            " VALUES (?, ?, ?, ?, 'demo')",
            [(asset.id, d.isoformat(), str(c), currency) for d, c in closes.items()],
        )
        last = closes[max(closes)]
        prices.save_price(conn, asset.id, Price(last, currency, "demo", datetime.now(UTC)))
        self.lines[name] = Line(asset, closes)
        return asset

    def entered(self, name: str, cls: str) -> Asset:
        """Actif sans cours public : valeur saisie à la main (D-056)."""
        asset = repository.create_asset(self.conn, name, cls, None, None, None, "EUR")
        self.lines[name] = Line(asset, {})
        return asset

    def close(self, name: str, day: date) -> Decimal:
        closes = self.lines[name].closes
        return closes[_business(day)]

    # --- Opérations -------------------------------------------------------------------------

    def op(self, account: Account, asset: Asset | None, day: date, kind: str, **kw):
        values = {"quantity": None, "unit_price": None, "amount": None, "fees": ZERO}
        values.update({k: kw.pop(k) for k in list(values) if k in kw})
        return repository.create_operation(
            self.conn, account.id, asset.id if asset else None, _business(day), kind,
            values["quantity"], values["unit_price"], values["amount"], values["fees"],
            kw.pop("currency", asset.currency if asset else account.currency),
            kw.pop("note", NOTE), check=False, **kw,
        )  # fmt: skip

    def cash(self, account: Account, day: date, amount: str, kind: str = "versement", **kw):
        return self.op(account, None, day, kind, amount=D(amount), **kw)

    def buy(self, account, name, day, eur: str | None = None, qty: str | None = None,
            fees: str = "1", decimals: int = 0, **kw):  # fmt: skip
        """Achat au cours de clôture du jour ; `eur` : montant visé en euros (converti au taux
        fictif du jour pour une ligne en dollars), quantité arrondie vers le bas."""
        line, day = self.lines[name], _business(day)
        price = self.close(name, day)
        if qty is None:
            budget = D(eur) * (self.usd[day] if line.asset.currency == "USD" else 1)
            quantity = (budget / price).quantize(D(1).scaleb(-decimals), ROUND_DOWN)
        else:
            quantity = D(qty)
        if quantity <= 0:
            return None
        return self.op(account, line.asset, day, "achat", quantity=quantity, unit_price=price,
                       fees=D(fees), **kw)  # fmt: skip

    def sell(self, account, name, day, qty: str, fees: str = "1", **kw):
        line = self.lines[name]
        return self.op(account, line.asset, day, "vente", quantity=D(qty),
                       unit_price=self.close(name, day), fees=D(fees), **kw)  # fmt: skip

    def held(self, name: str, day: date) -> Decimal:
        """Quantité détenue le soir de `day`, tous comptes confondus."""
        return valuations.quantity_on(self.conn, self.lines[name].asset.id, day)

    def finish(self) -> None:
        compute_positions(repository.list_operations(self.conn))  # lève PositionError


def months(start: date, end: date, day: int = 5):
    """Le `day` de chaque mois, de `start` à `end` compris."""
    y, m = start.year, start.month
    while (current := date(y, m, day)) <= end:
        if current >= start:
            yield current
        y, m = (y + 1, 1) if m == 12 else (y, m + 1)


def _first_of_month(today: date, back: int) -> date:
    """Premier jour du mois, `back` mois avant celui d'aujourd'hui."""
    index = today.year * 12 + today.month - 1 - back
    return date(index // 12, index % 12 + 1, 1)


# --- Comptes -----------------------------------------------------------------------------------


def fill(conn) -> None:
    """Remplit une base vide de la démo complète."""
    b = Builder(conn)
    today = b.today
    recent = _first_of_month(today, 3)  # avant : achats saisis ; après : achats programmés
    pea(b, recent)
    cto(b)
    assurance_vie(b)
    per(b)
    livrets_et_biens(b)
    crypto(b)
    reglages(b)
    b.finish()
    defi_sync.sync(conn, today)  # intérêts Aave des mois clos, comme à l'ouverture de l'app


def pea(b: Builder, recent: date) -> None:
    """PEA ouvert en 2019 : versement mensuel, ETF chaque mois, actions à dividende, une ligne
    vendue entièrement (actif soldé), un achat programmé dont des échéances attendent."""
    acc = repository.create_account(b.conn, "PEA démo", "pea", "Banque fictive", "EUR")
    repository.set_account_tax_info(b.conn, acc.id, date(2019, 1, 15), ZERO)
    b.quoted("ETF Monde fictif", "etf", "DEMO~MONDE.PA", "EUR", date(2018, 6, 1), "14.20",
             seed=1)  # fmt: skip
    b.quoted("ETF Émergents fictif", "etf", "DEMO~EMERG.PA", "EUR", date(2018, 6, 1), "21.50",
             beta="0.8", drift="-0.03", seed=2)  # fmt: skip
    b.quoted("Banque Alpha", "action", "DEMO~ALPHA.PA", "EUR", date(2018, 6, 1), "48.00",
             beta="1.3", seed=3)  # fmt: skip
    b.quoted("Énergie Bêta", "action", "DEMO~BETA.PA", "EUR", date(2018, 6, 1), "52.00",
             beta="0.6", drift="0.02", seed=4)  # fmt: skip
    b.quoted("Auto Kappa", "action", "DEMO~KAPPA.PA", "EUR", date(2018, 6, 1), "17.00",
             beta="1.6", drift="-0.12", seed=5)  # fmt: skip
    start = date(2019, 1, 15)
    b.cash(acc, start, "5000")
    b.buy(acc, "ETF Monde fictif", start, eur="2800")
    b.buy(acc, "Banque Alpha", start, eur="1000")
    b.buy(acc, "Auto Kappa", start, eur="900")
    for day in months(date(2019, 2, 5), recent - timedelta(days=1)):
        b.cash(acc, day, "320")
        b.buy(acc, "ETF Monde fictif", day + timedelta(days=1), eur="250", fees="0.5")
        if day.month % 3 == 0:
            b.buy(acc, "ETF Émergents fictif", day + timedelta(days=1), eur="190", fees="0.5")
    b.cash(acc, date(2020, 3, 18), "2000", note="versement fictif pendant le creux")
    b.buy(acc, "ETF Monde fictif", date(2020, 3, 19), eur="1990")
    b.cash(acc, date(2021, 6, 9), "1200")
    b.buy(acc, "Énergie Bêta", date(2021, 6, 10), eur="1190")
    sold = date(2023, 5, 10)
    sale = b.sell(acc, "Auto Kappa", sold, str(b.held("Auto Kappa", sold)))  # en moins-value
    b.buy(
        acc, "Énergie Bêta", sold + timedelta(days=1), eur=str(sale.quantity * sale.unit_price - 2)
    )
    for year in range(2020, b.today.year + 1):
        for name, month, pct in [("Banque Alpha", 5, "0.045"), ("Énergie Bêta", 6, "0.05")]:
            day = date(year, month, 20)
            if day > b.today or (name == "Énergie Bêta" and year < 2022):
                continue
            amount = (b.held(name, day) * b.close(name, day) * D(pct)).quantize(D("0.01"))
            b.op(acc, b.lines[name].asset, day, "dividende", amount=amount,
                 note="dividende fictif")  # fmt: skip
    repository.save_recurring(
        b.conn, None, acc.id, b.lines["ETF Monde fictif"].asset.id, D("250"), "mensuel", 6,
        recent, "achat programmé fictif",
    )  # fmt: skip


def cto(b: Builder) -> None:
    """Compte-titres ouvert en 2020 : lignes en dollars (taux du jour), dividendes avec impôt
    retenu, action offerte, droits de garde, plus-value et moins-value réalisées, espèces
    recalées sur un solde saisi (D-075), achat programmé toutes les deux semaines."""
    acc = repository.create_account(b.conn, "CTO démo", "cto", "Courtier fictif", "EUR")
    b.quoted("ETF S&P 500 fictif", "etf", "DEMO~SP500", "USD", date(2019, 6, 3), "280.00",
             beta="1.1", drift="0.03", seed=6)  # fmt: skip
    b.quoted("Logiciel Lambda", "action", "DEMO~LAMBDA", "USD", date(2019, 6, 3), "120.00",
             beta="1.5", drift="0.06", seed=7)  # fmt: skip
    b.quoted("Puces Mu", "action", "DEMO~MU", "USD", date(2019, 6, 3), "60.00",
             beta="1.4", drift="-0.09", seed=8)  # fmt: skip
    b.quoted("Foncière Omicron", "action", "DEMO~OMICRON.PA", "EUR", date(2019, 6, 3),
             "36.00", beta="0.7", drift="-0.02", seed=9)  # fmt: skip
    b.cash(acc, date(2020, 3, 2), "4000")
    b.buy(acc, "ETF S&P 500 fictif", date(2020, 3, 24), eur="5000")
    b.buy(acc, "Foncière Omicron", date(2020, 6, 15), eur="1500")
    b.buy(acc, "Logiciel Lambda", date(2021, 2, 8), eur="2500")
    b.buy(acc, "Puces Mu", date(2021, 11, 22), eur="1800")
    for year in range(2021, b.today.year + 1):
        b.cash(acc, date(year, 1, 10), "1500")
        if date(year, 12, 15) <= b.today:
            b.op(acc, None, date(year, 12, 15), "frais", amount=D("15"),
                 note="droits de garde fictifs")  # fmt: skip
        b.buy(acc, "ETF S&P 500 fictif", date(year, 1, 12), eur="1200")
    lam, omi = b.lines["Logiciel Lambda"].asset, b.lines["Foncière Omicron"].asset
    for day in months(date(2022, 3, 15), b.today, 15):
        if day.month % 3 == 0:  # dividende trimestriel en dollars, 15 % retenus à la source
            b.op(acc, lam, day, "dividende", amount=D("9.60"), currency="USD",
                 tax_withheld=D("1.44"), note="dividende fictif")  # fmt: skip
    for year in range(2021, b.today.year + 1):
        if date(year, 4, 20) <= b.today:
            b.op(acc, omi, date(year, 4, 20), "dividende", amount=D("62"),
                 note="dividende fictif")  # fmt: skip
    # Action offerte par la plateforme (type Stockperk, D-076).
    b.buy(acc, "Foncière Omicron", date(b.today.year - 1, 9, 9), qty="1", fees="0",
          offered=b.close("Foncière Omicron", date(b.today.year - 1, 9, 9)),
          note="action offerte fictive")  # fmt: skip
    # Moins-value réalisée l'an dernier, plus-value cette année (onglet Fiscalité).
    b.sell(acc, "Puces Mu", date(b.today.year - 1, 10, 6), "10")
    # Espèces lues chez le courtier il y a trois semaines (D-075) ; les opérations suivantes
    # les font bouger. Les lignes en dollars ne débitent pas les espèces en euros d'un CTO.
    b.conn.execute(
        "UPDATE account SET cash = '640.25', cash_date = ?,"
        " cash_op_id = (SELECT COALESCE(MAX(id), 0) FROM operation) WHERE id = ?",
        ((b.today - timedelta(days=21)).isoformat(), acc.id),
    )
    b.sell(acc, "Logiciel Lambda", b.today - timedelta(days=12), "4")
    repository.save_recurring(
        b.conn, None, acc.id, b.lines["ETF S&P 500 fictif"].asset.id, D("100"), "bimensuel", 1,
        b.today - timedelta(days=45), "achat programmé fictif",
    )  # fmt: skip


def assurance_vie(b: Builder) -> None:
    """Assurance-vie ouverte en 2017 (8 ans passés) : fonds euros à valeur saisie chaque fin
    d'année, unité de compte cotée, versements libres, un rachat partiel en 2025."""
    acc = repository.create_account(b.conn, "Assurance-vie démo", "assurance_vie",
                                    "Assureur fictif", "EUR")  # fmt: skip
    repository.set_account_tax_info(b.conn, acc.id, date(2017, 6, 1), ZERO)
    fonds = b.entered("Fonds euros fictif", "fonds_euros")
    b.quoted("UC Actions monde fictive", "fonds", "DEMO~UCMONDE", "EUR", date(2017, 1, 2),
             "100.00", beta="0.95", seed=10)  # fmt: skip
    b.cash(acc, date(2017, 6, 1), "15000")
    b.op(acc, fonds, date(2017, 6, 1), "achat", quantity=D("15000"), unit_price=D("1"))
    b.cash(acc, date(2019, 2, 4), "6000")
    b.buy(acc, "UC Actions monde fictive", date(2019, 2, 5), eur="6000", fees="0", decimals=3)
    b.cash(acc, date(2022, 11, 7), "3000")
    b.buy(acc, "UC Actions monde fictive", date(2022, 11, 8), eur="3000", fees="0", decimals=3)
    # Rachat partiel : vente d'unités puis retrait de l'argent.
    sale = b.sell(acc, "UC Actions monde fictive", date(2025, 3, 3), "15", fees="0")
    b.cash(acc, date(2025, 3, 5), str((sale.quantity * sale.unit_price).quantize(D("0.01"))),
           kind="retrait", note="rachat partiel fictif")  # fmt: skip
    value = D("15000")
    for year in range(2017, b.today.year):
        value = (value * (D("1.015") if year < 2022 else D("1.028"))).quantize(D("0.01"))
        valuations.save_valuation(b.conn, fonds, date(year, 12, 31), value)


def per(b: Builder) -> None:
    """PER ouvert en 2021 : un versement déductible chaque décembre, investi ; un versement de
    cette année pas encore investi (D-064)."""
    acc = repository.create_account(b.conn, "PER démo", "per", "Assureur fictif", "EUR")
    repository.set_account_tax_info(b.conn, acc.id, date(2021, 1, 11), ZERO)
    b.quoted("Fonds retraite fictif", "fonds", "DEMO~RETRAITE", "EUR", date(2020, 6, 1),
             "50.00", beta="0.7", seed=11)  # fmt: skip
    b.cash(acc, date(2021, 1, 11), "2000")
    b.buy(acc, "Fonds retraite fictif", date(2021, 1, 12), eur="2000", fees="0", decimals=3)
    for year in range(2021, b.today.year):
        b.cash(acc, date(year, 12, 10), "2400")
        b.buy(acc, "Fonds retraite fictif", date(year, 12, 11), eur="2400", fees="0", decimals=3)
    b.cash(acc, b.today - timedelta(days=40), "1500", note="versement fictif à investir")


def livrets_et_biens(b: Builder) -> None:
    """Livret A et LDDS (soldes saisis), virement du livret vers le PEA (D-085), appartement et
    SCPI à valeur saisie, pièces d'or, prêt immobilier recalé et prêt auto (D-107)."""
    conn = b.conn
    livret = repository.create_account(conn, "Livret A démo", "livret", "Banque fictive", "EUR",
                                       D("9200"))  # fmt: skip
    repository.create_account(conn, "LDDS démo", "livret", "Banque fictive", "EUR", D("4300"))
    pea_id = conn.execute("SELECT id FROM account WHERE kind = 'pea'").fetchone()[0]
    b.cash(livret, b.today - timedelta(days=200), "1500", kind="retrait", to_account_id=pea_id,
           note="virement fictif vers le PEA")  # fmt: skip
    b.buy(repository.get_account(conn, pea_id), "ETF Monde fictif", b.today - timedelta(days=199),
          eur="1490")  # fmt: skip
    b.cash(livret, b.today - timedelta(days=90), "300", note="épargne fictive")

    immo = repository.create_account(conn, "Immobilier démo", "immobilier", None, "EUR")
    appart = b.entered("Appartement fictif", "immobilier")
    b.op(immo, appart, date(2017, 6, 1), "solde_initial", quantity=D("180000"),
         unit_price=D("1"))  # fmt: skip
    scpi = b.entered("SCPI fictive", "scpi")
    b.cash(immo, date(2022, 4, 4), "4000")
    b.op(immo, scpi, date(2022, 4, 5), "achat", quantity=D("20"), unit_price=D("200"))
    flats = ["182000", "186000", "192000", "197000", "205000", "212000", "206000", "204000",
             "209000"]  # fmt: skip
    for year, flat in zip(range(2017, 2026), flats, strict=True):
        if date(year, 12, 31) < b.today:
            valuations.save_valuation(conn, appart, date(year, 12, 31), D(flat))
    for year, share in [(2022, "4000"), (2023, "3920"), (2024, "3800"), (2025, "3760")]:
        if date(year, 12, 31) < b.today:
            valuations.save_valuation(conn, scpi, date(year, 12, 31), D(share))

    coffre = repository.create_account(conn, "Coffre démo", "autre", None, "EUR")
    gold = b.entered("Pièces d'or fictives", "or")
    b.op(coffre, gold, date(2020, 8, 3), "solde_initial", quantity=D("4"), unit_price=D("380"))
    valuations.save_valuation(conn, gold, b.today - timedelta(days=30), D("2480"))

    pret = repository.create_debt(conn, {
        "name": "Prêt immobilier fictif", "kind": "immobilier", "institution": "Banque fictive",
        "principal": D("170000"), "rate": D("0.013"), "start_date": date(2017, 6, 1),
        "months": 240,
    })  # fmt: skip
    # Capital lu sur le tableau d'amortissement en début d'année : l'estimation repart de là.
    repository.save_debt_balance(conn, pret.id, date(2026, 1, 15), D("102600"))
    repository.create_debt(conn, {
        "name": "Prêt auto fictif", "kind": "conso", "institution": "Banque fictive",
        "principal": D("8000"), "rate": D("0.045"), "start_date": date(2025, 1, 10),
        "months": 48,
    })  # fmt: skip


def reglages(b: Builder) -> None:
    """Frais courants, catégories, couleurs, cibles, listes de suivi, livret de comparaison."""
    conn, lines = b.conn, b.lines
    for name, ter in [("ETF Monde fictif", "0.20"), ("ETF Émergents fictif", "0.30"),
                      ("ETF S&P 500 fictif", "0.07"), ("UC Actions monde fictive", "0.85"),
                      ("Fonds retraite fictif", "0.60")]:  # fmt: skip
        repository.set_asset_ter(conn, lines[name].asset.id, D(ter))
    for name, category in [
        ("ETF Monde fictif", "Monde"),
        ("UC Actions monde fictive", "Monde"),
        ("ETF Émergents fictif", "Émergents"),
        ("ETF S&P 500 fictif", "États-Unis"),
        ("Logiciel Lambda", "États-Unis"),
        ("Puces Mu", "États-Unis"),
        ("Fonds euros fictif", "Sécurisé"),
        ("Fonds retraite fictif", "Retraite"),
    ]:
        repository.set_asset_category(conn, lines[name].asset.id, category)
    # Les douze couleurs de la palette (D-150) : les douze premières lignes une couleur chacune,
    # les autres restent en couleur automatique (D-052).
    for color, (asset_id,) in enumerate(
        conn.execute("SELECT id FROM asset ORDER BY id LIMIT 12").fetchall(), start=1
    ):
        conn.execute("UPDATE asset SET color = ? WHERE id = ?", (color, asset_id))
    targets = {"ETF Monde fictif": "45", "ETF Émergents fictif": "10", "ETF S&P 500 fictif": "20"}
    repository.save_targets(conn, {lines[n].asset.id: D(p) for n, p in targets.items()})
    repository.save_category_targets(conn, {"Monde": D("50"), "États-Unis": D("20"),
                                            "Émergents": D("10"), "Sécurisé": D("20")})  # fmt: skip
    repository.create_benchmark(conn, "livret", "Livret A (taux saisi)", "Livrets", None,
                                D("2.4"))  # fmt: skip
    for name, items in [
        ("Indices", [("CAC 40", "^FCHI"), ("S&P 500", "^GSPC"), ("Nikkei 225", "^N225")]),
        ("Cryptos", [("Bitcoin", "BTC-EUR"), ("Ether", "ETH-EUR")]),
    ]:
        wl = repository.create_watchlist(conn, name)
        for label, ticker in items:
            repository.add_watchlist_item(conn, wl["id"], label, ticker)


# Marchés Aave v3 d'Ethereum : adresses PUBLIQUES de contrats d'Aave (pool) et de jetons, jamais
# un portefeuille (règle 3). L'app y lit les vrais taux une fois par jour ; les taux fictifs
# enregistrés ici servent tant qu'elle ne les a pas, et hors ligne.
AAVE_ETH = {"chain_id": 1, "chain_name": "Ethereum", "market_name": "Aave Ethereum",
            "market": "0x87870Bca3F3fD6335C3F4ce8392D69350B4fA4E2"}  # fmt: skip
USDC_TOKEN = "0xA0b86991c6218b36c1d19D4a2e9Eb0cE3606eB48"
WETH_TOKEN = "0xC02aaA39b223FE8D0A0e5C4F27eAD9083C756Cc2"


def crypto(b: Builder) -> None:
    """Plateforme à l'étranger (3916-bis) et portefeuille personnel (D-104), cryptos cotées en
    dollars sur un compte en euros (D-085), transfert, échange crypto-crypto, ventes imposables
    (D-066), frais, récompenses de chaque type (D-092), dépôts Aave vérifiés (D-095) et un
    emprunt dont le facteur de santé approche l'alerte (D-108)."""
    conn, today = b.conn, b.today
    start = date(2019, 6, 3)
    for name, sym, price, beta, drift, seed, wobble in [
        ("Bitcoin fictif", "BTC", "9000", "2.2", "0.15", 20, "2"),
        ("Ether fictif", "ETH", "250", "2.5", "0.15", 21, "3"),
        ("Solana fictif", "SOL", "6", "3", "0.25", 22, "4"),
        ("USDC fictif", "USDC", "1", "0", "0", 23, "0.02"),
        ("Jeton airdrop fictif", "AIR", "2", "1", "-0.45", 24, "3"),
    ]:
        b.quoted(name, "crypto", f"DEMO~{sym}", "USD", start, price, beta, drift, seed,
                 coingecko_id=f"demo-{sym.lower()}", wobble=wobble)  # fmt: skip
    plat = repository.create_account(conn, "Plateforme crypto démo", "crypto",
                                     "Plateforme fictive", "EUR")  # fmt: skip
    repository.set_account_custody(conn, plat.id, "exchange", True)
    wallet = repository.create_account(conn, "Portefeuille démo", "crypto", None, "EUR")
    repository.set_account_custody(conn, wallet.id, "wallet", False)
    aave_acc = repository.create_account(conn, "Aave Ethereum démo", "crypto", None, "EUR")
    repository.set_account_custody(conn, aave_acc.id, "wallet", False)

    b.cash(plat, date(2020, 4, 1), "3000")
    b.buy(plat, "Bitcoin fictif", date(2020, 4, 6), eur="2000", decimals=6, fees="2")
    b.buy(plat, "Ether fictif", date(2020, 5, 11), eur="900", decimals=5, fees="1")
    for year in range(2021, today.year + 1):
        amount = {2021: "2100", 2022: "1500", 2023: "1500", 2024: "1500"}.get(year, "300")
        b.cash(plat, date(year, 1, 4), amount)
    b.buy(plat, "Ether fictif", date(2021, 1, 5), eur="1400", decimals=5, fees="1.5")
    b.buy(plat, "Solana fictif", date(2021, 9, 6), eur="600", decimals=3, fees="1")
    b.buy(plat, "USDC fictif", date(2022, 1, 5), eur="1450", decimals=2, fees="0")
    b.buy(plat, "Bitcoin fictif", date(2023, 1, 9), eur="1400", decimals=6, fees="1.5")
    b.buy(plat, "USDC fictif", date(2024, 1, 8), eur="1400", decimals=2, fees="0")
    # Frais de réseau payés en euros sur la plateforme (onglet Rendements crypto).
    for year in range(2021, today.year + 1):
        if date(year, 6, 1) <= today:
            b.op(plat, None, date(year, 6, 1), "frais", amount=D("4.50"),
                 note="frais de retrait fictifs")  # fmt: skip

    # Bitcoin et ether envoyés vers le portefeuille personnel (D-085).
    for name, day, qty in [("Bitcoin fictif", date(2023, 3, 6), "0.05"),
                           ("Ether fictif", date(2024, 2, 5), "1.5")]:  # fmt: skip
        b.op(plat, b.lines[name].asset, day, "transfert_sortant", quantity=D(qty),
             unit_price=b.close(name, day), to_account_id=wallet.id,
             note="envoi fictif vers le portefeuille")  # fmt: skip

    # Ventes imposables (l'an dernier et cette année) et échange crypto-crypto, non imposable.
    b.sell(plat, "Bitcoin fictif", date(today.year - 1, 11, 10), "0.01", fees="2")
    b.sell(plat, "Bitcoin fictif", today - timedelta(days=25), "0.008", fees="2")
    swap_day = today - timedelta(days=60)
    eth = b.sell(plat, "Ether fictif", swap_day, "0.3", fees="0", crypto_swap=True,
                 note="échange fictif")  # fmt: skip
    sol_qty = (eth.quantity * eth.unit_price / b.close("Solana fictif", swap_day)).quantize(
        D("0.001"), ROUND_DOWN)  # fmt: skip
    b.buy(plat, "Solana fictif", swap_day, qty=str(sol_qty), fees="0", note="échange fictif")

    # Récompenses sur 12 mois : Earn en USDC, staking en SOL, cashback en BTC, un airdrop et
    # un parrainage (D-092).
    for m in range(1, 13):
        day = _business(today - timedelta(days=30 * m))
        for name, qty, kind in [
            ("USDC fictif", "6.2", "earn"),
            ("Solana fictif", "0.07", "staking"),
            ("Bitcoin fictif", "0.00002", "cashback"),
        ]:
            if kind == "cashback" and m > 6:
                continue
            price = b.close(name, day)
            b.op(plat, b.lines[name].asset, day, "achat", quantity=D(qty), unit_price=price,
                 offered=D(qty) * price, reward_kind=kind, note="récompense fictive")  # fmt: skip
    for name, back, qty, kind in [("Jeton airdrop fictif", 300, "40", "airdrop"),
                                  ("Bitcoin fictif", 150, "0.0003", "parrainage")]:  # fmt: skip
        day = _business(today - timedelta(days=back))
        price = b.close(name, day)
        b.op(plat, b.lines[name].asset, day, "achat", quantity=D(qty), unit_price=price,
             offered=D(qty) * price, reward_kind=kind, note="cadeau fictif")  # fmt: skip

    # Aave : USDC et ether déposés depuis la plateforme et le portefeuille, puis un emprunt.
    deposit = _business(date(today.year - 1, 3, 3))
    for src, name, qty in [(plat, "USDC fictif", "1200"), (wallet, "Ether fictif", "1")]:
        b.op(src, b.lines[name].asset, deposit, "transfert_sortant", quantity=D(qty),
             unit_price=b.close(name, deposit), to_account_id=aave_acc.id,
             note="dépôt fictif sur Aave")  # fmt: skip
    usdc, ether = b.lines["USDC fictif"].asset, b.lines["Ether fictif"].asset
    lines = [
        defi_sync.create_line(conn, aave_acc.id, usdc.id, {**AAVE_ETH, "token": USDC_TOKEN,
                                                           "symbol": "USDC"}),
        defi_sync.create_line(conn, aave_acc.id, ether.id, {**AAVE_ETH, "token": WETH_TOKEN,
                                                            "symbol": "WETH"}),
    ]  # fmt: skip
    borrow = defi_sync.create_borrow(conn, usdc.id, {**AAVE_ETH, "token": USDC_TOKEN,
                                                     "symbol": "USDC"})  # fmt: skip
    days = [d for d in b.days if d >= deposit] + [today]
    for line, base, side in [(lines[0], "0.045", "supply"), (lines[1], "0.018", "supply"),
                             (borrow, "0.062", "borrow")]:  # fmt: skip
        rates = {d: (D(base) * (1 + _noise(n, line.id * 7 + len(side)) * 15)).quantize(D("0.0001"))
                 for n, d in enumerate(days)}  # fmt: skip
        defi_sync.save_rates(conn, line, rates, side)
    conn.executemany(
        "INSERT OR REPLACE INTO defi_reserve (chain_id, market, token, liquidation_threshold,"
        " fetched_on) VALUES (1, ?, ?, ?, ?)",
        [(AAVE_ETH["market"].lower(), t.lower(), v, today.isoformat())
         for t, v in [(USDC_TOKEN, "0.78"), (WETH_TOKEN, "0.83")]],
    )  # fmt: skip
    # Emprunt réglé pour un facteur de santé d'environ 1,6, juste au-dessus de l'alerte (1,5).
    borrowed = _business(date(today.year, 1, 12))
    collateral = 1200 * D("0.78") + b.close("Ether fictif", today) * D("0.83")
    defi_sync.add_borrow_flow(conn, borrow.id, borrowed, (collateral / D("1.6")).quantize(D(1)))
    # Soldes lus dans Aave (« Vérifier ») : l'estimation repart de ces chiffres.
    # Un peu moins que l'estimation : les taux réels ont varié.
    checked = _business(today - timedelta(days=40))
    years = D((checked - deposit).days) / 365
    read = (1200 * D("1.045") ** years - D("2.37")).quantize(D("0.01"))
    defi_sync.save_check(conn, lines[0].id, checked, read)
