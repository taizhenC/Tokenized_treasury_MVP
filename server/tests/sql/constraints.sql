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


-- ---------- Lesson 11 (migration 004): the cash leg ----------
-- Each attack breaks exactly ONE rule, so the error can only name that rule.
-- They run before any delivery or payment, so every settlement balance is still the seed's
-- (net 0, collateral 1,000 x $95.00, caps alpha $38,440 / beta $15,000 / gamma $25,000 / Delta $0).

-- name: c16_operator_with_an_account
-- The settlement desk holds no account (separation of duties).
INSERT INTO users (id, username, password_hash, role, account_id) VALUES (95, 'x-op', 'h', 'OPERATOR', 101);

-- name: c17_duplicate_settling_bank_id
INSERT INTO settling_banks (id, bank_name) VALUES (201, 'Another bank (demo)');

-- name: c17b_duplicate_settling_bank_name
INSERT INTO settling_banks (id, bank_name) VALUES (203, 'Settling Bank One (demo)');

-- name: c17c_account_settled_by_an_unknown_bank
UPDATE accounts SET settling_bank_id = 299 WHERE id = 104;

-- name: c17d_delete_a_bank_that_still_settles_for_accounts
DELETE FROM settling_banks WHERE id = 201;

-- name: c18_second_settlement_day
-- singleton defaults to true and is the PRIMARY KEY, so a second row would be a second true.
INSERT INTO settlement_day (business_date, phase, max_net_debit_cap_cents) VALUES (CURRENT_DATE, 'OPEN', 3844000);

-- name: c18b_settlement_day_singleton_false
INSERT INTO settlement_day (singleton, business_date, phase, max_net_debit_cap_cents)
VALUES (false, CURRENT_DATE, 'OPEN', 3844000);

-- name: c18c_unknown_phase
UPDATE settlement_day SET phase = 'CLOSED';

-- name: c18d_negative_maximum_net_debit_cap
UPDATE settlement_day SET max_net_debit_cap_cents = -1;

-- name: c18e_settlement_day_without_a_date
UPDATE settlement_day SET business_date = NULL;

-- name: c19_second_collateral_price_for_an_asset
INSERT INTO collateral_prices (asset_id, prior_close_cents, haircut_bps) VALUES (1, 10000, 500);

-- name: c19b_collateral_price_for_an_unknown_asset
INSERT INTO collateral_prices (asset_id, prior_close_cents, haircut_bps) VALUES (99, 10000, 500);

-- name: c19c_zero_prior_close
UPDATE collateral_prices SET prior_close_cents = 0 WHERE asset_id = 1;

-- name: c19d_haircut_below_2_percent
UPDATE collateral_prices SET haircut_bps = 199 WHERE asset_id = 1;

-- name: c19e_haircut_above_100_percent
UPDATE collateral_prices SET haircut_bps = 10001 WHERE asset_id = 1;

-- name: c20_cap_inputs_for_an_unknown_account
INSERT INTO net_debit_cap_inputs (account_id, avg_top3_peak_cents, factor_bps) VALUES (999, 0, 10000);

-- name: c20b_second_cap_inputs_row
INSERT INTO net_debit_cap_inputs (account_id, avg_top3_peak_cents, factor_bps) VALUES (101, 0, 10000);

-- name: c20c_negative_peak_average
UPDATE net_debit_cap_inputs SET avg_top3_peak_cents = -1 WHERE account_id = 101;

-- name: c20d_factor_below_one
UPDATE net_debit_cap_inputs SET factor_bps = 9999 WHERE account_id = 101;

-- name: c20e_factor_above_two
UPDATE net_debit_cap_inputs SET factor_bps = 20001 WHERE account_id = 101;

-- name: c21_debit_beyond_the_net_debit_cap
-- Alpha one cent past its cap ($38,440.00). Its collateral ($95,000) still covers that, so ONLY the cap breaks.
UPDATE settlement_accounts SET net_cents = -net_debit_cap_cents - 1 WHERE account_id = 101;

-- name: c21b_collateral_monitor_below_zero
-- The demo's refused mint of 995 as a raw UPDATE: gamma keeps 105 traditional units of collateral
-- (105 x 9500) against a 1,000,000 debit -> CM = 997,500 - 1,000,000 = -2,500. Within its cap.
UPDATE settlement_accounts SET collateral_value_cents = 105 * 9500, net_cents = -1000000 WHERE account_id = 103;

