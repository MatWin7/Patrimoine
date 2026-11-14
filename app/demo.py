"""Remplit data/demo.db avec des données FICTIVES : `uv run python -m app.demo`.

Toujours data/demo.db, quelle que soit PATRIMOINE_DB : ce script ne doit jamais toucher la
vraie base. Ensuite : `PATRIMOINE_DB=data/demo.db uv run python -m app`.

`uv run python -m app.demo --grand` remplit data/demo-grand.db : 3 comptes et une trentaine
de lignes fictives, pour voir l'app comme quelqu'un qui a beaucoup de lignes.
"""

import sys
from datetime import UTC, date, datetime, timedelta
from decimal import Decimal
from decimal import Decimal as D

from app import db, prices, repository, valuations
from app.providers import Price

DEMO_DB = db.ROOT / "data" / "demo.db"
DEMO_GRAND_DB = db.ROOT / "data" / "demo-grand.db"


def fake_history(
    conn,
    asset_id: int,
    start: date,
    end: date,
    millis: int = 5000,
    scale: int = 1,
    currency: str = "EUR",
) -> None:
    """Clôtures FICTIVES, jours ouvrés seulement : la courbe d'évolution s'affiche hors ligne.

    Marche pseudo-aléatoire reproductible (en millièmes d'euro, entiers : aucun float), avec
    une légère hausse de fond ; `scale` amplifie les pas. Les tickers DEMO*.PA n'existent pas
    chez Yahoo : rien ne les écrase.
    """
    day, n = start, 0
    while day <= end:
        if day.weekday() < 5:
            n += 1
            millis += ((n * 7919) % 41 - 17) * scale  # pas de -17 à +23 millièmes (× scale)
            conn.execute(
                "INSERT OR REPLACE INTO price_history (asset_id, price_date, close, currency,"
                " source) VALUES (?, ?, ?, ?, 'demo')",
                (asset_id, day.isoformat(), str(Decimal(millis) / 1000), currency),
            )
        day += timedelta(days=1)
    # Dernière clôture aussi comme « cours en ligne » : le total et la fin de la courbe concordent.
    prices.save_price(
        conn, asset_id, Price(Decimal(millis) / 1000, currency, "demo", datetime.now(UTC))
    )


# Portefeuille FICTIF « grand » : (compte, nom, classe, devise, prix de départ en millièmes,
# quantité). Noms inventés, tickers DEMO-* inexistants chez Yahoo et CoinGecko.
GRAND_LINES = [
    ("PEA", "ETF Monde fictif", "etf", "EUR", 5000, "900"),
    ("PEA", "ETF Europe fictif", "etf", "EUR", 25000, "60"),
    ("PEA", "ETF Émergents fictif", "etf", "EUR", 20000, "70"),
    ("PEA", "ETF Petites capis fictif", "etf", "EUR", 12000, "40"),
    ("PEA", "Banque Alpha", "action", "EUR", 60000, "15"),
    ("PEA", "Énergie Bêta", "action", "EUR", 48000, "20"),
    ("PEA", "Luxe Gamma", "action", "EUR", 700000, "2"),
    ("PEA", "Santé Delta", "action", "EUR", 90000, "8"),
    ("PEA", "Industrie Epsilon", "action", "EUR", 110000, "6"),
    ("PEA", "Télécom Zêta", "action", "EUR", 6000, "60"),
    ("PEA", "Distribution Êta", "action", "EUR", 30000, "12"),
    ("PEA", "Aéro Thêta", "action", "EUR", 150000, "3"),
    ("PEA", "Chimie Iota", "action", "EUR", 160000, "2"),
    ("PEA", "Auto Kappa", "action", "EUR", 15000, "25"),
    ("CTO", "ETF S&P 500 fictif", "etf", "USD", 450000, "8"),
    ("CTO", "ETF Nasdaq fictif", "etf", "USD", 380000, "5"),
    ("CTO", "Logiciel Lambda", "action", "USD", 320000, "4"),
    ("CTO", "Puces Mu", "action", "USD", 110000, "10"),
    ("CTO", "Commerce Nu", "action", "USD", 140000, "6"),
    ("CTO", "Boissons Xi", "action", "USD", 60000, "9"),
    ("CTO", "Foncière Omicron", "action", "EUR", 40000, "15"),
    ("CTO", "ETF Or fictif", "etf", "EUR", 70000, "10"),
    ("CTO", "ETF Obligations fictif", "etf", "EUR", 100000, "12"),
    ("CTO", "Assurance Pi", "action", "EUR", 28000, "20"),
    ("CTO", "Transport Rhô", "action", "EUR", 8000, "50"),
    ("Crypto", "Crypto Alpha fictive", "crypto", "EUR", 50000000, "0.02"),
    ("Crypto", "Crypto Bêta fictive", "crypto", "EUR", 2500000, "0.4"),
    ("Crypto", "Crypto Gamma fictive", "crypto", "EUR", 150000, "3"),
    ("Crypto", "Crypto Delta fictive", "crypto", "EUR", 5000, "40"),
    ("Crypto", "Crypto Epsilon fictive", "crypto", "EUR", 8000, "15"),
]


