"""Migration d'une base v1 vers la version actuelle (données FICTIVES)."""

import sqlite3
from pathlib import Path

import pytest

from app import backup as backup_module
from app import db, migrate

V1 = Path(__file__).parent / "fixtures" / "schema_v1.sql"


def columns(conn, table):
    return [r[1] for r in conn.execute(f"PRAGMA table_info({table})")]


@pytest.fixture
def base_v1(db_file):
    conn = sqlite3.connect(db_file)
    conn.executescript(V1.read_text(encoding="utf-8"))
    conn.execute("INSERT INTO account (name, kind) VALUES ('PEA fictif', 'pea')")
    conn.commit()
    conn.close()
    return db_file


def test_base_v1_refusee_au_demarrage(base_v1):
    with pytest.raises(RuntimeError, match="app.migrate"):
        db.init_db()


def test_migration_garde_les_donnees(base_v1, tmp_path):
    assert db.migrate() == (1, db.SCHEMA_VERSION)
    assert db.init_db() == db.SCHEMA_VERSION  # démarrage de nouveau possible
    with db.connect() as conn:
        row = conn.execute("SELECT name, cash, cash_date FROM account").fetchone()
        tables = ("account", "asset", "operation", "price", "price_history")
        migrated = {t: columns(conn, t) for t in tables}
    conn.close()
    assert tuple(row) == ("PEA fictif", "0", None)

    fresh = tmp_path / "neuve.db"
    db.init_db(fresh)
    conn = sqlite3.connect(fresh)
    expected = {t: columns(conn, t) for t in migrated}
    conn.close()
    # Les colonnes ajoutées par ALTER TABLE vont en fin de table : on compare les ensembles.
    assert {t: set(c) for t, c in migrated.items()} == {t: set(c) for t, c in expected.items()}


def test_commande_migrate_sauvegarde_d_abord(base_v1, tmp_path, monkeypatch, capsys):
    saved = []

    def fake_backup(path, label=""):
        target = backup_module.backup(path, tmp_path / "sauvegardes", label=label)
        saved.append(target)
        return target

    monkeypatch.setattr(migrate, "backup", fake_backup)
    migrate.main()
    [target] = saved
    copy = sqlite3.connect(target)
    assert copy.execute("PRAGMA user_version").fetchone()[0] == 1  # la copie est d'avant
    copy.close()
    migrate.main()  # deuxième fois : rien à faire, pas de nouvelle sauvegarde
    assert len(saved) == 1
    assert "rien à faire" in capsys.readouterr().out


def base_en_version(path, version):
    """Base fictive d'une version donnée : schéma v1 + numéro forcé (le contenu importe peu)."""
    conn = sqlite3.connect(path)
    conn.executescript(V1.read_text(encoding="utf-8"))
    conn.execute(f"PRAGMA user_version = {version}")
    conn.commit()
    conn.close()
    return path


def version_of(path):
    return migrate.read_version(path)


def test_lancement_met_a_jour_chaque_base_apres_copie(base_v1, tmp_path):
    """Base unique ou bases des profils (D-120) : chacune en retard est copiée puis migrée."""
    autre = base_en_version(tmp_path / "profil-b.db", 1)
    a_jour = tmp_path / "profil-c.db"
    db.init_db(a_jour)
    messages = migrate.upgrade([base_v1, autre, a_jour, tmp_path / "absente.db"])
    assert len(messages) == 2
    assert [version_of(p) for p in (base_v1, autre, a_jour)] == [db.SCHEMA_VERSION] * 3
    copies = sorted(backup_module.backup_dir().glob("*-avant-migration-*.db"))
    assert [c.name.split("-avant-")[0] for c in copies] == ["profil-b", "test"]
    assert {version_of(c) for c in copies} == {1}  # copies d'avant la mise à jour
    assert not (tmp_path / "absente.db").exists()  # rien de créé : init_db s'en charge
    assert migrate.upgrade([base_v1, autre]) == []  # deuxième lancement : rien à faire


