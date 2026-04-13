# DATABASE IMPLEMENTATION CHECKLIST - PHASE 0b

**Version:** 1.0
**Date:** 2026-02-05
**Assigned To:** Haiku (Phase 0b Implementation)
**Phase:** Phase 0b (Database Implementation)
**Status:** TASK LIST - Complete checklist for Haiku

---

## OVERVIEW

This checklist provides step-by-step implementation tasks for Phase 0b (Database Implementation). Haiku should complete each task sequentially, marking items complete as they finish.

**Prerequisites**:
- Phase 0a (Infrastructure Architecture) complete ✅
- Read DATABASE_SCHEMAS.md (complete SQL schemas)
- Read DATABASE_DECISIONS.md (architectural rationale)
- PostgreSQL 14+ installed (production)
- SQLite 3.35+ available (test)

**Deliverables**:
- All database schemas created
- Database initialization scripts
- Migration framework
- Database access abstraction layer
- Unit tests (90%+ coverage)

---

## SECTION 1: ENVIRONMENT SETUP

### Task 1.1: PostgreSQL Installation & Configuration

- [ ] Install PostgreSQL 14+ (if not already installed)
- [ ] Create database user: `helium_admin`
- [ ] Generate strong password (min 32 characters)
- [ ] Grant CREATE DATABASE privilege to `helium_admin`
- [ ] Configure pg_hba.conf for local connections
- [ ] Test connection: `psql -U helium_admin -h localhost`

**Validation**:
```bash
psql -U helium_admin -c "SELECT version();"
```

**Expected Output**: PostgreSQL 14.x or higher

---

### Task 1.2: SQLite Verification

- [ ] Verify SQLite installed: `sqlite3 --version`
- [ ] Ensure version >= 3.35 (for generated columns, JSON support)
- [ ] Create test database directory: `./data/test/`
- [ ] Test SQLite FTS5 support: `sqlite3 :memory: "SELECT fts5()"`

**Validation**:
```bash
sqlite3 --version
```

**Expected Output**: 3.35.0 or higher

---

### Task 1.3: Project Structure Setup

- [ ] Create directory structure:
  ```
  Helium/Services/Core/
  ├── schemas/
  │   ├── postgres/
  │   └── sqlite/
  ├── migrations/
  ├── src/
  │   └── database/
  ├── tests/
  │   └── test_database/
  └── scripts/
  ```
- [ ] Copy schemas from DATABASE_SCHEMAS.md to respective directories
- [ ] Create .env file for database credentials (DO NOT commit)
- [ ] Add .env to .gitignore

---

## SECTION 2: POSTGRESQL SCHEMA IMPLEMENTATION

### Task 2.1: Create invoices.db (Production)

- [ ] Extract `invoices` table schema from DATABASE_SCHEMAS.md
- [ ] Save to `schemas/postgres/invoices.sql`
- [ ] Extract `invoice_line_items` schema
- [ ] Extract `invoice_attachments` schema
- [ ] Extract `invoice_history` schema
- [ ] Add all trigger functions (update_updated_at, broadcast_*)
- [ ] Add all indexes
- [ ] Add schema_version table

**Execute**:
```bash
psql -U helium_admin -c "CREATE DATABASE invoices;"
psql -U helium_admin -d invoices -f schemas/postgres/invoices.sql
```

**Validation**:
```sql
-- Verify all tables exist
SELECT table_name FROM information_schema.tables
WHERE table_schema = 'public'
ORDER BY table_name;
```

**Expected Output**: `invoice_attachments`, `invoice_history`, `invoice_line_items`, `invoices`, `schema_version`

---

### Task 2.2: Create customers.db

- [ ] Extract `customers` table schema
- [ ] Save to `schemas/postgres/customers.sql`
- [ ] Extract `customer_name_variants` schema
- [ ] Extract `customer_contacts` schema
- [ ] Add trigger functions
- [ ] Add indexes (including `company_name_normalized`)
- [ ] Add schema_version table

**Execute**:
```bash
psql -U helium_admin -c "CREATE DATABASE customers;"
psql -U helium_admin -d customers -f schemas/postgres/customers.sql
```

**Validation**:
```sql
SELECT table_name FROM information_schema.tables
WHERE table_schema = 'public'
ORDER BY table_name;
```

**Expected Output**: `customer_contacts`, `customer_name_variants`, `customers`, `schema_version`

---

