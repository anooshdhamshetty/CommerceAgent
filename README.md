# Agentic checkout — Razorpay buildathon (Track 01)

An agent pipeline that takes a natural-language purchase request, searches a
merchant catalog, reasons about fit within budget/stock, and — only after a
deterministic policy check and two user confirmations — creates and captures
a Razorpay **test-mode** payment. Every step is logged to an audit trail
visible in the UI.

This implements the full pipeline discussed end-to-end: query agent → fetch
agent → reasoning agent → deterministic gate (with OTP for high-value
orders) → order agent → payment agent → **upsell agent**, with a pydantic
guardrail schema at every agent boundary and a graceful fallback path when
nothing matches. It also ships a standalone script proving the merchant is
transactable by a completely separate AI agent, not just this project's UI.

## Stack

- **Backend**: Python, FastAPI, Google Gemini API (`gemini-3.5-flash`, free tier)
  for the query/reasoning agents, Razorpay Python SDK (test mode), Supabase
  (Postgres) for the catalog/orders/audit log — with an in-memory fallback
  so it still runs before Supabase is wired up.
- **Frontend**: React + Vite, Razorpay Checkout.js widget for the actual
  payment UI.

## Project layout

```
buildathon/
  backend/
    app/
      agents/            query_agent, fetch_agent, reasoning_agent, order_agent, payment_agent, upsell_agent
      guardrails/         pydantic schemas — one per agent boundary
      main.py              FastAPI routes
      orchestrator.py      the single state machine everything routes through
      gate.py               the deterministic (non-LLM) money gate, incl. OTP challenge/verify
      catalog.py, audit.py, sanitize.py, llm.py, razorpay_client.py, supabase_client.py
    seed_data/products.json  sample catalog across 4 categories (also used as fallback if Supabase isn't set up)
    supabase_schema.sql     run this in Supabase's SQL editor
    seed_supabase.py        optional: seed via Python instead of SQL
    requirements.txt
    .env.example
  frontend/
    src/
      App.jsx, api.js
      components/  ProposalCard, FallbackCard, ReceiptCard, UpsellCard, AuditLedger
    .env.example
  external_agent_demo/
    agent_client.py        proves a THIRD-PARTY AI agent (no UI) can transact end-to-end
    requirements.txt
```

## 1. Set up Supabase

