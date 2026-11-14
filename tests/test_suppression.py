"""Supprimer un compte ou un actif qui a des opérations (D-074). Données FICTIVES."""

from fastapi.testclient import TestClient

from app.main import app


def _setup(client):
    pea = client.post("/api/accounts", json={"name": "PEA fictif", "kind": "pea"}).json()
    cto = client.post("/api/accounts", json={"name": "CTO fictif", "kind": "cto"}).json()
    usd = client.post(
        "/api/accounts", json={"name": "CTO dollars", "kind": "cto", "currency": "USD"}
    ).json()
    etf = client.post(
        "/api/assets", json={"name": "ETF fictif", "asset_class": "etf", "ticker": "AAA.PA"}
    ).json()
    autre = client.post(
        "/api/assets", json={"name": "ETF bis", "asset_class": "etf", "ticker": "BBB.PA"}
    ).json()

    def op(account, asset, op_type, qty, day):
        res = client.post(
            "/api/operations",
            json={
                "account_id": account["id"],
                "asset_id": asset["id"],
                "op_date": day,
                "op_type": op_type,
                "quantity": qty,
                "unit_price": "10",
                "fees": "0",
            },
        )
        assert res.status_code == 201, res.text

    op(pea, etf, "achat", "5", "2025-01-02")
    op(pea, etf, "vente", "2", "2025-02-03")
    return pea, cto, usd, etf, autre


def test_refus_sans_choix_puis_suppression_des_operations(db_file):
    with TestClient(app) as client:
        pea, *_ = _setup(client)
        res = client.delete(f"/api/accounts/{pea['id']}")
        assert res.status_code == 409 and "2 opération" in res.json()["detail"]
        assert client.delete(f"/api/accounts/{pea['id']}?operations=supprimer").status_code == 204
        assert client.get("/api/operations").json() == []
        assert [a["name"] for a in client.get("/api/accounts").json()] == [
            "CTO fictif",
            "CTO dollars",
        ]
    # Sauvegarde faite juste avant, à côté de la base.
    assert list((db_file.parent / "sauvegardes").glob("*avant-suppression*.db"))


def test_deplacement_vers_un_autre_compte(db_file):
    with TestClient(app) as client:
        pea, cto, usd, *_ = _setup(client)
        bad = client.delete(f"/api/accounts/{pea['id']}?operations=deplacer&vers={usd['id']}")
        assert bad.status_code == 422  # devise différente
        assert client.delete(f"/api/accounts/{pea['id']}?operations=deplacer").status_code == 422
        res = client.delete(f"/api/accounts/{pea['id']}?operations=deplacer&vers={cto['id']}")
        assert res.status_code == 204
        ops = client.get("/api/operations").json()
        assert {o["account_id"] for o in ops} == {cto["id"]} and len(ops) == 2


def test_deplacement_d_actif(db_file):
    with TestClient(app) as client:
        pea, cto, usd, etf, autre = _setup(client)
        client.post(
            "/api/operations",
            json={
                "account_id": pea["id"],
                "asset_id": autre["id"],
                "op_date": "2025-03-01",
                "op_type": "achat",
                "quantity": "1",
                "unit_price": "10",
                "fees": "0",
            },
        )
        res = client.delete(f"/api/assets/{autre['id']}?operations=deplacer&vers={etf['id']}")
        assert res.status_code == 204  # deux lignes fusionnées : les quantités s'additionnent
        assert len([a for a in client.get("/api/assets").json() if a["name"] == "ETF bis"]) == 0
        # Vers un actif neuf : achat, vente et achat suivent.
        neuf = client.post(
            "/api/assets", json={"name": "ETF neuf", "asset_class": "etf", "ticker": "CCC.PA"}
        ).json()
        res = client.delete(f"/api/assets/{etf['id']}?operations=deplacer&vers={neuf['id']}")
        assert res.status_code == 204
        assert {o["asset_id"] for o in client.get("/api/operations").json()} == {neuf["id"]}


def _wallet(client):
    """Plateforme crypto qui envoie une partie de ses titres vers un wallet sans opération."""
    a = client.post("/api/accounts", json={"name": "Plateforme fictive", "kind": "crypto"}).json()
    b = client.post("/api/accounts", json={"name": "Wallet fictif", "kind": "crypto"}).json()
    c = client.post("/api/accounts", json={"name": "Ledger fictif", "kind": "crypto"}).json()
    asset = client.post(
        "/api/assets",
        json={"name": "Crypto fictive", "asset_class": "crypto", "coingecko_id": "fictif"},
    ).json()
    trade = {"account_id": a["id"], "asset_id": asset["id"], "unit_price": "100"}
    for body in (
        {"op_date": "2025-01-02", "op_type": "achat", "quantity": "2"},
        {"op_date": "2025-01-05", "op_type": "transfert_sortant", "quantity": "0.5",
         "to_account_id": b["id"]},
    ):  # fmt: skip
        assert client.post("/api/operations", json={**trade, **body}).status_code == 201
    return a, b, c


def _held(client):
    positions = client.get("/api/portfolio").json()["positions"]
    return {(p["account"], p["quantity"]) for p in positions}


def test_wallet_alimente_par_transfert_demande_un_choix(db_file):
    """Un compte qui n'a reçu que des transferts n'est pas vide (D-085)."""
    with TestClient(app) as client:
        _, b, _ = _wallet(client)
        res = client.delete(f"/api/accounts/{b['id']}")
        assert res.status_code == 409 and "1 opération" in res.json()["detail"]
        # Supprimer : le transfert reste, ses titres sortent de l'app.
        assert client.delete(f"/api/accounts/{b['id']}?operations=supprimer").status_code == 204
        [_, out] = client.get("/api/operations").json()
        assert out["op_type"] == "transfert_sortant" and out["to_account_id"] is None
        assert _held(client) == {("Plateforme fictive", "1.5")}


def test_wallet_deplace_vers_un_autre_compte(db_file):
    with TestClient(app) as client:
        a, b, c = _wallet(client)
        res = client.delete(f"/api/accounts/{b['id']}?operations=deplacer&vers={c['id']}")
        assert res.status_code == 204
        assert _held(client) == {("Plateforme fictive", "1.5"), ("Ledger fictif", "0.5")}


def test_wallet_rendu_au_compte_de_depart(db_file):
    """Déplacé vers le compte d'où partait le transfert : le transfert disparaît."""
    with TestClient(app) as client:
        a, b, _ = _wallet(client)
        res = client.delete(f"/api/accounts/{b['id']}?operations=deplacer&vers={a['id']}")
        assert res.status_code == 204
        assert [o["op_type"] for o in client.get("/api/operations").json()] == ["achat"]
        assert _held(client) == {("Plateforme fictive", "2")}
