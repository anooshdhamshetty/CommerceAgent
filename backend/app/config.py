import os
from dotenv import load_dotenv

load_dotenv()

GROQ_API_KEY = os.getenv("GROQ_API_KEY", "")
LLM_MODEL = os.getenv("LLM_MODEL", "llama-3.1-8b-instant")

SUPABASE_URL = os.getenv("SUPABASE_URL", "")
SUPABASE_KEY = os.getenv("SUPABASE_KEY", "")

RAZORPAY_KEY_ID = os.getenv("RAZORPAY_KEY_ID", "")
RAZORPAY_KEY_SECRET = os.getenv("RAZORPAY_KEY_SECRET", "")

# Merchant-configured policy (this is the "bounded" part of the pipeline)
BUDGET_AUTO_APPROVE_LIMIT = float(os.getenv("BUDGET_AUTO_APPROVE_LIMIT", "2000"))
MAX_QUERY_RETRIES = int(os.getenv("MAX_QUERY_RETRIES", "1"))
# How many times a shopper may click a relaxation option before we stop offering
# more adjustments and show a final "couldn't find it" message with no buttons.
# This caps the *user-driven* adjust loop (distinct from MAX_QUERY_RETRIES, which
# caps the pipeline's own internal auto-broaden retries within a single search).
MAX_RELAX_ATTEMPTS = int(os.getenv("MAX_RELAX_ATTEMPTS", "3"))
HALLUCINATION_TOLERANCE = float(os.getenv("HALLUCINATION_TOLERANCE", "0.01"))  # ₹ tolerance for LLM math mismatch

# DEMO-ONLY. When true, the OTP code is echoed back in the API response so the
# frontend can show it as a popup instead of the user checking server logs.
# There is no real SMS/email gateway wired up in this project, so this is the
# only way a judge/tester ever sees the code. Set to "false" (or wire up a
# real OTP provider in gate.py) before this ever goes anywhere real — an OTP
# that's readable by whoever is trying to pay is not a security check anymore.
SHOW_OTP_IN_RESPONSE = os.getenv("SHOW_OTP_IN_RESPONSE", "true").lower() == "true"

CORS_ORIGINS = os.getenv("CORS_ORIGINS", "http://localhost:5173").split(",")