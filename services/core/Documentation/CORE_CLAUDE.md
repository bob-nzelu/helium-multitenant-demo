# CORE_CLAUDE.MD — MANDATORY PROTOCOL FOR CORE SERVICE IMPLEMENTATION

**Version:** 2.0
**Last Updated:** 2026-03-19
**Status:** BINDING PROTOCOL — READ THIS FIRST BEFORE ANY IMPLEMENTATION
**Supersedes:** v1.0 (2026-01-31)

---

## RULE #0: ABSOLUTE NO-HALLUCINATIONS POLICY

**IF ANYTHING IS UNCLEAR OR AMBIGUOUS: BREAK AND ASK THE USER.**

**DO NOT MAKE ASSUMPTIONS. DO NOT GUESS. DO NOT HALLUCINATE.**

- If something is unclear → STOP immediately and ask
- If a spec is ambiguous → Ask for clarification
- If two interpretations are possible → Ask which one
- If something conflicts → Break and ask
- Zero tolerance: User will call out hallucinations immediately

### ALWAYS RECOMMEND WHEN ASKING

**When you ask a question, you MUST include your recommendation and explain WHY.**

The user is the architect. You are the engineer who has read all the specs. The user expects you to have an informed opinion — not just present options and wait. A question without a recommendation forces the user to do YOUR analysis work.

**Format:**
```
QUESTION: [What needs deciding]

OPTIONS:
A) [Option A]
B) [Option B]

MY RECOMMENDATION: Option A
WHY: [Your reasoning based on what you've read in the specs, schemas,
      existing patterns, and architectural constraints]
```

**Bad:** "Should we use approach A or B?" (forces user to analyze both)
**Good:** "Should we use approach A or B? I recommend A because the canonical schema already uses X pattern, and B would require migrating Y." (user confirms or overrides with full context)

This applies to ALL questions — design decisions, implementation choices, ambiguity resolution, edge cases. Never ask a naked question.

---

## RULE #1: READ DOCUMENTATION HIERARCHY

**BEFORE writing a single line of code, read in this order:**

1. **CORE_CLAUDE.md** (this file) — master protocol
2. **DECISIONS_V2.md** — 24 binding architectural decisions (PostgreSQL, SSE, canonical schemas, etc.)
3. **Workstreams/WS{N}_{NAME}/MENTAL_MODEL.md** — exhaustive mental model for your workstream
4. **Workstreams/WS{N}_{NAME}/API_CONTRACTS.md** — endpoint specs
5. **Workstreams/WS{N}_{NAME}/DECISIONS.md** — WS-specific decisions
6. **Workstreams/WS{N}_{NAME}/DEPENDENCIES.md** — what you need from other WSs
7. **VALIDATION_CHECKS.md** — 73 canonical validation checks (if your WS implements any)

**Decision hierarchy:** DECISIONS_V2.md > Canonical Schema SQL > WS-specific DECISIONS.md > API_CONTRACTS.md > Your judgment

---

## RULE #2: CORE SERVICE SCOPE & BOUNDARIES

### Mission
Transform raw files into FIRS-compliant Nigerian eInvoices while managing customer and inventory master data and coordinating with Edge for FIRS submission.

### Core DOES:
- Execute Transforma scripts (customer-specific data extraction)
- Call HIS for enrichment (HSN, VAT, categories, postal validation)
- Call IntelliCore for PDF extraction (Textract + LLM validation)
- Generate IRN (Invoice Reference Number) and QR codes
- Manage 3 canonical databases (invoices, customers, inventory)
- Queue invoices to Edge for FIRS submission
- Broadcast SSE events to Float SDK
- Handle preview → finalize two-stage flow
- Generate .hlm export files
- Generate reports (statistics, compliance, audit)
- Apply user edits during finalization
- Enforce RBAC access control
- Accept/reject inbound B2B invoices

### Core DOES NOT:
- Accept file uploads (Relay does this)
- Validate HMAC signatures (Relay does this)
- Submit to FIRS directly (Edge does this)
- Build transformation scripts (Transforma does this — Core is a consumer)
- Manage blob storage (HeartBeat does this)
- Own the AI model (HIS owns the embedding model)