def test_copies_avant_migration_jamais_tournees():
    """La rotation (D-114) ne supprime que les copies « -auto- »."""
    assert not backup_module.AUTO_NAME.match("test-avant-migration-2026-11-13-101010.db")


def test_base_plus_recente_refusee_sans_rien_toucher(base_v1, tmp_path):
    future = base_en_version(tmp_path / "future.db", db.SCHEMA_VERSION + 1)
    with pytest.raises(migrate.UpgradeError, match="plus récente.*Rien n'a été modifié"):
        migrate.upgrade([base_v1, future])
    assert version_of(base_v1) == 1  # vérifiée avant toute écriture : l'autre base attend aussi
    assert version_of(future) == db.SCHEMA_VERSION + 1
    assert not backup_module.backup_dir().exists()


def test_base_illisible_refusee(tmp_path):
    abimee = tmp_path / "abimee.db"
    abimee.write_bytes(b"ceci n'est pas une base SQLite" * 200)
    with pytest.raises(migrate.UpgradeError, match="illisible"):
        migrate.upgrade([abimee])


def test_sauvegarde_ratee_base_intacte(base_v1, monkeypatch):
    def disque_plein(path, label=""):
        raise OSError("No space left on device")

    monkeypatch.setattr(migrate, "backup", disque_plein)
    with pytest.raises(migrate.UpgradeError, match="pas été modifiée"):
        migrate.upgrade([base_v1])
    assert version_of(base_v1) == 1


def test_migration_ratee_nomme_la_copie(base_v1, monkeypatch):
    def en_panne(path):
        raise sqlite3.OperationalError("database is locked")

    monkeypatch.setattr(migrate.db, "migrate", en_panne)
    with pytest.raises(migrate.UpgradeError, match="avant-migration") as err:
        migrate.upgrade([base_v1])
    [copie] = backup_module.backup_dir().glob("*-avant-migration-*.db")
    assert str(copie) in str(err.value)


def test_lanceur_s_arrete_sur_une_base_plus_recente(tmp_path, monkeypatch, capsys):
    from app import launcher

    future = base_en_version(tmp_path / "future.db", db.SCHEMA_VERSION + 1)
    monkeypatch.setenv("PATRIMOINE_DB", str(future))
    monkeypatch.setattr(launcher, "first_free_socket", lambda ports: pytest.fail("serveur lancé"))
    assert launcher.run(display=launcher.NOTHING) == 1
    assert "plus récente" in capsys.readouterr().out
    assert version_of(future) == db.SCHEMA_VERSION + 1


def test_migration_v12_garde_les_achats_programmes(db_file):
    """v11 -> v12 : la table recurring est recréée pour accepter « bimensuel » (D-071)."""
    db.init_db()
    conn = sqlite3.connect(db_file)
    old = (
        Path(__file__).parents[1] / "app" / "migrations" / "006_achats_programmes.sql"
    ).read_text(encoding="utf-8")
    conn.executescript(
        "ALTER TABLE account DROP COLUMN abroad;"
        "ALTER TABLE account DROP COLUMN custody;"
        "DROP TABLE recurring;"
        + old.split("PRAGMA")[0]
        + """
        INSERT INTO account (id, name, kind) VALUES (1, 'PEA fictif', 'pea');
        INSERT INTO asset (id, name, asset_class, ticker, currency)
            VALUES (1, 'ETF', 'etf', 'X.PA', 'EUR');
        INSERT INTO recurring (account_id, asset_id, amount, frequency, day, start_date, last_due)
            VALUES (1, 1, '100', 'hebdomadaire', 2, '2025-01-01', '2025-02-04');
        ALTER TABLE operation DROP COLUMN offered;
        ALTER TABLE operation DROP COLUMN tax_withheld;
        PRAGMA user_version = 11;
        """
    )
    conn.close()
    assert db.migrate() == (11, db.SCHEMA_VERSION)
    with db.connect() as conn:
        row = conn.execute("SELECT amount, frequency, day, last_due FROM recurring").fetchone()
        conn.execute(
            "INSERT INTO recurring (account_id, asset_id, amount, frequency, day, start_date)"
            " VALUES (1, 1, '25', 'bimensuel', 5, '2025-01-03')"
        )
    conn.close()
    assert tuple(row) == ("100", "hebdomadaire", 2, "2025-02-04")


