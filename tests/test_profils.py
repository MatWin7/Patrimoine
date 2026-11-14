"""Profils (D-098) : une base par personne, choisie par un jeton de session, code facultatif.

Données fictives uniquement ; tout se passe dans un dossier temporaire, jamais dans data/.
"""

import json
import sqlite3
import sys
import threading
from datetime import timedelta

import pytest
from fastapi.testclient import TestClient

from app import db, main, migrate, profiles
from app.main import app

PEA = {"name": "PEA fictif", "kind": "pea"}


@pytest.fixture
def data_dir(tmp_path, monkeypatch):
    """Dossier data/ jetable, sans PATRIMOINE_DB : le mode profils peut s'activer."""
    monkeypatch.delenv("PATRIMOINE_DB", raising=False)
    monkeypatch.setattr(profiles, "DATA_DIR", tmp_path)
    profiles.reset_sessions()
    yield tmp_path
    profiles.reset_sessions()


def names(client) -> list[str]:
    return [a["name"] for a in client.get("/api/accounts").json()]


def test_sans_profil_l_app_reste_sur_la_base_unique(data_dir):
    with TestClient(app) as client:
        assert client.get("/api/profiles").json()["enabled"] is False
        assert client.post("/api/accounts", json=PEA).status_code == 201
    assert (data_dir / "patrimoine.db").is_file()
    assert not (data_dir / "profils.json").exists()


def test_premier_profil_garde_la_base_actuelle_en_principal(data_dir):
    with TestClient(app) as client:
        client.post("/api/accounts", json=PEA)
        res = client.post("/api/profiles", json={"name": "Marie"})
        assert res.status_code == 201
        assert res.json()["id"] == "marie"
        # Le navigateur qui a créé le profil reste sur sa base, ouverte d'office.
        state = client.get("/api/profiles").json()
        assert state["current"]["id"] == "principal"
        assert [p["name"] for p in state["profiles"]] == ["Principal", "Marie"]
        assert names(client) == ["PEA fictif"]
    registry = json.loads((data_dir / "profils.json").read_text(encoding="utf-8"))
    assert [p["file"] for p in registry["profiles"]] == ["patrimoine.db", "profils/marie.db"]
    assert (data_dir / "profils" / "marie.db").is_file()


def test_deux_profils_ne_voient_jamais_les_comptes_l_un_de_l_autre(data_dir):
    with TestClient(app) as a, TestClient(app) as b:
        a.post("/api/profiles", json={"name": "Marie"})
        assert a.post("/api/profiles/marie/open", json={}).status_code == 200
        a.post("/api/accounts", json={"name": "Compte de Marie", "kind": "cto"})
        b.post("/api/profiles/principal/open", json={})
        b.post("/api/accounts", json={"name": "Compte principal", "kind": "cto"})
        assert names(a) == ["Compte de Marie"]
        assert names(b) == ["Compte principal"]


def test_requetes_simultanees_sur_deux_profils(data_dir):
    """La base est choisie par requête (ContextVar), pas par une variable globale du serveur."""
    with TestClient(app) as a, TestClient(app) as b:
        a.post("/api/profiles", json={"name": "Marie"})
        a.post("/api/profiles/marie/open", json={})
        b.post("/api/profiles/principal/open", json={})
        errors = []

        def burst(client, name):
            for i in range(15):
                res = client.post("/api/accounts", json={"name": f"{name} {i}", "kind": "cto"})
                if res.status_code != 201:
                    errors.append(res.text)

        threads = [threading.Thread(target=burst, args=c) for c in ((a, "M"), (b, "P"))]
        for t in threads:
            t.start()
        for t in threads:
            t.join()
        assert errors == []
        assert all(n.startswith("M ") for n in names(a)) and len(names(a)) == 15
        assert all(n.startswith("P ") for n in names(b)) and len(names(b)) == 15


def test_sans_session_seul_le_choix_du_profil_repond(data_dir):
    profiles.create("Marie")
    with TestClient(app) as client:
        res = client.get("/api/accounts")
        assert res.status_code == 401
        assert res.headers["x-profile"] == "required"
        assert client.post("/api/accounts", json=PEA).status_code == 401
        assert client.post("/api/profiles", json={"name": "Intrus"}).status_code == 401
        assert client.get("/api/health").status_code == 200  # le lanceur attend un 200
        assert client.get("/api/profiles").json()["current"] is None
    assert [p.id for p in profiles.load()] == ["principal", "marie"]


