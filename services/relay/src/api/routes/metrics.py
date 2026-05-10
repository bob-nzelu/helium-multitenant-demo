"""
GET /metrics — Prometheus metrics endpoint (Decision 5A).

Returns basic service info gauges (instance, up, module-cache, redis)
plus the in-memory counters tracked by ``observability.counters``
(per CSSV1 R9 — bearer-removed alarm + future R-chip placeholders).

Phase 2 will swap the hand-built exposition for ``prometheus_client``
once the counter set graduates from "alarm rates" to "request
histograms" (CSSV1 R4 + R8 turn the placeholder counters into proper
histograms).

No authentication required (standard for /metrics endpoints; allowed
by the CLAUDE.md golden-rule carve-out for unauth health/metrics).
"""

import logging
from typing import List

from fastapi import APIRouter, Request
from fastapi.responses import PlainTextResponse

from ...observability import counters

logger = logging.getLogger(__name__)

router = APIRouter()


def _format_labels(labels: dict) -> str:
    """Render a {k: v} dict as a Prometheus label suffix `{k="v",...}`."""
    if not labels:
        return ""
    parts = ",".join(f'{k}="{v}"' for k, v in sorted(labels.items()))
    return "{" + parts + "}"


def _emit_counters() -> List[str]:
    """Render ``observability.counters`` as Prometheus exposition lines.

    Emits one ``# HELP`` + ``# TYPE`` header per counter name in
    :data:`counters.COUNTER_HELP`, then a sample line per
    ``(name, labels)`` combination tracked. Counters with no observed
    samples emit only the header — that's fine; Prometheus handles
    zero-sample exposition.
    """
    # Bucket samples by name so we emit one HELP/TYPE block + many lines.
    by_name: dict = {}
    for name, labels, value in counters.get_all():
        by_name.setdefault(name, []).append((labels, value))

    out: List[str] = []
    for name, (help_text, type_str) in counters.COUNTER_HELP.items():
        out.append(f"# HELP {name} {help_text}")
        out.append(f"# TYPE {name} {type_str}")
        for labels, value in by_name.get(name, []):
            out.append(f"{name}{_format_labels(labels)} {value}")
        out.append("")
    return out


@router.get(
    "/metrics",
    response_class=PlainTextResponse,
    summary="Prometheus metrics",
)
async def metrics(request: Request):
    """
    Export Prometheus-format metrics.

    Service-info gauges + CSSV1 counters (alarming + placeholders).
    """
    config = request.app.state.config
    module_cache = request.app.state.module_cache
    redis = request.app.state.redis

    module_cache_up = 1 if module_cache.is_loaded else 0
    redis_up = 1 if redis.is_available else 0

    lines: List[str] = [
        "# HELP helium_relay_info Relay service information",
        "# TYPE helium_relay_info gauge",
        f'helium_relay_info{{instance_id="{config.instance_id}",version="{request.app.version}"}} 1',
        "",
        "# HELP helium_relay_up Relay service health (1=up, 0=down)",
        "# TYPE helium_relay_up gauge",
        "helium_relay_up 1",
        "",
        "# HELP helium_relay_module_cache_loaded Module cache status (1=loaded, 0=not loaded)",
        "# TYPE helium_relay_module_cache_loaded gauge",
        f"helium_relay_module_cache_loaded {module_cache_up}",
        "",
        "# HELP helium_relay_redis_connected Redis connection status (1=connected, 0=disconnected)",
        "# TYPE helium_relay_redis_connected gauge",
        f"helium_relay_redis_connected {redis_up}",
        "",
    ]

    # Append in-memory counters (CSSV1 R9 + future placeholders).
    lines.extend(_emit_counters())

    return PlainTextResponse(
        content="\n".join(lines) + "\n",
        media_type="text/plain; version=0.0.4; charset=utf-8",
    )