### Task 2.3: Create inventory.db

- [ ] Extract `inventory` table schema
- [ ] Save to `schemas/postgres/inventory.sql`
- [ ] Extract `inventory_name_variants` schema
- [ ] Extract `inventory_transactions` schema
- [ ] Add trigger functions
- [ ] Add indexes (including `product_name_normalized`)
- [ ] Add computed column: `quantity_available`
- [ ] Add schema_version table

**Execute**:
```bash
psql -U helium_admin -c "CREATE DATABASE inventory;"
psql -U helium_admin -d inventory -f schemas/postgres/inventory.sql
```

**Validation**:
```sql
SELECT table_name FROM information_schema.tables
WHERE table_schema = 'public'
ORDER BY table_name;
```

**Expected Output**: `inventory`, `inventory_name_variants`, `inventory_transactions`, `schema_version`

---

### Task 2.4: Create notifications.db

- [ ] Extract `notifications` table schema
- [ ] Save to `schemas/postgres/notifications.sql`
- [ ] Add trigger functions (broadcast_notification_*)
- [ ] Add indexes
- [ ] Add schema_version table

**Execute**:
```bash
psql -U helium_admin -c "CREATE DATABASE notifications;"
psql -U helium_admin -d notifications -f schemas/postgres/notifications.sql
```

**Validation**:
```sql
SELECT table_name FROM information_schema.tables
WHERE table_schema = 'public'
ORDER BY table_name;
```

**Expected Output**: `notifications`, `schema_version`

---

### Task 2.5: Create core.db (PostgreSQL for Production)

- [ ] Extract `core_queue` table schema (PostgreSQL version)
- [ ] Save to `schemas/postgres/core_queue.sql`
- [ ] Add indexes (priority + created_at compound index)
- [ ] Add schema_version table

**Execute**:
```bash
psql -U helium_admin -c "CREATE DATABASE core;"
psql -U helium_admin -d core -f schemas/postgres/core_queue.sql
```

**Validation**:
```sql
SELECT table_name FROM information_schema.tables
WHERE table_schema = 'public'
ORDER BY table_name;
```

**Expected Output**: `core_queue`, `schema_version`

---

## SECTION 3: SQLITE SCHEMA IMPLEMENTATION (TEST)

### Task 3.1: Create invoices_test.db (SQLite)

- [ ] Extract SQLite version of `invoices` schema from DATABASE_SCHEMAS.md
- [ ] Save to `schemas/sqlite/invoices_sqlite.sql`
- [ ] Extract SQLite `invoice_line_items` schema
- [ ] Extract SQLite `invoice_attachments` schema
- [ ] Extract SQLite `invoice_history` schema
- [ ] Add SQLite triggers (update_updated_at)
- [ ] Add indexes (SQLite syntax: `CREATE INDEX idx_name ON table(column)`)
- [ ] Add schema_version table

**Execute**:
```bash
sqlite3 ./data/test/invoices_test.db < schemas/sqlite/invoices_sqlite.sql
```

**Validation**:
```bash
sqlite3 ./data/test/invoices_test.db ".tables"
```

**Expected Output**: `invoice_attachments  invoice_history  invoice_line_items  invoices  schema_version`

---

### Task 3.2: Create core.db (SQLite for Test)

- [ ] Extract SQLite version of `core_queue` schema
- [ ] Save to `schemas/sqlite/core_queue_sqlite.sql`
- [ ] Add indexes
- [ ] Add schema_version table

**Execute**:
```bash
sqlite3 ./data/test/core.db < schemas/sqlite/core_queue_sqlite.sql
```

**Validation**:
```bash
sqlite3 ./data/test/core.db ".tables"
```

**Expected Output**: `core_queue  schema_version`

---

## SECTION 4: DATABASE ACCESS ABSTRACTION LAYER

### Task 4.1: Create Universal ResourceClient

**IMPORTANT**: Read UNIVERSAL_ACCESS_PATTERN.md before implementing this task.

- [ ] Create `src/database/resource_client.py`
- [ ] Implement auto-detect logic (localhost vs remote)
- [ ] Support PostgreSQL connection (psycopg3)
- [ ] Support SQLite connection (sqlite3)
- [ ] Support connection pooling
- [ ] Implement context manager (`with` statement support)
- [ ] Add retry logic for transient errors
- [ ] Add connection health checks

