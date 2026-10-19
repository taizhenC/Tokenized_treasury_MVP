-- convert_to_traditional.sql   (Lesson 6; idempotency parts added in Lesson 8; collateral in Lesson 12)
-- POST /api/conversions  { "direction": "TO_TRADITIONAL", "quantity": 10, "idempotencyKey": "..." }
-- Burn, inside ONE account: tokenized_units -= q, traditional_units += q. The total does not change.
-- Same app steps, same named-parameter dict, same one-connection/one-transaction pattern, same
-- rowcount rule, error mapping and LOCK ORDER (position, then settlement account) as
-- convert_to_tokenized.sql; only the type, the balance that is checked, and the signs are swapped.
-- request_hash e.g. 'CONVERT_TO_TRADITIONAL|102|1|10'  (type|account_id|asset_id|quantity).
--
-- Lesson 12: the q units become traditional again, so they COUNT as collateral again:
--   collateral_value_cents += q x collateral_cents_per_unit. That can only RAISE the Collateral
--   Monitor (collateral + net), so a burn needs no new check.

-- name: reserve_idempotency_key
INSERT INTO operations
  (operation_type, actor_user_id, source_account_id, asset_id, quantity, idempotency_key, request_hash)
VALUES
  ('CONVERT_TO_TRADITIONAL', %(actor_user_id)s, %(account_id)s, %(asset_id)s, %(quantity)s,
   %(idempotency_key)s, %(request_hash)s)
ON CONFLICT (actor_user_id, idempotency_key) DO NOTHING
RETURNING id;
-- APP DECIDES: 1 row -> keep id, continue with lock_position.  0 rows -> find_existing_operation.

-- name: find_existing_operation
SELECT id, request_hash, result
FROM operations
WHERE actor_user_id = %(actor_user_id)s
  AND idempotency_key = %(idempotency_key)s;
-- APP DECIDES (then ROLLBACK): same request_hash -> 200 + stored result; different -> 409.

-- name: lock_position
SELECT traditional_units, tokenized_units
FROM positions
WHERE account_id = %(account_id)s
  AND asset_id = %(asset_id)s
FOR UPDATE;
-- APP DECIDES: 0 rows -> 500.  tokenized_units < quantity -> 422 "insufficient tokenized units".

-- name: lock_settlement_account
-- Lesson 12. The settlement row, locked AFTER the position row (the one global lock order).
SELECT net_cents, collateral_value_cents, net_debit_cap_cents
FROM settlement_accounts
WHERE account_id = %(account_id)s
FOR UPDATE;
-- APP DECIDES: 0 rows -> 500. Nothing to check: the burn only adds collateral.

-- name: read_collateral_price
-- Lesson 12. What one unit is worth as collateral (prior close less the haircut). No lock.
SELECT prior_close_cents, haircut_bps, collateral_cents_per_unit
FROM collateral_prices
WHERE asset_id = %(asset_id)s;
-- APP DECIDES: the burn flow does NOT run this statement: it has no check to make, and
--   update_collateral reads the price itself. It is here because both conversion files list the
--   same blocks (queries.CONVERSION_BLOCKS). To see what one burned unit adds back, run it with
--   db.try_block: queries/convert_to_traditional.sql read_collateral_price asset_id=1

-- name: apply_conversion
UPDATE positions
SET tokenized_units   = tokenized_units   - %(quantity)s,
    traditional_units = traditional_units + %(quantity)s
WHERE account_id = %(account_id)s
  AND asset_id = %(asset_id)s
RETURNING traditional_units, tokenized_units;
-- APP DECIDES: rowcount must be 1, otherwise raise.
-- Safety net: the CHECK (tokenized_units >= 0).

-- name: update_collateral
-- Lesson 12. The q units are traditional again, so they count as collateral again (relative update).
-- Same price subquery as the mint, with the sign swapped.
UPDATE settlement_accounts
SET collateral_value_cents = collateral_value_cents
      + %(quantity)s * (SELECT collateral_cents_per_unit FROM collateral_prices WHERE asset_id = %(asset_id)s)
WHERE account_id = %(account_id)s
RETURNING collateral_value_cents;
-- APP DECIDES: rowcount must be 1, otherwise raise. The result body does not show it (unchanged).

-- name: insert_ledger_entries
INSERT INTO ledger_entries (operation_id, account_id, asset_id, representation, units_delta)
VALUES
  (%(operation_id)s, %(account_id)s, %(asset_id)s, 'TOKENIZED',   -%(quantity)s),
  (%(operation_id)s, %(account_id)s, %(asset_id)s, 'TRADITIONAL',  %(quantity)s);
-- APP DECIDES: rowcount must be 2.

-- name: store_result
-- result = Jsonb(result_dict)
UPDATE operations
SET result = %(result)s
WHERE id = %(operation_id)s;
-- APP DECIDES: rowcount must be 1, otherwise raise.
