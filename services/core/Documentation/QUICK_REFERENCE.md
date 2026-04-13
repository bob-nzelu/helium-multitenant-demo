# CORE SERVICE - QUICK REFERENCE GUIDE

**Version:** 1.0
**Last Updated:** 2026-01-31
**Purpose:** Quick lookup for commonly needed information

---

## 🎯 QUICK LOOKUP BY ROLE

### 👤 User (Project Manager/Architect)

**Your Documents:**
- [ ] README.md - Navigation & overview
- [ ] CORE_CLAUDE.md - Binding protocol
- [ ] DECISIONS.md - Architectural decisions
- [ ] PHASES_OVERVIEW.md - Phase structure & timeline
- [ ] IMPLEMENTATION_SUMMARY.md - Your next steps

**Your Tasks:**
1. Review & approve documentation
2. Create/customize PHASE_X_DECISIONS.md files (8 phases)
3. Create/customize PHASE_X_CHECKLIST.md files (8 phases)
4. Create/customize API_CONTRACTS.md files (8 phases)
5. Share with OPUS (Infrastructure)
6. Coordinate variant schedules

**Timeline:** ~20-30 hours over next 2 weeks

---

### 🧠 OPUS (Infrastructure + Phases 6, 7, 8)

**Your Documents:**
- [ ] CORE_CLAUDE.md (binding protocol)
- [ ] DECISIONS.md (architectural decisions)
- [ ] PHASES_OVERVIEW.md (understand phases)
- [ ] PHASE_TEMPLATE.md (phase structure)

**Your Timeline:**
- **Weeks 1:** Build Infrastructure (foundation for all phases)
- **Weeks 3-4:** Implement Phases 6-8 (after HAIKU/SONNET complete 1-5)

**Your Responsibilities:**
1. Build database layer (5 tables)
2. Build API framework (FastAPI + 18 endpoints)
3. Build WebSocket server
4. Build access control system
5. Implement Phases 6-8 workers
6. Write tests (90%+ coverage)
7. Update status files every 1-2 hours

**Key Decisions:**
- Database: SQLite (all tiers)
- Workers: Tier-specific (threading vs Celery)
- Preview Mode: Two-stage (preview → finalize)
- Retry/Retransmit: Two different endpoints (full cycle vs exchange only)

**Critical Requirements:**
- 🚨 NO HALLUCINATIONS - ask if unclear
- ✅ Read DECISIONS.md first
- ✅ 90%+ test coverage (mandatory)
- ✅ Update status every 1-2 hours
- ✅ Commit each component when done

---

### 🐰 HAIKU (Phases 1 & 2)

**Your Documents:**
- [ ] CORE_CLAUDE.md (binding protocol)
- [ ] DECISIONS.md (architectural decisions)
- [ ] PHASE_TEMPLATE.md (phase structure)
- [ ] Wait for PHASE_1_DECISIONS.md (from user)
- [ ] Wait for PHASE_2_DECISIONS.md (from user)

**Your Timeline:**
- **Wait for Infrastructure from OPUS**
- **Weeks 1-2:** Implement Phases 1-2 (parallel with OPUS Infrastructure)

**Your Responsibilities:**

**Phase 1 (FETCH):**
1. QueueScannerWorker - Poll core_queue every 60 seconds
2. BlobFetcher - Fetch files from blob storage
3. Error handling - Handle blob not found, read failures
4. Tests - 90%+ coverage
5. Commit

**Phase 2 (PARSE):**
1. FileParserWorker - Detect file type & parse
2. PDF parser - pdfplumber or PyPDF2
3. Excel parser - openpyxl or pandas
4. CSV parser - pandas
5. XML parser - lxml
6. JSON parser - json module
7. Error handling - Handle parse errors gracefully
8. Tests - 90%+ coverage
9. Commit

**Key Decisions:**
- Batch size: 100 invoices per task
- Polling interval: 60 seconds
- Max retries: 3 with exponential backoff
- File size limit: 100MB

**Critical Requirements:**
- 🚨 NO HALLUCINATIONS - ask if unclear
- ✅ Read DECISIONS.md first
- ✅ 90%+ test coverage (mandatory)
- ✅ Update status every 1-2 hours
- ✅ Commit each component when done

---

### 🧬 SONNET (Phases 3, 4 & 5)

