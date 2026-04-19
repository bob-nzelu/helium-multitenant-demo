-- HeartBeat Auth Seed Data
-- Default roles, permissions, and step-up policies
-- Date: 2026-03-03

-- Default roles
INSERT INTO auth.roles (role_id, description) VALUES
    ('owner', 'Tenant owner - full system access'),
    ('admin', 'Administrator - manage users and configuration'),
    ('operator', 'Standard operator - business operations'),
    ('support', 'Support role - read-only access')
ON CONFLICT (role_id) DO NOTHING;

-- Default permissions
INSERT INTO auth.permissions (permission_id, description) VALUES
    ('invoice.view', 'View invoices'),
    ('invoice.create', 'Create invoices'),
    ('invoice.approve', 'Approve invoices'),
    ('invoice.finalize', 'Finalize invoices for submission'),
    ('invoice.delete', 'Delete invoices'),
    ('blob.upload', 'Upload files'),
    ('blob.download', 'Download files'),
    ('blob.delete', 'Delete files'),
    ('user.view', 'View user list'),
    ('user.create', 'Create users'),
    ('user.edit', 'Edit user profiles'),
    ('user.deactivate', 'Deactivate users'),
    ('role.assign', 'Assign roles to users'),
    ('config.view', 'View configuration'),
    ('config.edit', 'Edit configuration'),
    ('audit.view', 'View audit logs'),
    ('report.view', 'View reports'),
    ('report.generate', 'Generate reports'),
    ('notification.manage', 'Manage notifications'),
    ('system.admin', 'System administration')
ON CONFLICT (permission_id) DO NOTHING;

-- Role-permission mappings

-- Owner gets everything
INSERT INTO auth.role_permissions (role_id, permission_id)
SELECT 'owner', permission_id FROM auth.permissions
ON CONFLICT (role_id, permission_id) DO NOTHING;

-- Admin gets most things
INSERT INTO auth.role_permissions (role_id, permission_id) VALUES
    ('admin', 'invoice.view'),
    ('admin', 'invoice.create'),
    ('admin', 'invoice.approve'),
    ('admin', 'invoice.finalize'),
    ('admin', 'invoice.delete'),
    ('admin', 'blob.upload'),
    ('admin', 'blob.download'),
    ('admin', 'blob.delete'),
    ('admin', 'user.view'),
    ('admin', 'user.create'),
    ('admin', 'user.edit'),
    ('admin', 'user.deactivate'),
    ('admin', 'role.assign'),
    ('admin', 'config.view'),
    ('admin', 'config.edit'),
    ('admin', 'audit.view'),
    ('admin', 'report.view'),
    ('admin', 'report.generate'),
    ('admin', 'notification.manage')
ON CONFLICT (role_id, permission_id) DO NOTHING;

-- Operator gets business operations
INSERT INTO auth.role_permissions (role_id, permission_id) VALUES
    ('operator', 'invoice.view'),
    ('operator', 'invoice.create'),
    ('operator', 'invoice.approve'),
    ('operator', 'invoice.finalize'),
    ('operator', 'blob.upload'),
    ('operator', 'blob.download'),
    ('operator', 'report.view'),
    ('operator', 'notification.manage')
ON CONFLICT (role_id, permission_id) DO NOTHING;

-- Support gets read-only
INSERT INTO auth.role_permissions (role_id, permission_id) VALUES
    ('support', 'invoice.view'),
    ('support', 'blob.download'),
    ('support', 'report.view'),
    ('support', 'audit.view')
ON CONFLICT (role_id, permission_id) DO NOTHING;

-- Step-up policies (operation tier definitions)
INSERT INTO auth.step_up_policies (operation, tier, required_within_seconds, description) VALUES
    ('invoice.view', 'routine', 3600, 'Viewing invoices - standard JWT sufficient'),
    ('invoice.create', 'routine', 3600, 'Creating invoices - standard JWT sufficient'),
    ('blob.upload', 'routine', 3600, 'Uploading files - standard JWT sufficient'),
    ('blob.download', 'routine', 3600, 'Downloading files - standard JWT sufficient'),
    ('invoice.approve', 'auth', 300, 'Approving invoices - re-auth within 5 minutes'),
    ('invoice.finalize', 'auth', 300, 'Finalizing invoices - re-auth within 5 minutes'),
    ('config.edit', 'auth', 300, 'Editing configuration - re-auth within 5 minutes'),
    ('user.create', 'auth', 600, 'Creating users - re-auth within 10 minutes'),
    ('user.deactivate', 'auth', 300, 'Deactivating users - re-auth within 5 minutes'),
    ('role.assign', 'auth', 300, 'Assigning roles - re-auth within 5 minutes'),
    ('user.create_admin', 'immediate', 0, 'Creating admin users - immediate re-auth'),
    ('owner.deactivate', 'immediate', 0, 'Deactivating owner - immediate re-auth')
ON CONFLICT (operation) DO NOTHING;