-- name: c21c_negative_collateral_value
-- net_cents = +1 keeps CM at 0, so only the column's own CHECK breaks.
UPDATE settlement_accounts SET collateral_value_cents = -1, net_cents = 1 WHERE account_id = 104;

-- name: c21d_negative_net_debit_cap
-- net_cents = +1 keeps net >= -cap (1 >= 1), so only the column's own CHECK breaks.
UPDATE settlement_accounts SET net_debit_cap_cents = -1, net_cents = 1 WHERE account_id = 104;

-- name: c21e_second_settlement_account
INSERT INTO settlement_accounts (account_id, net_cents, collateral_value_cents, net_debit_cap_cents) VALUES (101, 0, 0, 0);

-- name: c21f_settlement_account_for_an_unknown_account
INSERT INTO settlement_accounts (account_id, net_cents, collateral_value_cents, net_debit_cap_cents) VALUES (999, 0, 0, 0);

-- name: c21g_null_net_settlement_balance
UPDATE settlement_accounts SET net_cents = NULL WHERE account_id = 101;

-- name: c21h_delta_cannot_take_a_one_cent_debit
-- Delta's cap is $0 (a lab choice): even with one unit's collateral, a 1 cent debit is refused.
UPDATE settlement_accounts SET collateral_value_cents = 9500, net_cents = -1 WHERE account_id = 104;

-- name: c22_amount_zero
INSERT INTO operations (operation_type, actor_user_id, source_account_id, destination_account_id, asset_id, quantity, amount_cents, idempotency_key, request_hash)
VALUES ('DELIVER_ORDER', 11, 101, 102, 1, 5, 0, 'neg-c22', 'h');

-- name: c22b_amount_too_big
-- One cent over $100 billion.
INSERT INTO operations (operation_type, actor_user_id, source_account_id, destination_account_id, asset_id, quantity, amount_cents, idempotency_key, request_hash)
VALUES ('DELIVER_ORDER', 11, 101, 102, 1, 5, 10000000000001, 'neg-c22b', 'h');

-- name: info_amount_at_the_upper_edge_is_accepted
-- Not an attack: exactly $100 billion (10,000,000,000,000 cents) is the largest amount allowed.
INSERT INTO operations (operation_type, actor_user_id, source_account_id, destination_account_id, asset_id, quantity, amount_cents, idempotency_key, request_hash)
VALUES ('DELIVER_ORDER', 11, 101, 102, 1, 5, 10000000000000, 'info-max-amount', 'h')
RETURNING amount_cents;

-- name: c23_self_delivery_order
INSERT INTO operations (operation_type, actor_user_id, source_account_id, destination_account_id, asset_id, quantity, amount_cents, idempotency_key, request_hash)
VALUES ('DELIVER_ORDER', 11, 101, 101, 1, 5, 100, 'neg-c23', 'h');

-- name: c24_system_key_used_twice
-- One order settled twice: actor NULL in both rows. NULLS NOT DISTINCT makes the two NULLs equal.
INSERT INTO operations (operation_type, actor_user_id, source_account_id, destination_account_id, asset_id, quantity, amount_cents, idempotency_key, request_hash)
VALUES ('DVP_SETTLEMENT', NULL, 101, 102, 1, 5, 100, 'dvp-neg', 'system'),
       ('DVP_SETTLEMENT', NULL, 101, 102, 1, 5, 100, 'dvp-neg', 'system');

-- name: s7_deliver_order_without_an_amount
INSERT INTO operations (operation_type, actor_user_id, source_account_id, destination_account_id, asset_id, quantity, idempotency_key, request_hash)
VALUES ('DELIVER_ORDER', 11, 101, 102, 1, 5, 'neg-s7', 'h');

-- name: s8_dvp_settlement_owned_by_a_person
-- A settlement is a system event (actor NULL): no participant's key can replay or block it.
INSERT INTO operations (operation_type, actor_user_id, source_account_id, destination_account_id, asset_id, quantity, amount_cents, idempotency_key, request_hash)
VALUES ('DVP_SETTLEMENT', 11, 101, 102, 1, 5, 100, 'neg-s8', 'h');

-- name: s9_payment_that_moves_units
INSERT INTO operations (operation_type, actor_user_id, destination_account_id, asset_id, quantity, amount_cents, idempotency_key, request_hash)
VALUES ('SETTLEMENT_PROGRESS_PAYMENT', 13, 103, 1, 5, 50000, 'neg-s9', 'h');

