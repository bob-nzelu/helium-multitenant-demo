-- 006_harmonize_role_casing.sql
-- Harmonizes role_id values from lowercase to Capitalized across
-- roles, role_permissions, and users tables.
-- Fresh installs get Capitalized values via 001_initial_schema.sql.

-- ─── Step 1: Add temporary capitalized roles ─────────────────────
-- (needed before FK-constrained tables can be updated)
INSERT OR IGNORE INTO roles (role_id, role_name, description)
    SELECT 'Owner', role_name, description FROM roles WHERE role_id = 'owner';
INSERT OR IGNORE INTO roles (role_id, role_name, description)
    SELECT 'Admin', role_name, description FROM roles WHERE role_id = 'admin';
INSERT OR IGNORE INTO roles (role_id, role_name, description)
    SELECT 'Operator', role_name, description FROM roles WHERE role_id = 'operator';
INSERT OR IGNORE INTO roles (role_id, role_name, description)
    SELECT 'Support', role_name, description FROM roles WHERE role_id = 'support';

-- ─── Step 2: Migrate users to capitalized role_id ────────────────
UPDATE users SET role_id = 'Owner'    WHERE role_id = 'owner';
UPDATE users SET role_id = 'Admin'    WHERE role_id = 'admin';
UPDATE users SET role_id = 'Operator' WHERE role_id = 'operator';
UPDATE users SET role_id = 'Support'  WHERE role_id = 'support';

-- ─── Step 3: Migrate role_permissions ────────────────────────────
UPDATE role_permissions SET role_id = 'Owner'    WHERE role_id = 'owner';
UPDATE role_permissions SET role_id = 'Admin'    WHERE role_id = 'admin';
UPDATE role_permissions SET role_id = 'Operator' WHERE role_id = 'operator';
UPDATE role_permissions SET role_id = 'Support'  WHERE role_id = 'support';

-- ─── Step 4: Remove old lowercase roles ──────────────────────────
DELETE FROM roles WHERE role_id IN ('owner', 'admin', 'operator', 'support');