def test_migration_v13_classe_fonds_et_listes(db_file):
    """v12 -> v13 : table asset recréée pour la classe « fonds », opérations gardées (D-072)."""
    db.init_db()
    with db.connect() as conn:
        conn.execute("INSERT INTO account (id, name, kind) VALUES (1, 'PEA fictif', 'pea')")
        conn.execute(
            "INSERT INTO asset (id, name, asset_class, ticker, currency, ter, category, position)"
            " VALUES (7, 'ETF fictif', 'etf', 'X.PA', 'EUR', '0.2', 'Monde', 3)"
        )
        conn.execute(
            "INSERT INTO operation (account_id, asset_id, op_date, op_type, quantity, unit_price,"
            " currency) VALUES (1, 7, '2025-01-02', 'achat', '1', '10', 'EUR')"
        )
    conn.close()
    conn = sqlite3.connect(db_file)
    conn.executescript(
        "ALTER TABLE account DROP COLUMN abroad;"
        "ALTER TABLE account DROP COLUMN custody;"
        "DROP TABLE watchlist_item; DROP TABLE watchlist;"
        "ALTER TABLE operation DROP COLUMN offered; ALTER TABLE operation DROP COLUMN tax_withheld;"
        "PRAGMA user_version = 12;"
    )
    conn.close()
    assert db.migrate() == (12, db.SCHEMA_VERSION)
    with db.connect() as conn:
        row = conn.execute(
            "SELECT name, ter, category, position FROM asset WHERE id = 7"
        ).fetchone()
        assert conn.execute("SELECT count(*) FROM operation WHERE asset_id = 7").fetchone()[0] == 1
        conn.execute(
            "INSERT INTO asset (name, asset_class, currency) VALUES ('UC', 'fonds', 'EUR')"
        )
        conn.execute("INSERT INTO watchlist (name) VALUES ('Test')")
    conn.close()
    assert tuple(row) == ("ETF fictif", "0.2", "Monde", 3)


def test_migration_v14_part_offerte_et_impot_retenu(tmp_path, monkeypatch):
    """v13 -> v14 (D-076) : deux colonnes vides ajoutées, opérations existantes intactes."""
    db_file = tmp_path / "test.db"
    monkeypatch.setenv("PATRIMOINE_DB", str(db_file))
    db.init_db(db_file)
    conn = sqlite3.connect(db_file)
    conn.executescript(
        """
        ALTER TABLE account DROP COLUMN abroad;
        ALTER TABLE account DROP COLUMN custody;
        INSERT INTO account (id, name, kind) VALUES (1, 'CTO fictif', 'cto');
        INSERT INTO asset (id, name, asset_class, ticker, currency)
            VALUES (1, 'Action fictive', 'action', 'FIC.PA', 'EUR');
        INSERT INTO operation (account_id, asset_id, op_date, op_type, quantity, unit_price,
            currency) VALUES (1, 1, '2025-01-02', 'achat', '1', '10', 'EUR');
        ALTER TABLE operation DROP COLUMN offered;
        ALTER TABLE operation DROP COLUMN tax_withheld;
        PRAGMA user_version = 13;
        """
    )
    conn.close()
    assert db.migrate() == (13, db.SCHEMA_VERSION)
    with db.connect() as conn:
        row = conn.execute("SELECT quantity, offered, tax_withheld FROM operation").fetchone()
    assert tuple(row) == ("1", None, None)


