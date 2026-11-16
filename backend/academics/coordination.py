"""Transaction-scoped coordination for participating academic operations."""

from hashlib import sha256
from uuid import UUID

from django.db import DEFAULT_DB_ALIAS, NotSupportedError, connections

_KEY_NAMESPACE = b"nlschool.academic-coordination.v1\0"
_STATE_ATTRIBUTE = "_academic_coordination_state"


def academic_coordination_key(tenant_id: UUID | str) -> int:
    """Return the stable signed PostgreSQL advisory key for a tenant UUID."""
    if not isinstance(tenant_id, (UUID, str)):
        raise ValueError("Provide a nonzero tenant UUID.")
    try:
        identity = UUID(str(tenant_id))
    except ValueError as exc:
        raise ValueError("Provide a nonzero tenant UUID.") from exc
    if identity.int == 0:
        raise ValueError("Provide a nonzero tenant UUID.")
    return int.from_bytes(sha256(_KEY_NAMESPACE + identity.bytes).digest()[:8], "big", signed=True)


def acquire_academic_coordination(tenant_id: UUID | str, *, using: str = DEFAULT_DB_ALIAS) -> None:
    """Acquire the tenant lock in an outer READ COMMITTED atomic transaction.

    The first call must precede savepoints, source reads, and row locks. Repeated
    calls for the same tenant are permitted in nested atomic blocks. The caller
    must authorize the actor and verify tenant existence after acquiring the lock.
    """
    key = academic_coordination_key(tenant_id)
    identity = UUID(str(tenant_id))
    db = connections[using]
    if db.vendor != "postgresql":
        raise NotSupportedError("Academic coordination requires PostgreSQL.")
    if not db.in_atomic_block or not db.commit_on_exit:
        raise RuntimeError("Start an outer transaction.atomic() before acquiring academic coordination.")
    with db.cursor() as cursor:
        cursor.execute("SELECT current_setting('transaction_isolation'), txid_current(), pg_backend_pid()")
        isolation, xid, pid = cursor.fetchone()
        if isolation != "read committed":
            raise RuntimeError("Academic coordination requires a READ COMMITTED transaction.")
        state = getattr(db, _STATE_ATTRIBUTE, None)
        if state and state[:2] == (pid, xid):
            if state[2] != identity:
                raise RuntimeError("An academic transaction may coordinate only one tenant.")
            return
        if any(savepoint is not None for savepoint in db.savepoint_ids):
            raise RuntimeError("Acquire academic coordination before entering a savepoint.")
        cursor.execute("SELECT pg_advisory_xact_lock(%s::bigint)", [key])
        setattr(db, _STATE_ATTRIBUTE, (pid, xid, identity))
