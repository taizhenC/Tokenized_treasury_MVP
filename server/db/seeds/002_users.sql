-- 002_users.sql   (Lesson 4 - DEV SEED DATA)
-- Three participants (one per approved account) and one auditor (no account).
-- DEV-ONLY PASSWORD HASH: every user below logs in with the password  demo-password-1
-- The hash below is a real bcrypt hash of that password (made with bcryptjs; the Python "bcrypt"
-- package that the API uses verifies $2b$ hashes from either library). Make your own with:
--   uv run --directory server python -c "import bcrypt; print(bcrypt.hashpw(b'demo-password-1', bcrypt.gensalt(10)).decode())"
-- and paste YOUR output here, inside single quotes. bcrypt adds a random salt, so your string
-- will differ from this one; both are valid.
-- This file is in seeds/, which the runner applies only with --seed (npm run db:seed). A deployed
-- demo gets its own users with a password you generate and never commit.

INSERT INTO users (id, username, password_hash, role, account_id)
VALUES
  (11, 'alpha',   '$2b$10$HlFur6huP5jxpqqw2wExGeY9qnG33WN/qZ8lrC4UbfZ2r1.XezZCm', 'PARTICIPANT', 101),
  (12, 'beta',    '$2b$10$HlFur6huP5jxpqqw2wExGeY9qnG33WN/qZ8lrC4UbfZ2r1.XezZCm', 'PARTICIPANT', 102),
  (13, 'gamma',   '$2b$10$HlFur6huP5jxpqqw2wExGeY9qnG33WN/qZ8lrC4UbfZ2r1.XezZCm', 'PARTICIPANT', 103),
  (14, 'auditor', '$2b$10$HlFur6huP5jxpqqw2wExGeY9qnG33WN/qZ8lrC4UbfZ2r1.XezZCm', 'AUDITOR',     NULL);