### Who Calls Core:
```
Relay         → POST /enqueue, POST /process_preview, POST /finalize
Float SDK     → GET endpoints, POST /search, SSE stream
Edge          → POST /update (FIRS responses)
HeartBeat     → GET /core_queue/status, GET /health
```

---

## RULE #3: FUNCTIONAL WORKSTREAM STRUCTURE (7 WSs)

Core is organized into **7 functional workstreams** (NOT phases):

```
WS0: FOUNDATION ──┬── WS1: INGESTION ──────┐
                  ├── WS2: PROCESSING ──────┼── WS3: ORCHESTRATOR ── WS5: FINALIZE
                  ├── WS4: ENTITY CRUD      │
                  └── WS6: OBSERVABILITY ───┘
```

| WS | Name | Endpoints | Effort |
|----|------|-----------|--------|
| WS0 | FOUNDATION | `/health`, `/metrics`, `/sse/stream` | LARGE |
| WS1 | INGESTION | `/enqueue`, `/core_queue/status` | MEDIUM |
| WS2 | PROCESSING | (none — internal pipeline) | LARGE |
| WS3 | ORCHESTRATOR | `/process_preview` | MEDIUM |
| WS4 | ENTITY CRUD | 11 GET/PUT/DELETE + `/search` + `/statistics` | MEDIUM |
| WS5 | FINALIZE | `/finalize`, `/retry`, `/retransmit`, B2B accept/reject, `/update` | LARGE |
| WS6 | OBSERVABILITY | Notification endpoints, audit, metrics, RBAC | SMALL-MEDIUM |

### Parallelism
After WS0 completes, WS1/WS2/WS4/WS6 run in parallel with zero cross-dependencies.
WS3 depends on WS1 + WS2. WS5 depends on WS3.

### Session Schedule
- **Session 1**: WS0 (must complete first)
- **Session 2**: WS1 + WS2 + WS4 + WS6 (all parallel) + WS5 partial
- **Session 3**: WS3 (wires WS1 + WS2)
- **Session 4**: WS5 completion
- **Session 5**: Integration testing

---

## RULE #4: TECHNOLOGY STACK (March 2026)

| Component | Technology | Notes |
|-----------|-----------|-------|
| **Database** | PostgreSQL 15+ | Single instance, 5 schemas (invoices, customers, inventory, core, notifications) |
| **Framework** | FastAPI (async) | uvicorn ASGI server |
| **DB Driver** | psycopg[binary] v3 | AsyncConnectionPool |
| **Real-time** | SSE only | `GET /sse/stream` — NO WebSocket |
| **Validation** | Pydantic v2 | Request/response models |
| **Logging** | structlog | Structured JSON |
| **Metrics** | prometheus-client | 8 metric families |
| **Scheduler** | APScheduler | PostgreSQL job store |
| **Deployment** | Docker Compose | PostgreSQL + Core |
| **Schemas** | Canonical SQL files | Source of truth in `Documentation/Schema/` |
| **Port** | 8080 | Configurable via `CORE_PORT` |

---

## RULE #5: EXTERNAL SERVICE DEPENDENCIES

### HIS (Helium Intelligence Service) — Enrichment
- **Status:** READY (160 tests, 90%+ coverage)
- **Port:** 8500
- **Key endpoint:** `POST /api/v1/enrich/batch`
- **Used by:** WS2 (Phase 4: ENRICH)
- **Capabilities:** HSN mapping, service codes, VAT, categories, postal validation
- **Model:** all-MiniLM-L6-v2 (local, CPU-only, 384-dim embeddings)

### IntelliCore (Pronalytics) — PDF Extraction
- **Status:** PARTIAL (routes built, Textract/LLM incomplete)
- **Key endpoint:** `POST /api/v1/extract`
- **Used by:** WS1 (Phase 2: PARSE) for PDF files only
- **Auth:** HMAC-SHA256
- **Stub until ready:** Mock PDF parser with fixture responses

### Transforma Script System — Transformation
- **Status:** NOT BUILT (prerequisite — see `WS_PREREQ_TRANSFORMA.md`)
- **Type:** Python library consumed by Core
- **Used by:** WS2 (Phase 3: TRANSFORM)
- **Interface:** `execute_transformation(script, raw_data, enrichment_results) → TransformationResult`
- **BLOCKS:** WS2 and WS3 cannot start until Transforma delivers