-- name: s10_payment_from_a_source_account
INSERT INTO operations (operation_type, actor_user_id, source_account_id, destination_account_id, amount_cents, idempotency_key, request_hash)
VALUES ('SETTLEMENT_PROGRESS_PAYMENT', 13, 102, 103, 50000, 'neg-s10', 'h');

-- name: s11_valued_cutoff_naming_an_account
INSERT INTO operations (operation_type, actor_user_id, destination_account_id, idempotency_key, request_hash)
VALUES ('VALUED_CUTOFF', 15, 101, 'neg-s11', 'h');

-- name: s12_final_settlement_without_an_actor
INSERT INTO operations (operation_type, idempotency_key, request_hash)
VALUES ('FINAL_SETTLEMENT', 'neg-s12', 'h');

-- name: s13_transfer_with_an_amount
-- The old types keep exactly their old rules: a tokenized transfer carries no money.
INSERT INTO operations (operation_type, actor_user_id, source_account_id, destination_account_id, asset_id, quantity, amount_cents, idempotency_key, request_hash)
VALUES ('TRANSFER', 11, 101, 102, 1, 5, 100, 'neg-s13', 'h');

-- name: s14_opening_without_an_asset
-- asset_id lost NOT NULL in 004 (a payment moves no units); the shape CHECK still demands it here.
INSERT INTO operations (operation_type, destination_account_id, quantity, idempotency_key, request_hash)
VALUES ('OPENING', 101, 5, 'neg-s14', 'h');

-- name: s15_conversion_without_a_quantity
INSERT INTO operations (operation_type, actor_user_id, source_account_id, asset_id, idempotency_key, request_hash)
VALUES ('CONVERT_TO_TOKENIZED', 11, 101, 1, 'neg-s15', 'h');

-- name: c26_instruction_for_an_unknown_order
INSERT INTO delivery_instructions (submitted_operation_id, business_date, deliverer_account_id, receiver_account_id, asset_id, quantity, amount_cents, status, recycle_reason)
VALUES (999999, CURRENT_DATE, 101, 102, 1, 5, 100, 'RECYCLING', 'RECEIVER_NET_DEBIT_CAP');

-- name: c26b_two_instructions_for_one_order
INSERT INTO delivery_instructions (submitted_operation_id, business_date, deliverer_account_id, receiver_account_id, asset_id, quantity, amount_cents, status, recycle_reason)
VALUES ((SELECT min(id) FROM operations), CURRENT_DATE, 101, 102, 1, 5, 100, 'RECYCLING', 'RECEIVER_NET_DEBIT_CAP'),
       ((SELECT min(id) FROM operations), CURRENT_DATE, 101, 102, 1, 5, 100, 'RECYCLING', 'RECEIVER_NET_DEBIT_CAP');

-- name: c26c_one_settlement_for_two_instructions
-- Two different orders claiming the same settlement event (one event settles at most one order).
INSERT INTO delivery_instructions (submitted_operation_id, business_date, deliverer_account_id, receiver_account_id, asset_id, quantity, amount_cents, status, settlement_operation_id)
VALUES ((SELECT min(id) FROM operations), CURRENT_DATE, 101, 102, 1, 5, 100, 'SETTLED', (SELECT min(id) FROM operations)),
       ((SELECT max(id) FROM operations), CURRENT_DATE, 101, 102, 1, 5, 100, 'SETTLED', (SELECT min(id) FROM operations));

-- name: c26d_settled_by_an_unknown_operation
INSERT INTO delivery_instructions (submitted_operation_id, business_date, deliverer_account_id, receiver_account_id, asset_id, quantity, amount_cents, status, settlement_operation_id)
VALUES ((SELECT min(id) FROM operations), CURRENT_DATE, 101, 102, 1, 5, 100, 'SETTLED', 999999);

-- name: c26e_unknown_deliverer
INSERT INTO delivery_instructions (submitted_operation_id, business_date, deliverer_account_id, receiver_account_id, asset_id, quantity, amount_cents, status, recycle_reason)
VALUES ((SELECT min(id) FROM operations), CURRENT_DATE, 999, 102, 1, 5, 100, 'RECYCLING', 'INSUFFICIENT_POSITION');

-- name: c26f_unknown_receiver
INSERT INTO delivery_instructions (submitted_operation_id, business_date, deliverer_account_id, receiver_account_id, asset_id, quantity, amount_cents, status, recycle_reason)
VALUES ((SELECT min(id) FROM operations), CURRENT_DATE, 101, 999, 1, 5, 100, 'RECYCLING', 'RECEIVER_NET_DEBIT_CAP');

