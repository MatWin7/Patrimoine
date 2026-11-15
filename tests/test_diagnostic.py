"""Diagnostic à copier dans un signalement (D-132) : utile, mais sans montant, nom, ticker ni
chemin. Données FICTIVES, dossier temporaire."""

import sqlite3

import httpx2
import pydantic
import pytest
from fastapi.testclient import TestClient

from app import diagnostic, main, outbound, profiles, providers, repository, settings
from app.main import app

MONTANT = "4321.98"
NOMS = ("Gisèle Fictive", "gisele-fictive", "Compte secret fictif", "Ligne secrète", "ZZFAKE")


@pytest.fixture(autouse=True)
def a_zero():
    diagnostic.reset()
    outbound.reset()
    yield
    diagnostic.reset()
    outbound.reset()


@pytest.fixture
def dossier(tmp_path, monkeypatch):
    """Dossier des données dont le chemin porte un nom d'utilisateur fictif, en mode profils."""
    folder = tmp_path / "utilisateur-fictif" / "Patrimoine"
    folder.mkdir(parents=True)
    monkeypatch.delenv("PATRIMOINE_DB", raising=False)
    monkeypatch.setattr(profiles, "DATA_DIR", folder)
    profiles.reset_sessions()
    yield folder
    profiles.reset_sessions()


def _remplir(client):
    client.post("/api/profiles", json={"name": "Gisèle Fictive"})
    assert client.post("/api/profiles/gisele-fictive/open", json={}).status_code == 200
    account = client.post("/api/accounts", json={"name": "Compte secret fictif", "kind": "cto"})
    asset = client.post(
        "/api/assets",
        json={"name": "Ligne secrète", "asset_class": "action", "ticker": "zzfake.pa"},
    )
    res = client.post(
        "/api/operations",
        json={
            "account_id": account.json()["id"],
            "asset_id": asset.json()["id"],
            "op_date": "2026-01-05",
            "op_type": "achat",
            "quantity": "37",
            "unit_price": MONTANT,
            "fees": "0",
        },
    )
    assert res.status_code == 201


def test_rien_qui_decrive_le_patrimoine_ni_la_personne(dossier, monkeypatch):
    copie = dossier.parent / "Clé USB de Gisèle"
    copie.mkdir()
    monkeypatch.setattr(settings, "is_network_path", lambda path: False)
    settings.save({"backup_copy_dir": str(copie)})

    def panne(conn):
        raise ValueError(f"montant {MONTANT} dans {dossier}")

    with TestClient(app, raise_server_exceptions=False) as client:
        _remplir(client)
        monkeypatch.setattr(repository, "list_accounts", panne)
        res = client.get("/api/accounts?compte=Compte%20secret%20fictif")
        assert res.status_code == 500
        assert "Copier le diagnostic" in res.json()["detail"]
        text = client.get("/api/diagnostic").json()["text"]

    for interdit in (*NOMS, MONTANT, "4321", str(dossier), "utilisateur-fictif", "Clé USB"):
        assert interdit.lower() not in text.lower(), interdit
    # Mais de quoi comprendre : version, base, taille par tranche, l'erreur et sa ligne.
    assert "Patrimoine 0.1.0" in text
    assert "2 profil(s) ; schémas : 2 × v" in text
    assert "comptes 1 à 9, actifs 1 à 9, opérations 1 à 9" in text
    assert "autre disque : réglé" in text
    assert "GET /api/accounts : ValueError" in text  # modèle de route, sans la requête
    assert "app/main.py:" in text and "(list_accounts)" in text


def test_sans_profil_ouvert_le_diagnostic_repond_sans_lire_de_base(dossier):
    profiles.create("Gisèle Fictive")
    with TestClient(app) as client:
        assert client.get("/api/accounts").status_code == 401
        res = client.get("/api/diagnostic")
    assert res.status_code == 200
    text = res.json()["text"]
    assert "Profil ouvert" not in text
    assert "2 profil(s)" in text
    assert "gisele" not in text.lower()


def test_erreur_sqlite_garde_son_message_les_autres_non():
    try:
        raise sqlite3.OperationalError("database is locked")
    except sqlite3.Error as err:
        kind, frames = diagnostic.describe_error(err)
    assert kind == "OperationalError : database is locked"
    assert frames and frames[0].startswith("app/tests") is False
    try:
        open("/chemin/inexistant/de/gisele.db", encoding="utf-8")  # noqa: SIM115
    except OSError as err:
        kind, _ = diagnostic.describe_error(err)
    assert kind == "FileNotFoundError (ENOENT)"
    try:
        raise ValueError(MONTANT)
    except ValueError as err:
        kind, _ = diagnostic.describe_error(err)
    assert kind == "ValueError"


