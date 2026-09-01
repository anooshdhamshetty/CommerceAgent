"""
Order creation agent module.
Exclusively interfaces with Razorpay order creation endpoints upon consumption of a valid, single-use gate token.
Lacks autonomous decision-making; acts strictly on parameters pre-approved by the deterministic gate.
"""
from app.gate import consume_gate_token
from app.razorpay_client import get_client
from app.supabase_client import table, is_connected, fallback_store, new_id
from app.guardrails.schemas import OrderRecord


def run_order_agent(session_id: str, gate_token: str) -> OrderRecord:
    approved = consume_gate_token(gate_token)
    if approved is None:
        raise PermissionError("Invalid or already-used gate token — order refused.")

    amount_paise = int(round(approved["amount"] * 100))
    client = get_client()
    rp_order = client.order.create({
        "amount": amount_paise,
        "currency": "INR",
        "payment_capture": 0,  # captured explicitly by payment agent after signature verification
        "notes": {"session_id": session_id, "sku": approved["sku"]},
    })

    record = OrderRecord(
        session_id=session_id,
        gate_token=gate_token,
        sku=approved["sku"],
        quantity=approved["quantity"],
        amount=approved["amount"],
        razorpay_order_id=rp_order["id"],
        status="created",
    )

    row = record.model_dump()
    row["id"] = new_id()
    if is_connected():
        table("orders").insert(row).execute()
    else:
        fallback_store()["orders"][rp_order["id"]] = row

    return record
