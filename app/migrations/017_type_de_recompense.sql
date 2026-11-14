-- Version 17 (D-092) : type de récompense d'un achat offert de crypto, affiché dans l'onglet
-- Crypto. Les gains déjà importés de Binance (D-082) le retrouvent dans leur note, qui liste les
-- libellés Binance ; le premier type reconnu l'emporte. Aucune autre donnée n'est modifiée.
ALTER TABLE operation ADD COLUMN reward_kind TEXT
    CHECK (reward_kind IN ('earn', 'staking', 'airdrop', 'cashback', 'parrainage', 'autre'));

UPDATE operation SET reward_kind = CASE
        WHEN note LIKE '%absent de l''export%' OR note LIKE '%reçus gratuitement%' THEN 'autre'
        WHEN note LIKE '%staking%' OR note LIKE '%eth 2.0%' OR note LIKE '%vault%' THEN 'staking'
        WHEN note LIKE '%airdrop%' OR note LIKE '%distribution%' OR note LIKE '%launchpool%'
            OR note LIKE '%megadrop%' OR note LIKE '%hodler%' THEN 'airdrop'
        WHEN note LIKE '%earn%' OR note LIKE '%interest%' OR note LIKE '%savings%' THEN 'earn'
        WHEN note LIKE '%cashback%' THEN 'cashback'
        WHEN note LIKE '%referral%' OR note LIKE '%kickback%' OR note LIKE '%commission%'
            OR note LIKE '%rebate%' THEN 'parrainage'
        ELSE 'autre'
    END
WHERE op_type = 'achat' AND offered IS NOT NULL AND source = 'csv' AND note LIKE 'Binance : %';

PRAGMA user_version = 17;