def fill_envelopes(conn, with_property: bool = False) -> None:
    """Livret, assurance-vie en fonds euros (et un bien immobilier) à valeur saisie (D-056)."""
    repository.create_account(conn, "Livret A démo", "livret", "Banque fictive", "EUR", D("3200"))
    av = repository.create_account(conn, "Assurance-vie démo", "assurance_vie", None, "EUR")
    fonds = repository.create_asset(
        conn, "Fonds euros fictif", "fonds_euros", None, None, None, "EUR"
    )
    lines = [
        (
            av,
            fonds,
            date(2024, 3, 1),
            "4000",
            [(date(2024, 12, 31), "4100"), (date(2025, 12, 31), "4210")],
        )
    ]
    per = repository.create_account(conn, "PER démo", "per", None, "EUR")
    fonds_per = repository.create_asset(
        conn, "Fonds retraite fictif", "autre", None, None, None, "EUR"
    )
    lines.append((per, fonds_per, date(2024, 5, 1), "3000", [(date(2025, 12, 31), "3400")]))
    # Un versement récent, pas encore investi : il compte dans les versements de l'année (D-064).
    repository.create_operation(
        conn, per.id, None, date.today() - timedelta(days=60), "versement", None, None,
        D("600"), D("0"), "EUR", "montant fictif",
    )  # fmt: skip
    if with_property:
        immo = repository.create_account(conn, "Biens démo", "immobilier", None, "EUR")
        appart = repository.create_asset(
            conn, "Appartement fictif", "immobilier", None, None, None, "EUR"
        )
        lines.append((immo, appart, date(2019, 6, 1), "150000", [(date(2025, 6, 1), "168000")]))
    for account, asset, day, invested, values in lines:
        # Titres déjà détenus en montant : une part = 1 € au départ, sans toucher aux espèces.
        repository.create_operation(
            conn, account.id, asset.id, day, "solde_initial", D(invested), D("1"), None,
            D("0"), "EUR", "montant fictif",
        )  # fmt: skip
        for when, value in values:
            valuations.save_valuation(conn, asset, when, D(value))


