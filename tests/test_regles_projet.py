"""Règles non négociables du CLAUDE.md vérifiées sur le code lui-même, à chaque PR."""

import ast
import re
import sqlite3
from pathlib import Path

from app import db

RACINE = Path(__file__).parent.parent
CODE = sorted((RACINE / "app").rglob("*.py"))
WEB = [p for p in (RACINE / "web").rglob("*") if p.is_file() and "vendor" not in p.parts]
SQL = [RACINE / "app" / "schema.sql", *sorted((RACINE / "app" / "migrations").glob("*.sql"))]


def test_aucun_float_dans_le_code():
    """Règle 7 : montants, prix, quantités et taux en Decimal, jamais en float."""
    trouves = []
    for fichier in CODE:
        for noeud in ast.walk(ast.parse(fichier.read_text(encoding="utf-8"))):
            appel = isinstance(noeud, ast.Call) and getattr(noeud.func, "id", None) == "float"
            litteral = isinstance(noeud, ast.Constant) and isinstance(noeud.value, float)
            if appel or litteral:
                trouves.append(f"{fichier.relative_to(RACINE)}:{noeud.lineno}")
    assert trouves == []


def test_fichiers_texte_lus_en_utf8():
    """Windows lit un fichier texte en cp1252 par défaut : schema.sql, profils.json ou un CSV
    accentué seraient mal lus. Toute lecture ou écriture de texte nomme donc l'encodage (D-118)."""
    oublis = []
    for fichier in [*CODE, *sorted((RACINE / "tests").glob("*.py"))]:
        for noeud in ast.walk(ast.parse(fichier.read_text(encoding="utf-8"))):
            if not isinstance(noeud, ast.Call):
                continue
            nom = getattr(noeud.func, "attr", None) or getattr(noeud.func, "id", None)
            mots = {k.arg for k in noeud.keywords}
            if nom in ("read_text", "write_text"):
                positionnel = len(noeud.args) >= (1 if nom == "read_text" else 2)
                if "encoding" not in mots and not positionnel:
                    oublis.append(f"{fichier.relative_to(RACINE)}:{noeud.lineno}")
            elif nom == "open" and getattr(noeud.func, "value", None) is not None:
                if getattr(noeud.func.value, "id", None) == "webbrowser":
                    continue  # webbrowser.open(url) : pas un fichier
                mode = noeud.args[0] if noeud.args else None  # Path.open(mode)
                if not (isinstance(mode, ast.Constant) and "b" in str(mode.value)):
                    if "encoding" not in mots:
                        oublis.append(f"{fichier.relative_to(RACINE)}:{noeud.lineno}")
            elif nom == "open":  # open(chemin, mode)
                mode = noeud.args[1] if len(noeud.args) > 1 else None
                binaire = isinstance(mode, ast.Constant) and "b" in str(mode.value)
                if not binaire and "encoding" not in mots:
                    oublis.append(f"{fichier.relative_to(RACINE)}:{noeud.lineno}")
    assert oublis == []


def test_aucune_colonne_real_en_base():
    """Règle 7 côté SQLite : les nombres sont stockés en TEXT."""
    sans_commentaires = (re.sub(r"--.*", "", f.read_text(encoding="utf-8")) for f in SQL)
    assert not any(
        re.search(r"\b(REAL|FLOAT|DOUBLE)\b", s, re.IGNORECASE) for s in sans_commentaires
    )


def test_le_front_ne_charge_rien_hors_du_poste():
    """Aucun CDN ni requête tierce à l'ouverture de la page (bibliothèques dans web/vendor/)."""
    externes = [
        f"{f.relative_to(RACINE)} : {url}"
        for f in WEB
        for url in re.findall(r"(?:https?:)?//[\w.-]+\.[a-z]{2,}[^\s\"')]*", f.read_text("utf-8"))
        if "w3.org" not in url  # espaces de noms SVG, jamais téléchargés
    ]
    assert externes == []


def test_serveur_lie_a_la_seule_boucle_locale():
    """Règle 6 : uvicorn écoute sur 127.0.0.1, jamais sur toutes les interfaces."""
    from app import launcher

    assert launcher.HOST == "127.0.0.1"
    lancement = (RACINE / "app" / "launcher.py").read_text(encoding="utf-8")
    assert re.findall(r"host=(\w+)", lancement) == ["HOST"]  # uvicorn.Config(host=HOST)
    assert "sock.bind((HOST, port))" in lancement
    sock = launcher.bind_socket(0)  # port 0 : le système en choisit un libre
    try:
        assert sock.getsockname()[0] == "127.0.0.1"
    finally:
        sock.close()
    assert not any("0.0.0.0" in f.read_text(encoding="utf-8") for f in CODE)


def test_telemetrie_de_fastapi_coupee():
    """Règle 2 : la télémétrie native de FastAPI reste éteinte, même avec un fournisseur
    OpenTelemetry branché (D-122). Sinon elle lirait chaque requête et son en-tête traceparent."""
    from fastapi import FastAPI
    from opentelemetry import trace

    from app.main import NO_TELEMETRY, app

    assert {k: app._telemetry[k] for k in NO_TELEMETRY} == NO_TELEMETRY
    assert not app._native_telemetry.enabled()

    class Fournisseur(trace.TracerProvider):  # fictif, comme un SDK installé et configuré
        def get_tracer(self, *args, **kwargs):
            return trace.NoOpTracer()

    # Témoin : avec un fournisseur, FastAPI s'allume par défaut ; nos réglages l'en empêchent.
    assert FastAPI(telemetry={"tracer_provider": Fournisseur()})._native_telemetry.enabled()
    coupee = FastAPI(telemetry={**NO_TELEMETRY, "tracer_provider": Fournisseur()})
    assert not coupee._native_telemetry.enabled()


def test_base_migree_identique_a_une_base_neuve(db_file, tmp_path):
    """Toutes les tables et colonnes d'une base v1 migrée existent dans une base neuve, et
    inversement : schema.sql et les migrations n'ont pas divergé."""

    def structure(chemin):
        conn = sqlite3.connect(chemin)
        tables = [r[0] for r in conn.execute("SELECT name FROM sqlite_master WHERE type='table'")]
        out = {t: {r[1] for r in conn.execute(f"PRAGMA table_info({t})")} for t in tables}
        conn.close()
        return out

    conn = sqlite3.connect(db_file)
    conn.executescript((RACINE / "tests" / "fixtures" / "schema_v1.sql").read_text("utf-8"))
    conn.close()
    db.migrate()
    neuve = tmp_path / "neuve.db"
    db.init_db(neuve)
    assert structure(db_file) == structure(neuve)


def test_fichiers_de_la_page_tous_charges_dans_l_ordre():
    """D-139 : la page est faite de web/app.js puis des fichiers de web/app/, chargés dans l'ordre
    de la liste APP_FILES de web/profiles.js. Un fichier oublié ne serait jamais chargé ; le
    démarrage (premier affichage) doit venir après tous les autres."""
    source = (RACINE / "web" / "profiles.js").read_text(encoding="utf-8")
    liste = re.search(r"const APP_FILES = \[(.*?)\];", source, re.S).group(1)
    charges = re.findall(r'"([^"]+)"', liste)
    sur_disque = {f"app/{p.name}" for p in (RACINE / "web" / "app").glob("*.js")}
    assert charges[0] == "app.js" and charges[-1] == "app/demarrage.js"
    assert len(charges) == len(set(charges))
    assert set(charges[1:]) == sur_disque
