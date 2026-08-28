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
MAX_QUERY_RETRIES = int(os.getenv("MAX_QUERY_RETRIES", "3"))
HALLUCINATION_TOLERANCE = float(os.getenv("HALLUCINATION_TOLERANCE", "0.01"))  # ₹ tolerance for LLM math mismatch

CORS_ORIGINS = os.getenv("CORS_ORIGINS", "http://localhost:5173").split(",")