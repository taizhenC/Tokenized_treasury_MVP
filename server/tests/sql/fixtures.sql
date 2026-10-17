-- fixtures.sql - deliberate corruptions for the reconciliation drills, each with its undo.

-- name: corrupt_a_move_without_ledger
-- A: silently move 5 traditional -> tokenized in account 102 (no ledger lines).
UPDATE positions SET traditional_units = traditional_units - 5, tokenized_units = tokenized_units + 5
WHERE account_id = 102 AND asset_id = 1;

-- name: undo_a
UPDATE positions SET traditional_units = traditional_units + 5, tokenized_units = tokenized_units - 5
WHERE account_id = 102 AND asset_id = 1;

-- name: corrupt_b_create_from_nothing
-- B: create 7 tokenized units out of nothing in account 103 (no ledger lines).
UPDATE positions SET tokenized_units = tokenized_units + 7 WHERE account_id = 103 AND asset_id = 1;

-- name: undo_b
UPDATE positions SET tokenized_units = tokenized_units - 7 WHERE account_id = 103 AND asset_id = 1;

-- name: corrupt_c_stray_ledger_line
-- C: a stray +4 TOKENIZED ledger line on Delta (104); positions untouched.
INSERT INTO ledger_entries (operation_id, account_id, asset_id, representation, units_delta)
VALUES ((SELECT min(id) FROM operations WHERE operation_type = 'OPENING'), 104, 1, 'TOKENIZED', 4);

-- name: undo_c
DELETE FROM ledger_entries WHERE account_id = 104;

-- name: corrupt_d_position
-- D: +7 tokenized in 103 WITH a matching +7 ledger line (only check 1 can see it).
UPDATE positions SET tokenized_units = tokenized_units + 7 WHERE account_id = 103 AND asset_id = 1;

-- name: corrupt_d_ledger
INSERT INTO ledger_entries (operation_id, account_id, asset_id, representation, units_delta)
VALUES ((SELECT min(id) FROM operations WHERE operation_type = 'OPENING'), 103, 1, 'TOKENIZED', 7);

-- name: undo_d_ledger
DELETE FROM ledger_entries
WHERE account_id = 103 AND units_delta = 7 AND representation = 'TOKENIZED'
  AND operation_id = (SELECT min(id) FROM operations WHERE operation_type = 'OPENING');

-- name: undo_d_position
UPDATE positions SET tokenized_units = tokenized_units - 7 WHERE account_id = 103 AND asset_id = 1;

-- name: corrupt_e_custody_figure
-- E: the custodian's figure changed to 2999.
UPDATE assets SET total_custodied_units = 2999 WHERE id = 1;

-- name: undo_e
UPDATE assets SET total_custodied_units = 3000 WHERE id = 1;
