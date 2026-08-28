# External agent demo

Proves the merchant is genuinely "AI-buyer-ready" — this script is a
stand-in for a completely separate AI shopping assistant that has never
seen this project's React UI. It only talks to the plain HTTP API.

## Run it

Make sure the backend (`../backend`) is running on port 8000 first.

```bash
pip install -r requirements.txt
python agent_client.py "3 lavender candles under 1500"
```

Try an out-of-stock request to see the graceful fallback from an external
caller's point of view:

```bash
python agent_client.py "2 sandalwood candles"
```

Try a high-value request to see the OTP gate trigger (needs an item/quantity
whose total exceeds your `BUDGET_AUTO_APPROVE_LIMIT`):

```bash
python agent_client.py "2 wireless earbuds under 3500"
```