**Interface**:
```python
class ResourceClient:
    def __init__(self, resource_name: str, config: dict):
        """Auto-detect connection type and initialize client"""
        pass

    async def connect(self):
        """Establish database connection"""
        pass

    async def execute(self, query: str, params: tuple = None):
        """Execute SQL query"""
        pass

    async def fetch_one(self, query: str, params: tuple = None):
        """Fetch single row"""
        pass

    async def fetch_all(self, query: str, params: tuple = None):
        """Fetch all rows"""
        pass

    async def transaction(self):
        """Context manager for transactions"""
        pass
```

---

### Task 4.2: Create Database-Specific Clients

- [ ] Create `src/database/postgres_client.py`
  - [ ] Implement connection using psycopg3
  - [ ] Add connection pooling (asyncpg pool)
  - [ ] Implement transaction support
  - [ ] Add prepared statement caching

- [ ] Create `src/database/sqlite_client.py`
  - [ ] Implement connection using aiosqlite
  - [ ] Add WAL mode for concurrency
  - [ ] Implement transaction support
  - [ ] Add query result mapping (dict rows)

---

### Task 4.3: Create Database Configuration

- [ ] Create `src/database/config.py`
- [ ] Load database URLs from environment
- [ ] Support connection string parsing
- [ ] Validate configuration on startup
- [ ] Provide database discovery (which DBs exist)

**Example Config**:
```python
DATABASE_CONFIG = {
    "invoices": {
        "type": "postgresql",
        "host": "localhost",
        "port": 5432,
        "database": "invoices",
        "user": "helium_admin",
        "password": os.getenv("HELIUM_DB_PASSWORD")
    },
    "invoices_test": {
        "type": "sqlite",
        "path": "./data/test/invoices_test.db"
    },
    # ... other databases
}
```

---

### Task 4.4: Create Repository Pattern Classes

- [ ] Create `src/database/repositories/invoice_repository.py`
  - [ ] `create_invoice(invoice_data: dict) -> int`
  - [ ] `get_invoice(invoice_id: str) -> dict`
  - [ ] `update_invoice(invoice_id: str, updates: dict) -> bool`
  - [ ] `delete_invoice(invoice_id: str) -> bool` (soft delete)
  - [ ] `list_invoices(filters: dict, limit: int, offset: int) -> list`

- [ ] Create `src/database/repositories/customer_repository.py`
  - [ ] `create_customer(customer_data: dict) -> int`
  - [ ] `get_customer_by_tin(tin: str) -> dict`
  - [ ] `get_customer_by_rc(rc_number: str) -> dict`
  - [ ] `update_customer(customer_id: str, updates: dict) -> bool`
  - [ ] `add_name_variant(customer_id: int, name_variant: str)`

- [ ] Create `src/database/repositories/inventory_repository.py`
  - [ ] `create_product(product_data: dict) -> int`
  - [ ] `get_product(product_id: str) -> dict`
  - [ ] `update_quantity(product_id: str, quantity_delta: float, transaction_type: str)`
  - [ ] `search_products(query: str) -> list` (fuzzy matching)

- [ ] Create `src/database/repositories/queue_repository.py`
  - [ ] `enqueue(queue_data: dict) -> str` (returns queue_id)
  - [ ] `dequeue(priority_order: bool = True) -> dict`
  - [ ] `mark_processing(queue_id: str)`
  - [ ] `mark_completed(queue_id: str)`
  - [ ] `mark_failed(queue_id: str, error_message: str)`

---

## SECTION 5: MIGRATION FRAMEWORK

### Task 5.1: Create Alembic Configuration

- [ ] Install Alembic: `pip install alembic`
- [ ] Initialize Alembic: `alembic init migrations`
- [ ] Configure `alembic.ini` for multiple databases
- [ ] Create `migrations/env.py` with multi-database support
- [ ] Add version tracking to each database

---

### Task 5.2: Create Initial Migration

- [ ] Generate initial migration: `alembic revision -m "Initial schema"`
- [ ] Populate `upgrade()` function with schema creation
- [ ] Populate `downgrade()` function with schema destruction
- [ ] Test migration on fresh database
- [ ] Test rollback

**Migration Template**:
```python
def upgrade():
    # Apply schema changes
    op.create_table('invoices', ...)
    op.create_index('idx_invoice_id', 'invoices', ['invoice_id'])

def downgrade():
    # Revert schema changes
    op.drop_table('invoices')
```

---