---

## RULE #6: CANONICAL DOCUMENTS

These documents are the source of truth. Any workstream that implements or modifies content MUST update the relevant canonical document.

| Document | Purpose | Update Rule |
|----------|---------|-------------|
| `DECISIONS_V2.md` | 24 binding architectural decisions | Only with user approval |
| `VALIDATION_CHECKS.md` | 73 numbered validation checks per data type | Any WS implementing a check marks it `[Implemented: WS{N}]` |
| `HLM_FORMAT.md` | .hlm export format specification | Any WS generating .hlm files must follow this spec |
| `REPORT_ENGINE.md` | Report types and generation | Any WS implementing reports must follow this spec |
| `WS_PREREQ_TRANSFORMA.md` | What Core needs from Transforma | Updated when interface changes |
| `Documentation/Schema/*.sql` | Canonical table definitions | NEVER modify without Schema Governance review |

---

## RULE #7: ENDPOINT INVENTORY (24+ endpoints)

### Processing Flow (Relay → Core)
| # | Endpoint | WS | Method |
|---|----------|----|--------|
| 1 | `/api/v1/enqueue` | WS1 | POST |
| 2 | `/api/v1/process_preview` | WS3 | POST |
| 3 | `/api/v1/finalize` | WS5 | POST |

### Real-time
| 4 | `/sse/stream` | WS0 | GET (SSE) |

### Entity CRUD
| 5-10 | `/api/v1/{invoice,customer,inventory}/{id}` | WS4 | GET |
| 5-10 | `/api/v1/{invoices,customers,inventories}` | WS4 | GET |
| 11 | `/api/v1/entity/{type}/{id}` | WS4 | PUT |
| 12 | `/api/v1/entity/{type}/{id}` | WS4 | DELETE |

### Search + Statistics
| 13 | `/api/v1/search` | WS4 | POST |
| 14 | `/api/v1/statistics` | WS4 | GET |

### Finalization + Edge
| 15 | `/api/v1/retry` | WS5 | POST |
| 16 | `/api/v1/retransmit` | WS5 | POST |
| 17 | `/api/v1/invoice/{id}/accept` | WS5 | POST |
| 18 | `/api/v1/invoice/{id}/reject` | WS5 | POST |
| 19 | `/api/v1/update` | WS5 | POST |

### Notifications
| 20 | `/api/v1/notifications` | WS6 | GET |
| 21 | `/api/v1/notifications/{id}/read` | WS6 | PUT |
| 22 | `/api/v1/notifications/{id}/dismiss` | WS6 | PUT |

### Reports
| 23 | `/api/v1/reports/generate` | WS4/WS6 | POST |
| 24 | `/api/v1/reports/{id}/download` | WS4/WS6 | GET |

### Schema + Monitoring
| 25 | `/api/v1/schema/{type}.hlm` | WS0 | GET |
| 26 | `/api/v1/core_queue/status` | WS1 | GET |
| 27 | `/api/v1/health` | WS0 | GET |
| 28 | `/api/v1/metrics` | WS0/WS6 | GET |

---

## RULE #8: QUESTION PHASE BEFORE CODING

**For EVERY workstream session:**

