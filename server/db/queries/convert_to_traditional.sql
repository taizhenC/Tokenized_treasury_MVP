-- convert_to_traditional.sql   (Lesson 6; idempotency parts added in Lesson 8)
-- POST /api/conversions  { "direction": "TO_TRADITIONAL", "quantity": 10, "idempotencyKey": "..." }
-- Burn, inside ONE account: tokenized_units -= q, traditional_units += q. The total does not change.
-- Same app steps, same named-parameter dict, same one-connection/one-transaction pattern, same
-- rowcount rule and error mapping as convert_to_tokenized.sql; only the type, the balance that is
-- checked, and the signs are swapped.
-- request_hash e.g. 'CONVERT_TO_TRADITIONAL|102|1|10'  (type|account_id|asset_id|quantity).

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

-- name: apply_conversion
UPDATE positions
SET tokenized_units   = tokenized_units   - %(quantity)s,
    traditional_units = traditional_units + %(quantity)s
WHERE account_id = %(account_id)s
  AND asset_id = %(asset_id)s
RETURNING traditional_units, tokenized_units;
-- APP DECIDES: rowcount must be 1, otherwise raise.
-- Safety net: the CHECK (tokenized_units >= 0).

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
