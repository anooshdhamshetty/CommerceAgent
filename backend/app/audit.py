"""
Audit logging module.
Records all pipeline step execution details sequentially for session traceability and debugging.
"""
import datetime
from app.supabase_client import table, is_connected, fallback_store, new_id


def log_event(session_id: str, step: str, payload: dict):
    entry = {
        "id": new_id(),
        "session_id": session_id,
        "step": step,
        "payload": payload,
        "created_at": datetime.datetime.utcnow().isoformat(),
    }
    if is_connected():
        table("audit_log").insert(entry).execute()
    else:
        fallback_store()["audit_log"].append(entry)
    return entry


def get_trail(session_id: str):
    if is_connected():
        res = (
            table("audit_log")
            .select("*")
            .eq("session_id", session_id)
            .order("created_at")
            .execute()
        )
        return res.data or []
    return [e for e in fallback_store()["audit_log"] if e["session_id"] == session_id]
