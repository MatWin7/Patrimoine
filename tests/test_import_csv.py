"""Import CSV générique (D-053), sur des fichiers FICTIFS."""

from datetime import date
from decimal import Decimal as D
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from app.importers.csv_generique import (
    decode,
    guess_mapping,
    parse_date,
    parse_decimal,
    parse_type,
    read_rows,
)
from app.main import app

FICHIER = (Path(__file__).parent / "fixtures" / "import_fictif.csv").read_text(encoding="utf-8")


@pytest.mark.parametrize(
    ("text", "expected"),
    [
        ("1 234,56 €", D("1234.56")),
        ("1,234.56", D("1234.56")),
        ("1.234,56", D("1234.56")),
        ("-12,5", D("-12.5")),
        ("0.00012345", D("0.00012345")),
        ("12,50 EUR", D("12.50")),
        ("", None),
    ],
)
def test_nombres(text, expected):
    assert parse_decimal(text) == expected


def test_nombre_illisible():
    with pytest.raises(ValueError, match="illisible"):
        parse_decimal("douze")


def test_dates_et_types():
    assert parse_date("03/01/2025") == parse_date("2025-01-03T10:30:00") == date(2025, 1, 3)
    assert parse_type("Achat comptant") == "achat"
    assert parse_type("SELL") == "vente"
    assert parse_type("Virement entrant") == "versement"
    assert parse_type("Virement sortant") == "retrait"
    assert parse_type("Coupon") == "dividende"
    with pytest.raises(ValueError):
        parse_type("Opération mystère")


def test_fichier_excel_windows_et_correspondance_proposee():
    text = decode(FICHIER.encode("cp1252"))  # accents d'Excel français relus correctement
    rows = read_rows(text)
    assert rows[0][0] == "Date opération" and len(rows) == 8
    mapping = guess_mapping(rows[0])
    # L'ISIN est préféré au libellé pour reconnaître l'actif.
    assert mapping == {
        "date": 0,
        "type": 3,
        "asset": 2,
        "asset_name": 1,
        "quantity": 4,
        "unit_price": 5,
        "amount": 7,
        "fees": 6,
        "currency": 8,
        "note": None,
    }


@pytest.fixture
def client(db_file):
    with TestClient(app) as c:
        yield c


@pytest.fixture
def pea(client):
    account = client.post("/api/accounts", json={"name": "PEA test", "kind": "pea"}).json()
    client.post(
        "/api/assets",
        json={"name": "ETF fictif", "asset_class": "etf", "ticker": "x.pa", "isin": "FR0000000001"},
    )
    return account["id"]


def test_apercu_puis_import_puis_reimport(client, pea):
    body = {"account_id": pea, "text": FICHIER}
    preview = client.post("/api/import/preview", json=body).json()
    statuses = [(r["line"], r["status"]) for r in preview["rows"]]
    assert statuses == [
        (2, "ok"),
        (3, "ok"),
        (4, "ok"),  # même achat deux fois le même jour : deux opérations distinctes
        (5, "actif_inconnu"),
        (6, "ok"),
        (7, "ok"),
        (8, "erreur"),  # 32/03 n'existe pas
    ]
    assert preview["rows"][4]["unit_price"] == "55.5"
    # Actif inconnu : son nom (colonne Libellé) sert à pré-remplir sa création.
    assert (preview["rows"][3]["asset"], preview["rows"][3]["asset_name"]) == (
        "FR0000000099",
        "ACTION INCONNUE",
    )
    assert preview["rows"][5]["op_type"] == "dividende"
    assert client.get("/api/operations").json() == []  # l'aperçu n'enregistre rien

    res = client.post("/api/import", json=body)
    assert res.status_code == 201 and res.json()["imported"] == 5
    # 1 000 versés − 2×501 : 2 € de plus supposés versés (D-062), puis + 276,5 + 3,2.
    assert client.get("/api/accounts").json()[0]["cash"] == "279.70"
    [position] = client.get("/api/portfolio").json()["positions"]
    assert position["quantity"] == "15"

    # Même fichier une 2e fois : tout est reconnu, rien n'est doublé.
    again = client.post("/api/import/preview", json=body).json()
    assert again["counts"] == {"deja_importee": 5, "actif_inconnu": 1, "erreur": 1}
    assert client.post("/api/import", json=body).json()["imported"] == 0


