# HeartBeat Trace Model Decision Note

Date: 2026-03-01
Status: Approved
Reference: HELIUM_SECURITY_SPEC.docx (2026-03-01), sections 3.1-3.3, 6

## Decision: Three-Level Trace Chain

Helium adopts a three-level trace model for end-to-end request traceability.
HeartBeat is the canonical store for all trace IDs on blob records, and
propagates them via SSE to SDK clients.

## Trace Levels

```
user_trace_id (SDK, UUID v7)           — Client origin
       |
       v
x_trace_id (Relay, UUID v7)            — Server authority
       |
       v
invoice_trace_id (Core, UUID v7)       — Invoice-specific (invoices only)
```

### Level 1: user_trace_id (SDK-generated)

- **Generator**: Float SDK, on user gesture (before any HTTP call)
- **Format**: UUID v7 (time-ordered)
- **Purpose**: Client-side origin trace. Proves the request originated from a specific user action.
- **Scope**: Written to both `file_entries` and `blob_batches` at staging time.
- **Lifecycle**: Immutable once generated. Survives retries.

### Level 2: x_trace_id (Relay-generated)

- **Generator**: Relay Service, after JWT validation (HELIUM_SECURITY_SPEC section 3.2)
- **Format**: UUID v7 (time-ordered)
- **Purpose**: Server-authoritative trace. Proves the request passed authentication and was accepted by the platform.
- **Scope**: Propagated on all internal service calls (HeartBeat, Core). Delivered to SDK via SSE.
- **Lifecycle**: Generated once per Relay request. Links back to `user_trace_id`.
- **HeartBeat responsibility**: Store on blob records. Deliver to SDK via SSE event.

### Level 3: invoice_trace_id (Core-generated)

- **Generator**: Core Service, when an invoice is identified during extraction
- **Format**: UUID v7 (time-ordered)
- **Purpose**: Invoice-specific trace. Links a processed invoice back to the originating file and request chain.
- **Scope**: Written to `invoices` table only. Not on blob records.
- **Lifecycle**: Generated per-invoice. Links back to both `x_trace_id` and `user_trace_id`.

## Storage by Record Type

| Record Type | user_trace_id | x_trace_id | invoice_trace_id |
|---|---|---|---|
| `file_entries` (blob.db) | Yes | Yes (via SSE) | No |
| `blob_batches` (blob.db) | Yes | Yes (via SSE) | No |
| `invoices` (invoices.db) | Yes | Yes | Yes |

## HeartBeat Responsibilities

1. **Receive** `user_trace_id` and `x_trace_id` from Relay on file upload.
2. **Store** both trace IDs on `blob_batches` and `file_entries` records in blob.db.
3. **Propagate** trace IDs to SDK via SSE events (batch_confirmed, file_status_changed).
4. **Forward** both trace IDs to Core when handing off batches for processing.
5. **Log** `x_trace_id` in all internal log entries for the request lifecycle.

## Identity Fields (also on blob records)

HeartBeat stores these identity fields alongside trace IDs:

| Field | Source | Purpose |
|---|---|---|
| `helium_user_id` | JWT `sub` claim | Authenticated user (immutable) |
| `float_id` | JWT claim | Float instance tied to machine |
| `session_id` | JWT claim | Session (resets at hard re-auth) |
| `machine_guid` | USER-TRACE block | Windows MachineGuid (primary anchor) |
| `mac_address` | USER-TRACE block | Primary NIC MAC (corroborating) |
| `computer_name` | USER-TRACE block | OS hostname (label) |

## SSE Liveness (replaces cipher polling)

Per HELIUM_SECURITY_SPEC section 6:

| Old Model | New Model |
|---|---|
| SDK polls HeartBeat every 10 min with cipher | SDK sends `hb_ack` on each SSE event |
| Cipher written to disk | Cipher pushed via SSE, never written to disk |
| Missed poll = stale session | Missed ack = session timeout (configurable) |

HeartBeat tracks `last_ack_at` per session. No separate cipher file management needed.