**Your Documents:**
- [ ] CORE_CLAUDE.md (binding protocol)
- [ ] DECISIONS.md (architectural decisions)
- [ ] PHASE_TEMPLATE.md (phase structure)
- [ ] Wait for PHASE_3_DECISIONS.md (from user)
- [ ] Wait for PHASE_4_DECISIONS.md (from user)
- [ ] Wait for PHASE_5_DECISIONS.md (from user)

**Your Timeline:**
- **Wait for Infrastructure + Phases 1-2 from OPUS/HAIKU**
- **Weeks 2-3:** Implement Phases 3-5

**Your Responsibilities:**

**Phase 3 (TRANSFORM):**
1. TransformationWorker - Execute customer-specific scripts
2. ScriptLoader - Load scripts from config.db
3. Executor - Execute scripts in isolated namespace
4. ScriptValidator - Validate scripts before execution (security)
5. ModularExecution - Support extract, validate, enrich, finalize modules
6. Tests - 90%+ coverage
7. Commit

**Phase 4 (ENRICH):**
1. EnrichmentWorker - Call Prodeus APIs
2. ProduesClient - HSN, category, postal, AI APIs
3. CircuitBreaker - Handle API failures gracefully
4. ParallelExecution - Call APIs in parallel (async)
5. RetryLogic - Exponential backoff (max 3 times)
6. GracefulDegradation - Continue if APIs fail, flag as MANUAL
7. Tests - 90%+ coverage
8. Commit

**Phase 5 (RESOLVE):**
1. ResolutionWorker - Match customers & inventory to master data
2. EntityMatcher - Fuzzy matching on name, address, SKU
3. CustomerResolver - Match/merge customer data
4. InventoryResolver - Match/merge inventory data
5. FuzzyMatching - Use fuzzy string matching algorithm
6. Tests - 90%+ coverage
7. Commit

**Key Decisions:**
- Enrichment: Graceful degradation (continue if Prodeus APIs fail)
- Circuit breaker: Open after 5 failures, reset after 60s
- Entity resolution: Fuzzy matching if exact match not found
- Error handling: Log + flag as MANUAL, don't fail

**Critical Requirements:**
- 🚨 NO HALLUCINATIONS - ask if unclear
- ✅ Read DECISIONS.md first
- ✅ 90%+ test coverage (mandatory)
- ✅ Update status every 1-2 hours
- ✅ Commit each component when done

---

## 📊 ENDPOINTS AT A GLANCE

| # | Endpoint | Method | Phase | Variant | Purpose |
|----|----------|--------|-------|---------|---------|
| 1 | `/api/v1/process` | POST | 1-8 | ALL | Main entry point |
| 2 | `/api/v1/retry` | POST | 8 | OPUS | Retry FIRS (full cycle) |
| 3 | `/api/v1/retransmit` | POST | 6/8 | OPUS | Transmit signed invoices |
| 4 | `/api/v1/entity/{type}/{id}` | PUT | 8 | OPUS | Update entity |
| 5 | `/api/v1/entity/{type}/{id}` | DELETE | 8 | OPUS | Delete entity |
| 6 | `/api/v1/update` | POST | 8 | OPUS | Generic update |
| 7 | `/api/v1/invoice/{id}/accept` | POST | 8 | OPUS | Accept B2B |
| 8 | `/api/v1/invoice/{id}/reject` | POST | 8 | OPUS | Reject B2B |
| 9 | `/api/v1/invoice/{id}` | GET | — | SDK WS1 | Fetch invoice |
| 10 | `/api/v1/invoices` | GET | — | SDK WS1 | List invoices |
| 11 | `/api/v1/customer/{id}` | GET | — | Future | Fetch customer |
| 12 | `/api/v1/customers` | GET | — | Future | List customers |
| 13 | `/api/v1/inventory/{id}` | GET | — | Future | Fetch inventory |
| 14 | `/api/v1/inventories` | GET | — | Future | List inventory |
| 15 | `/api/v1/search` | POST | — | SDK WS2 | FTS5 search |
| 16 | `/api/v1/events` | WS | Infra | OPUS | WebSocket sync |
| 17 | `/api/v1/core_queue/status` | GET | 8 | OPUS | Queue status |
| 18 | `/api/v1/health` | GET | 1 | HAIKU | Health check |

---

## 🎯 8 PHASES AT A GLANCE