### Task 5.3: Create Migration Helper Scripts

- [ ] Create `scripts/migrate_up.sh` (apply migrations)
- [ ] Create `scripts/migrate_down.sh` (rollback migrations)
- [ ] Create `scripts/migrate_status.sh` (check current version)
- [ ] Add database backup before migration
- [ ] Add rollback on migration failure

---

## SECTION 6: TESTING

### Task 6.1: Unit Tests for ResourceClient

- [ ] Create `tests/test_database/test_resource_client.py`
- [ ] Test auto-detect logic (localhost vs remote)
- [ ] Test PostgreSQL connection
- [ ] Test SQLite connection
- [ ] Test connection pooling
- [ ] Test retry logic
- [ ] Test transaction rollback
- [ ] Test connection errors (network failure, wrong credentials)

**Target**: 90%+ code coverage

---

### Task 6.2: Unit Tests for Repositories

- [ ] Create `tests/test_database/test_invoice_repository.py`
  - [ ] Test create_invoice (success)
  - [ ] Test create_invoice (duplicate invoice_id)
  - [ ] Test get_invoice (exists)
  - [ ] Test get_invoice (not found)
  - [ ] Test soft delete (24-hour recovery)
  - [ ] Test list_invoices with filters

- [ ] Create `tests/test_database/test_customer_repository.py`
  - [ ] Test create_customer with TIN
  - [ ] Test create_customer with RC Number
  - [ ] Test create_customer without TIN or RC (should fail)
  - [ ] Test TIN validation (format: 12345678-001)
  - [ ] Test RC Number validation (format: RC123456)
  - [ ] Test name variant tracking
  - [ ] Test canonical name selection

- [ ] Create `tests/test_database/test_inventory_repository.py`
  - [ ] Test create_product
  - [ ] Test update_quantity (increase)
  - [ ] Test update_quantity (decrease)
  - [ ] Test quantity_available computation
  - [ ] Test transaction history tracking
  - [ ] Test fuzzy product search

- [ ] Create `tests/test_database/test_queue_repository.py`
  - [ ] Test enqueue
  - [ ] Test dequeue (priority ordering)
  - [ ] Test mark_processing
  - [ ] Test mark_completed
  - [ ] Test mark_failed

**Target**: 90%+ code coverage per repository

---

### Task 6.3: Integration Tests

- [ ] Create `tests/test_database/test_integration.py`
- [ ] Test invoice creation with customer reference (cross-database)
- [ ] Test invoice creation with inventory items (cross-database)
- [ ] Test atomic transaction (all databases commit or rollback together)
- [ ] Test WebSocket trigger firing (PostgreSQL only)
- [ ] Test environment flag isolation (customers.db shared)

---

### Task 6.4: Performance Tests

- [ ] Create `tests/test_database/test_performance.py`
- [ ] Benchmark invoice insert (target: <10ms)
- [ ] Benchmark invoice query by invoice_id (target: <5ms)
- [ ] Benchmark invoice list with filters (target: <50ms for 1000 rows)
- [ ] Benchmark customer fuzzy search (target: <100ms)
- [ ] Benchmark queue dequeue (target: <10ms)

---

## SECTION 7: DOCUMENTATION

### Task 7.1: API Documentation

- [ ] Document ResourceClient API (docstrings)
- [ ] Document each repository method (parameters, return values, exceptions)
- [ ] Generate API docs using Sphinx or pdoc
- [ ] Add usage examples for each repository

---

### Task 7.2: Database Setup Guide

- [ ] Create `Documentation/DATABASE_SETUP_GUIDE.md`
- [ ] Step-by-step PostgreSQL installation
- [ ] Step-by-step SQLite setup
- [ ] Environment variable configuration
- [ ] Troubleshooting common issues
- [ ] Backup and restore procedures

---

### Task 7.3: Schema Diagram

- [ ] Generate ER diagram for invoices.db (use `pgModeler` or `dbdiagram.io`)
- [ ] Generate ER diagram for customers.db
- [ ] Generate ER diagram for inventory.db
- [ ] Save diagrams to `Documentation/schemas/`

---

## SECTION 8: DELIVERY & HANDOFF

### Task 8.1: Code Review Checklist

- [ ] All code follows Python PEP 8 style guide
- [ ] All functions have docstrings
- [ ] All functions have type hints
- [ ] No hardcoded credentials (use environment variables)
- [ ] No SQL injection vulnerabilities (use parameterized queries)
- [ ] Error handling for all database operations
- [ ] Logging for all database operations (INFO level)

