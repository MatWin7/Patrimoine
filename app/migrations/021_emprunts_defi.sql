-- Version 21 (D-108) : emprunts Aave (dette en jetons qui grossit au taux d'emprunt public)
-- et seuils de liquidation publics des marchés, pour le facteur de santé. Quatre tables
-- neuves, aucune donnée existante touchée ; les taux d'emprunt vont dans defi_rate
-- (side = 'borrow', déjà prévu).
CREATE TABLE IF NOT EXISTS defi_borrow (
    id          INTEGER PRIMARY KEY,
    asset_id    INTEGER NOT NULL REFERENCES asset(id),
    chain_id    INTEGER NOT NULL,
    chain_name  TEXT    NOT NULL,
    market      TEXT    NOT NULL,
    market_name TEXT    NOT NULL,
    token       TEXT    NOT NULL,
    symbol      TEXT    NOT NULL
);

-- Emprunt (+) ou remboursement (−) d'un jour, en jetons.
CREATE TABLE IF NOT EXISTS defi_borrow_flow (
    id        INTEGER PRIMARY KEY,
    borrow_id INTEGER NOT NULL REFERENCES defi_borrow(id) ON DELETE CASCADE,
    flow_date TEXT    NOT NULL,
    quantity  TEXT    NOT NULL
);

-- Dette lue dans Aave à une date (« Vérifier ») : l'estimation repart de ce chiffre.
CREATE TABLE IF NOT EXISTS defi_borrow_check (
    borrow_id  INTEGER NOT NULL REFERENCES defi_borrow(id) ON DELETE CASCADE,
    check_date TEXT    NOT NULL,
    quantity   TEXT    NOT NULL,
    PRIMARY KEY (borrow_id, check_date)
);

-- Seuil de liquidation public d'un jeton sur un marché (0,78 = 78 %), relu une fois par jour.
CREATE TABLE IF NOT EXISTS defi_reserve (
    chain_id              INTEGER NOT NULL,
    market                TEXT    NOT NULL,
    token                 TEXT    NOT NULL,
    liquidation_threshold TEXT    NOT NULL,
    fetched_on            TEXT    NOT NULL,
    PRIMARY KEY (chain_id, market, token)
);

PRAGMA user_version = 21;
