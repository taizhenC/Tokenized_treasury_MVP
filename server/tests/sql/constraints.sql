-- constraints.sql - attacks the database must REJECT.
-- Each block is one statement; the test runs it in a transaction that is always rolled back and
-- expects the error named in tests/test_20_constraints.py.

-- name: c01_negative_tokenized_balance
UPDATE positions SET tokenized_units = tokenized_units - 1000000 WHERE account_id = 101 AND asset_id = 1;

-- name: c01b_negative_traditional_balance
UPDATE positions SET traditional_units = -1 WHERE account_id = 104 AND asset_id = 1;

-- name: c02_quantity_zero
INSERT INTO operations (operation_type, actor_user_id, source_account_id, destination_account_id, asset_id, quantity, idempotency_key, request_hash)
VALUES ('TRANSFER', 11, 101, 102, 1, 0, 'neg-c2', 'h');

-- name: c02b_quantity_too_big
INSERT INTO operations (operation_type, actor_user_id, source_account_id, destination_account_id, asset_id, quantity, idempotency_key, request_hash)
VALUES ('TRANSFER', 11, 101, 102, 1, 1000001, 'neg-c2b', 'h');

-- name: c03_duplicate_actor_key
INSERT INTO operations (operation_type, actor_user_id, source_account_id, asset_id, quantity, idempotency_key, request_hash)
VALUES ('CONVERT_TO_TOKENIZED', 11, 101, 1, 5, 'neg-dup', 'h'),
       ('CONVERT_TO_TOKENIZED', 11, 101, 1, 5, 'neg-dup', 'h');

-- name: c04_representation_cash
INSERT INTO ledger_entries (operation_id, account_id, asset_id, representation, units_delta)
VALUES ((SELECT min(id) FROM operations), 101, 1, 'CASH', 5);

-- name: c04b_representation_lowercase
INSERT INTO ledger_entries (operation_id, account_id, asset_id, representation, units_delta)
VALUES ((SELECT min(id) FROM operations), 101, 1, 'tokenized', 5);

-- name: c05_ledger_line_without_position
INSERT INTO ledger_entries (operation_id, account_id, asset_id, representation, units_delta)
VALUES ((SELECT min(id) FROM operations), 99, 1, 'TOKENIZED', 5);

-- name: c05b_ledger_line_for_missing_position_asset
INSERT INTO ledger_entries (operation_id, account_id, asset_id, representation, units_delta)
VALUES ((SELECT min(id) FROM operations), 104, 2, 'TOKENIZED', 5);

-- name: c05c_ledger_line_for_missing_operation
INSERT INTO ledger_entries (operation_id, account_id, asset_id, representation, units_delta)
VALUES (999999, 101, 1, 'TOKENIZED', 5);

-- name: c05d_ledger_line_with_ids_swapped
INSERT INTO ledger_entries (operation_id, account_id, asset_id, representation, units_delta)
VALUES ((SELECT min(id) FROM operations), 1, 101, 'TOKENIZED', 5);

-- name: c06_operation_type_mint
INSERT INTO operations (operation_type, actor_user_id, source_account_id, asset_id, quantity, idempotency_key, request_hash)
VALUES ('MINT', 11, 101, 1, 5, 'neg-c6', 'h');

-- name: c07_self_transfer
INSERT INTO operations (operation_type, actor_user_id, source_account_id, destination_account_id, asset_id, quantity, idempotency_key, request_hash)
VALUES ('TRANSFER', 11, 101, 101, 1, 5, 'neg-c7', 'h');

-- name: c08_transfer_without_actor
INSERT INTO operations (operation_type, actor_user_id, source_account_id, destination_account_id, asset_id, quantity, idempotency_key, request_hash)
VALUES ('TRANSFER', NULL, 101, 102, 1, 5, 'neg-c8', 'h');

-- name: c08b_unknown_actor
INSERT INTO operations (operation_type, actor_user_id, source_account_id, asset_id, quantity, idempotency_key, request_hash)
VALUES ('CONVERT_TO_TOKENIZED', 999, 101, 1, 5, 'neg-c8b', 'h');

-- name: c08c_unknown_destination
INSERT INTO operations (operation_type, actor_user_id, source_account_id, destination_account_id, asset_id, quantity, idempotency_key, request_hash)
VALUES ('TRANSFER', 11, 101, 999, 1, 5, 'neg-c8c', 'h');

