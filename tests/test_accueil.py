"""Page de démarrage réglable (D-134) : fond pris dans une liste, mot d'accueil borné, image
personnelle vérifiée et rangée dans le dossier des données. Images FICTIVES (quelques octets)."""

import json
import os

import pytest
from fastapi.testclient import TestClient

from app import profiles, startpage
from app.main import app

JPEG = b"\xff\xd8\xff\xe0" + b"image fictive" * 4 + b"\xff\xd9"
PNG = b"\x89PNG\r\n\x1a\n" + b"image fictive"


@pytest.fixture
def client(db_file):
    with TestClient(app) as c:
        yield c


def _section() -> dict:
    data = json.loads((profiles.DATA_DIR / "reglages.json").read_text(encoding="utf-8"))
    return data["accueil"]


def _envoyer(client, data: bytes):
    return client.put(
        "/api/start/image", content=data, headers={"Content-Type": "application/octet-stream"}
    )


def test_valeurs_par_defaut(client):
    assert client.get("/api/start").json() == {
        "fond": "auto",
        "texte": startpage.DEFAULT_TEXT,
        "texte_defaut": startpage.DEFAULT_TEXT,
        "texte_max": 120,
        "image": None,
    }
    assert client.get("/api/start/image").status_code == 404


def test_fond_choisi_dans_la_liste(client):
    assert client.put("/api/start", json={"fond": "crepuscule"}).json()["fond"] == "crepuscule"
    assert _section() == {"fond": "crepuscule"}
    for refuse in ("rouge", "url(http://exemple.test/x.png)", "../nuit"):
        assert client.put("/api/start", json={"fond": refuse}).status_code in (400, 422)
    assert client.put("/api/start", json={"fond": "nuit", "autre": 1}).status_code == 422
    assert client.get("/api/start").json()["fond"] == "crepuscule"


def test_fond_image_refuse_sans_image(client):
    res = client.put("/api/start", json={"fond": "image"})
    assert res.status_code == 400
    assert "Aucune image" in res.json()["detail"]


def test_mot_d_accueil_borne_et_remis_a_l_origine(client):
    res = client.put("/api/start", json={"texte": "  Bonjour   la famille \n"})
    assert res.json()["texte"] == "Bonjour la famille"
    assert _section() == {"texte": "Bonjour la famille"}
    # Du HTML reste du texte : rangé tel quel, la page l'écrit avec textContent.
    balise = "<b>Bonjour</b>"
    assert client.put("/api/start", json={"texte": balise}).json()["texte"] == balise
    assert client.put("/api/start", json={"texte": "x" * 121}).status_code == 400
    assert client.put("/api/start", json={"texte": "a\x00b"}).status_code == 400
    assert client.put("/api/start", json={"texte": "a\x1bb"}).status_code == 400
    assert client.put("/api/start", json={"texte": "abc\u202edcba"}).status_code == 400
    assert client.put("/api/start", json={"texte": 12}).status_code == 422
    assert client.get("/api/start").json()["texte"] == balise
    # Vide ou null : retour au texte d'origine, plus rien d'écrit dans le fichier.
    assert client.put("/api/start", json={"texte": ""}).json()["texte"] == startpage.DEFAULT_TEXT
    assert "texte" not in _section()
    client.put("/api/start", json={"texte": "Autre"})
    assert client.put("/api/start", json={"texte": None}).json()["texte"] == startpage.DEFAULT_TEXT


def test_un_champ_absent_ne_bouge_pas(client):
    client.put("/api/start", json={"fond": "aube", "texte": "Bonjour"})
    client.put("/api/start", json={"texte": "Salut"})
    assert _section() == {"fond": "aube", "texte": "Salut"}
    client.put("/api/start", json={"fond": "ciel"})
    assert _section() == {"fond": "ciel", "texte": "Salut"}


def test_image_enregistree_servie_puis_retiree(client):
    res = _envoyer(client, JPEG)
    assert res.status_code == 200
    etat = res.json()
    assert etat["fond"] == "image" and isinstance(etat["image"], int)
    fichier = profiles.DATA_DIR / "accueil" / "fond.jpg"
    assert fichier.read_bytes() == JPEG
    assert not list(fichier.parent.glob("*.tmp"))
    servie = client.get("/api/start/image")
    assert servie.content == JPEG
    assert servie.headers["content-type"] == "image/jpeg"
    assert servie.headers["x-content-type-options"] == "nosniff"
    assert servie.headers["cache-control"] == "no-store"
    # Un autre fond peut être repris sans perdre l'image, puis l'image rechoisie.
    assert client.put("/api/start", json={"fond": "nuit"}).json()["image"] == etat["image"]
    assert client.put("/api/start", json={"fond": "image"}).json()["fond"] == "image"
    retiree = client.delete("/api/start/image").json()
    assert retiree["fond"] == "auto" and retiree["image"] is None
    assert not fichier.exists()
    assert client.get("/api/start/image").status_code == 404
    assert client.delete("/api/start/image").status_code == 200  # déjà retirée : sans erreur


def test_retirer_l_image_garde_un_autre_fond(client):
    _envoyer(client, JPEG)
    client.put("/api/start", json={"fond": "dore"})
    assert client.delete("/api/start/image").json()["fond"] == "dore"