def test_lignes_citees_seulement_dans_le_code_de_l_app():
    try:
        diagnostic.size_range("pas un nombre")  # type: ignore[arg-type]
    except TypeError as err:
        _, frames = diagnostic.describe_error(err)
    assert frames == (frames[0],) and frames[0].startswith("app/diagnostic.py:")
    try:
        pydantic.TypeAdapter(int).validate_python("pas un nombre")
    except pydantic.ValidationError as err:
        _, frames = diagnostic.describe_error(err)
    assert frames[0] == "dans pydantic"  # le nom de la bibliothèque, jamais son chemin
    assert all(f.startswith("app/") for f in frames[1:])


def test_erreurs_gardees_en_nombre_limite():
    for i in range(diagnostic.MAX_ERRORS + 3):
        try:
            raise KeyError(i)
        except KeyError as err:
            diagnostic.record(err, "essai")
    total, seen = diagnostic.errors()
    assert total == diagnostic.MAX_ERRORS + 3 and len(seen) == diagnostic.MAX_ERRORS
    lines = diagnostic.errors_lines()
    assert sum(" essai : KeyError" in line for line in lines) == diagnostic.SHOWN_ERRORS
    assert "13 en tout" in lines[-1]


@pytest.mark.parametrize(
    ("n", "tranche"),
    [(0, "0"), (1, "1 à 9"), (9, "1 à 9"), (10, "10 à 99"), (999, "100 à 999")]
    + [(1000, "1 000 à 9 999"), (10_000, "10 000 et plus"), (250_000, "10 000 et plus")],
)
def test_tailles_par_tranche(n, tranche):
    assert diagnostic.size_range(n) == tranche


def test_services_codes_en_erreur_et_sans_reponse():
    def repond(request: httpx2.Request) -> httpx2.Response:
        if "chart/A" in str(request.url):
            return httpx2.Response(429)
        if "chart/B" in str(request.url):
            raise httpx2.ConnectError("coupé", request=request)
        return httpx2.Response(200, json={})

    with providers.new_client(httpx2.MockTransport(repond)) as client:
        for ticker in ("A", "A", "B", "C"):
            try:
                client.get(f"https://query1.finance.yahoo.com/v8/finance/chart/{ticker}")
            except httpx2.ConnectError:
                pass
        with pytest.raises(outbound.OutboundRefused):
            client.get("https://exemple.invalid/")
    lines = diagnostic.services_lines()
    assert lines == [
        "  Yahoo Finance : 4 requête(s) ; en erreur : HTTP 429 × 2 ; 1 sans réponse",
        "  bloquées (adresse non déclarée) : 1",
    ]


def test_commande_hors_de_l_app(db_file, capsys):
    diagnostic.main()
    out = capsys.readouterr().out
    assert out.startswith("Patrimoine ")
    assert "hors de l'app" in out
    assert "Services joints" not in out and "Erreurs inattendues" not in out
    assert str(db_file) not in out


def test_route_accessible_sans_profil_listee(dossier):
    """La route est dans la courte liste de celles qui répondent sans profil ouvert : rien
    d'autre n'y est ajouté par mégarde."""
    paths = ("/api/health", "/api/profiles", "/api/window", "/api/diagnostic")
    assert all(main._without_profile(_req(p)) for p in paths)
    assert not main._without_profile(_req("/api/accounts"))


def _req(path: str):
    from starlette.requests import Request

    scope = {"type": "http", "method": "GET", "path": path, "headers": [], "query_string": b""}
    return Request(scope)


def test_erreur_de_mise_a_jour_seulement_si_message_connue(monkeypatch):
    from app import updates

    for stored, shown in [
        ("GitHub a répondu 403", "erreur : GitHub a répondu 403"),
        ("GitHub est injoignable", "erreur : GitHub est injoignable"),
        (f"retouché à la main {MONTANT}", "erreur : message non reconnu"),
    ]:
        monkeypatch.setattr(updates, "load", lambda s=stored: updates.Settings(True, error=s))
        assert diagnostic.updates_line().endswith(shown)


def test_messages_d_erreur_de_mise_a_jour_tous_reconnus():
    """Un nouveau message d'UpdateError doit être ajouté à la liste du diagnostic."""
    import re
    from pathlib import Path

    code = (Path(diagnostic.__file__).parent / "updates.py").read_text(encoding="utf-8")
    fixes = set(re.findall(r'UpdateError\("([^"]+)"\)', code))
    assert fixes and fixes <= diagnostic.UPDATE_ERRORS