-- name: c08d_missing_idempotency_key
INSERT INTO operations (operation_type, actor_user_id, source_account_id, asset_id, quantity, request_hash)
VALUES ('CONVERT_TO_TOKENIZED', 11, 101, 1, 5, 'h');

-- name: c08e_empty_idempotency_key
INSERT INTO operations (operation_type, actor_user_id, source_account_id, asset_id, quantity, idempotency_key, request_hash)
VALUES ('CONVERT_TO_TOKENIZED', 11, 101, 1, 5, '', 'h');

-- name: s1_conversion_with_destination
INSERT INTO operations (operation_type, actor_user_id, source_account_id, destination_account_id, asset_id, quantity, idempotency_key, request_hash)
VALUES ('CONVERT_TO_TOKENIZED', 11, 101, 102, 1, 5, 'neg-s1', 'h');

-- name: s2_transfer_without_accounts
INSERT INTO operations (operation_type, actor_user_id, asset_id, quantity, idempotency_key, request_hash)
VALUES ('TRANSFER', 11, 1, 5, 'neg-s2', 'h');

-- name: s3_transfer_without_destination
INSERT INTO operations (operation_type, actor_user_id, source_account_id, asset_id, quantity, idempotency_key, request_hash)
VALUES ('TRANSFER', 11, 101, 1, 5, 'neg-s3', 'h');

-- name: s4_conversion_without_account
INSERT INTO operations (operation_type, actor_user_id, asset_id, quantity, idempotency_key, request_hash)
VALUES ('CONVERT_TO_TRADITIONAL', 11, 1, 5, 'neg-s4', 'h');

-- name: s5_opening_with_actor_and_source
INSERT INTO operations (operation_type, actor_user_id, source_account_id, destination_account_id, asset_id, quantity, idempotency_key, request_hash)
VALUES ('OPENING', 11, 102, 101, 1, 5, 'neg-s5', 'h');

-- name: s6_opening_credited_to_a_person
INSERT INTO operations (operation_type, actor_user_id, destination_account_id, asset_id, quantity, idempotency_key, request_hash)
VALUES ('OPENING', 11, 101, 1, 5, 'neg-s6', 'h');

-- name: c09_zero_ledger_delta
INSERT INTO ledger_entries (operation_id, account_id, asset_id, representation, units_delta)
VALUES ((SELECT min(id) FROM operations), 101, 1, 'TOKENIZED', 0);

-- name: c10_second_position_row
INSERT INTO positions (account_id, asset_id, traditional_units, tokenized_units) VALUES (101, 1, 0, 0);

-- name: c10b_null_balance
UPDATE positions SET tokenized_units = NULL WHERE account_id = 101;

-- name: c11_role_admin
INSERT INTO users (id, username, password_hash, role, account_id) VALUES (90, 'x-admin', 'h', 'ADMIN', NULL);

-- name: c11b_participant_without_account
INSERT INTO users (id, username, password_hash, role, account_id) VALUES (91, 'x-p', 'h', 'PARTICIPANT', NULL);

-- name: c11c_auditor_with_account
INSERT INTO users (id, username, password_hash, role, account_id) VALUES (92, 'x-a', 'h', 'AUDITOR', 101);

-- name: c11d_duplicate_username
INSERT INTO users (id, username, password_hash, role, account_id) VALUES (93, 'alpha', 'h', 'PARTICIPANT', 101);

-- name: c11e_participant_linked_to_a_user_id
INSERT INTO users (id, username, password_hash, role, account_id) VALUES (94, 'x-swap', 'h', 'PARTICIPANT', 11);

-- name: c12_unit_precision_two
UPDATE assets SET unit_precision = 2 WHERE id = 1;

-- name: c12b_negative_custody
UPDATE assets SET total_custodied_units = -1 WHERE id = 1;

-- name: c13_duplicate_wallet_ref
INSERT INTO accounts (id, participant_name, wallet_ref, is_approved) VALUES (109, 'Dup', 'SIM-WALLET-ALPHA', true);

-- name: c15_delete_position_with_history
DELETE FROM positions WHERE account_id = 101;

-- name: l3_typed_identity_id
INSERT INTO operations (id, operation_type, actor_user_id, source_account_id, asset_id, quantity, idempotency_key, request_hash)
VALUES (1, 'CONVERT_TO_TOKENIZED', 11, 101, 1, 5, 'neg-id', 'h');

-- name: info_new_account_not_approved_by_default
INSERT INTO accounts (id, participant_name, wallet_ref) VALUES (109, 'Default check', 'SIM-WALLET-TMP')
RETURNING id, is_approved;