1. Create a project at [supabase.com](https://supabase.com) (free tier is fine).
2. Open **SQL Editor → New query**, paste the contents of
   `backend/supabase_schema.sql`, and run it. This creates the `products`,
   `orders`, and `audit_log` tables and seeds the sample candle catalog.
3. Go to **Project Settings → API** and copy your **Project URL** and
   **anon public key** (or service_role key for full write access during
   the hackathon).

## 2. Set up Razorpay test mode

1. Sign in to the [Razorpay dashboard](https://dashboard.razorpay.com).
2. Make sure **Test Mode** is toggled on (top of the dashboard).
3. Go to **Settings → API Keys → Generate Test Key** and copy the
   `Key Id` and `Key Secret`.

## 3. Set up Gemini

Get a free API key from [aistudio.google.com/apikey](https://aistudio.google.com/apikey)
— no credit card required. Used by the query and reasoning agents. Free tier
is roughly 15 requests/minute and 1,500/day on `gemini-3.5-flash`, which
comfortably covers a demo session (each search uses up to 2 calls, retried
up to `MAX_QUERY_RETRIES` times).

## 4. Run the backend

```bash
cd backend
python -m venv venv
venv/bin/activate          # Windows: venv\Scripts\activate
pip install -r requirements.txt

cp .env.example .env
# now edit .env and fill in:
#   GEMINI_API_KEY
#   SUPABASE_URL, SUPABASE_KEY
#   RAZORPAY_KEY_ID, RAZORPAY_KEY_SECRET

uvicorn app.main:app --reload --port 8000
```

Visit `http://localhost:8000/api/health` — you should see `{"status":"ok"}`.
Visit `http://localhost:8000/docs` for interactive API docs.

> The app runs even without Supabase/Razorpay/Anthropic keys set — it falls
> back to an in-memory catalog so you can sanity-check the server starts.
> You need all three configured for the actual chat → payment flow to work.

## 5. Run the frontend

```bash
cd frontend
npm install
cp .env.example .env    # defaults to http://localhost:8000, change if needed
npm run dev
```

Visit `http://localhost:5173`.

## 6. Try it

Type: `3 lavender candles under 1500`

- Query agent parses it, fetch agent hits the catalog, reasoning agent
  proposes an order.
- Confirm the order → deterministic gate checks budget/stock in plain code
  → Razorpay test order is created.
- Razorpay's checkout modal opens. Use test card `4111 1111 1111 1111`, any
  future expiry, any CVV, any name.
- On success, payment is verified + captured, and a receipt appears.
- If the item purchased has a genuinely complementary in-stock item within
  your remaining budget, an **upsell offer** appears right after — accept
  it to run a real second Razorpay payment for that item.
- Watch the **audit trail** panel on the right populate live with every
  step, in order — this is your "explainable" proof for judges.

To see the **graceful fallback**, ask for something out of stock, e.g.
`2 sandalwood candles` (seeded with 0 stock) — the agent will suggest
in-stock alternatives instead of failing silently.

To see the **OTP gate**, ask for something whose total exceeds
`BUDGET_AUTO_APPROVE_LIMIT` (default ₹2000), e.g. `2 wireless earbuds under 3500`.
Confirming the order won't create a Razorpay order yet — it'll ask for a
6-digit code. **Check your backend terminal** — that's where the code is
printed (standing in for an SMS/email gateway in this demo).

## 7. Prove a third-party agent can transact too

`external_agent_demo/agent_client.py` is a standalone script with zero
knowledge of the React UI — it only calls the plain HTTP API, the same way
a genuinely separate AI shopping assistant would. This is the concrete
proof that the merchant is transactable end-to-end by *any* agent, not just
the one built for this project's own interface.

```bash
cd external_agent_demo
pip install -r requirements.txt
python agent_client.py "3 lavender candles under 1500"
```

## Agent-readable catalog

A cold AI buyer agent — one that has never seen this project's UI — can
discover this merchant with no prior integration:

1. `GET /.well-known/agent-manifest.json` — what's sold here, where the
   catalog and checkout endpoints live, and the merchant's policy
   constraints (auto-approve limit, OTP requirement, audit trail location).
2. `GET /api/catalog?category=&max_price=` — the actual product listing,
   filterable, with no chat pipeline required to browse it.

`external_agent_demo/agent_client.py` demonstrates exactly this discovery
sequence before attempting any purchase.

## Where the guardrails live

| Boundary | Guardrail |
|---|---|
| Query agent output | `ProductQuery` pydantic schema — rejects malformed/out-of-range fields |
| Catalog → Fetch agent | `FetchedProduct` schema on every row; malformed rows dropped, not passed on |
| Reasoning agent output | `ReasoningDecision` schema (enum-constrained decision) **plus** deterministic recompute — the orchestrator recalculates `price × qty` itself and overrides the LLM's number if it doesn't match (`recompute_and_verify` in `reasoning_agent.py`) |
| Reasoning → Gate | The gate (`gate.py`) contains **zero LLM calls** — plain `if` checks on budget, stock, and OTP threshold |
| Gate → Order agent | Single-use, expiring `gate_token` (persisted in Supabase) — an order can't be created without a token the gate itself issued, and if that token required OTP, it can't be consumed until the code is verified |
| High-value orders | Gate-issued OTP challenge; `verify_otp()` in `gate.py` — order agent is never called for these until the code matches |
| Order → Payment agent | Payment agent looks up the amount from its own order record, never from the frontend request — a tampered client-side amount can't change what gets captured |
| Upsell agent output | `UpsellSuggestion` schema **plus** a guardrail that rejects any suggested SKU that wasn't actually in the candidate list offered to it |
| Catalog text → any LLM prompt | `sanitize.py` strips instruction-like phrases (prompt-injection defense) before product text reaches an agent's context |

## Notes for the demo

- `BUDGET_AUTO_APPROVE_LIMIT` in `.env` controls the OTP threshold — raise or
  lower it to demo the "gated above a threshold" behavior live.
- `MAX_QUERY_RETRIES` caps the query→fetch→reasoning retry loop so an
  unmatchable request fails gracefully instead of looping forever.
- The audit panel polls every 2 seconds — for a slicker demo you could swap
  this for a Supabase realtime subscription, but polling is simpler to
  reason about under time pressure.
