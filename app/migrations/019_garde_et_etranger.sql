-- Version 19 (D-104) : garde d'un compte crypto (plateforme d'échange ou portefeuille
-- personnel) et établissement à l'étranger (comptes à déclarer, 3916 / 3916-bis). Deux
-- colonnes ajoutées ; les comptes existants sont devinés d'après leur nom et leur
-- établissement (mêmes listes que app/custody.py), ce qui n'est pas reconnu reste à préciser.
ALTER TABLE account ADD COLUMN custody TEXT CHECK (custody IN ('exchange', 'wallet'));
ALTER TABLE account ADD COLUMN abroad INTEGER NOT NULL DEFAULT 0 CHECK (abroad IN (0, 1));

-- Portefeuilles d'abord : « Coinbase Wallet » est un portefeuille, « Coinbase » une plateforme.
UPDATE account SET custody = 'wallet'
WHERE kind = 'crypto' AND (
    lower(name || ' ' || coalesce(institution, '')) LIKE '%ledger%'
    OR lower(name || ' ' || coalesce(institution, '')) LIKE '%trezor%'
    OR lower(name || ' ' || coalesce(institution, '')) LIKE '%metamask%'
    OR lower(name || ' ' || coalesce(institution, '')) LIKE '%rabby%'
    OR lower(name || ' ' || coalesce(institution, '')) LIKE '%phantom%'
    OR lower(name || ' ' || coalesce(institution, '')) LIKE '%trust wallet%'
    OR lower(name || ' ' || coalesce(institution, '')) LIKE '%exodus%'
    OR lower(name || ' ' || coalesce(institution, '')) LIKE '%rainbow%'
    OR lower(name || ' ' || coalesce(institution, '')) LIKE '%coinbase wallet%'
    OR lower(name || ' ' || coalesce(institution, '')) LIKE '%tangem%'
    OR lower(name || ' ' || coalesce(institution, '')) LIKE '%keystone%'
    OR lower(name || ' ' || coalesce(institution, '')) LIKE '%bitbox%'
    OR lower(name || ' ' || coalesce(institution, '')) LIKE '%electrum%'
    OR lower(name || ' ' || coalesce(institution, '')) LIKE '%sparrow%'
    OR lower(name || ' ' || coalesce(institution, '')) LIKE '%aave%'
);

UPDATE account SET custody = 'exchange'
WHERE kind = 'crypto' AND custody IS NULL AND (
    lower(name || ' ' || coalesce(institution, '')) LIKE '%binance%'
    OR lower(name || ' ' || coalesce(institution, '')) LIKE '%kraken%'
    OR lower(name || ' ' || coalesce(institution, '')) LIKE '%coinbase%'
    OR lower(name || ' ' || coalesce(institution, '')) LIKE '%bitpanda%'
    OR lower(name || ' ' || coalesce(institution, '')) LIKE '%bitstamp%'
    OR lower(name || ' ' || coalesce(institution, '')) LIKE '%bybit%'
    OR lower(name || ' ' || coalesce(institution, '')) LIKE '%okx%'
    OR lower(name || ' ' || coalesce(institution, '')) LIKE '%kucoin%'
    OR lower(name || ' ' || coalesce(institution, '')) LIKE '%crypto.com%'
    OR lower(name || ' ' || coalesce(institution, '')) LIKE '%gemini%'
    OR lower(name || ' ' || coalesce(institution, '')) LIKE '%bitget%'
    OR lower(name || ' ' || coalesce(institution, '')) LIKE '%bitvavo%'
    OR lower(name || ' ' || coalesce(institution, '')) LIKE '%paymium%'
    OR lower(name || ' ' || coalesce(institution, '')) LIKE '%coinhouse%'
    OR lower(name || ' ' || coalesce(institution, '')) LIKE '%revolut%'
    OR lower(name || ' ' || coalesce(institution, '')) LIKE '%trade republic%'
);

-- Établissements étrangers connus, tout type de compte, hors portefeuilles personnels.
UPDATE account SET abroad = 1
WHERE coalesce(custody, '') <> 'wallet' AND (
    lower(name || ' ' || coalesce(institution, '')) LIKE '%binance%'
    OR lower(name || ' ' || coalesce(institution, '')) LIKE '%kraken%'
    OR lower(name || ' ' || coalesce(institution, '')) LIKE '%coinbase%'
    OR lower(name || ' ' || coalesce(institution, '')) LIKE '%bitpanda%'
    OR lower(name || ' ' || coalesce(institution, '')) LIKE '%bitstamp%'
    OR lower(name || ' ' || coalesce(institution, '')) LIKE '%bybit%'
    OR lower(name || ' ' || coalesce(institution, '')) LIKE '%okx%'
    OR lower(name || ' ' || coalesce(institution, '')) LIKE '%kucoin%'
    OR lower(name || ' ' || coalesce(institution, '')) LIKE '%crypto.com%'
    OR lower(name || ' ' || coalesce(institution, '')) LIKE '%gemini%'
    OR lower(name || ' ' || coalesce(institution, '')) LIKE '%bitget%'
    OR lower(name || ' ' || coalesce(institution, '')) LIKE '%bitvavo%'
    OR lower(name || ' ' || coalesce(institution, '')) LIKE '%revolut%'
    OR lower(name || ' ' || coalesce(institution, '')) LIKE '%trade republic%'
    OR lower(name || ' ' || coalesce(institution, '')) LIKE '%degiro%'
    OR lower(name || ' ' || coalesce(institution, '')) LIKE '%interactive brokers%'
    OR lower(name || ' ' || coalesce(institution, '')) LIKE '%scalable%'
    OR lower(name || ' ' || coalesce(institution, '')) LIKE '%n26%'
    OR lower(name || ' ' || coalesce(institution, '')) LIKE '%saxo%'
    OR lower(name || ' ' || coalesce(institution, '')) LIKE '%freedom24%'
);

PRAGMA user_version = 19;
