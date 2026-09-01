"""
Payment processing agent module.
Verifies Razorpay checkout signatures to ensure client-side integrity and captures payments server-side.
Derives payment amounts strictly from trusted internal order records rather than client payloads.
"""

#pyrefly: ignore [missing-import]
import razorpay.errors
from app.razorpay_client import get_client
from app.supabase_client import table, is_connected, fallback_store
from app.guardrails.schemas import PaymentVerification


def run_payment_agent(verification: PaymentVerification) -> dict:
    client = get_client()

    try:
        client.utility.verify_payment_signature({
            "razorpay_order_id": verification.razorpay_order_id,
            "razorpay_payment_id": verification.razorpay_payment_id,
            "razorpay_signature": verification.razorpay_signature,
        })
    except razorpay.errors.SignatureVerificationError:
        _update_status(verification.razorpay_order_id, "failed")
        raise ValueError("Payment signature verification failed — payment refused.")

    order = _get_order(verification.razorpay_order_id)
    if order is None:
        raise ValueError("No matching order found for this payment.")

    amount_paise = int(round(order["amount"] * 100))
    client.payment.capture(verification.razorpay_payment_id, amount_paise)

    _update_status(verification.razorpay_order_id, "paid")
    return {
        "status": "paid",
        "razorpay_order_id": verification.razorpay_order_id,
        "razorpay_payment_id": verification.razorpay_payment_id,
        "amount": order["amount"],
        "sku": order["sku"],
        "quantity": order["quantity"],
    }


def _get_order(razorpay_order_id: str):
    if is_connected():
        res = table("orders").select("*").eq("razorpay_order_id", razorpay_order_id).limit(1).execute()
        rows = res.data or []
        return rows[0] if rows else None
    return fallback_store()["orders"].get(razorpay_order_id)


def _update_status(razorpay_order_id: str, status: str):
    if is_connected():
        table("orders").update({"status": status}).eq("razorpay_order_id", razorpay_order_id).execute()
    else:
        order = fallback_store()["orders"].get(razorpay_order_id)
        if order:
            order["status"] = status
