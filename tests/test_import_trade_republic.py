"""Profil d'import Trade Republic (D-071), sur un export FICTIF."""

from datetime import date
from decimal import Decimal as D
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from app.importers import csv_generique, trade_republic
from app.importers.csv_generique import CsvLine
from app.main import app
from app.recurring import pending_dates, period_key

FICHIER = (Path(__file__).parent / "fixtures" / "trade_republic_fictif.csv").read_text(
    encoding="utf-8"
)


def lire(text: str) -> dict[int, CsvLine]:
    rows = csv_generique.read_rows(text)
    assert trade_republic.detect(rows[0])
    return {line.line: line for line in trade_republic.parse(rows[0], rows[1:])}


def test_types_reconnus_et_lignes_ignorees():
    lines = lire(FICHIER)
    assert lines[2].skip == "dépense par carte"
    assert lines[3].skip == "virement du compte espèces"
    assert lines[4].skip.startswith("virement entre le compte espèces et le PEA")
    assert (lines[5].op_type, lines[5].part, lines[5].amount) == ("versement", "pea", D("300.00"))
    # Achat programmé : partie, ISIN, classe proposée, frais à part du montant.
    assert (lines[7].op_type, lines[7].part, lines[7].asset, lines[7].plan) == (
        "achat",
        "pea",
        "IE00TEST0001",
        True,
    )
    assert lines[7].asset_class == "etf" and lines[7].key.endswith("000000000006")
    assert (lines[14].plan, lines[14].fees) == (False, D("1.00"))
    assert lines[12].skip == "intérêts du compte espèces"
    # Dividende net, impôt retenu à part (D-076) ; Saveback sans achat ce jour-là : ignoré.
    assert (lines[15].op_type, lines[15].amount) == ("dividende", D("0.30"))
    assert (lines[15].tax_withheld, lines[15].note) == (D("0.05"), None)
    assert lines[16].skip.startswith("prime Trade Republic sans achat")
    assert lines[17].skip == lines[18].skip == "prime Trade Republic"
    assert (lines[19].part, lines[21].part) == ("obligations", "non_cote")
    assert lines[20].skip.startswith("prépaiement du non coté")
    # Non coté : quantité et prix, pas de montant.
    assert (lines[21].quantity, lines[21].unit_price) == (D("5"), D("10.00"))
    # Fusion : vente et achat liés, prix fixés plus tard (prix de revient).
    assert (lines[22].op_type, lines[22].quantity, lines[22].unit_price) == ("vente", D("10"), None)
    assert (lines[23].op_type, lines[23].asset, lines[23].merger) == (
        "achat",
        "IE00TEST0006",
        lines[22].merger,
    )
    # Entrée et sortie du même titre le même jour : transfert interne, ignoré.
    assert lines[24].skip == lines[25].skip
    assert lines[26].note.startswith("Coupon")
    # Remboursement d'obligation : titres sortis + argent reçu = une vente.
    assert (lines[27].op_type, lines[27].quantity, lines[27].unit_price) == (
        "vente",
        D("100"),
        D("1"),
    )
    assert lines[27].op_date == date(2025, 7, 1) and lines[28].skip.startswith("regroupée")
    assert (lines[29].op_type, lines[29].quantity, lines[29].fees) == ("vente", D("1"), D("1.00"))
    assert "inconnu « WEIRD_EVENT »" in lines[30].error


def test_actions_offertes():
    """Stockperk et Saveback (D-076) : la prime devient la part offerte de l'achat du même titre
    le même jour (le plus proche en montant), plus de dividende."""
    lines = lire(FICHIER)
    assert lines[31].skip.startswith("prime Trade Republic reportée")
    assert (lines[32].offered, lines[32].note) == (
        D("10.00"),
        "Offert par Trade Republic (Stockperk)",
    )
    # Deux achats le même jour : le plus proche en montant (3 €) reçoit le Saveback de 2,80 €.
    assert lines[33].offered is None
    assert (lines[34].offered, lines[34].plan) == (D("2.80"), False)
    assert lines[35].skip.startswith("prime Trade Republic reportée")
    assert not any(line.op_type == "dividende" and line.offered for line in lines.values())
    # Cadeau sans titre (D-078) : l'achat du jour dont le total (frais compris) est le plus
    # proche reçoit le cadeau ; il couvre titres et frais.
    assert (lines[36].offered, lines[36].fees) == (D("31.00"), D("1.00"))
    assert lines[37].skip.startswith("prime Trade Republic reportée")
    assert lines[38].offered is None
    # Coupon : impôt retenu à part, note « Coupon ».
    assert (lines[26].note, lines[26].tax_withheld) == ("Coupon", D("0.60"))


def test_export_repasse_par_excel():
    """Lignes entières entre guillemets et Windows-1252 : même lecture que l'export brut."""
    wrapped = "\r\n".join('"' + line.replace('"', '""') + '"' for line in FICHIER.splitlines())
    text = csv_generique.decode(wrapped.encode("cp1252"))
    assert lire(text) == lire(FICHIER)