| Phase | Step | Variant | Purpose | Timeline |
|-------|------|---------|---------|----------|
| 0 | INFRASTRUCTURE | OPUS | Foundation (DB, API, WebSocket) | Week 1 |
| 1 | FETCH | HAIKU | Queue reading, blob fetching | Weeks 1-2 |
| 2 | PARSE | HAIKU | File parsing (PDF, Excel, CSV, XML, JSON) | Weeks 1-2 |
| 3 | TRANSFORM | SONNET | Customer transformation scripts | Weeks 2-3 |
| 4 | ENRICH | SONNET | Prodeus APIs (async, error handling) | Weeks 2-3 |
| 5 | RESOLVE | SONNET | Entity resolution, fuzzy matching | Weeks 2-3 |
| 6 | PORTO BELLO | OPUS | Business logic gate (architecture ready) | Week 3 |
| 7 | BRANCH | OPUS | Preview vs immediate mode | Week 3 |
| 8 | FINALIZE | OPUS | Database, WebSocket, Edge integration | Weeks 3-4 |

---

## 🚨 CRITICAL RULES (Copy These!)

### Rule #0: NO HALLUCINATIONS (ZERO TOLERANCE)
```
If anything unclear → STOP immediately
Show the issue, present options, ask which to use
DO NOT guess, assume, or make up specs
```

### Rule #1: READ DECISIONS.MD FIRST
```
Before coding, read DECISIONS.md
Respect decisions as constraints
If conflict with decision, BREAK & ASK (don't code around it)
```

### Rule #2: 90%+ TEST COVERAGE (MANDATORY)
```
Coverage < 90% = do not commit
Must be measured with pytest-cov
Must report coverage at end of phase
All tests must pass before commit
```

### Rule #3: STATUS FILE UPDATES (EVERY 1-2 HOURS)
```
Track progress: X% (N/M components)
Track blockers immediately
Final report at completion (100%, commit hash, coverage %)
```

### Rule #4: COMMIT AS YOU GO
```
When each component done (not at end of phase)
All tests passing, coverage >= 90%
Proper commit message format
```

### Rule #5: BREAK & ASK FORMAT
```
⚠️ CLARIFICATION NEEDED: [Topic]

ISSUE: [What is unclear?]
FOUND IN: [Spec/Decision/Code]
OPTIONS:
A) [First interpretation]
B) [Second interpretation]

Which should I use?
```

---

## 📋 DECISION QUICK REFERENCE

| Decision | Value |
|----------|-------|
| **Database** | SQLite (all tiers) |
| **Workers** | Tier-specific (threading/Celery) |
| **Preview Mode** | Two-stage (preview → finalize) |
| **Error Handling** | Graceful degradation (Prodeus APIs) |
| **Retry/Retransmit** | Two endpoints (full cycle vs exchange only) |
| **IRN Generation** | Deterministic hash-based |
| **Transformation Scripts** | Stored in config.db, dynamically executed |
| **Batch Size** | 100 invoices per task |
| **Polling Interval** | 60 seconds |
| **Circuit Breaker** | Open after 5 failures, reset after 60s |
| **Cache TTL** | 1 hour |
| **Preview Expiry** | 24 hours |
| **Data Retention** | 24 hours (processed), 30 days (failed) |
| **WebSocket** | Database triggers auto-broadcast |
| **Access Control** | RBAC via permissions table |
| **Logging** | Structured JSON logs |
| **Testing** | TDD approach, 90%+ coverage |
| **Deployment** | Docker-first for all tiers |

---

## 📝 STATUS FILE TEMPLATE (Update Every 1-2 Hours)

**Copy this template and fill in:**

```markdown
# PHASE X: [STEP NAME] - STATUS REPORT

**Status:** 🔵 IN_PROGRESS
**Progress:** X% (N/M components)
**Last Checkpoint:** [Date Time] - [What was completed]
**Current Stage:** Stage Y: [Description]

## Completed
- [Component 1]

## In Progress
- [Current work]

## Next Steps
1. [What's next]

## Issues & Blockers
- [None]

## Test Coverage
- Current: X%
- Target: 90%

## Commits
- [hash] - [message]
```

---

## 🔄 VARIANT HAND-OFF SEQUENCE