---

### Task 8.2: Test Coverage Report

- [ ] Run pytest with coverage: `pytest --cov=src/database --cov-report=html`
- [ ] Verify coverage >= 90%
- [ ] Generate coverage report: `coverage html`
- [ ] Save report to `tests/coverage/`

**Validation**:
```bash
pytest --cov=src/database --cov-report=term
```

**Expected Output**: `TOTAL coverage >= 90%`

---

### Task 8.3: Final Validation

- [ ] Run all unit tests: `pytest tests/test_database/`
- [ ] Run integration tests: `pytest tests/test_database/test_integration.py`
- [ ] Run performance tests: `pytest tests/test_database/test_performance.py`
- [ ] Verify all PostgreSQL databases exist and have correct schemas
- [ ] Verify all SQLite databases exist and have correct schemas
- [ ] Test migration up: `alembic upgrade head`
- [ ] Test migration down: `alembic downgrade -1`
- [ ] Test ResourceClient auto-detect (localhost and remote)

---

### Task 8.4: Handoff to Phase 0c (API Framework)

- [ ] Create handoff document: `Documentation/PHASE_0B_HANDOFF.md`
- [ ] List all deliverables with file paths
- [ ] Provide database connection examples
- [ ] Document any known issues or limitations
- [ ] Provide contact information for questions

**Handoff Contents**:
```markdown
# Phase 0b Handoff Document

## Deliverables
- ✅ PostgreSQL schemas (5 databases)
- ✅ SQLite schemas (2 databases)
- ✅ ResourceClient abstraction layer
- ✅ Repository pattern implementations
- ✅ Migration framework (Alembic)
- ✅ Unit tests (90%+ coverage)
- ✅ Integration tests
- ✅ Performance benchmarks

## Database Access Examples
[Code examples...]

## Known Issues
[List any known limitations...]

## Contact
- Haiku: haiku@anthropic.com
```

---

## APPENDIX A: DEPENDENCY INSTALLATION

```bash
# PostgreSQL adapter (async)
pip install psycopg[binary]
pip install psycopg-pool

# SQLite adapter (async)
pip install aiosqlite

# Migration framework
pip install alembic

# Testing
pip install pytest
pip install pytest-asyncio
pip install pytest-cov

# Type checking
pip install mypy
```

---

## APPENDIX B: TESTING COMMANDS

```bash
# Run all tests
pytest tests/test_database/

# Run with coverage
pytest --cov=src/database --cov-report=html tests/test_database/

# Run specific test file
pytest tests/test_database/test_invoice_repository.py

# Run specific test function
pytest tests/test_database/test_invoice_repository.py::test_create_invoice

# Type checking
mypy src/database/
```

---

## APPENDIX C: TROUBLESHOOTING

### Issue: PostgreSQL connection refused

**Solution**:
```bash
# Check PostgreSQL is running
sudo systemctl status postgresql

# Start PostgreSQL
sudo systemctl start postgresql

# Check pg_hba.conf allows local connections
sudo nano /etc/postgresql/14/main/pg_hba.conf
# Ensure line exists: local all helium_admin md5
```

---

### Issue: SQLite database locked

**Solution**:
```python
# Enable WAL mode for better concurrency
conn = sqlite3.connect('database.db')
conn.execute('PRAGMA journal_mode=WAL')
conn.close()
```

---

### Issue: Alembic "Can't locate revision identified by"

**Solution**:
```bash
# Reset Alembic version table
psql -U helium_admin -d invoices -c "DELETE FROM alembic_version;"

# Re-stamp current version
alembic stamp head
```

---

## SUCCESS CRITERIA

Phase 0b is complete when:

- ✅ All 5 PostgreSQL databases created with correct schemas
- ✅ All 2 SQLite databases created with correct schemas
- ✅ ResourceClient abstraction layer implemented
- ✅ All 4 repository classes implemented (Invoice, Customer, Inventory, Queue)
- ✅ Alembic migration framework configured
- ✅ Test coverage >= 90%
- ✅ All integration tests passing
- ✅ Performance benchmarks meet targets
- ✅ Documentation complete (API docs, setup guide, handoff doc)
- ✅ Code review checklist complete

---

**End of Checklist**

**Next Phase**: Phase 0c (API Framework) - Sonnet
