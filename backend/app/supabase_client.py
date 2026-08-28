"""
Thin Supabase wrapper. Every function degrades gracefully to an in-memory
fallback if SUPABASE_URL / SUPABASE_KEY aren't set yet, so the app still runs
end-to-end for local testing before you've wired up Supabase.
"""
import uuid
from app.config import SUPABASE_URL, SUPABASE_KEY

_supabase = None
_using_fallback = False

# in-memory fallback stores
_fallback_db = {
    "products": [],
    "sessions": {},
    "orders": {},
    "audit_log": [],
    "gate_tokens": {},
}

if SUPABASE_URL and SUPABASE_KEY:
    try:
        from supabase import create_client
        _supabase = create_client(SUPABASE_URL, SUPABASE_KEY)
    except Exception as e:  # pragma: no cover
        print(f"[supabase_client] Falling back to in-memory store: {e}")
        _using_fallback = True
else:
    _using_fallback = True
    print("[supabase_client] SUPABASE_URL/SUPABASE_KEY not set — using in-memory store. "
          "Data will NOT persist across restarts. See README to connect Supabase.")


def is_connected() -> bool:
    return _supabase is not None


def table(name: str):
    """Return the supabase table client, or None if running on fallback."""
    if _supabase is None:
        return None
    return _supabase.table(name)


def fallback_store():
    return _fallback_db


def new_id() -> str:
    return str(uuid.uuid4())