def test_plans_detectes():
    plans = trade_republic.detect_plans(list(lire(FICHIER).values()), today=date(2025, 2, 1))
    by_key = {p.key: p for p in plans}
    weekly = by_key["pea|IE00TEST0001"]
    assert (weekly.frequency, weekly.day, weekly.amount, weekly.count) == (
        "hebdomadaire",
        1,  # lundi
        D("100.00"),
        3,
    )
    assert weekly.active
    biweekly = by_key["cto|US00TEST0002"]
    assert (biweekly.frequency, biweekly.day, biweekly.amount) == ("bimensuel", 5, D("25.00"))
    assert (biweekly.first, biweekly.last) == (date(2025, 1, 3), date(2025, 1, 31))
    # Longtemps sans exécution : plan arrêté, pas coché d'office.
    late = trade_republic.detect_plans(list(lire(FICHIER).values()), today=date(2025, 6, 1))
    assert not any(p.active for p in late)


def test_echeances_toutes_les_deux_semaines():
    due = pending_dates(date(2025, 1, 1), "bimensuel", 5, None, date(2025, 2, 10))
    assert due == [date(2025, 1, 3), date(2025, 1, 17), date(2025, 1, 31)]
    after = pending_dates(date(2025, 1, 1), "bimensuel", 5, date(2025, 1, 17), date(2025, 2, 10))
    assert after == [date(2025, 1, 31)]
    # Deux échéances à 14 jours d'écart : deux quinzaines différentes.
    assert period_key(date(2025, 1, 3), "bimensuel") < period_key(date(2025, 1, 17), "bimensuel")


@pytest.fixture
def client(db_file):
    with TestClient(app) as c:
        yield c


ACTIFS = [
    {"name": "ETF Monde fictif", "asset_class": "etf", "ticker": "ETF1.DE", "isin": "IE00TEST0001"},
    {
        "name": "Action fictive",
        "asset_class": "action",
        "ticker": "ACT1.DE",
        "isin": "US00TEST0002",
    },
    {
        "name": "Fonds ancien fictif",
        "asset_class": "etf",
        "ticker": "OLD.PA",
        "isin": "LU00TEST0005",
    },
    {
        "name": "Fonds nouveau fictif",
        "asset_class": "etf",
        "ticker": "NEW.PA",
        "isin": "IE00TEST0006",
    },
    {"name": "Obligation fictive 2025", "asset_class": "obligation", "isin": "XS00TEST0003"},
    {"name": "Fonds non coté fictif", "asset_class": "autre", "isin": "LU00TEST0004"},
]


def test_premiere_utilisation_puis_import(client):
    body = {"text": FICHIER}
    # Aucun compte : le relevé est reconnu, ses parties attendent un compte.
    preview = client.post("/api/import/preview", json=body).json()
    assert preview["profile"]["id"] == "trade_republic"
    parts = {p["part"]: p for p in preview["parts"]}
    assert set(parts) == {"pea", "cto", "obligations", "non_cote"}
    assert parts["pea"]["suggested_name"] == "Trade Republic PEA"
    assert all(p["account_id"] is None for p in parts.values())
    assert preview["counts"]["sans_compte"] > 0
    # Les lignes ignorées ne sont pas renvoyées, seulement leurs raisons ; ni contrepartie ni IBAN.
    assert all(r["status"] != "ignoree" for r in preview["rows"])
    assert {"reason": "dépense par carte", "count": 1} in preview["ignored"]
    assert "Personne Fictive" not in str(preview) and "FR76" not in str(preview)

    # Comptes créés comme le propose l'aperçu : reconnus d'eux-mêmes ; obligations et non coté
    # suivent le compte-titres.
    pea = client.post(
        "/api/accounts",
        json={"name": "Trade Republic PEA", "kind": "pea", "institution": "Trade Republic"},
    ).json()["id"]
    cto = client.post(
        "/api/accounts",
        json={"name": "Trade Republic CTO", "kind": "cto", "institution": "Trade Republic"},
    ).json()["id"]
    preview = client.post("/api/import/preview", json=body).json()
    assert {p["part"]: p["account_id"] for p in preview["parts"]} == {
        "pea": pea,
        "cto": cto,
        "obligations": cto,
        "non_cote": cto,
    }
    assert preview["counts"]["actif_inconnu"] > 0
    unknown = {
        r["asset"]: r["asset_class"] for r in preview["rows"] if r["status"] == "actif_inconnu"
    }
    assert unknown["XS00TEST0003"] == "obligation" and unknown["LU00TEST0004"] == "autre"

    for asset in ACTIFS:
        assert client.post("/api/assets", json=asset).status_code == 201
    preview = client.post("/api/import/preview", json=body).json()
    assert preview["counts"] == {"ok": 22, "ignoree": 14, "erreur": 1}
    plans = {p["key"]: p for p in preview["plans"]}
    assert plans["cto|US00TEST0002"]["problem"] is None
    assert plans["cto|US00TEST0002"]["account"] == "Trade Republic CTO"

    res = client.post("/api/import", json={**body, "plans": ["cto|US00TEST0002"]})
    assert res.status_code == 201 and res.json() == {
        "imported": 22,
        "plans": 1,
        "counts": preview["counts"],
        "auto_assets": [],
    }
    ops = client.get("/api/operations").json()
    merger = sorted(
        (o["op_type"], D(o["quantity"]), D(o["unit_price"]))
        for o in ops
        if "Fusion" in (o["note"] or "")
    )
    # 10 parts achetées 20 € + 1 € de frais : prix de revient 201 €, repris par les 8 nouvelles.
    assert merger == [("achat", 8, D("25.125")), ("vente", 10, D("20.1"))]
    positions = {
        p["asset"]: p["quantity"] for p in client.get("/api/portfolio").json()["positions"]
    }
    assert "Fonds ancien fictif" not in positions and positions["Fonds nouveau fictif"] == "8"
    assert "Obligation fictive 2025" not in positions  # remboursée
    assert positions["ETF Monde fictif"] == "5"  # 3 × 2 achetées, 1 vendue
    # Plan créé : toutes les deux semaines, échéances du relevé déjà traitées.
    [plan] = client.get("/api/recurring").json()["items"]
    assert (plan["frequency"], plan["day"], plan["amount"], plan["last_due"]) == (
        "bimensuel",
        5,
        "25.00",
        "2025-01-31",
    )

    # Réimport : rien n'est doublé, et le plan existe déjà.
    again = client.post("/api/import/preview", json=body).json()
    assert again["counts"] == {"deja_importee": 22, "ignoree": 14, "erreur": 1}
    assert {p["key"]: p["problem"] for p in again["plans"]}["cto|US00TEST0002"] == (
        "déjà dans tes achats programmés"
    )
    assert (
        client.post("/api/import", json={**body, "plans": ["cto|US00TEST0002"]}).json()["imported"]
        == 0
    )