-- name: c26g_unknown_asset
INSERT INTO delivery_instructions (submitted_operation_id, business_date, deliverer_account_id, receiver_account_id, asset_id, quantity, amount_cents, status, recycle_reason)
VALUES ((SELECT min(id) FROM operations), CURRENT_DATE, 101, 102, 99, 5, 100, 'RECYCLING', 'RECEIVER_NET_DEBIT_CAP');

-- name: c27_instruction_quantity_zero
INSERT INTO delivery_instructions (submitted_operation_id, business_date, deliverer_account_id, receiver_account_id, asset_id, quantity, amount_cents, status, recycle_reason)
VALUES ((SELECT min(id) FROM operations), CURRENT_DATE, 101, 102, 1, 0, 100, 'RECYCLING', 'RECEIVER_NET_DEBIT_CAP');

-- name: c27b_instruction_amount_zero
INSERT INTO delivery_instructions (submitted_operation_id, business_date, deliverer_account_id, receiver_account_id, asset_id, quantity, amount_cents, status, recycle_reason)
VALUES ((SELECT min(id) FROM operations), CURRENT_DATE, 101, 102, 1, 5, 0, 'RECYCLING', 'RECEIVER_NET_DEBIT_CAP');

-- name: c27c_unknown_status
-- It keeps a reason and has no settlement, so only the list of statuses refuses it.
INSERT INTO delivery_instructions (submitted_operation_id, business_date, deliverer_account_id, receiver_account_id, asset_id, quantity, amount_cents, status, recycle_reason)
VALUES ((SELECT min(id) FROM operations), CURRENT_DATE, 101, 102, 1, 5, 100, 'PENDING', 'RECEIVER_NET_DEBIT_CAP');

-- name: c27d_unknown_recycle_reason
-- WAITING_ON_COUNTERPARTY is what a participant is SHOWN (masking); the database keeps the real reason.
INSERT INTO delivery_instructions (submitted_operation_id, business_date, deliverer_account_id, receiver_account_id, asset_id, quantity, amount_cents, status, recycle_reason)
VALUES ((SELECT min(id) FROM operations), CURRENT_DATE, 101, 102, 1, 5, 100, 'RECYCLING', 'WAITING_ON_COUNTERPARTY');

-- name: c27e_self_delivery_instruction
INSERT INTO delivery_instructions (submitted_operation_id, business_date, deliverer_account_id, receiver_account_id, asset_id, quantity, amount_cents, status, recycle_reason)
VALUES ((SELECT min(id) FROM operations), CURRENT_DATE, 101, 101, 1, 5, 100, 'RECYCLING', 'RECEIVER_NET_DEBIT_CAP');

-- name: c28_settled_without_a_settlement_operation
INSERT INTO delivery_instructions (submitted_operation_id, business_date, deliverer_account_id, receiver_account_id, asset_id, quantity, amount_cents, status)
VALUES ((SELECT min(id) FROM operations), CURRENT_DATE, 101, 102, 1, 5, 100, 'SETTLED');

-- name: c28b_recycling_with_a_settlement_operation
INSERT INTO delivery_instructions (submitted_operation_id, business_date, deliverer_account_id, receiver_account_id, asset_id, quantity, amount_cents, status, recycle_reason, settlement_operation_id)
VALUES ((SELECT min(id) FROM operations), CURRENT_DATE, 101, 102, 1, 5, 100, 'RECYCLING', 'RECEIVER_NET_DEBIT_CAP', (SELECT max(id) FROM operations));

-- name: c28c_settled_with_a_recycle_reason
INSERT INTO delivery_instructions (submitted_operation_id, business_date, deliverer_account_id, receiver_account_id, asset_id, quantity, amount_cents, status, recycle_reason, settlement_operation_id)
VALUES ((SELECT min(id) FROM operations), CURRENT_DATE, 101, 102, 1, 5, 100, 'SETTLED', 'RECEIVER_NET_DEBIT_CAP', (SELECT max(id) FROM operations));

-- name: c28d_recycling_without_a_reason
INSERT INTO delivery_instructions (submitted_operation_id, business_date, deliverer_account_id, receiver_account_id, asset_id, quantity, amount_cents, status)
VALUES ((SELECT min(id) FROM operations), CURRENT_DATE, 101, 102, 1, 5, 100, 'RECYCLING');

