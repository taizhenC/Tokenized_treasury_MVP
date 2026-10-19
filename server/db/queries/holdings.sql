-- holdings.sql   (Lesson 5 - GET /api/holdings, plus two small lookups the app needs)
-- Plain reads: no transaction block needed.
-- Unit columns are INTEGER; psycopg returns them as Python ints, FastAPI sends them as JSON numbers.

-- name: find_asset_by_symbol
-- The app runs this the first time it needs the asset, keeps the id, and passes it as asset_id
-- everywhere else (no magic ASSET_ID constant in the code).
-- symbol = 'DEMO-TREASURY'
SELECT id, symbol, total_custodied_units, unit_precision
FROM assets
WHERE symbol = %(symbol)s;
-- APP DECIDES: 0 rows -> 500 "run the seeds first".

-- APP DECIDES which holdings statement to run, from the SESSION user (never from the request):
--   PARTICIPANT          -> holdings_for_account with account_id = the user's own account_id
--   AUDITOR or OPERATOR  -> holdings_all

-- name: holdings_for_account
-- account_id = account_id of the logged-in participant
SELECT a.id AS account_id,
       a.participant_name,
       a.wallet_ref,
       a.is_approved,
       s.symbol,
       p.traditional_units,
       p.tokenized_units,
       p.traditional_units + p.tokenized_units AS total_units
FROM positions p
JOIN accounts a ON a.id = p.account_id
JOIN assets   s ON s.id = p.asset_id
WHERE p.account_id = %(account_id)s
ORDER BY s.symbol;

-- name: holdings_all
-- AUDITOR or OPERATOR: every account.
SELECT a.id AS account_id,
       a.participant_name,
       a.wallet_ref,
       a.is_approved,
       s.symbol,
       p.traditional_units,
       p.tokenized_units,
       p.traditional_units + p.tokenized_units AS total_units
FROM positions p
JOIN accounts a ON a.id = p.account_id
JOIN assets   s ON s.id = p.asset_id
ORDER BY a.id, s.symbol;

-- name: list_accounts
-- Any logged-in user: the directory for the transfer form's "to" dropdown (no balances).
SELECT id, participant_name, wallet_ref, is_approved
FROM accounts
ORDER BY id;