def test_saisie_manuelle_deja_presente(client, pea):
    asset_id = client.get("/api/assets").json()[0]["id"]
    client.post(
        "/api/operations",
        json={"account_id": pea, "asset_id": asset_id, "op_date": "2025-01-03",
              "op_type": "achat", "quantity": "10", "unit_price": "50"},
    )  # fmt: skip
    rows = client.post("/api/import/preview", json={"account_id": pea, "text": FICHIER}).json()
    assert [r["status"] for r in rows["rows"][1:3]] == ["deja_saisie", "deja_saisie"]


def test_import_incoherent_annule_en_entier(client, pea):
    text = "date;type;isin;quantite;prix\n2025-01-03;vente;FR0000000001;3;10\n"
    res = client.post("/api/import", json={"account_id": pea, "text": text})
    assert res.status_code == 422 and "rien n'a été enregistré" in res.json()["detail"]
    assert client.get("/api/operations").json() == []


def test_correspondance_choisie_et_sans_en_tete(client, pea):
    text = "FR0000000001,2025-01-03,buy,4,12.5\n"
    mapping = {"asset": 0, "date": 1, "type": 2, "quantity": 3, "unit_price": 4}
    body = {"account_id": pea, "text": text, "mapping": mapping, "header": False}
    data = client.post("/api/import/preview", json=body).json()
    assert data["columns"] == ["Colonne 1", "Colonne 2", "Colonne 3", "Colonne 4", "Colonne 5"]
    assert data["rows"][0]["status"] == "ok" and data["rows"][0]["asset_name"] == "ETF fictif"


def test_devise_differente_refusee(client, pea):
    text = "date;type;isin;quantite;prix;devise\n2025-01-03;achat;FR0000000001;1;10;USD\n"
    row = client.post("/api/import/preview", json={"account_id": pea, "text": text}).json()
    assert row["rows"][0]["status"] == "erreur" and "USD" in row["rows"][0]["message"]


def test_actif_reconnu_par_son_nom(client, pea):
    # ISIN absent de l'actif enregistré : le nom (colonne Libellé) suffit à le reconnaître.
    text = "date;libelle;isin;type;quantite;prix\n2025-01-03;etf fictif;LU0000000002;achat;1;10\n"
    row = client.post("/api/import/preview", json={"account_id": pea, "text": text}).json()
    assert row["rows"][0]["status"] == "ok" and row["rows"][0]["asset_name"] == "ETF fictif"


def test_lignes_collees_depuis_un_tableur(client, pea):
    # Copier-coller depuis Excel : colonnes séparées par des tabulations.
    text = (
        "Date\tType\tISIN ou ticker\tNom\tQuantité\tPrix unitaire\tMontant\tFrais\tNote\n"
        "02/01/2025\tVersement\t\t\t\t\t1000\t\texemple fictif\n"
        "03/01/2025\tAchat\tFR0000000001\tETF exemple\t10\t50,00\t\t1,00\texemple fictif\n"
    )
    data = client.post("/api/import/preview", json={"account_id": pea, "text": text}).json()
    # En-têtes du modèle téléchargeable : tous reconnus d'office.
    assert data["mapping"] == {
        "date": 0,
        "type": 1,
        "asset": 2,
        "asset_name": 3,
        "quantity": 4,
        "unit_price": 5,
        "amount": 6,
        "fees": 7,
        "currency": None,
        "note": 8,
    }
    assert [r["status"] for r in data["rows"]] == ["ok", "ok"]