def test_jeton_invente_ou_nom_de_profil_dans_le_cookie_refuse(data_dir):
    profiles.create("Marie", code="1234")
    with TestClient(app) as client:
        for value in ("marie", "principal", "x" * 43):
            client.cookies.set(main.SESSION_COOKIE, value)
            assert client.get("/api/accounts").status_code == 401


def test_code_juste_faux_puis_blocage(data_dir):
    profiles.create("Marie", code="2468")
    with TestClient(app) as client:
        assert client.post("/api/profiles/marie/open", json={}).status_code == 400
        assert client.post("/api/profiles/marie/open", json={"code": "0000"}).status_code == 400
        assert client.get("/api/accounts").status_code == 401
        assert client.post("/api/profiles/marie/open", json={"code": "2468"}).status_code == 200
        assert client.get("/api/accounts").status_code == 200
        for _ in range(profiles.MAX_FAILURES):
            client.post("/api/profiles/marie/open", json={"code": "9999"})
        bloque = client.post("/api/profiles/marie/open", json={"code": "2468"})
        assert bloque.status_code == 429


def test_le_code_n_est_jamais_garde_ni_renvoye_en_clair(data_dir):
    profiles.create("Marie", code="code-secret-fictif")
    text = (data_dir / "profils.json").read_text(encoding="utf-8")
    assert "code-secret-fictif" not in text
    assert json.loads(text)["profiles"][1]["code"]["hash"]
    with TestClient(app) as client:
        body = client.get("/api/profiles").text
    assert "hash" not in body and "salt" not in body and "profils/" not in body
    if sys.platform != "win32":  # Windows : pas de droits Unix, le dossier de l'utilisateur protège
        assert (data_dir / "profils.json").stat().st_mode & 0o077 == 0
        assert (data_dir / "profils" / "marie.db").stat().st_mode & 0o077 == 0


def test_seul_le_profil_ouvert_change_son_code(data_dir):
    profiles.create("Marie", code="1111")
    with TestClient(app) as client:
        client.post("/api/profiles/principal/open", json={})
        autre = client.put("/api/profiles/marie/code", json={"current": "", "new": "2222"})
        assert autre.status_code == 403
        client.post("/api/profiles/marie/open", json={"code": "1111"})
        faux = client.put("/api/profiles/marie/code", json={"current": "0000", "new": "2222"})
        assert faux.status_code == 400
        ok = client.put("/api/profiles/marie/code", json={"current": "1111", "new": "2222"})
        assert ok.status_code == 200
        client.post("/api/profiles/lock")
        assert client.post("/api/profiles/marie/open", json={"code": "2222"}).status_code == 200


def test_profil_demo_refuse_un_code(data_dir):
    demo = profiles.create_demo()
    with pytest.raises(profiles.ProfileError):
        profiles.set_code(demo.id, "1234")
    assert not profiles.get(demo.id).has_code


def test_photo_du_profil(data_dir):
    import base64

    jpeg = "data:image/jpeg;base64," + base64.b64encode(b"\xff\xd8\xff" + b"0" * 50).decode()
    profiles.create("Marie")
    assert profiles.set_photo("marie", jpeg).public()["photo"] == jpeg
    assert profiles.get("marie").photo == jpeg
    for mauvaise in (
        "data:image/svg+xml;base64,PHN2Zz4=",  # type non accepté
        "data:image/png;base64," + base64.b64encode(b"pas un png").decode(),  # en-tête faux
        "data:image/jpeg;base64," + "A" * 70_000,  # trop gros
        "https://exemple.fr/moi.jpg",
    ):
        with pytest.raises(profiles.ProfileError):
            profiles.set_photo("marie", mauvaise)
    assert profiles.set_photo("marie", None).photo is None


def test_code_oublie_retire_dans_le_terminal(data_dir, capsys):
    profiles.create("Marie", code="1357")
    profiles.main(["sans-code", "marie"])
    assert "Code retiré" in capsys.readouterr().out
    assert not profiles.get("marie").has_code


def test_verrouiller_et_inactivite(data_dir, monkeypatch):
    profiles.create("Marie")
    with TestClient(app) as client:
        client.post("/api/profiles/marie/open", json={})
        assert client.post("/api/profiles/lock").status_code == 204
        assert client.get("/api/accounts").status_code == 401
        client.post("/api/profiles/marie/open", json={})
        later = profiles._now() + profiles.idle_limit() + timedelta(seconds=1)
        monkeypatch.setattr(profiles, "_now", lambda: later)
        assert client.get("/api/accounts").status_code == 401