def test_migration_v15_transfert_sortant(tmp_path, monkeypatch):
    """v14 -> v15 (D-082) : table des opérations recréée pour le type « transfert_sortant »,
    opérations (colonnes dans l'ordre d'une base migrée) et compteur d'id gardés."""
    db_file = tmp_path / "test.db"
    monkeypatch.setenv("PATRIMOINE_DB", str(db_file))
    db.init_db(db_file)
    conn = sqlite3.connect(db_file)
    conn.executescript(
        """
        ALTER TABLE account DROP COLUMN abroad;
        ALTER TABLE account DROP COLUMN custody;
        INSERT INTO account (id, name, kind) VALUES (1, 'Crypto fictif', 'crypto');
        INSERT INTO asset (id, name, asset_class, coingecko_id, currency)
            VALUES (1, 'Crypto fictive', 'crypto', 'fictif', 'EUR');
        INSERT INTO operation (id, account_id, asset_id, op_date, op_type, quantity, unit_price,
            currency, offered) VALUES (7, 1, 1, '2025-01-02', 'achat', '2', '10', 'EUR', '5');
        INSERT INTO operation (account_id, asset_id, op_date, op_type, quantity, unit_price,
            currency) VALUES (1, 1, '2025-01-03', 'achat', '1', '10', 'EUR');
        DELETE FROM operation WHERE id = 8;
        ALTER TABLE operation DROP COLUMN offered;
        ALTER TABLE operation DROP COLUMN tax_withheld;
        ALTER TABLE operation ADD COLUMN offered TEXT;
        ALTER TABLE operation ADD COLUMN tax_withheld TEXT;
        UPDATE operation SET offered = '5' WHERE id = 7;
        PRAGMA user_version = 14;
        """
    )
    conn.close()
    assert db.migrate() == (14, db.SCHEMA_VERSION)
    with db.connect() as conn:
        row = conn.execute("SELECT id, quantity, offered, op_type FROM operation").fetchone()
        assert tuple(row) == (7, "2", "5", "achat")
        conn.execute(
            "INSERT INTO operation (account_id, asset_id, op_date, op_type, quantity, unit_price,"
            " currency) VALUES (1, 1, '2025-02-01', 'transfert_sortant', '1', '12', 'EUR')"
        )
        new_id = conn.execute("SELECT max(id) FROM operation").fetchone()[0]
    assert new_id == 9  # l'id 8, supprimé, n'est jamais réutilisé


def test_migration_v16_destination_des_transferts(tmp_path, monkeypatch):
    """v15 -> v16 (D-085) : deux colonnes ajoutées, opérations gardées sans destination."""
    db_file = tmp_path / "test.db"
    monkeypatch.setenv("PATRIMOINE_DB", str(db_file))
    db.init_db(db_file)
    conn = sqlite3.connect(db_file)
    conn.executescript(
        """
        ALTER TABLE account DROP COLUMN abroad;
        ALTER TABLE account DROP COLUMN custody;
        INSERT INTO account (id, name, kind) VALUES (1, 'Crypto fictif', 'crypto');
        INSERT INTO asset (id, name, asset_class, coingecko_id, currency)
            VALUES (1, 'Crypto fictive', 'crypto', 'fictif', 'EUR');
        INSERT INTO operation (id, account_id, asset_id, op_date, op_type, quantity, unit_price,
            currency) VALUES (3, 1, 1, '2025-01-02', 'achat', '2', '10', 'EUR');
        ALTER TABLE operation DROP COLUMN reward_kind;
        ALTER TABLE operation DROP COLUMN to_account_id;
        ALTER TABLE operation DROP COLUMN destination;
        PRAGMA user_version = 15;
        """
    )
    conn.close()
    assert db.migrate() == (15, db.SCHEMA_VERSION)
    with db.connect() as conn:
        row = conn.execute(
            "SELECT id, quantity, to_account_id, destination FROM operation"
        ).fetchone()
    assert tuple(row) == (3, "2", None, None)