@pytest.mark.parametrize(
    "data",
    [
        b"",
        PNG,
        b"<svg xmlns='http://www.w3.org/2000/svg'><script>alert(1)</script></svg>",
        b"\xff\xd8\xff\xe0 coupee avant la fin",
        b"MZ" + b"\x00" * 64,
    ],
)
def test_image_refusee_si_ce_n_est_pas_un_jpeg(client, data):
    res = _envoyer(client, data)
    assert res.status_code == 400
    assert not (profiles.DATA_DIR / "accueil" / "fond.jpg").exists()
    assert client.get("/api/start").json()["fond"] == "auto"


def test_image_trop_lourde_refusee(client):
    lourde = JPEG[:-2] + b"\x00" * startpage.IMAGE_MAX_BYTES + b"\xff\xd9"
    assert _envoyer(client, lourde).status_code == 413
    assert startpage.image_bytes() is None
    with pytest.raises(startpage.StartPageError):  # même sans l'en-tête de taille
        startpage.set_image(lourde)


def test_image_trop_lourde_envoyee_en_morceaux(client):
    """Sans en-tête de taille (envoi en morceaux) : la lecture s'arrête à la limite."""

    def morceaux():
        yield JPEG[:-2]
        for _ in range(4):
            yield b"\x00" * (1024 * 1024)
        yield b"\xff\xd9"

    res = client.put(
        "/api/start/image",
        content=morceaux(),
        headers={"Content-Type": "application/octet-stream"},
    )
    assert res.status_code == 413
    assert startpage.image_bytes() is None


def test_emoji_compose_accepte(client):
    famille = "Bonjour \U0001f468\u200d\U0001f469\u200d\U0001f467"
    assert client.put("/api/start", json={"texte": famille}).json()["texte"] == famille


def test_image_remplacee_change_d_adresse(client):
    premier = _envoyer(client, JPEG).json()["image"]
    autre = JPEG[:-2] + b"suite" + b"\xff\xd9"
    fichier = profiles.DATA_DIR / "accueil" / "fond.jpg"
    ancien = premier - 1000  # horloge parfois peu précise : l'ancienne image est vieillie
    os.utime(fichier, ns=(ancien * 10**6, ancien * 10**6))
    assert _envoyer(client, autre).json()["image"] > ancien
    assert client.get("/api/start/image").content == autre


def test_fichier_abime_revient_au_defaut(client):
    (profiles.DATA_DIR / "reglages.json").write_text(
        json.dumps({"accueil": {"fond": "image", "texte": "x" * 500}}), encoding="utf-8"
    )
    etat = client.get("/api/start").json()
    assert etat["fond"] == "auto"  # fond « image » sans fichier
    assert etat["texte"] == startpage.DEFAULT_TEXT
    (profiles.DATA_DIR / "reglages.json").write_text('{"accueil": [1, 2]}', encoding="utf-8")
    assert client.get("/api/start").json()["fond"] == "auto"


def test_les_autres_reglages_ne_bougent_pas(client):
    client.put("/api/settings", json={"backup_keep": 30})
    client.put("/api/updates", json={"enabled": False})
    client.put("/api/start", json={"fond": "ciel"})
    data = json.loads((profiles.DATA_DIR / "reglages.json").read_text(encoding="utf-8"))
    assert data["reglages"] == {"backup_keep": 30}
    assert data["updates"]["enabled"] is False
    assert data["accueil"] == {"fond": "ciel"}


def test_requete_d_un_autre_site_refusee(client):
    autre_site = {"Sec-Fetch-Site": "cross-site"}
    assert client.get("/api/start/image", headers=autre_site).status_code == 403
    assert client.put("/api/start", json={"fond": "nuit"}, headers=autre_site).status_code == 403
    origine = {"Origin": "http://exemple.test"}
    assert client.put("/api/start", json={"fond": "nuit"}, headers=origine).status_code == 403
    assert client.get("/api/start").json()["fond"] == "auto"


# --- Avec des profils : lecture avant le choix du profil, écriture profil ouvert ----------------


@pytest.fixture
def data_dir(tmp_path, monkeypatch):
    monkeypatch.delenv("PATRIMOINE_DB", raising=False)
    monkeypatch.setattr(profiles, "DATA_DIR", tmp_path)
    profiles.reset_sessions()
    yield tmp_path
    profiles.reset_sessions()


def test_lecture_sans_profil_ouvert_ecriture_refusee(data_dir):
    with TestClient(app) as ouvert:
        assert ouvert.post("/api/profiles", json={"name": "Marie"}).status_code == 201
        assert _envoyer(ouvert, JPEG).status_code == 200
        assert ouvert.put("/api/start", json={"texte": "Bonjour"}).status_code == 200
    with TestClient(app) as sans_profil:
        etat = sans_profil.get("/api/start")
        assert etat.status_code == 200
        assert etat.json()["fond"] == "image" and etat.json()["texte"] == "Bonjour"
        assert sans_profil.get("/api/start/image").content == JPEG
        assert sans_profil.put("/api/start", json={"fond": "nuit"}).status_code == 401
        assert _envoyer(sans_profil, JPEG).status_code == 401
        assert sans_profil.delete("/api/start/image").status_code == 401
        assert sans_profil.get("/api/start").json()["fond"] == "image"