def test_tutoriel_vu_par_profil(data_dir):
    profiles.create("Marie")
    with TestClient(app) as client:
        client.post("/api/profiles/marie/open", json={})
        refus = client.patch("/api/profiles/principal", json={"tutorial_done": True})
        assert refus.status_code == 403
        res = client.patch("/api/profiles/marie", json={"tutorial_done": True, "name": "Marie D."})
        assert res.json() == {**res.json(), "tutorial_done": True, "name": "Marie D."}
    assert not profiles.get("principal").tutorial_done


def test_noms_et_identifiants(data_dir):
    assert profiles.slug("Élodie M.") == "elodie-m"
    assert profiles.slug("!!!") == "profil"
    profiles.create("Élodie M.")
    assert profiles.create("Patrimoine").id == "patrimoine-2"  # nom de la base d'origine
    with pytest.raises(profiles.ProfileError):
        profiles.create("élodie m.")
    with pytest.raises(profiles.ProfileError):
        profiles.create("Paul", code="12")  # code trop court


def test_chemin_hors_de_data_refuse(data_dir):
    profiles.create("Marie")
    registry = json.loads((data_dir / "profils.json").read_text(encoding="utf-8"))
    registry["profiles"][1]["file"] = "../../ailleurs.db"
    (data_dir / "profils.json").write_text(json.dumps(registry), encoding="utf-8")
    with pytest.raises(RuntimeError, match="invalide"):
        profiles.load()


def test_patrimoine_db_force_une_base_unique(data_dir, monkeypatch, tmp_path):
    profiles.create("Marie")
    monkeypatch.setenv("PATRIMOINE_DB", str(tmp_path / "debogage.db"))
    with TestClient(app) as client:
        assert client.get("/api/profiles").json()["enabled"] is False
        assert client.get("/api/accounts").status_code == 200
        assert client.post("/api/profiles", json={"name": "Paul"}).status_code == 409


def test_sans_profil_choisi_aucune_base_par_defaut(data_dir):
    """Hors requête (ou requête sans profil) : erreur, jamais la base d'un autre en silence."""
    profiles.create("Marie")
    with pytest.raises(db.NoProfileSelected):
        db.db_path()


def test_profil_en_retard_de_version_n_empeche_pas_les_autres(data_dir):
    profiles.create("Marie")
    with sqlite3.connect(data_dir / "profils" / "marie.db") as conn:
        conn.executescript(
            "ALTER TABLE account DROP COLUMN abroad; ALTER TABLE account DROP COLUMN custody;"
            f"PRAGMA user_version = {db.SCHEMA_VERSION - 1};"
        )
    with TestClient(app) as client:
        client.post("/api/profiles/marie/open", json={})
        res = client.get("/api/accounts")
        assert res.status_code == 503 and "migrate" in res.text
        client.post("/api/profiles/principal/open", json={})
        assert client.get("/api/accounts").status_code == 200


def test_migration_et_sauvegarde_de_chaque_profil(data_dir, monkeypatch, capsys):
    profiles.create("Marie")
    assert profiles.databases() == [data_dir / "patrimoine.db", data_dir / "profils/marie.db"]
    migrate.main()
    out = capsys.readouterr().out
    assert out.count("déjà en version") == 2


def test_apercu_d_import_en_fil_garde_le_profil(data_dir):
    """L'aperçu en continu lit le CSV dans un autre fil : il doit voir la base du profil."""
    profiles.create("Marie")
    with TestClient(app) as client:
        client.post("/api/profiles/marie/open", json={})
        account = client.post("/api/accounts", json={"name": "CTO", "kind": "cto"}).json()["id"]
        text = "date;quantite\n2024-01-02;1\n"
        res = client.post("/api/import/preview/stream", json={"text": text, "account_id": account})
        assert "Aperçu impossible pour l'instant." not in res.text
        assert '"result"' in res.text or '"error"' in res.text


def test_memoires_du_processus_separees_par_base(tmp_path):
    """Prix de la veille, historiques complétés : rangés par base, les id d'actif se répètent
    d'un profil à l'autre (l'actif 1 de Marie n'est pas celui de Paul)."""
    a, b = sqlite3.connect(tmp_path / "a.db"), sqlite3.connect(tmp_path / "b.db")
    try:
        assert db.conn_key(a) != db.conn_key(b)
        assert db.conn_key(a).endswith("a.db")
    finally:
        a.close()
        b.close()


