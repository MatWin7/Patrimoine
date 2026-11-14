-- Schéma SQLite de « Patrimoine » — version 14 (v1 : jalon 1 ; v2 : espèces par compte ;
-- v3 : historique des clôtures ; v4 : versements et retraits, espèces calculées, indices ;
-- v5 : couleur choisie pour un actif ; v6 : achats programmés ; v7 : enveloppes, valeurs saisies ;
-- v8 : allocation cible, repères fiscaux, frais courants ; v9 : catégories ; v10 : fiscalité
-- des cryptos ; v11 : ordre choisi des comptes et actifs ; v12 : achats toutes les 2 semaines ; v13 : listes de suivi, classe fonds ;
-- v14 : part offerte d'un achat, impôt retenu sur un dividende).
-- Une base existante se met à jour avec `uv run python -m app.migrate` (sauvegarde d'abord).
-- Conventions (voir docs/decisions.md) :
--   * Montants, prix, quantités et taux : TEXT contenant un Decimal ("1234.5600").
--     Jamais REAL (float). Conversion en decimal.Decimal dans app/db.py.
--   * Dates : TEXT ISO 8601 ("2026-10-10"), horodatages en UTC ("2026-10-10T12:00:00Z").
--   * Devises : code ISO 4217 en majuscules ("EUR", "USD").
--   * Version du schéma : PRAGMA user_version, incrémentée à chaque migration.

PRAGMA foreign_keys = ON;

-- Un compte = une enveloppe où sont détenus des actifs (PEA, CTO, assurance-vie, livret...).
-- Un livret n'a que des espèces : son solde saisi, versements et retraits (D-056).
-- Dernières colonnes : `position`, ordre choisi dans « Gérer » (D-070) ; `custody`, garde d'un
-- compte crypto, plateforme d'échange ('exchange') ou portefeuille personnel ('wallet'), NULL
-- = à préciser et toujours NULL hors crypto (D-104) ; `abroad`, établissement à l'étranger,
-- compte à déclarer chaque année (3916, 3916-bis pour la crypto). Aucun commentaire entre ces
-- colonnes : ALTER TABLE ... DROP COLUMN (tests de migration) s'y perd.
CREATE TABLE IF NOT EXISTS account (
    id          INTEGER PRIMARY KEY,
    name        TEXT    NOT NULL UNIQUE,
    kind        TEXT    NOT NULL CHECK (kind IN ('pea', 'cto', 'crypto', 'assurance_vie', 'per',
                                                 'livret', 'immobilier', 'autre')),
    institution TEXT,                       -- ex. "BoursoBank" ; libellé libre
    currency    TEXT    NOT NULL DEFAULT 'EUR' CHECK (length(currency) = 3),
    -- Espèces = solde saisi (cash) + effet des opérations enregistrées après cette saisie,
    -- c'est-à-dire d'id > cash_op_id (D-041). Ressaisir le solde recale le compte sur la banque.
    cash        TEXT    NOT NULL DEFAULT '0',  -- dernier solde espèces saisi (Decimal)
    cash_date   TEXT,                       -- date de la dernière saisie du solde espèces
    cash_op_id  INTEGER NOT NULL DEFAULT 0, -- dernière opération déjà comprise dans `cash`
    created_at  TEXT    NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%SZ', 'now')),
    -- Repères fiscaux (D-059) : date d'ouverture (5 ans d'un PEA, 8 ans d'une assurance-vie)
    -- et versements faits avant le suivi (plafond de 150 000 € d'un PEA).
    opened_on   TEXT,
    deposits_before TEXT NOT NULL DEFAULT '0',
    position    INTEGER NOT NULL DEFAULT 0,
    custody     TEXT CHECK (custody IN ('exchange', 'wallet')),
    abroad      INTEGER NOT NULL DEFAULT 0 CHECK (abroad IN (0, 1))
);

-- Un actif = une ligne du patrimoine. Action, ETF et crypto ont un prix public ; les autres
-- classes peuvent n'avoir aucun identifiant : leur valeur est saisie à la main (D-056).
CREATE TABLE IF NOT EXISTS asset (
    id            INTEGER PRIMARY KEY,
    name          TEXT NOT NULL,
    -- fonds : OPCVM / unité de compte, coté ou non (D-072)
    asset_class   TEXT NOT NULL CHECK (asset_class IN ('action', 'etf', 'fonds', 'crypto',
                                       'obligation', 'fonds_euros', 'scpi', 'or', 'immobilier',
                                       'autre')),
    isin          TEXT UNIQUE CHECK (isin IS NULL OR length(isin) = 12),
    ticker        TEXT,                     -- ticker Yahoo, ex. "CW8.PA"
    coingecko_id  TEXT,                     -- ex. "bitcoin"
    currency      TEXT NOT NULL CHECK (length(currency) = 3),  -- devise de cotation
    -- Couleur dans les graphiques : NULL = automatique, 1 à 7 = --series-N de styles.css (D-052).
    color         INTEGER CHECK (color IS NULL OR color BETWEEN 1 AND 7),
    ter           TEXT,                     -- frais courants d'un fonds, en % par an (D-060)
    category      TEXT,                     -- catégorie personnelle ; vide = la classe (D-061)
    position      INTEGER NOT NULL DEFAULT 0, -- ordre choisi dans « Gérer » (D-070)
    -- Action, ETF, crypto : il faut au moins un identifiant de prix exploitable.
    CHECK (asset_class NOT IN ('action', 'etf', 'crypto')
           OR ticker IS NOT NULL OR coingecko_id IS NOT NULL),
    CHECK (asset_class <> 'crypto' OR coingecko_id IS NOT NULL)
);
CREATE UNIQUE INDEX IF NOT EXISTS ux_asset_ticker    ON asset(ticker)       WHERE ticker IS NOT NULL;
CREATE UNIQUE INDEX IF NOT EXISTS ux_asset_coingecko ON asset(coingecko_id) WHERE coingecko_id IS NOT NULL;

-- Une opération sur un compte. « transaction » étant un mot réservé SQL, la table s'appelle operation.
--   solde_initial : instantané d'une position existante (quantité + PRU), évite de ressaisir l'historique.
--   achat / vente : quantity et unit_price obligatoires.
--   transfert_sortant : titres ou crypto envoyés ailleurs (wallet, autre courtier), ni vente ni
--     espèces ; unit_price = valeur unitaire estimée ce jour-là (D-082).
--   dividende / frais : amount obligatoire, quantity vide.
--   versement / retrait : argent qui entre sur le compte ou en sort, amount obligatoire, sans actif.
-- AUTOINCREMENT : un id n'est jamais réutilisé, ce qui garde cash_op_id fiable après une suppression.
CREATE TABLE IF NOT EXISTS operation (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    account_id  INTEGER NOT NULL REFERENCES account(id) ON DELETE RESTRICT,
    asset_id    INTEGER          REFERENCES asset(id)   ON DELETE RESTRICT,
    op_date     TEXT    NOT NULL,           -- date de l'opération (YYYY-MM-DD)
    op_type     TEXT    NOT NULL CHECK (op_type IN ('solde_initial', 'achat', 'vente', 'transfert_sortant', 'dividende', 'frais', 'versement', 'retrait')),
    quantity    TEXT,                       -- Decimal, > 0 ; le sens vient de op_type
    unit_price  TEXT,                       -- Decimal, dans la devise de l'opération
    amount      TEXT,                       -- Decimal, montant brut (dividende, frais)
    fees        TEXT    NOT NULL DEFAULT '0',
    currency    TEXT    NOT NULL CHECK (length(currency) = 3),
    note        TEXT,
    source      TEXT    NOT NULL DEFAULT 'manuel',  -- 'manuel' ou nom de l'importeur CSV
    import_key  TEXT,                       -- empreinte de la ligne importée, évite les doublons
    -- Vente d'un compte crypto (D-066) : échange contre une autre crypto (1 = non imposable) et
    -- valeur globale du portefeuille crypto ce jour-là en EUR (vide = estimée par l'app).
    crypto_swap     INTEGER NOT NULL DEFAULT 0 CHECK (crypto_swap IN (0, 1)),
    portfolio_value TEXT,
    -- Achat (D-076) : part payée par la plateforme (Saveback, Stockperk), ni versement ni argent
    -- investi. Dividende : impôt retenu à la source (le montant reste le net reçu).
    offered         TEXT,
    tax_withheld    TEXT,
    -- Transfert sortant ou retrait (D-085) : compte de l'app qui reçoit les titres (même prix de
    -- revient) ou l'argent (versement) ; sinon destination hors de l'app en texte libre.
    to_account_id   INTEGER REFERENCES account(id) ON DELETE SET NULL,
    destination     TEXT,
    -- Achat offert d'une crypto (D-092) : type de récompense (intérêts Earn, staking, airdrop,
    -- cashback, parrainage, autre), affiché dans l'onglet Crypto. Vide = achat ordinaire.
    reward_kind     TEXT CHECK (reward_kind IN ('earn', 'staking', 'airdrop', 'cashback', 'parrainage', 'autre')),
    created_at  TEXT    NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%SZ', 'now')),
    CHECK (
        (op_type IN ('solde_initial', 'achat', 'vente', 'transfert_sortant')
            AND asset_id IS NOT NULL AND quantity IS NOT NULL AND unit_price IS NOT NULL)
        OR (op_type = 'dividende' AND asset_id IS NOT NULL AND amount IS NOT NULL AND quantity IS NULL)
        OR (op_type = 'frais' AND amount IS NOT NULL AND quantity IS NULL)
        OR (op_type IN ('versement', 'retrait')
            AND asset_id IS NULL AND amount IS NOT NULL AND quantity IS NULL)
    )
);
CREATE INDEX IF NOT EXISTS ix_operation_account_asset ON operation(account_id, asset_id, op_date);
CREATE UNIQUE INDEX IF NOT EXISTS ux_operation_import ON operation(source, import_key) WHERE import_key IS NOT NULL;

-- Cache et historique des prix publics.
CREATE TABLE IF NOT EXISTS price (
    asset_id    INTEGER NOT NULL REFERENCES asset(id) ON DELETE CASCADE,
    fetched_at  TEXT    NOT NULL,           -- horodatage UTC de la récupération
    price       TEXT    NOT NULL,           -- Decimal
    currency    TEXT    NOT NULL CHECK (length(currency) = 3),
    source      TEXT    NOT NULL,           -- 'yahoo', 'coingecko', 'manuel'
    PRIMARY KEY (asset_id, fetched_at, source)
);

-- Taux de change BCE : 1 EUR = rate unités de currency (convention Frankfurter).
CREATE TABLE IF NOT EXISTS fx_rate (
    rate_date   TEXT NOT NULL,              -- jour ouvré BCE (YYYY-MM-DD)
    currency    TEXT NOT NULL CHECK (length(currency) = 3 AND currency <> 'EUR'),
    rate        TEXT NOT NULL,              -- Decimal
    PRIMARY KEY (rate_date, currency)
);

-- Clôtures quotidiennes passées (une par jour de cotation), pour la courbe d'évolution.
-- Distincte de `price` (cache du dernier cours, horodaté à la seconde) : ici une ligne par jour.
CREATE TABLE IF NOT EXISTS price_history (
    asset_id    INTEGER NOT NULL REFERENCES asset(id) ON DELETE CASCADE,
    price_date  TEXT    NOT NULL,           -- jour de cotation (YYYY-MM-DD), heure de la place
    close       TEXT    NOT NULL,           -- Decimal, cours de clôture
    currency    TEXT    NOT NULL CHECK (length(currency) = 3),
    source      TEXT    NOT NULL,           -- 'yahoo', ou 'manuel' (valeur saisie, D-056)
    PRIMARY KEY (asset_id, price_date)
);

-- Liste des références de comparaison (onglets Comparaison et Calendrier), modifiable :
-- un indice Yahoo (ticker public) ou un livret (taux annuel saisi, rien ne part sur le réseau).
CREATE TABLE IF NOT EXISTS benchmark (
    id          INTEGER PRIMARY KEY,
    kind        TEXT    NOT NULL CHECK (kind IN ('indice', 'livret')),
    label       TEXT    NOT NULL,
    grp         TEXT,                       -- groupe affiché dans la liste (« Monde », « Livrets »...)
    ticker      TEXT,                       -- indice : ticker Yahoo
    rate        TEXT,                       -- livret : taux annuel en % (Decimal)
    position    INTEGER NOT NULL DEFAULT 0, -- ordre dans la liste
    CHECK ((kind = 'indice' AND ticker IS NOT NULL) OR (kind = 'livret' AND rate IS NOT NULL))
);
INSERT INTO benchmark (kind, label, grp, ticker, position) VALUES
    ('indice', 'MSCI World (iShares, en EUR)', 'Monde', 'IWDA.AS', 1),
    ('indice', 'MSCI World (Amundi, en EUR)', 'Monde', 'CW8.PA', 2),
    ('indice', 'Marchés émergents (iShares)', 'Monde', 'EIMI.L', 3),
    ('indice', 'CAC 40', 'Europe', '^FCHI', 4),
    ('indice', 'Euro Stoxx 50', 'Europe', '^STOXX50E', 5),
    ('indice', 'S&P 500', 'États-Unis (en USD, converti)', '^GSPC', 6),
    ('indice', 'Nasdaq Composite', 'États-Unis (en USD, converti)', '^IXIC', 7);

-- Achats programmés (investissement régulier, D-055) : chaque mois échu est proposé à valider
-- (prix et quantité réels) ou à ignorer ; rien n'est enregistré tout seul.
CREATE TABLE IF NOT EXISTS recurring (
    id          INTEGER PRIMARY KEY,
    account_id  INTEGER NOT NULL REFERENCES account(id) ON DELETE CASCADE,
    asset_id    INTEGER NOT NULL REFERENCES asset(id)   ON DELETE CASCADE,
    amount      TEXT    NOT NULL,           -- Decimal, montant visé par achat, devise de l'actif
    frequency   TEXT    NOT NULL DEFAULT 'mensuel'
                CHECK (frequency IN ('mensuel', 'trimestriel', 'hebdomadaire', 'bimensuel')),
    -- jour du mois (1 à 28) ; en hebdomadaire et bimensuel (toutes les 2 semaines), jour de la
    -- semaine (1 = lundi, 7 = dimanche)
    day         INTEGER NOT NULL CHECK (day BETWEEN 1 AND 28),
    start_date  TEXT    NOT NULL,           -- premier jour possible (YYYY-MM-DD)
    last_due    TEXT,                       -- dernière échéance validée ou ignorée (YYYY-MM-DD)
    note        TEXT
);

-- Allocation cible (D-058) : part visée de chaque ligne, en % (Decimal).
CREATE TABLE IF NOT EXISTS target (
    asset_id    INTEGER PRIMARY KEY REFERENCES asset(id) ON DELETE CASCADE,
    pct         TEXT    NOT NULL
);

-- Allocation cible par catégorie (D-061) : part visée de chaque catégorie, en %.
CREATE TABLE IF NOT EXISTS category_target (
    category    TEXT    PRIMARY KEY,
    pct         TEXT    NOT NULL
);

-- Listes de suivi (onglet Visualisations, D-072) : actions, ETF, indices ou cryptos suivis par
-- leur ticker Yahoo public, détenus ou non. Aucun montant ni quantité : seulement des noms.
CREATE TABLE IF NOT EXISTS watchlist (
    id          INTEGER PRIMARY KEY,
    name        TEXT    NOT NULL UNIQUE,
    position    INTEGER NOT NULL DEFAULT 0  -- ordre des listes (ordre de création)
);
CREATE TABLE IF NOT EXISTS watchlist_item (
    id           INTEGER PRIMARY KEY,
    watchlist_id INTEGER NOT NULL REFERENCES watchlist(id) ON DELETE CASCADE,
    label        TEXT    NOT NULL,
    ticker       TEXT    NOT NULL,          -- ticker Yahoo, ex. "AI.PA", "^FCHI", "BTC-EUR"
    position     INTEGER NOT NULL DEFAULT 0,
    UNIQUE (watchlist_id, ticker)
);

-- Dépôts DeFi suivis (D-095) : une ligne (compte, jeton) reliée à un marché Aave v3 public
-- (chaîne, adresse du pool, adresse du jeton : des contrats d'Aave, jamais un portefeuille).
-- Ses intérêts sont estimés chaque mois en achats offerts (source 'aave').
CREATE TABLE IF NOT EXISTS defi_line (
    id          INTEGER PRIMARY KEY,
    account_id  INTEGER NOT NULL REFERENCES account(id) ON DELETE CASCADE,
    asset_id    INTEGER NOT NULL REFERENCES asset(id) ON DELETE CASCADE,
    protocol    TEXT    NOT NULL DEFAULT 'aave' CHECK (protocol IN ('aave')),
    chain_id    INTEGER NOT NULL,
    chain_name  TEXT    NOT NULL,
    market      TEXT    NOT NULL CHECK (length(market) = 42 AND market GLOB '0x*'),
    market_name TEXT    NOT NULL,
    token       TEXT    NOT NULL CHECK (length(token) = 42 AND token GLOB '0x*'),
    symbol      TEXT    NOT NULL,
    created_at  TEXT    NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%SZ', 'now')),
    UNIQUE (account_id, asset_id)
);
-- Taux publics d'un marché, un par jour (moyenne du jour, APY en Decimal) : dépôt (supply)
-- maintenant, emprunt (borrow) prévu pour les dettes.
CREATE TABLE IF NOT EXISTS defi_rate (
    chain_id    INTEGER NOT NULL,
    market      TEXT    NOT NULL,
    token       TEXT    NOT NULL,
    side        TEXT    NOT NULL CHECK (side IN ('supply', 'borrow')),
    rate_date   TEXT    NOT NULL,
    apy         TEXT    NOT NULL,
    PRIMARY KEY (chain_id, market, token, side, rate_date)
);
-- Soldes lus dans Aave et saisis par l'utilisateur (« Vérifier ») : ils recalent l'estimation.
CREATE TABLE IF NOT EXISTS defi_check (
    line_id     INTEGER NOT NULL REFERENCES defi_line(id) ON DELETE CASCADE,
    check_date  TEXT    NOT NULL,
    quantity    TEXT    NOT NULL,
    PRIMARY KEY (line_id, check_date)
);

-- Dettes (D-107) : patrimoine net = actifs − capital restant dû.
CREATE TABLE IF NOT EXISTS debt (
    id          INTEGER PRIMARY KEY,
    name        TEXT    NOT NULL,
    kind        TEXT    NOT NULL CHECK (kind IN ('immobilier', 'conso', 'etudiant', 'autre')),
    institution TEXT,
    currency    TEXT    NOT NULL DEFAULT 'EUR',
    principal   TEXT    NOT NULL,
    rate        TEXT,
    start_date  TEXT    NOT NULL,
    months      INTEGER CHECK (months IS NULL OR months > 0),
    position    INTEGER NOT NULL DEFAULT 0
);

CREATE TABLE IF NOT EXISTS debt_balance (
    debt_id      INTEGER NOT NULL REFERENCES debt(id) ON DELETE CASCADE,
    balance_date TEXT    NOT NULL,
    balance      TEXT    NOT NULL,
    PRIMARY KEY (debt_id, balance_date)
);

-- Emprunts Aave et seuils de liquidation (D-108).
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
