"""Ce qui sort de cet ordinateur (D-131) : liste déclarée, client qui refuse le reste, compteur
en mémoire, et page « Comment ça marche » servie sans réseau."""

import re
from pathlib import Path

import httpx2
import pytest
from fastapi.testclient import TestClient

from app import backup, outbound, providers, updates
from app.main import app, http_client
from app.providers import yahoo

RACINE = Path(__file__).parent.parent
# Adresses écrites dans le code mais jamais appelées : liens montrés à l'utilisateur.
LIENS_SEULEMENT = {"github.com"}


@pytest.fixture(autouse=True)
def compteurs_a_zero():
    outbound.reset()
    yield
    outbound.reset()


def _client(vus: list):
    def repond(request: httpx2.Request) -> httpx2.Response:
        vus.append(str(request.url))
        return httpx2.Response(200, json={"ok": True})

    return providers.new_client(httpx2.MockTransport(repond))


def test_chaque_adresse_du_code_est_declaree():
    """Une source ajoutée sans être déclarée ici serait refusée par le client : ce test le dit
    avant, et la page « Ce qui sort » reste complète."""
    declares = {h for s in outbound.SERVICES for h in s.hosts}
    vues = set()
    for fichier in sorted((RACINE / "app").rglob("*.py")):
        texte = fichier.read_text(encoding="utf-8")
        vues |= set(re.findall(r"https?://([a-z0-9.-]+\.[a-z]{2,})", texte))
    assert vues - declares - LIENS_SEULEMENT == set()
    assert updates.API_HOST in declares
    # Et rien de déclaré pour rien : chaque service a encore une adresse dans le code.
    assert declares - vues - {updates.API_HOST} == set()


def test_requete_declaree_comptee():
    vus = []
    with _client(vus) as client:
        client.get("https://query1.finance.yahoo.com/v8/finance/chart/CW8.PA")
        client.get("https://api.github.com/repos/x/y/releases/latest")
        client.get("https://query1.finance.yahoo.com/v1/finance/search")
    assert len(vus) == 3
    par_cle = {s["key"]: s for s in outbound.summary()["services"]}
    assert par_cle["yahoo"]["count"] == 2
    assert par_cle["github"]["count"] == 1
    assert par_cle["yahoo"]["last_at"] is not None
    assert par_cle["aave"] == {**par_cle["aave"], "count": 0, "last_at": None}


@pytest.mark.parametrize(
    "url",
    [
        "https://exemple.invalid/collecte",
        "http://query1.finance.yahoo.com/v8/finance/chart/CW8.PA",  # pas https
        "https://query1.finance.yahoo.com.exemple.invalid/",  # suffixe trompeur
        "https://api.github.com:8443/repos/x/y/releases/latest",  # autre port
    ],
)
def test_adresse_non_declaree_refusee_avant_envoi(url):
    vus = []
    with _client(vus) as client, pytest.raises(outbound.OutboundRefused):
        client.get(url)
    assert vus == []  # rien n'a atteint le transport
    assert outbound.summary()["refused"] == 1


def test_redirection_vers_une_autre_adresse_refusee():
    vus = []

    def repond(request: httpx2.Request) -> httpx2.Response:
        vus.append(request.url.host)
        return httpx2.Response(302, headers={"location": "https://exemple.invalid/"})

    with providers.new_client(httpx2.MockTransport(repond)) as client:
        with pytest.raises(outbound.OutboundRefused):
            client.get("https://api.github.com/x", follow_redirects=True)
    assert vus == ["api.github.com"]


def test_refus_vu_comme_une_source_injoignable():
    """Une source refusée se comporte comme une panne : le cours en cache reste affiché."""
    with providers.new_client(httpx2.MockTransport(lambda r: httpx2.Response(200))) as client:
        with pytest.raises(providers.ProviderError, match="injoignable"):
            providers.get_json(client, "https://exemple.invalid/")


def test_source_reelle_passe_par_le_crochet():
    corps = {
        "chart": {
            "result": [
                {
                    "meta": {"regularMarketPrice": 101.5, "currency": "EUR"},
                    "indicators": {"quote": [{"close": [101.5]}]},
                }
            ]
        }
    }
    with providers.new_client(
        httpx2.MockTransport(lambda r: httpx2.Response(200, json=corps))
    ) as c:
        try:
            yahoo.get_price(c, "CW8.PA")
        except providers.ProviderError:
            pass  # forme de réponse simplifiée : seul le passage par le crochet compte ici
    assert {s["key"]: s["count"] for s in outbound.summary()["services"]}["yahoo"] == 1


def test_comment_ca_marche_sans_reseau(db_file):
    def interdit(request):
        raise AssertionError(f"requête inattendue : {request.url}")

    app.dependency_overrides[http_client] = lambda: httpx2.Client(
        transport=httpx2.MockTransport(interdit)
    )
    with TestClient(app) as c:
        r = c.get("/api/about")
    assert r.status_code == 200
    body = r.json()
    assert body["data_dir"]
    assert body["backups"]["keep"] >= 1
    assert body["updates"] is None  # question pas encore posée
    noms = [s["name"] for s in body["outbound"]["services"]]
    assert noms[0] == "Yahoo Finance" and "GitHub" in noms
    assert all(s["count"] == 0 for s in body["outbound"]["services"])
    assert "montants" in body["outbound"]["never_sent"]


def test_derniere_sauvegarde_du_profil_ouvert(db_file):
    dossier = backup.backup_dir()
    dossier.mkdir(parents=True, exist_ok=True)
    for nom in (
        f"{db_file.stem}-auto-2026-11-01-080000.db",
        f"{db_file.stem}-auto-2026-11-14-090000.db",
        "autre-auto-2026-11-15-090000.db",  # une autre base
        f"{db_file.stem}-avant-suppression-2026-11-15-100000.db",  # pas une copie auto
    ):
        (dossier / nom).write_bytes(b"")
    with TestClient(app) as c:
        backups = c.get("/api/about").json()["backups"]
    assert backups["last"] == "2026-11-14"