```
Week 1:
├─ OPUS starts Infrastructure (foundation layer)
├─ HAIKU waits for Infrastructure
└─ SONNET waits for Infrastructure

Week 1-2:
├─ OPUS continues Infrastructure
├─ HAIKU starts Phase 1: FETCH (parallel)
└─ SONNET waits for Phase 2 completion

Week 2-3:
├─ OPUS continues Infrastructure (should be done by now)
├─ HAIKU starts Phase 2: PARSE
└─ SONNET starts Phase 3: TRANSFORM (parallel)

Week 2-3:
├─ HAIKU finishes Phase 2
├─ SONNET continues Phase 4: ENRICH
└─ SONNET continues Phase 5: RESOLVE

Week 3:
├─ OPUS starts Phase 6: PORTO BELLO (after Phase 5)
├─ OPUS starts Phase 7: BRANCH
└─ OPUS starts Phase 8: FINALIZE (final)

Week 4:
├─ OPUS finishes Phase 8
└─ Integration testing begins

Week 5:
├─ Performance testing
├─ Deployment preparation
└─ Documentation updates

Week 6:
└─ Deployment to test environment
```

---

## ✅ DAILY CHECKLIST FOR EACH VARIANT

### At Start of Day
- [ ] Read relevant phase documentation
- [ ] Review status file from yesterday
- [ ] Check for any blockers
- [ ] Plan work for the day

### During Work
- [ ] Write tests first (TDD)
- [ ] Implement component
- [ ] Run tests (must pass)
- [ ] Check coverage (must be >= 90%)
- [ ] Commit when component done
- [ ] If unclear → BREAK & ASK (don't guess)

### At End of Day (or every 1-2 hours)
- [ ] Update status file
- [ ] Update progress percentage
- [ ] Note current stage
- [ ] Log any blockers
- [ ] Report final coverage if phase complete

---

## 🆘 WHEN YOU GET STUCK

1. **If unclear on spec:**
   - [ ] Check API_CONTRACTS.md
   - [ ] Check PHASE_X_DECISIONS.md
   - [ ] Check DECISIONS.md
   - [ ] If still unclear → BREAK & ASK

2. **If test failing:**
   - [ ] Re-read the failing test
   - [ ] Check what it expects vs what you implemented
   - [ ] Fix implementation to match spec
   - [ ] Never modify test to match incorrect implementation

3. **If coverage too low:**
   - [ ] Identify uncovered code
   - [ ] Write tests for uncovered paths
   - [ ] Focus on error handling & edge cases
   - [ ] Coverage must reach 90% before commit

4. **If blocker found:**
   - [ ] Document the blocker clearly
   - [ ] Update status file immediately
   - [ ] BREAK & ASK user (provide context)
   - [ ] Don't code around the blocker

---

## 📞 ASKING FOR HELP (FORMAT)

```
⚠️ CLARIFICATION NEEDED: [Topic]

I encountered something unclear and need your guidance:

ISSUE: [What is unclear?]
FOUND IN: [Spec/Decision/Code]
OPTIONS:
A) [First interpretation]
   - Pro: [benefit]
   - Con: [drawback]

B) [Second interpretation]
   - Pro: [benefit]
   - Con: [drawback]

Which should I use, or do you have another idea?

[WAIT FOR RESPONSE BEFORE CODING]
```

---

## 📚 DOCUMENTATION FILES

**In Core/Documentation/:**
- README.md - Navigation (read first)
- CORE_CLAUDE.md - Binding protocol (critical)
- DECISIONS.md - Architectural decisions (binding)
- PHASES_OVERVIEW.md - Phase structure (reference)
- PHASE_TEMPLATE.md - Template (for phases)
- QUICK_REFERENCE.md - This file
- IMPLEMENTATION_SUMMARY.md - Your action items
- CORE_ARCHITECTURE.md - Full architecture (2000+ lines)

**Per Phase (create from template):**
- PHASE_X_DECISIONS.md
- PHASE_X_CHECKLIST.md
- API_CONTRACTS.md
- PHASE_X_STATUS.md

---

## 🎓 REMEMBER

1. ✅ **Read DECISIONS.md first** - Understand constraints
2. ✅ **Ask before coding** - If anything unclear
3. ✅ **90%+ coverage** - Non-negotiable
4. ✅ **Update status every 1-2 hrs** - Track progress
5. ✅ **Commit early & often** - Each component done
6. ✅ **NO HALLUCINATIONS** - Always ask if unclear

**→ Follow these, and Core will be implemented flawlessly.**

---

**Last Updated:** 2026-01-31
**Version:** 1.0
**Status:** READY FOR USE
