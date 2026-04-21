"""
CallerContext — unified auth result across all auth paths.

Per BACKEND_SERVICE_AUTH_AND_ABUSE_SPEC.md §3.5, every request to Relay
presents credentials in one of three forms (HMAC, user JWT, service creds).
The dispatcher in deps.authenticate_request resolves any of them into a
single CallerContext that downstream handlers consume. This keeps auth
logic out of business code.
"""

from dataclasses import dataclass, field
from typing import List, Literal, Optional


ActorType = Literal["user", "service", "erp"]


@dataclass(frozen=True)
class CallerContext:
    """
    Resolved identity + permissions for a single request.

    Produced by deps.authenticate_request. Consumed by route handlers
    and downstream clients (HeartBeatClient, CoreClient).

    Fields:
        actor_type:
            "user"    — came from user JWT path (Float/Reader)
            "service" — came from service api_key:api_secret path (Core, HB)
            "erp"     — came from HMAC path (external ERP system)

        tenant_id:
            Which tenant this caller belongs to. For "user" from JWT claims.
            For "erp" looked up from tenants.json. For "service" may be
            platform-wide ("_shared").

        identifier:
            Stable caller identity — user_id ("user"), api_key ("service"/"erp").
            Used for rate-limit keys, audit logs, and as the api_key passed
            through to HeartBeat on blob/audit calls.

        permissions:
            Permission strings this caller has. Populated for "user" from
            introspect response. For "erp" derived from tenant-role mapping.
            For "service" from api_credentials.permissions JSON.

        source_id:
            X-Source-ID header value (only present for "user" path).
            Identifies which frontend app (Float vs Reader) on the device.

        trace_id:
            X-Trace-ID header value (echoed or freshly generated).

        downstream_auth_header:
            Ready-to-use Authorization header for downstream calls.
            For "user": "Bearer <jwt>" (HB can re-introspect if needed).
            For "service"/"erp": "Bearer <api_key>:<api_secret>" or the
            service creds Relay uses to talk to HB on this caller's behalf.

        raw_api_key:
            The api_key string, useful for audit log entries and for Relay's
            downstream HeartBeatClient calls that expect an api_key argument.
    """

    actor_type: ActorType
    tenant_id: str
    identifier: str
    permissions: List[str] = field(default_factory=list)
    source_id: Optional[str] = None
    trace_id: str = ""
    downstream_auth_header: str = ""
    raw_api_key: str = ""

    def has_permission(self, required: str) -> bool:
        """
        Check if this caller has a specific permission.

        Wildcard '*' matches everything (platform-admin pattern).
        Exact string match otherwise.
        """
        return "*" in self.permissions or required in self.permissions

    @property
    def is_user(self) -> bool:
        return self.actor_type == "user"

    @property
    def is_service(self) -> bool:
        return self.actor_type == "service"

    @property
    def is_erp(self) -> bool:
        return self.actor_type == "erp"