def test_onglet_reste_sur_l_ancien_profil_refuse(data_dir):
    """Deux onglets du même navigateur partagent le cookie : l'onglet qui croit être sur
    Marie après l'ouverture de Principal ailleurs reçoit 409, rien n'est écrit chez Principal."""
    profiles.create("Marie")
    with TestClient(app) as client:
        client.post("/api/profiles/marie/open", json={})
        client.post("/api/profiles/principal/open", json={})  # l'autre onglet
        res = client.post("/api/accounts", json=PEA, headers={"X-Profile-Id": "marie"})
        assert res.status_code == 409 and res.headers["x-profile"] == "changed"
        assert names(client) == []
        ok = client.get("/api/accounts", headers={"X-Profile-Id": "principal"})
        assert ok.status_code == 200


# --- Exporter, restaurer, supprimer (étape 3) ---------------------------------------------


def test_exporter_puis_restaurer_un_profil(data_dir):
    profiles.create("Marie")
    with TestClient(app) as client:
        client.post("/api/profiles/marie/open", json={})
        client.post("/api/accounts", json={"name": "Compte fictif", "kind": "cto"})
        res = client.get("/api/profiles/marie/export")
        assert res.status_code == 200
        assert res.content.startswith(b"SQLite format 3\x00")
        assert "patrimoine-marie-" in res.headers["content-disposition"]
        restored = client.post("/api/profiles/restore?name=Marie copie", content=res.content)
        assert restored.status_code == 201
        new_id = restored.json()["id"]
        client.post(f"/api/profiles/{new_id}/open", json={})
        assert names(client) == ["Compte fictif"]


def test_exporter_la_base_d_un_autre_profil_refuse(data_dir):
    profiles.create("Marie", code="1234")
    with TestClient(app) as client:
        client.post("/api/profiles/principal/open", json={})
        assert client.get("/api/profiles/marie/export").status_code == 403


def _base(path, sql):
    with sqlite3.connect(path) as conn:
        conn.executescript(sql)
    conn.close()
    return path.read_bytes()


@pytest.mark.parametrize(
    "piege",
    [
        "CREATE TRIGGER t AFTER INSERT ON account BEGIN DELETE FROM account; END;",
        "CREATE VIEW v AS SELECT 1;",
        "CREATE TABLE inconnue (x);",
    ],
)
def test_restauration_piegee_refusee(data_dir, tmp_path, piege):
    """Un .db reçu d'ailleurs peut contenir du code SQL qui s'exécute tout seul : refusé."""
    path = tmp_path / "recue.db"
    db.init_db(path)
    data = _base(path, piege)
    profiles.create("Marie")
    with TestClient(app) as client:
        client.post("/api/profiles/principal/open", json={})
        res = client.post("/api/profiles/restore?name=Piège", content=data)
        assert res.status_code == 400
    assert [p.id for p in profiles.load()] == ["principal", "marie"]
    assert not list((data_dir / "profils").glob("piege*"))
    assert not list(data_dir.glob(".restauration-*"))


def test_restauration_d_un_fichier_quelconque_refusee(data_dir):
    profiles.create("Marie")
    with TestClient(app) as client:
        client.post("/api/profiles/principal/open", json={})
        res = client.post("/api/profiles/restore?name=Texte", content=b"pas une base")
        assert res.status_code == 400


def test_restauration_d_une_ancienne_version_mise_a_jour(data_dir, tmp_path):
    path = tmp_path / "ancienne.db"
    db.init_db(path)
    with sqlite3.connect(path) as conn:
        conn.executescript(
            "ALTER TABLE account DROP COLUMN abroad; ALTER TABLE account DROP COLUMN custody;"
            f"PRAGMA user_version = {db.SCHEMA_VERSION - 1};"
        )
    conn.close()
    profile = profiles.restore("Ancienne", path.read_bytes())
    with sqlite3.connect(profiles.db_file(profile)) as conn:
        assert db.schema_version(conn) == db.SCHEMA_VERSION
    conn.close()


