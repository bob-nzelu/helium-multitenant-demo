# HELIUM CORE SERVICE - DOCUMENTATION

**Version:** 1.0
**Last Updated:** 2026-01-31
**Status:** READY FOR IMPLEMENTATION

---

## 📚 DOCUMENTATION OVERVIEW

This directory contains all specifications, decisions, and protocols for implementing the Helium Core Service.

**Core Service Purpose:** Transform raw invoice files into FIRS-compliant structured data while managing customer and inventory master data.

---

## 📖 DOCUMENTS (Read in Order)

### 1. **CORE_CLAUDE.md** ⭐ START HERE

**READ THIS FIRST** - Master protocol that ALL Claude variants must follow

Contains:
- 🚨 NO-HALLUCINATIONS rule (CRITICAL - zero tolerance)
- ✅ Core service scope & boundaries
- ✅ 8-phase implementation strategy
- ✅ 18 API endpoints with responsibility matrix
- ✅ Variant-specific responsibilities (OPUS, HAIKU, SONNET)
- ✅ Communication protocols & status file formats
- ✅ Testing requirements (90%+ coverage mandatory)

**Who reads this:** OPUS, HAIKU, SONNET (EVERYONE - it's binding)

**When to read:** BEFORE any implementation work starts

---

### 2. **DECISIONS.md** ⭐ BINDING DECISIONS

Architectural decisions made for Core Service

Contains:
- Database technology (SQLite)
- Worker deployment model (threading vs Celery)
- Transformation script storage & execution
- Error handling strategy (graceful degradation)
- Preview mode & finalization
- IRN generation algorithm
- QR code generation
- WebSocket architecture
- Porto Bello workflow
- Access control model
- Retry vs retransmit strategy
- Idempotence strategy
- Caching strategy
- Batch processing size
- Data retention & cleanup
- Monitoring & metrics
- Logging format
- Environment configuration
- Integration with Relay & Edge
- Testing strategy
- Deployment & scaling
- Backward compatibility
- Specification-first development

**Who reads this:** OPUS, HAIKU, SONNET (required reading)

**When to read:** BEFORE starting your phase (to understand constraints)

**Important:** These decisions are BINDING - no overrides without user approval

---

### 3. **PHASES_OVERVIEW.md** - Phase Breakdown & Timeline

High-level overview of all 9 phases (Infrastructure + 8 processing phases)

Contains:
- Executive summary (phases 1-8 + infrastructure)
- Execution timeline (parallel execution possible)
- Phase-by-phase details:
  - Phase 0: Infrastructure (OPUS)
  - Phase 1: FETCH (HAIKU)
  - Phase 2: PARSE (HAIKU)
  - Phase 3: TRANSFORM (SONNET)
  - Phase 4: ENRICH (SONNET)
  - Phase 5: RESOLVE (SONNET)
  - Phase 6: PORTO BELLO (OPUS)
  - Phase 7: BRANCH (OPUS)
  - Phase 8: FINALIZE (OPUS)
- Variant assignments & rationale
- Phase dependencies graph
- Effort estimation (320-450 total hours)
- Quality gates for each phase

**Who reads this:** User (to understand phase structure), all variants (for context)

**When to read:** After CORE_CLAUDE.md, to understand big picture

---

### 4. **PHASE_TEMPLATE.md** - Template for Phase-Specific Docs

Template that gets customized for each phase

Contains:
- Phase purpose
- Phase inputs/outputs
- Phase architecture
- Phase-specific decisions
- API contracts
- Workers
- Implementation checklist
- Dependencies
- Performance targets
- Error codes
- Integration points
- Testing strategy
- Status tracking

**Who reads this:** User (to create phase docs), Claude variants (to see structure)

**When to read:** When creating phase-specific documentation

---

### 5. **CORE_ARCHITECTURE.md** - Full Architecture Specification

Detailed specification of Core Service architecture (ALREADY EXISTS - 2000+ lines)

Contains:
- Service overview
- Core responsibilities
- 8-step processing pipeline (detailed)
- Worker architecture
- Database schemas (full SQL)
- API specification (endpoints, requests, responses)
- WebSocket implementation
- Porto Bello workflow
- Scaling strategy
- Idempotence design
- Memory cleanup
- Error handling
- Audit logs
- Deployment guide
- Monitoring & metrics

**Who reads this:** Architects, technical leads, all implementers

**When to read:** For detailed architecture understanding

---

## 🎯 PHASE-SPECIFIC DOCUMENTATION

Each phase has its own folder with dedicated docs:

```
PHASE_1_FETCH/
├── PHASE_1_DECISIONS.md      (Phase-specific decisions)
├── PHASE_1_CHECKLIST.md      (Implementation checklist)
├── API_CONTRACTS.md          (Phase endpoint specs)
└── PHASE_1_STATUS.md         (Status file - updated hourly)

PHASE_2_PARSE/
├── PHASE_2_DECISIONS.md
├── PHASE_2_CHECKLIST.md
├── API_CONTRACTS.md
└── PHASE_2_STATUS.md

[Continue for PHASES 3-8...]
```

---

## 🏗️ INFRASTRUCTURE DOCUMENTATION

```
INFRASTRUCTURE/
├── DATABASE_SCHEMAS.md       (All 5 table definitions)
├── API_FRAMEWORK.md          (FastAPI setup, routing)
├── WEBSOCKET_SPEC.md         (Event definitions, broadcasting)
├── ACCESS_CONTROL.md         (Permissions, RBAC)
└── INFRASTRUCTURE_STATUS.md  (Status file)
```

---

## 🎓 HOW TO USE THIS DOCUMENTATION

### For Users (Project Manager/Architect)

1. **Read CORE_CLAUDE.md** - Understand binding protocol
2. **Read DECISIONS.md** - Understand architectural constraints
3. **Read PHASES_OVERVIEW.md** - Understand phase structure & timeline
4. **Customize phase-specific DECISIONS files** - Edit decisions for each phase
5. **Prepare phase checklists** - Adapt template for each phase
6. **Prepare API contracts** - Define endpoints for each phase
7. **Share with Claude variants** - Provide each variant their phase docs

---

### For OPUS (Infrastructure + Phases 6, 7, 8)

1. **Read CORE_CLAUDE.md** ← REQUIRED (binding protocol)
2. **Read DECISIONS.md** ← REQUIRED (understand constraints)
3. **Build Infrastructure** (Phases 1-7 depend on this):
   - Create database schemas
   - Create FastAPI app + routing
   - Create WebSocket server
   - Create permissions system
4. **Wait for HAIKU & SONNET to complete phases 1-5**
5. **Implement Phases 6-8** after dependencies ready
6. **Update status files every 1-2 hours**
7. **Maintain 90%+ test coverage**
8. **Commit when each component complete**

---

### For HAIKU (Phases 1 & 2)

1. **Read CORE_CLAUDE.md** ← REQUIRED (binding protocol)
2. **Read DECISIONS.md** ← REQUIRED (understand constraints)
3. **Wait for Infrastructure to complete**
4. **Read PHASE_1_DECISIONS.md** + PHASE_1_CHECKLIST.md
5. **Implement Phase 1: FETCH**
   - QueueScannerWorker
   - BlobFetcher
   - Error handling
6. **Commit Phase 1 when complete**
7. **Read PHASE_2_DECISIONS.md** + PHASE_2_CHECKLIST.md
8. **Implement Phase 2: PARSE**
   - FileParserWorker
   - PDF, Excel, CSV, XML, JSON parsers
   - Error handling
9. **Commit Phase 2 when complete**
10. **Update status files every 1-2 hours**
11. **Maintain 90%+ test coverage**

---

### For SONNET (Phases 3, 4 & 5)

1. **Read CORE_CLAUDE.md** ← REQUIRED (binding protocol)
2. **Read DECISIONS.md** ← REQUIRED (understand constraints)
3. **Wait for Infrastructure + Phases 1-2 to complete**
4. **Read PHASE_3_DECISIONS.md** + PHASE_3_CHECKLIST.md
5. **Implement Phase 3: TRANSFORM**
   - TransformationWorker
   - Script loading & execution
   - Script validation
6. **Commit Phase 3 when complete**
7. **Read PHASE_4_DECISIONS.md** + PHASE_4_CHECKLIST.md
8. **Implement Phase 4: ENRICH**
   - EnrichmentWorker
   - Prodeus API calls (parallel async)
   - Circuit breaker pattern
   - Graceful degradation
9. **Commit Phase 4 when complete**
10. **Read PHASE_5_DECISIONS.md** + PHASE_5_CHECKLIST.md
11. **Implement Phase 5: RESOLVE**
    - ResolutionWorker
    - Entity fuzzy matching
    - Master data merging
12. **Commit Phase 5 when complete**
13. **Update status files every 1-2 hours**
14. **Maintain 90%+ test coverage**

---

## ✅ CRITICAL REQUIREMENTS (All Variants Must Follow)

### Rule #0: NO HALLUCINATIONS (ZERO TOLERANCE)
- If anything is unclear → STOP and ask user
- Break & ask format: Show the issue, options, ask which to use
- Do NOT guess, assume, or make up specifications

### Rule #1: READ DECISIONS FIRST
- Before coding, read DECISIONS.md + phase-specific DECISIONS
- Respect decisions as constraints
- If a decision conflicts with your idea, ask user (don't override)

### Rule #2: Follow API Contracts Exactly
- Function signatures must match API_CONTRACTS.md
- Error codes must be implemented
- Data models must match schema

### Rule #3: Maintain 90%+ Test Coverage
- Mandatory (not optional)
- Measured with coverage tools
- Must report coverage numbers at end of phase

### Rule #4: Update Status Files Every 1-2 Hours
- Track progress (X%, N/M components)
- Track blockers immediately
- Final report when complete (100%, commit hash, coverage %)

### Rule #5: Commit When Each Component Done
- Not at end of phase (do it as you go)
- All tests must pass before committing
- Coverage >= 90% before committing
- Follow commit message format

### Rule #6: Break & Ask Protocol
When encountering ambiguity:
```
⚠️ CLARIFICATION NEEDED: [Topic]

I encountered something unclear and need your guidance:

ISSUE: [What is unclear?]
FOUND IN: [Spec/Decision/Code]
OPTIONS:
A) [First interpretation]
B) [Second interpretation]

Which should I use, or do you have another idea?

DO NOT code further until you get an answer.
```

---

## 🔄 STATUS FILE FORMAT (Update Every 1-2 Hours)

**In Progress:**
```
**Status:** 🔵 IN_PROGRESS
**Progress:** 65% (4/6 files)
**Last Checkpoint:** [2026-01-31 14:35] - Implementation stage X complete
**Current Stage:** Stage Y: [Description]
**Issues & Blockers:** [Any blockers?]
```

**Complete:**
```
**Status:** 🟢 COMPLETE
**Progress:** 100% (6/6 files)
**Last Checkpoint:** [2026-01-31 16:45] - Implementation complete
**Final Test Coverage Report:**
- Overall Coverage: 92% ✅
- Module breakdown:
  - worker.py: 95%
  - database.py: 89%
  - api.py: 91%
- Test Results: 145 tests passed, 0 failed
**Final Commits:**
- [hash1] - feat(phase-x): Complete Phase X implementation
- [hash2] - test(phase-x): Add comprehensive test suite
**Issues & Blockers:** [None - Complete and committed]
```

---

## 🚀 QUICK START CHECKLIST

### Before Implementation Begins

- [ ] User reads all documentation (CORE_CLAUDE.md, DECISIONS.md, PHASES_OVERVIEW.md)
- [ ] User reviews and approves phase structure & variant assignments
- [ ] User creates/customizes PHASE_X_DECISIONS.md for each phase
- [ ] User creates/customizes PHASE_X_CHECKLIST.md for each phase
- [ ] User creates/customizes API_CONTRACTS.md for each phase
- [ ] OPUS reads Core/Documentation/ completely
- [ ] HAIKU reads Core/Documentation/ completely
- [ ] SONNET reads Core/Documentation/ completely

### OPUS (Infrastructure)

- [ ] Build database layer (5 tables + schemas)
- [ ] Build API framework (FastAPI + 18 endpoint stubs)
- [ ] Build WebSocket server
- [ ] Build access control (permissions + RBAC)
- [ ] Setup logging & error handling
- [ ] Create 90%+ covered tests
- [ ] Commit to git
- [ ] Update INFRASTRUCTURE_STATUS.md

### HAIKU (Phases 1-2)

- [ ] Wait for Infrastructure ready
- [ ] Implement Phase 1 (FETCH)
  - [ ] QueueScannerWorker
  - [ ] BlobFetcher
  - [ ] Tests + 90% coverage
  - [ ] Commit
- [ ] Implement Phase 2 (PARSE)
  - [ ] FileParserWorker
  - [ ] 5 parsers (PDF, Excel, CSV, XML, JSON)
  - [ ] Tests + 90% coverage
  - [ ] Commit
- [ ] Update PHASE_1_STATUS.md and PHASE_2_STATUS.md

### SONNET (Phases 3-5)

- [ ] Wait for Infrastructure + Phases 1-2 ready
- [ ] Implement Phase 3 (TRANSFORM)
  - [ ] TransformationWorker
  - [ ] Script loading & execution
  - [ ] Tests + 90% coverage
  - [ ] Commit
- [ ] Implement Phase 4 (ENRICH)
  - [ ] EnrichmentWorker
  - [ ] Prodeus API clients
  - [ ] Circuit breaker
  - [ ] Tests + 90% coverage
  - [ ] Commit
- [ ] Implement Phase 5 (RESOLVE)
  - [ ] ResolutionWorker
  - [ ] Fuzzy matching
  - [ ] Master data merging
  - [ ] Tests + 90% coverage
  - [ ] Commit
- [ ] Update PHASE_3_STATUS.md, PHASE_4_STATUS.md, PHASE_5_STATUS.md

### OPUS (Phases 6-8)

- [ ] Wait for Phases 1-5 ready
- [ ] Implement Phase 6 (PORTO BELLO)
  - [ ] PortoBelloWorker
  - [ ] Business logic gate
  - [ ] Tests + 90% coverage
  - [ ] Commit
- [ ] Implement Phase 7 (BRANCH)
  - [ ] BranchingWorker
  - [ ] Preview generator
  - [ ] Tests + 90% coverage
  - [ ] Commit
- [ ] Implement Phase 8 (FINALIZE)
  - [ ] FinalizationWorker
  - [ ] All endpoints (10 endpoints)
  - [ ] Database transactions
  - [ ] WebSocket broadcasting
  - [ ] Edge API integration
  - [ ] Tests + 90% coverage
  - [ ] Commit
- [ ] Update PHASE_6_STATUS.md, PHASE_7_STATUS.md, PHASE_8_STATUS.md

### Integration & Deployment

- [ ] Run all tests together (should be 100% passing)
- [ ] Verify 90%+ coverage across codebase
- [ ] Performance testing & tuning
- [ ] Create deployment documentation
- [ ] Deploy to test environment
- [ ] Smoke testing & verification

---

## 📞 CONTACT & SUPPORT

If anything is unclear:
1. Check CORE_CLAUDE.md Rule #0 (NO HALLUCINATIONS)
2. Check DECISIONS.md (architectural constraints)
3. Check phase-specific documentation
4. If still unclear → BREAK & ASK (follow protocol)

Do NOT guess. Always ask.

---

## 📋 DOCUMENT MAINTENANCE

| Document | Owner | Update Frequency | Purpose |
|----------|-------|------------------|---------|
| CORE_CLAUDE.md | User | Once (binding) | Master protocol |
| DECISIONS.md | User | Once (binding) | Architectural constraints |
| PHASES_OVERVIEW.md | User | Once | Phase structure |
| PHASE_TEMPLATE.md | User | Once | Template for phases |
| PHASE_X_DECISIONS.md | User | Once per phase | Phase-specific constraints |
| PHASE_X_CHECKLIST.md | Variant | Per phase | Implementation tracking |
| PHASE_X_STATUS.md | Variant | Every 1-2 hrs | Progress tracking |
| API_CONTRACTS.md | User | Once per phase | Endpoint specifications |
| Core/src/ | Variant | Continuous | Implementation code |

---

**Last Updated:** 2026-01-31
**Version:** 1.0
**Status:** READY FOR IMPLEMENTATION

**Next Step:** User to create phase-specific documentation + share with variants
