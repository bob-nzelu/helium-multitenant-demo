-- 003_add_role_to_provisioned_users.sql
-- Adds role_id to provisioned_users for existing databases.
-- Fresh installs get this via 001_initial_schema.sql.

ALTER TABLE provisioned_users
    ADD COLUMN role_id TEXT NOT NULL DEFAULT 'Operator'
        CHECK(role_id IN ('Owner', 'Admin', 'Operator', 'Support'));

-- Backfill: promote any existing provisioned user to Owner.
-- Rationale: if a user was provisioned without a role they came
-- from the installer as the initial admin — Owner is the correct
-- default for that case. A fresh install will set role explicitly.
UPDATE provisioned_users SET role_id = 'Owner' WHERE role_id = 'Operator';
