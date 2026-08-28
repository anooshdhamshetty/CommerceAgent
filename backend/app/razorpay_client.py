import razorpay
from app.config import RAZORPAY_KEY_ID, RAZORPAY_KEY_SECRET

_client = None
if RAZORPAY_KEY_ID and RAZORPAY_KEY_SECRET:
    _client = razorpay.Client(auth=(RAZORPAY_KEY_ID, RAZORPAY_KEY_SECRET))


class RazorpayNotConfigured(Exception):
    pass


def get_client():
    if _client is None:
        raise RazorpayNotConfigured(
            "RAZORPAY_KEY_ID / RAZORPAY_KEY_SECRET not set. Add your test-mode keys to backend/.env — see README."
        )
    return _client
