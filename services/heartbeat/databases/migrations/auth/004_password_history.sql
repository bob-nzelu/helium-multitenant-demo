-- Migration 004 — Password History
-- Stores bcrypt hashes of the last N passwords per user so that
-- change_password() can reject recycled passwords (PW_RECYCLED).
--
-- Policy (enforced in auth_handler.change_password):
--   • A new password must not match any of the user's last 5 stored hashes.
--   • The current active hash is already in users.password_hash;
--     history rows are prior passwords only.
--   • Hashes are kept in insertion order (set_at DESC for newest-first queries).
--   • On password change:
--       1. Query last 5 history rows + current password_hash.
--       2. bcrypt.checkpw(new_pw, each_hash) — reject if any match (PW_RECYCLED).
--       3. INSERT current hash into history before overwriting users.password_hash.
--       4. DELETE history rows beyond the 5-row cap (per user, oldest first).
--
-- Cascade:  rows are automatically deleted when the parent user is deleted.

CREATE TABLE IF NOT EXISTS password_history (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    user_id     TEXT    NOT NULL,
    hash        TEXT    NOT NULL,   -- bcrypt hash of the historical password
    set_at      TEXT    NOT NULL,   -- ISO-8601 UTC timestamp when this password was set
    FOREIGN KEY (user_id) REFERENCES users(user_id) ON DELETE CASCADE
);

CREATE INDEX IF NOT EXISTS idx_pw_history_user_recent
    ON password_history(user_id, set_at DESC);

-- Track this migration
INSERT OR IGNORE INTO schema_migrations (version, applied_at)
VALUES ('004_password_history', datetime('now'));