def test_achat_programme_existant_avance(client):
    pea = client.post("/api/accounts", json={"name": "Mon PEA", "kind": "pea"}).json()["id"]
    for asset in ACTIFS:
        client.post("/api/assets", json=asset)
    asset_id = next(
        a["id"] for a in client.get("/api/assets").json() if a["isin"] == "IE00TEST0001"
    )
    client.post(
        "/api/recurring",
        json={
            "account_id": pea,
            "asset_id": asset_id,
            "amount": "100",
            "frequency": "hebdomadaire",
            "day": 1,
            "start_date": "2025-01-01",
        },
    )
    cto = client.post("/api/accounts", json={"name": "Mon CTO", "kind": "cto"}).json()["id"]
    body = {"text": FICHIER, "accounts": {"pea": pea, "cto": cto}}
    assert client.post("/api/import", json=body).status_code == 201
    [plan] = client.get("/api/recurring").json()["items"]
    # Les 3 lundis du relevé ne seront pas reproposés dans « Gérer ».
    assert plan["last_due"] == "2025-01-20"


def test_compte_choisi_et_partie_sans_compte(client):
    cto = client.post("/api/accounts", json={"name": "Compte-titres", "kind": "cto"}).json()["id"]
    for asset in ACTIFS:
        client.post("/api/assets", json=asset)
    # Compte sans établissement : jamais pris d'office pour une plateforme (D-083).
    preview = client.post("/api/import/preview", json={"text": FICHIER}).json()
    assert all(p["account_id"] is None for p in preview["parts"])
    body = {"text": FICHIER, "accounts": {"cto": cto, "obligations": None}}
    preview = client.post("/api/import/preview", json=body).json()
    by_part = {p["part"]: p["account_id"] for p in preview["parts"]}
    # Pas de PEA : ses lignes attendent un compte ; obligations laissées sans compte exprès.
    assert by_part == {"pea": None, "cto": cto, "obligations": None, "non_cote": cto}
    statuses = {r["line"]: r["status"] for r in preview["rows"]}
    assert statuses[7] == statuses[19] == "sans_compte" and statuses[21] == "ok"


def test_action_cotee_en_dollars(client):
    client.post("/api/accounts", json={"name": "Trade Republic CTO", "kind": "cto"})
    client.post(
        "/api/assets",
        json={
            "name": "Action fictive",
            "asset_class": "action",
            "ticker": "ACT1",
            "isin": "US00TEST0002",
            "currency": "USD",
        },
    )
    rows = client.post("/api/import/preview", json={"text": FICHIER}).json()["rows"]
    row = next(r for r in rows if r["line"] == 6)
    assert "cotation en EUR" in row["message"]
    assert row["asset_id"] is not None  # bouton « Modifier » de l'aperçu (D-083)


def test_generique_force(client):
    """« Lire comme un CSV quelconque » : il faut alors choisir le compte."""
    res = client.post("/api/import/preview", json={"text": FICHIER, "profile": "generique"})
    assert res.status_code == 422
    account = client.post("/api/accounts", json={"name": "CTO", "kind": "cto"}).json()["id"]
    preview = client.post(
        "/api/import/preview", json={"text": FICHIER, "profile": "generique", "account_id": account}
    ).json()
    assert preview["profile"] is None and preview["mapping"]["date"] is not None
