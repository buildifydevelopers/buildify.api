# Deploying BuildifyAPI to Vercel

This guide shows minimal steps to deploy the `buildify.api` FastAPI app to Vercel using the Python runtime.

## Files added
- `vercel.json` — Routes all requests to `app/main.py` using the `@vercel/python` builder.

## Required Environment Variables
Set these in Vercel Project > Settings > Environment Variables:

- `RAZORPAY_SECRET_KEY` — Your Razorpay secret key (keep private)
- `FIREBASE_CREDENTIALS_JSON` — JSON string of your Firebase service account (or use `FIREBASE_CREDENTIALS_PATH` to upload a file)
- `PB_DATABASE_URL` — Optional. Not required for the Firestore-only payment verification endpoint.
- `API_AUTH_TOKEN` — Optional API auth token
- Any LLM provider keys used by the API (e.g., `GEMINI_API_KEY`, `CEREBRAS_API_KEY`)

## Build & Start
Vercel will use `vercel.json` and `api/index.py` to build and serve the Firestore-only payment verifier.
Locally, you can run:

```bash
pip install -r requirements.txt
uvicorn api.index:app --reload
```

## Verification Endpoint
- `POST /api/payments/verify` — verify Razorpay signature and save the payment in Firestore
- `GET /api/health` — health check for the payment verification service

## Notes
- `FIREBASE_CREDENTIALS_JSON` is preferred for serverless platforms; the SDK will write it to a temp file and initialize the Admin SDK.
- Keep `RAZORPAY_SECRET_KEY` private — do not expose it client-side.

If you want, I can prepare a minimal `api/verify_test.py` file for local testing before deployment.