1. Read documentation hierarchy (Rule #1)
2. **ASK clarifying questions** using AskUserQuestion tool — **ALWAYS include your recommendation and WHY** (see Rule #0)
3. Summarize understanding and get user confirmation
4. Only then: begin implementation
5. Update status file (WS STATUS.md) every 1-2 hours
6. 90%+ test coverage mandatory — real PostgreSQL in tests, not mocks

**Every question must recommend.** The user's time is valuable. Don't present bare options — present your analysis, your pick, and your reasoning. The user confirms or overrides. This is faster for everyone.

---

## RULE #9: TESTING REQUIREMENTS

- **Minimum:** 90% code coverage per workstream
- **Database tests:** Use `testcontainers-python` for real PostgreSQL (no mocking DB layer)
- **ML tests:** Real models preferred, JSON fixtures for unit tests only
- **Tools:** pytest + pytest-asyncio + pytest-cov
- **Write tests as you implement** — not at the end

---

## RULE #10: ERROR HANDLING

- All 13 error codes from DECISIONS_V2.md must be implemented
- Graceful degradation: HIS down → skip enrichment, flag MANUAL. Edge down → keep in queue. HeartBeat audit down → log locally.
- Circuit breaker: 5 consecutive failures → open for 60 seconds
- Retry: Exponential backoff (1s, 2s, 4s, 8s) max 3 attempts for transient errors
- Structured error responses: `{"error": "CODE", "message": "...", "details": [...]}`

---

## RULE #11: GIT COMMITS

```
feat(ws{n}): [Component] - [What was done]

[Detailed description]

Co-Authored-By: Claude Opus 4.6 (1M context) <noreply@anthropic.com>
```

---

## RULE #12: CROSS-PROJECT PREFERENCES (Standing Orders)

1. Always use AskUserQuestion tool for clarifying questions
2. Docker Compose for multi-service setups
3. PostgreSQL for multi-tenant — SQLite only for embedded
4. Go full scope in sessions — no holding back
5. Sonnet 4.5 preferred for sub-agents (cost)
6. No mocks for ML models — real models preferred
7. Write tests as you implement — not at the end

---

## DOCUMENTATION STRUCTURE

```
Core/Documentation/
├── CORE_CLAUDE.md                    ← You are here (master protocol v2.0)
├── DECISIONS_V2.md                   ← 24 binding decisions (March 2026)
├── VALIDATION_CHECKS.md              ← 73 canonical validation checks
├── HLM_FORMAT.md                     ← .hlm export format spec
├── REPORT_ENGINE.md                  ← Report types and generation
├── WS_PREREQ_TRANSFORMA.md          ← Transforma interface contract
├── CORE_ARCHITECTURE_REFINED.md      ← Refined architecture (Feb 2026)
├── CRITICAL_UPDATES_REQUIRED.md      ← 9 critical changes (incorporated into WSs)
│
├── Workstreams/
│   ├── WS0_FOUNDATION/               ← 6 docs (MENTAL_MODEL, API_CONTRACTS, etc.)
│   ├── WS1_INGESTION/                ← 6 docs
│   ├── WS2_PROCESSING/               ← 6 docs
│   ├── WS3_ORCHESTRATOR/             ← 6 docs
│   ├── WS4_ENTITY_CRUD/              ← 6 docs
│   ├── WS5_FINALIZE/                 ← 6 docs
│   └── WS6_OBSERVABILITY/            ← 6 docs
│
├── _archived/                        ← Stale docs (DECISIONS_V1, old phase docs)
└── INFRASTRUCTURE/                   ← Legacy infra docs (may be stale)
```

---

## SUMMARY: 12 CRITICAL RULES

| # | Rule | Key Point |
|---|------|-----------|
| 0 | No hallucinations | Break and ask if ANYTHING unclear |
| 1 | Read docs hierarchy | DECISIONS_V2 > Canonical Schema > WS DECISIONS > API CONTRACTS |
| 2 | Service scope | Core transforms files → FIRS invoices. Calls HIS/IntelliCore/Transforma |
| 3 | 7 functional workstreams | WS0-WS6 with dependency graph. WS1/WS2/WS4/WS6 parallel |
| 4 | Tech stack | PostgreSQL, FastAPI, SSE, psycopg3, Pydantic v2 |
| 5 | External deps | HIS (ready), IntelliCore (partial), Transforma (prerequisite) |
| 6 | Canonical documents | VALIDATION_CHECKS.md, HLM_FORMAT.md, REPORT_ENGINE.md |
| 7 | 24+ endpoints | Full inventory with WS assignments |
| 8 | Question phase | Ask BEFORE coding. AskUserQuestion tool. |
| 9 | Testing | 90%+ coverage. Real PostgreSQL. pytest-asyncio. |
| 10 | Error handling | 13 error codes. Graceful degradation. Circuit breaker. |
| 11 | Git commits | feat(ws{n}): message format |
| 12 | Standing orders | PostgreSQL, Docker Compose, full scope, Sonnet for sub-agents |

---

**THIS FILE IS THE MASTER PROTOCOL. ALL CORE IMPLEMENTATION SESSIONS MUST READ IT FIRST.**

**Last Updated:** 2026-03-19
**Version:** 2.0 — BINDING PROTOCOL