-- name: c28e_dropped_without_a_reason
INSERT INTO delivery_instructions (submitted_operation_id, business_date, deliverer_account_id, receiver_account_id, asset_id, quantity, amount_cents, status)
VALUES ((SELECT min(id) FROM operations), CURRENT_DATE, 101, 102, 1, 5, 100, 'DROPPED_AT_CUTOFF');

-- name: c29_cash_line_for_an_unknown_operation
INSERT INTO cash_ledger_entries (operation_id, business_date, ledger, account_id, amount_cents)
VALUES (999999, CURRENT_DATE, 'PARTICIPANT_SETTLEMENT', 101, 5);

-- name: c29b_cash_line_for_an_unknown_settlement_account
INSERT INTO cash_ledger_entries (operation_id, business_date, ledger, account_id, amount_cents)
VALUES ((SELECT min(id) FROM operations), CURRENT_DATE, 'PARTICIPANT_SETTLEMENT', 999, 5);

-- name: c29c_zero_cash_line
INSERT INTO cash_ledger_entries (operation_id, business_date, ledger, account_id, amount_cents)
VALUES ((SELECT min(id) FROM operations), CURRENT_DATE, 'PARTICIPANT_SETTLEMENT', 101, 0);

-- name: c29d_unknown_cash_ledger
INSERT INTO cash_ledger_entries (operation_id, business_date, ledger, account_id, amount_cents)
VALUES ((SELECT min(id) FROM operations), CURRENT_DATE, 'CASH', NULL, 5);

-- name: c29e_participant_line_without_an_account
INSERT INTO cash_ledger_entries (operation_id, business_date, ledger, account_id, amount_cents)
VALUES ((SELECT min(id) FROM operations), CURRENT_DATE, 'PARTICIPANT_SETTLEMENT', NULL, 5);

-- name: c29f_fed_line_naming_an_account
INSERT INTO cash_ledger_entries (operation_id, business_date, ledger, account_id, amount_cents)
VALUES ((SELECT min(id) FROM operations), CURRENT_DATE, 'DTC_FED_ACCOUNT', 101, 5);

-- name: c29g_cash_line_without_a_business_date
INSERT INTO cash_ledger_entries (operation_id, ledger, account_id, amount_cents)
VALUES ((SELECT min(id) FROM operations), 'PARTICIPANT_SETTLEMENT', 101, 5);

-- name: c29h_participant_line_naming_a_bank
-- Only a Fed line may name a settling bank; a participant line already names its account.
INSERT INTO cash_ledger_entries (operation_id, business_date, ledger, account_id, settling_bank_id, amount_cents)
VALUES ((SELECT min(id) FROM operations), CURRENT_DATE, 'PARTICIPANT_SETTLEMENT', 101, 201, 5);

-- name: c29i_fed_line_for_an_unknown_bank
INSERT INTO cash_ledger_entries (operation_id, business_date, ledger, account_id, settling_bank_id, amount_cents)
VALUES ((SELECT min(id) FROM operations), CURRENT_DATE, 'DTC_FED_ACCOUNT', NULL, 299, 5);

-- name: c30_a_day_closes_only_once
INSERT INTO settlement_figures (business_date, account_id, final_net_cents)
VALUES (CURRENT_DATE, 101, 5), (CURRENT_DATE, 101, 5);

-- name: c30b_figure_for_an_unknown_settlement_account
INSERT INTO settlement_figures (business_date, account_id, final_net_cents) VALUES (CURRENT_DATE, 999, 5);

-- name: c30c_two_nss_lines_for_one_bank
INSERT INTO nss_file_lines (business_date, settling_bank_id, net_net_cents)
VALUES (CURRENT_DATE, 201, 5), (CURRENT_DATE, 201, 5);

-- name: c30d_nss_line_for_an_unknown_bank
INSERT INTO nss_file_lines (business_date, settling_bank_id, net_net_cents) VALUES (CURRENT_DATE, 299, 5);

-- name: l11_typed_generated_column
-- collateral_cents_per_unit is computed by Postgres: nobody can type a value into it.
UPDATE collateral_prices SET collateral_cents_per_unit = 10000 WHERE asset_id = 1;

-- name: info_generated_column_follows_the_haircut
-- Not an attack: a 10% haircut instead of 5% -> 10000 x 9000 / 10000 = 9000, recomputed by Postgres.
UPDATE collateral_prices SET haircut_bps = 1000 WHERE asset_id = 1
RETURNING collateral_cents_per_unit;
