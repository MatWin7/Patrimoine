"""Emprunts Aave et facteur de santé (D-108). Données FICTIVES ; faux serveur Aave."""

import sqlite3
from datetime import date, timedelta
from decimal import Decimal as D

from app import db, defi, main
from tests.test_aave import BORROW_APY, MARKET, START, TODAY, TOKEN, client, post, setup_line

__all__ = ["client"]  # fixture partagée avec test_aave


def test_facteur_de_sante():
    """1 000 € déposés à 78 % de seuil couvrent 780 € de dette : 600 € empruntés -> 1,3."""
    assert defi.health_factor([(D("1000"), D("0.78"))], [D("600")]) == D("1.3")
    assert defi.health_factor([(D("1000"), D("0.78"))], []) is None
    assert defi.health_factor([], [D("10")]) == 0


def test_dette_grossit_au_taux_d_emprunt():
    """Même calcul que les dépôts : 1 000 jetons empruntés à 5 % pendant 365 jours -> 1 050."""
    start = date(2025, 1, 1)
    rates = {start: D("0.05")}
    est = defi.estimate({start: D("1000")}, rates, {}, start + timedelta(days=364))
    assert round(est.balance, 6) == D("1050.000000")


def owed(value: str) -> bool:
    """Dette vérifiée de 310, plus au plus un jour d'intérêts (test qui passe minuit)."""
    return D("310") <= D(value) <= D("310.05")


def usd_rate(db_file):
    conn = sqlite3.connect(db_file)
    conn.execute(
        "INSERT INTO fx_rate (rate_date, currency, rate) VALUES (?, 'USD', '1')",
        (TODAY.isoformat(),),
    )
    conn.commit()
    conn.close()


def test_api_emprunt_sante_et_patrimoine_net(client, db_file):
    account, asset, _ = setup_line(client)
    usd_rate(db_file)  # 1 € = 1 $ (fictif) : valeurs en euros connues
    body = {
        "asset_id": asset["id"],
        "chain_id": 42161,
        "market": MARKET,
        "token": TOKEN,
        "quantity": "600",
        "op_date": START.isoformat(),
    }
    out = post(client, "/api/aave/borrows", body)
    [borrow] = out["borrows"]
    assert borrow["apy_today_pct"] == "5.00" and D(borrow["balance"]) > D("600")
    # Seule l'adresse du marché et du jeton part vers Aave, jamais une quantité.
    assert not any("600" in str(r) for r in client.sent if "query" in r)
    [market] = out["markets"]
    hf = D(market["health_factor"])
    # Dépôt ≈ 1 000 $ + intérêts à 3,65 % × 0,78 ÷ (600 $ + intérêts à 5 %) ≈ 1,3 au départ,
    # 1,29 en fin de mois, quand le dépôt a vieilli : la dette court plus vite.
    assert D("1.28") < hf < D("1.32") and market["warning"] is True

    # Remboursement plus gros que la dette : refusé ; remboursement partiel : la santé monte.
    move = {"direction": "remboursement", "quantity": "1000", "op_date": TODAY.isoformat()}
    post(client, f"/api/aave/borrows/{borrow['id']}/moves", move, status=422)
    out = post(client, f"/api/aave/borrows/{borrow['id']}/moves", move | {"quantity": "300"})
    assert D(out["markets"][0]["health_factor"]) > D("2.5")

    # Vérifier : la dette repart du montant lu dans Aave.
    check = {"check_date": TODAY.isoformat(), "quantity": "310"}
    out = post(client, f"/api/aave/borrows/{borrow['id']}/checks", check, status=200)
    # Le serveur compte jusqu'à son « aujourd'hui » : si le test passe minuit, TODAY (fixé à
    # l'import) est déjà la veille et un jour d'intérêts à 5 % s'ajoute (≈ 0,04 $).
    assert owed(out["borrows"][0]["balance"])

    # Patrimoine net, Dettes et Rendements crypto voient l'emprunt.
    debts = client.get("/api/debts").json()
    assert (
        owed(debts["aave"][0]["value_eur"]) and debts["total_eur"] == debts["aave"][0]["value_eur"]
    )
    with db.connect() as conn:
        assert main._debts_total_eur(conn, {"EUR": D("1")}, TODAY) == D("310")
    conn.close()
    overview = client.get("/api/crypto/rewards").json()["overview"]
    assert abs(D(overview["borrow_cost"]) - D("310") * BORROW_APY) <= D("0.02")
    assert abs(D(overview["health_factor"]) - D(out["markets"][0]["health_factor"])) <= D("0.01")

    assert client.delete(f"/api/aave/borrows/{borrow['id']}").status_code == 204
    assert client.get("/api/aave/borrows").json()["borrows"] == []