def fill_grand(conn) -> None:
    """Base de démo « grand portefeuille » : versements, un achat par ligne, historique."""
    start = date(2024, 1, 2)
    # Taux fictif enregistré : la conversion USD -> EUR marche hors ligne.
    conn.execute(
        "INSERT OR REPLACE INTO fx_rate (rate_date, currency, rate) VALUES (?, 'USD', '1.10')",
        (date.today().isoformat(),),
    )
    accounts = {
        "PEA": repository.create_account(conn, "PEA démo", "pea", "Banque fictive", "EUR"),
        "CTO": repository.create_account(conn, "CTO démo", "cto", "Courtier fictif", "EUR"),
        "Crypto": repository.create_account(
            conn, "Crypto démo", "crypto", "Plateforme fictive", "EUR"
        ),
    }
    # Plateforme fictive « à l'étranger » : la carte des comptes à déclarer a une ligne (D-104).
    repository.set_account_custody(conn, accounts["Crypto"].id, "exchange", True)
    # Versé = coût des achats en EUR du compte + 500 € (les achats en USD ne touchent pas
    # aux espèces en EUR du compte).
    deposits = dict.fromkeys(accounts, Decimal("500"))
    for where, _, _, currency, millis, qty in GRAND_LINES:
        if currency == "EUR":
            deposits[where] += Decimal(millis) / 1000 * Decimal(qty) + 1
    for where, account in accounts.items():
        repository.create_operation(
            conn, account.id, None, start, "versement", None, None, deposits[where],
            Decimal("0"), "EUR", "versement fictif",
        )  # fmt: skip
    created = {}
    for i, (where, name, cls, currency, millis, qty) in enumerate(GRAND_LINES):
        slug = f"demo-{i:02d}"
        asset = repository.create_asset(
            conn,
            name,
            cls,
            None,
            None if cls == "crypto" else f"{slug.upper()}.PA",
            slug if cls == "crypto" else None,
            currency,
        )
        bought = start + timedelta(days=7 * i)
        repository.create_operation(
            conn, accounts[where].id, asset.id, bought, "achat", Decimal(qty),
            Decimal(millis) / 1000, None, Decimal("1.00"), currency, "achat fictif",
        )  # fmt: skip
        # Une ligne sur trois baisse : il faut aussi des moins-values à afficher. Pas
        # proportionnels au prix (prix de départ >= 5 €) : ±30 % au plus sur deux ans.
        scale = max(1, millis // 10000) * (-1 if i % 3 == 1 else 1)
        fake_history(conn, asset.id, bought, date.today(), millis, scale, currency)
        created[name] = asset
    fill_envelopes(conn, with_property=True)
    fill_income(conn, accounts, created)


def fill_income(conn, accounts, assets) -> None:
    """Dividendes, frais, frais courants, cibles et ouverture du PEA, tous fictifs (D-057 à
    D-060) : de quoi remplir les onglets Revenus, Cible et Fiscalité."""
    repository.set_account_tax_info(conn, accounts["PEA"].id, date(2019, 5, 15), D("12000"))
    for name, ter in [
        ("ETF Monde fictif", "0.20"),
        ("ETF Petites capis fictif", "0.35"),
        ("ETF Or fictif", "0.12"),
        ("ETF Obligations fictif", "0.15"),
    ]:
        repository.set_asset_ter(conn, assets[name].id, D(ter))
    today = date.today()
    # Mois de versement différents selon la ligne (calendrier des dividendes) ; impôt retenu à
    # la source sur une ligne du compte-titres (D-076).
    for where, name, amount, months, withheld in [
        ("PEA", "Banque Alpha", "54", (14, 2), None),
        ("PEA", "Énergie Bêta", "62", (13, 1), None),
        ("PEA", "Santé Delta", "28", (16, 4), None),
        ("CTO", "Foncière Omicron", "12", (15, 9, 3), None),
        ("CTO", "Assurance Pi", "41", (14, 2), "6.15"),
    ]:
        for m in months:
            day = today - timedelta(days=30 * m)
            repository.create_operation(
                conn, accounts[where].id, assets[name].id, day, "dividende", None, None,
                D(amount), D("0"), "EUR", "dividende fictif",
                tax_withheld=D(withheld) if withheld else None,
            )  # fmt: skip
    # Action offerte par la plateforme (type Stockperk, D-076) : 10 € sur un achat de 12 €.
    repository.create_operation(
        conn, accounts["CTO"].id, assets["Foncière Omicron"].id, today - timedelta(days=50),
        "achat", D("1"), D("12"), None, D("0"), "EUR", "action offerte fictive", offered=D("10"),
    )  # fmt: skip
    repository.create_operation(
        conn, accounts["CTO"].id, None, today - timedelta(days=60), "frais", None, None,
        D("12"), D("0"), "EUR", "droits de garde fictifs",
    )  # fmt: skip
    # Vente partielle sur le compte-titres : une plus-value à estimer dans « Fiscalité ».
    repository.create_operation(
        conn, accounts["CTO"].id, assets["Transport Rhô"].id, today - timedelta(days=20),
        "vente", D("20"), D("9.40"), None, D("1.00"), "EUR", "vente fictive",
    )  # fmt: skip
    # Crypto (D-066) : une vente en euros, imposable, et un échange contre une autre crypto.
    crypto = accounts["Crypto"].id
    repository.create_operation(
        conn, crypto, assets["Crypto Bêta fictive"].id, today - timedelta(days=40),
        "vente", D("0.2"), D("3000"), None, D("1.00"), "EUR", "vente fictive",
    )  # fmt: skip
    swap = repository.create_operation(
        conn, crypto, assets["Crypto Gamma fictive"].id, today - timedelta(days=30),
        "vente", D("1"), D("160"), None, D("0"), "EUR", "échange fictif",
    )  # fmt: skip
    repository.set_sale_tax_info(conn, swap.id, True, None)
    repository.create_operation(
        conn, crypto, assets["Crypto Delta fictive"].id, today - timedelta(days=30),
        "achat", D("32"), D("5"), None, D("0"), "EUR", "échange fictif",
    )  # fmt: skip
    # Récompenses fictives (D-092, onglet Crypto) : intérêts mensuels, staking, un airdrop.
    for m in range(1, 13):
        for name, qty, price, kind in [
            ("Crypto Delta fictive", "0.12", "5", "earn"),
            ("Crypto Gamma fictive", "0.004", "150", "staking"),
        ]:
            repository.create_operation(
                conn, crypto, assets[name].id, today - timedelta(days=30 * m), "achat", D(qty),
                D(price), None, D("0"), "EUR", "récompense fictive",
                offered=D(qty) * D(price), reward_kind=kind,
            )  # fmt: skip
    repository.create_operation(
        conn, crypto, assets["Crypto Epsilon fictive"].id, today - timedelta(days=95), "achat",
        D("2"), D("12"), None, D("0"), "EUR", "airdrop fictif", offered=D("24"),
        reward_kind="airdrop",
    )  # fmt: skip
    # Dette fictive (D-107) : un prêt conso amortissable, pour le patrimoine net.
    repository.create_debt(
        conn,
        {
            "name": "Prêt auto fictif",
            "kind": "conso",
            "institution": "Banque fictive",
            "principal": D("8000"),
            "rate": D("0.045"),
            "start_date": date(2025, 1, 10),
            "months": 48,
        },
    )
    targets = {"ETF Monde fictif": "40", "ETF S&P 500 fictif": "20", "ETF Obligations fictif": "10"}
    repository.save_targets(conn, {assets[n].id: D(p) for n, p in targets.items()})
    # Catégories personnelles fictives (D-061) ; les autres lignes gardent leur classe.
    for name, category in [
        ("ETF Monde fictif", "Monde"),
        ("ETF Europe fictif", "Europe"),
        ("ETF S&P 500 fictif", "États-Unis"),
        ("ETF Nasdaq fictif", "États-Unis"),
        ("ETF Émergents fictif", "Émergents"),
        ("ETF Obligations fictif", "Obligations"),
        ("ETF Or fictif", "Or"),
    ]:
        repository.set_asset_category(conn, assets[name].id, category)
    repository.save_category_targets(
        conn,
        {"Monde": D("50"), "États-Unis": D("20"), "Émergents": D("15"), "Obligations": D("10"),
         "Or": D("5")},
    )  # fmt: skip


def fill_demo(conn) -> None:
    """Données fictives de data/demo.db, aussi utilisées par le profil Démo (D-098)."""
    pea = repository.create_account(conn, "PEA démo", "pea", "Banque fictive", "EUR")
    etf = repository.create_asset(conn, "ETF Monde fictif", "etf", None, "DEMO.PA", None, "EUR")
    action = repository.create_asset(
        conn, "Action fictive", "action", None, "DEMO2.PA", None, "EUR"
    )
    # Versements fictifs puis achats : les espèces du compte se calculent toutes seules.
    for jour, amount in [
        (date(2025, 3, 1), "250"),
        (date(2025, 6, 2), "100"),
        (date(2025, 8, 29), "150"),
    ]:
        repository.create_operation(
            conn,
            pea.id,
            None,
            jour,
            "versement",
            None,
            None,
            Decimal(amount),
            Decimal("0"),
            "EUR",
            "versement fictif",
        )
    for asset, jour, qty, price in [
        (etf, date(2025, 3, 3), "40", "5.10"),
        (action, date(2025, 6, 3), "3", "30.00"),
        (etf, date(2025, 9, 1), "25", "5.62"),
    ]:
        repository.create_operation(
            conn,
            pea.id,
            asset.id,
            jour,
            "achat",
            Decimal(qty),
            Decimal(price),
            None,
            Decimal("1.00"),
            "EUR",
            "achat fictif",
        )
    # Achat programmé fictif depuis deux mois : des échéances attendent d'être validées.
    today = date.today()
    start = date(today.year - (today.month <= 2), (today.month - 3) % 12 + 1, 1)
    repository.save_recurring(
        conn, None, pea.id, etf.id, Decimal("50"), "mensuel", 5, start, "achat programmé fictif"
    )
    fill_envelopes(conn)
    fake_history(conn, etf.id, date(2025, 2, 24), date.today())
    fake_history(conn, action.id, date(2025, 5, 26), date.today(), millis=30000, scale=-9)


def main() -> None:
    if "--grand" in sys.argv[1:]:
        db.init_db(DEMO_GRAND_DB)
        with db.connect(DEMO_GRAND_DB) as conn:
            if repository.list_operations(conn):
                print(f"{DEMO_GRAND_DB} contient déjà des opérations : rien à faire.")
                return
            fill_grand(conn)
        conn.close()
        print(f"Base de démo prête : {DEMO_GRAND_DB}")
        return
    db.init_db(DEMO_DB)
    with db.connect(DEMO_DB) as conn:
        if repository.list_operations(conn):
            print(f"{DEMO_DB} contient déjà des opérations : rien à faire.")
            return
        fill_demo(conn)
    conn.close()
    print(f"Base de démo prête : {DEMO_DB}")


if __name__ == "__main__":
    main()