def test_supprimer_un_profil_va_dans_la_corbeille(data_dir):
    profiles.create("Marie", code="1357")
    with TestClient(app) as client:
        client.post("/api/profiles/principal/open", json={})
        sans_nom = client.request("DELETE", "/api/profiles/marie", json={"confirm": "Mari"})
        assert sans_nom.status_code == 400
        sans_code = client.request("DELETE", "/api/profiles/marie", json={"confirm": "Marie"})
        assert sans_code.status_code == 400
        ok = client.request(
            "DELETE", "/api/profiles/marie", json={"confirm": "marie", "code": "1357"}
        )
        assert ok.status_code == 204
        assert [p["id"] for p in client.get("/api/profiles").json()["profiles"]] == ["principal"]
        dernier = client.request("DELETE", "/api/profiles/principal", json={"confirm": "Principal"})
        assert dernier.status_code == 400
    assert not (data_dir / "profils" / "marie.db").exists()
    assert len(list((data_dir / "corbeille").glob("marie-*.db"))) == 1
    assert len(list((data_dir / "sauvegardes").glob("marie-avant-suppression-*.db"))) == 1


def test_supprimer_son_propre_profil_ferme_la_session(data_dir):
    profiles.create("Marie")
    with TestClient(app) as client:
        client.post("/api/profiles/marie/open", json={})
        res = client.request("DELETE", "/api/profiles/marie", json={"confirm": "Marie"})
        assert res.status_code == 204
        assert client.get("/api/accounts").status_code == 401


# --- Profil Démo (étape 4) ------------------------------------------------------------------


def test_profil_demo_au_premier_lancement_reste_seul(data_dir):
    with TestClient(app) as client:
        res = client.post("/api/profiles/demo")
        assert res.status_code == 201
        assert res.json()["demo"] is True and res.json()["name"] == "Démo"
        state = client.get("/api/profiles").json()
        assert state["current"]["id"] == profiles.DEMO_ID
        assert [p["name"] for p in state["profiles"]] == ["Démo"]  # pas de « Principal » vide
        assert "PEA démo" in names(client)


def test_vrai_profil_cree_depuis_la_demo(data_dir):
    with TestClient(app) as client:
        client.post("/api/profiles/demo")
        assert client.post("/api/profiles", json={"name": "Marie"}).status_code == 201
        client.post("/api/profiles/marie/open", json={})
        assert names(client) == []
        state = client.get("/api/profiles").json()
        assert [p["name"] for p in state["profiles"]] == ["Démo", "Marie"]


def test_demo_garde_principal_si_la_base_contient_des_donnees(data_dir):
    with TestClient(app) as client:
        client.post("/api/accounts", json=PEA)
        client.post("/api/profiles/demo")
        state = client.get("/api/profiles").json()
        assert state["current"]["id"] == "principal"
        assert [p["name"] for p in state["profiles"]] == ["Principal", "Démo"]
        assert names(client) == ["PEA fictif"]  # la base existante n'est pas touchée


def test_recreer_le_profil_demo_le_remet_a_neuf(data_dir):
    profiles.create_demo()
    with TestClient(app) as client:
        client.post(f"/api/profiles/{profiles.DEMO_ID}/open", json={})
        client.post("/api/accounts", json={"name": "Ajout de test", "kind": "cto"})
        assert "Ajout de test" in names(client)
        assert client.post("/api/profiles/demo").status_code == 201
        assert "Ajout de test" not in names(client)
        assert "PEA démo" in names(client)
    assert [p.id for p in profiles.load()].count(profiles.DEMO_ID) == 1


def test_premier_lancement_base_vide_devient_le_profil_sans_principal(data_dir):
    with TestClient(app) as client:
        res = client.post("/api/profiles", json={"name": "Marie", "adopt_empty": True})
        assert res.status_code == 201
        assert res.json()["id"] == "marie"
        state = client.get("/api/profiles").json()
        assert state["current"]["id"] == "marie"
        assert [p["name"] for p in state["profiles"]] == ["Marie"]
        client.post("/api/accounts", json=PEA)
    registry = json.loads((data_dir / "profils.json").read_text(encoding="utf-8"))
    assert [p["file"] for p in registry["profiles"]] == ["patrimoine.db"]
    assert not (data_dir / "profils").exists()


def test_premier_lancement_avec_code(data_dir):
    with TestClient(app) as client:
        client.post("/api/profiles", json={"name": "Marie", "code": "abcd", "adopt_empty": True})
        assert client.get("/api/profiles").json()["current"]["has_code"] is True
    with TestClient(app) as other:
        assert other.get("/api/accounts").status_code == 401


def test_base_deja_remplie_garde_principal_meme_avec_adopt_empty(data_dir):
    with TestClient(app) as client:
        client.post("/api/accounts", json=PEA)
        client.post("/api/profiles", json={"name": "Marie", "adopt_empty": True})
        state = client.get("/api/profiles").json()
        assert [p["name"] for p in state["profiles"]] == ["Principal", "Marie"]
        assert names(client) == ["PEA fictif"]
