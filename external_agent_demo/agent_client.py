"""
Standalone demo proving the merchant is transactable by ANY AI agent, not
just this project's own React UI. This script has zero knowledge of the
frontend — it only speaks to the plain HTTP API, exactly the way a
completely separate third-party AI shopping assistant would.

This is the concrete proof for "make the merchant transactable by an AI
buyer end to end" — the agent driving the purchase here isn't the one
we built the UI around.

Usage:
    pip install -r requirements.txt
    python agent_client.py "3 lavender candles under 1500"
"""
import sys
import requests

API_URL = "http://localhost:8000"


def main():
    request_text = " ".join(sys.argv[1:]) or "3 lavender candles under 1500"

    # Step 0: discover the merchant cold — a real third-party agent has no
    # prior integration with this specific store, only this one well-known URL.
    print("[external agent] discovering merchant via /.well-known/agent-manifest.json")
    manifest = requests.get(f"{API_URL}/.well-known/agent-manifest.json").json()
    print(f"[external agent] merchant: {manifest['merchant']}")
    print(f"[external agent] policy: auto-approve up to ₹{manifest['policy']['auto_approve_limit']}, OTP required above that")

    catalog_url = API_URL + manifest["catalog"]["url"]
    print(f"[external agent] browsing catalog at {catalog_url}")
    catalog = requests.get(catalog_url).json()["products"]
    print(f"[external agent] {len(catalog)} products visible, e.g.: "
          f"{[p['name'] for p in catalog[:3]]}")

    print(f"\n[external agent] requesting: {request_text!r}")

    chat = requests.post(f"{API_URL}/api/chat", json={"session_id": None, "message": request_text}).json()
    session_id = chat["session_id"]

    if chat["status"] == "fallback":
        print(f"[external agent] no match: {chat['message']}")
        print(f"[external agent] alternatives offered: {chat.get('alternatives')}")
        return

    print(f"[external agent] proposal: {chat['quantity']} x {chat['name']} = ₹{chat['total_amount']}")
    print("[external agent] auto-confirming (in production this would be the calling agent's own policy check)")

    confirm = requests.post(f"{API_URL}/api/confirm-order", json={"session_id": session_id}).json()

    if not confirm["approved"]:
        print(f"[external agent] gate refused the order: {confirm['reason']}")
        return

    if confirm.get("requires_otp"):
        print("[external agent] order requires OTP (above the merchant's auto-approve limit)")
        print("[external agent] check the BACKEND server console for the 6-digit code")
        code = input("[external agent] enter code: ").strip()
        confirm = requests.post(
            f"{API_URL}/api/verify-otp",
            json={"session_id": session_id, "gate_token": confirm["gate_token"], "code": code},
        ).json()
        if not confirm["approved"]:
            print(f"[external agent] OTP verification failed: {confirm['reason']}")
            return

    print(f"[external agent] Razorpay order created: {confirm['razorpay_order_id']} for ₹{confirm['amount']}")
    print("[external agent] completing the actual payment needs Razorpay's checkout widget (a browser")
    print("[external agent] step by design) — this script proves the agent can drive every decision up")
    print("[external agent] to that point with zero human UI involved.")

    trail = requests.get(f"{API_URL}/api/audit/{session_id}").json()
    print(f"\n[external agent] full audit trail for session {session_id} ({len(trail['events'])} events):")
    for e in trail["events"]:
        print(f"  - {e['step']}")


if __name__ == "__main__":
    main()
