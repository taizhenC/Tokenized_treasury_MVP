-- auth.sql   (Lesson 5 - POST /api/login, and loading the session user on every request)
-- Single read statements: the app runs each one on a pooled connection, no transaction block needed.
-- Parameters are NAMED: %(username)s is filled from {"username": ...}. psycopg sends the value
-- separately from the SQL text, so a username can never change the statement (no SQL injection).
-- There is no session table: the session is a signed cookie that holds only the user id.

-- name: find_user_by_username
-- Login. username = the username typed by the user.
SELECT id, username, password_hash, role, account_id
FROM users
WHERE username = %(username)s;
-- APP DECIDES: 0 rows, OR bcrypt.checkpw(password, password_hash) is False -> 401 with the SAME
--   message for both. When 0 rows come back the app still runs bcrypt against a fixed dummy hash,
--   so an unknown username takes about as long as a wrong password.
--   The app gives the pooled connection back BEFORE the slow bcrypt check.
--   Otherwise it stores ONLY the user id in the session: request.session["user_id"] = id, and
--   answers with id, username, role, account_id from this row (never password_hash).

-- name: find_user_by_id
-- Every request. user_id = request.session["user_id"].
-- password_hash is deliberately NOT selected: it never leaves the login route.
SELECT id, username, role, account_id
FROM users
WHERE id = %(user_id)s;
-- APP DECIDES: 0 rows -> 401. Otherwise role and account_id from THIS row drive every
--   authorization decision (never a role or account id sent by the browser).