def test_migration_v17_type_de_recompense(tmp_path, monkeypatch):
    """v16 -> v17 (D-092) : colonne ajoutée, gains Binance déjà importés typés d'après leur note,
    achats ordinaires et saisies à la main laissés sans type."""
    db_file = tmp_path / "test.db"
    monkeypatch.setenv("PATRIMOINE_DB", str(db_file))
    db.init_db(db_file)
    conn = sqlite3.connect(db_file)
    conn.executescript(
        """
        ALTER TABLE account DROP COLUMN abroad;
        ALTER TABLE account DROP COLUMN custody;
        INSERT INTO account (id, name, kind) VALUES (1, 'Crypto fictif', 'crypto');
        INSERT INTO asset (id, name, asset_class, coingecko_id, currency)
            VALUES (1, 'Crypto fictive', 'crypto', 'fictif', 'EUR');
        ALTER TABLE operation DROP COLUMN reward_kind;
        INSERT INTO operation (id, account_id, asset_id, op_date, op_type, quantity, unit_price,
            currency, source, offered, note) VALUES
            (1, 1, 1, '2025-01-02', 'achat', '1', '10', 'EUR', 'csv', '10',
                'Binance : Simple Earn Flexible Interest (31 lignes)'),
            (2, 1, 1, '2025-01-03', 'achat', '1', '10', 'EUR', 'csv', '10',
                'Binance : Launchpool Interest (2 lignes)'),
            (3, 1, 1, '2025-01-04', 'achat', '1', '10', 'EUR', 'csv', '10',
                'Binance : ETH 2.0 Staking Rewards (3 lignes)'),
            (4, 1, 1, '2025-01-05', 'achat', '1', '10', 'EUR', 'csv', '10',
                'Binance : reçu absent de l''export (Earn, airdrop...), compté offert'),
            (5, 1, 1, '2025-01-06', 'achat', '1', '10', 'EUR', 'csv', '10',
                'Binance : Referral Commission (1 lignes)'),
            (6, 1, 1, '2025-01-07', 'achat', '1', '10', 'EUR', 'csv', NULL,
                'Binance : USDT → BTC'),
            (7, 1, 1, '2025-01-08', 'achat', '1', '10', 'EUR', 'manuel', '10', 'Binance : earn');
        PRAGMA user_version = 16;
        """
    )
    conn.close()
    assert db.migrate() == (16, db.SCHEMA_VERSION)
    with db.connect() as conn:
        kinds = dict(conn.execute("SELECT id, reward_kind FROM operation").fetchall())
    assert kinds == {
        1: "earn",
        2: "airdrop",
        3: "staking",
        4: "autre",
        5: "parrainage",
        6: None,
        7: None,
    }


def test_migration_v18_depots_defi(tmp_path, monkeypatch):
    """v17 -> v18 (D-095) : tables des dépôts Aave ajoutées, opérations existantes intactes."""
    db_file = tmp_path / "test.db"
    monkeypatch.setenv("PATRIMOINE_DB", str(db_file))
    db.init_db(db_file)
    conn = sqlite3.connect(db_file)
    conn.executescript(
        """
        ALTER TABLE account DROP COLUMN abroad;
        ALTER TABLE account DROP COLUMN custody;
        DROP TABLE defi_check;
        DROP TABLE defi_rate;
        DROP TABLE defi_line;
        INSERT INTO account (id, name, kind) VALUES (1, 'Crypto fictif', 'crypto');
        INSERT INTO asset (id, name, asset_class, coingecko_id, currency)
            VALUES (1, 'Jeton fictif', 'crypto', 'fictif', 'USD');
        INSERT INTO operation (id, account_id, asset_id, op_date, op_type, quantity, unit_price,
            currency) VALUES (1, 1, 1, '2025-01-02', 'achat', '10', '1', 'USD');
        PRAGMA user_version = 17;
        """
    )
    conn.close()
    assert db.migrate() == (17, db.SCHEMA_VERSION)
    with db.connect() as conn:
        assert conn.execute("SELECT quantity FROM operation").fetchall()[0][0] == "10"
        conn.execute(
            "INSERT INTO defi_line (account_id, asset_id, chain_id, chain_name, market,"
            " market_name, token, symbol) VALUES (1, 1, 1, 'Ethereum', ?, 'Core', ?, 'USDC')",
            ("0x" + "1" * 40, "0x" + "2" * 40),
        )
        with pytest.raises(sqlite3.IntegrityError):
            conn.execute(
                "INSERT INTO defi_line (account_id, asset_id, chain_id, chain_name, market,"
                " market_name, token, symbol) VALUES (1, 1, 1, 'E', 'x', 'C', 'y', 'U')"
            )
