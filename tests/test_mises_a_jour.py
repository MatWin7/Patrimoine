"""Avis de mise à jour (D-129) : aucune requête sans accord, une par jour au plus, rien envoyé,
réponse de GitHub traitée comme non fiable. Numéros de version FICTIFS."""

import json
from datetime import date

import httpx2
import pytest
from fastapi.testclient import TestClient

from app import updates
from app.main import app, http_client
from tests.conftest import fake_client

TODAY = date(2026, 11, 14)


class FakeGitHub:
    """Faux GitHub : compte et garde les requêtes reçues."""

    def __init__(self, status=200, body=None):
        self.status = status
        self.body = body if body is not None else {"tag_name": "v9.8.7"}
        self.requests: list[httpx2.Request] = []

    def __call__(self, request: httpx2.Request) -> httpx2.Response:
        self.requests.append(request)
        return httpx2.Response(self.status, json=self.body)

    def client(self):
        return fake_client(self)


@pytest.fixture(autouse=True)
def oubli_du_dernier_essai(monkeypatch):
    """La limite d'un appel par jour est aussi gardée en mémoire : repartir de zéro."""
    monkeypatch.setattr(updates, "_last_attempt", None)


@pytest.fixture
def github():
    fake = FakeGitHub()
    app.dependency_overrides[http_client] = fake.client
    return fake


@pytest.fixture
def client(db_file):
    with TestClient(app) as c:
        yield c


@pytest.mark.parametrize(
    ("text", "expected"),
    [
        ("v0.2.0", (0, 2, 0)),
        ("1.10.3", (1, 10, 3)),
        ("v0.2.0-rc.1", None),  # préversion : jamais proposée
        ("v1.2", None),
        ("v1.2.3<script>", None),
        ("v99999.0.0", None),
        ("v01.2.3", None),  # zéro en tête : le lien construit ne serait pas celui de l'étiquette
        (" v1.2.3", None),
        ("v1.2.3\n", None),
        ("v\u0661.\u0662.\u0663", None),  # chiffres arabes-indiens : ASCII seulement
        (None, None),
    ],
)
def test_numero_de_version(text, expected):
    assert updates.parse_version(text) == expected


def test_comparaison_des_versions():
    assert updates.is_newer("0.10.0", "0.9.1")  # numérique, pas alphabétique
    assert not updates.is_newer("0.2.0", "0.2.0")
    assert not updates.is_newer("0.1.9", "0.2.0")
    assert not updates.is_newer("0.2.0", "0.2.0-rc.1")
    assert not updates.is_newer(None, "0.2.0")


def test_aucune_requete_avant_la_reponse(client, github):
    data = client.get("/api/updates").json()
    assert data["enabled"] is None  # la page pose la question
    assert data["newer"] is False
    assert github.requests == []


def test_refus_aucune_requete(client, github):
    assert client.put("/api/updates", json={"enabled": False}).json()["enabled"] is False
    client.get("/api/updates")
    assert github.requests == []


def test_accord_une_requete_par_jour_sans_rien_envoyer(client, github):
    client.put("/api/updates", json={"enabled": True})
    data = client.get("/api/updates").json()
    client.get("/api/updates")  # seconde ouverture de page le même jour : pas de nouvel appel
    assert len(github.requests) == 1
    request = github.requests[0]
    assert str(request.url) == "https://api.github.com/repos/MatWin7/Patrimoine/releases/latest"
    assert request.method == "GET" and request.content == b""
    assert data["newer"] and data["latest"] == "9.8.7"
    assert data["url"] == "https://github.com/MatWin7/Patrimoine/releases/tag/v9.8.7"


def test_verification_du_lendemain(github, tmp_path):
    updates.set_enabled(True)
    updates.check(github.client(), TODAY)
    updates.check(github.client(), date(2026, 11, 15))
    assert len(github.requests) == 2


def test_verifier_maintenant_passe_outre_la_limite_et_le_refus(client, github):
    client.put("/api/updates", json={"enabled": False})
    data = client.post("/api/updates/check").json()
    data = client.post("/api/updates/check").json()
    assert len(github.requests) == 2
    assert data["enabled"] is False and data["latest"] == "9.8.7"


def test_choix_garde_dans_le_dossier_des_donnees(client, github):
    from app import profiles

    client.put("/api/updates", json={"enabled": True})
    saved = json.loads((profiles.DATA_DIR / "reglages.json").read_text(encoding="utf-8"))
    assert saved["updates"]["enabled"] is True


def test_reglages_abimes_question_reposee(client, github):
    from app import profiles

    (profiles.DATA_DIR / "reglages.json").write_text("{pas du json", encoding="utf-8")
    assert client.get("/api/updates").json()["enabled"] is None
    assert github.requests == []


def test_choix_strictement_booleen(client):
    assert client.put("/api/updates", json={"enabled": "oui"}).status_code == 422


def test_aucune_release_publiee(github):
    github.status = 404
    updates.set_enabled(True)
    settings = updates.check(github.client(), TODAY)
    assert settings.latest is None and settings.error is None
    assert updates.status(settings)["newer"] is False


@pytest.mark.parametrize(
    ("status", "body"),
    [
        (500, {}),
        (200, {"pas": "de tag"}),
        (200, {"tag_name": "https://piege.example/v9.9.9"}),
        (200, {"tag_name": "v9.9.9", "prerelease": True}),
        (200, ["liste"]),
    ],
)
def test_reponse_inattendue_ignoree(github, status, body):
    github.status, github.body = status, body
    updates.set_enabled(True)
    settings = updates.check(github.client(), TODAY)
    assert settings.error and settings.latest is None
    assert updates.status(settings)["url"] is None
    # L'échec compte comme la vérification du jour : pas d'appel à chaque ouverture de page.
    updates.check(github.client(), TODAY)
    assert len(github.requests) == 1


def test_reseau_coupe(client):
    client.put("/api/updates", json={"enabled": True})
    data = client.get("/api/updates").json()  # client hors ligne de conftest
    assert data["error"] == "GitHub est injoignable" and data["newer"] is False


def test_ancienne_version_vue_garde_apres_un_echec(github):
    updates.set_enabled(True)
    updates.check(github.client(), TODAY)
    github.status = 500
    settings = updates.check(github.client(), date(2026, 11, 15))
    assert settings.latest == "9.8.7" and settings.error


def test_reponse_trop_grande_refusee(github, monkeypatch):
    monkeypatch.setattr(updates, "MAX_RESPONSE_BYTES", 10)
    updates.set_enabled(True)
    assert updates.check(github.client(), TODAY).error == "réponse de GitHub trop grande"


def test_ecriture_refusee_depuis_un_autre_site(client):
    res = client.put(
        "/api/updates", json={"enabled": True}, headers={"Origin": "https://piege.example"}
    )
    assert res.status_code == 403


def test_numero_range_sous_sa_forme_canonique(github):
    from app import profiles

    reglages = {"updates": {"enabled": True, "checked": "2026-11-14", "latest": "v9.8.7"}}
    (profiles.DATA_DIR / "reglages.json").write_text(json.dumps(reglages), encoding="utf-8")
    settings = updates.load()
    assert settings.latest == "9.8.7"
    assert updates.status(settings)["url"].endswith("/tag/v9.8.7")


def _routes(table):
    """Faux serveur qui répond selon l'adresse demandée ; garde les adresses reçues."""
    seen = []

    def handler(request):
        seen.append(str(request.url))
        status, headers, body = table[str(request.url)]
        return httpx2.Response(status, headers=headers, json=body)

    return handler, seen


def test_depot_transfere_redirection_suivie_vers_l_api():
    nouvelle = "https://api.github.com/repositories/42/releases/latest"
    handler, seen = _routes(
        {
            updates.LATEST_URL: (301, {"Location": nouvelle}, {}),
            nouvelle: (200, {}, {"tag_name": "v9.8.7"}),
        }
    )
    assert updates.fetch_latest(fake_client(handler)) == "9.8.7"
    assert seen == [updates.LATEST_URL, nouvelle]


@pytest.mark.parametrize(
    "location",
    [
        "https://piege.example/releases/latest",
        "http://api.github.com/repos/x/y/releases/latest",  # pas https
        "https://api.github.com:8443/repos/x/y/releases/latest",
        "",
    ],
)
def test_redirection_hors_de_l_api_refusee(location):
    handler, seen = _routes({updates.LATEST_URL: (302, {"Location": location}, {})})
    with pytest.raises(updates.UpdateError):
        updates.fetch_latest(fake_client(handler))
    assert seen == [updates.LATEST_URL]  # rien n'est demandé ailleurs


def test_boucle_de_redirections_arretee():
    handler, seen = _routes({updates.LATEST_URL: (301, {"Location": updates.LATEST_URL}, {})})
    with pytest.raises(updates.UpdateError, match="trop de redirections"):
        updates.fetch_latest(fake_client(handler))
    assert len(seen) == updates.MAX_REDIRECTS + 1


def test_une_par_jour_meme_si_les_reglages_ne_s_ecrivent_pas(github, monkeypatch):
    updates.set_enabled(True)

    def disque_plein(settings):
        raise OSError("disque plein")

    monkeypatch.setattr(updates, "save", disque_plein)
    with pytest.raises(OSError):
        updates.check(github.client(), TODAY)
    # le premier essai a échoué à l'écriture ; les suivants du même jour ne repartent pas
    updates.check(github.client(), TODAY)
    updates.check(github.client(), TODAY)
    assert len(github.requests) == 1
