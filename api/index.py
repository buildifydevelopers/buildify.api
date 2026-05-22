import hashlib
import hmac
import logging
import os
import tempfile
from typing import Optional

from fastapi import FastAPI, HTTPException
from firebase_admin import credentials, firestore, initialize_app, _apps
from pydantic import BaseModel

logger = logging.getLogger(__name__)
app = FastAPI(title="Buildify Payment Verification API")


def init_firestore() -> firestore.Client:
    if _apps:
        return firestore.client()

    firebase_credentials_json = os.getenv("FIREBASE_CREDENTIALS_JSON")
    firebase_credentials_path = os.getenv("FIREBASE_CREDENTIALS_PATH", "firebase-key.json")

    if firebase_credentials_json:
        tmp_file = tempfile.NamedTemporaryFile(mode="w", suffix=".json", delete=False)
        tmp_file.write(firebase_credentials_json)
        tmp_file.flush()
        tmp_file.close()
        cred = credentials.Certificate(tmp_file.name)
        initialize_app(cred)
        logger.info("Initialized Firebase from FIREBASE_CREDENTIALS_JSON")
        return firestore.client()

    if os.path.exists(firebase_credentials_path):
        cred = credentials.Certificate(firebase_credentials_path)
        initialize_app(cred)
        logger.info(f"Initialized Firebase from {firebase_credentials_path}")
        return firestore.client()

    raise RuntimeError(
        "Firebase credentials not found. Set FIREBASE_CREDENTIALS_JSON or FIREBASE_CREDENTIALS_PATH."
    )


db = init_firestore()


class PaymentVerificationRequest(BaseModel):
    razorpay_payment_id: str
    razorpay_order_id: Optional[str] = None
    razorpay_signature: str
    amount: int
    category: str
    user_id: str
    user_email: str


class PaymentVerificationResponse(BaseModel):
    status: str
    payment_id: str
    message: str


def verify_signature(payment_id: str, order_id: Optional[str], signature: str, secret: str) -> bool:
    if order_id:
        payload = f"{order_id}|{payment_id}"
    else:
        payload = payment_id

    expected = hmac.new(secret.encode(), payload.encode(), hashlib.sha256).hexdigest()
    return hmac.compare_digest(expected, signature)


@app.get("/")
async def root() -> dict:
    return {
        "status": "ok",
        "service": "Buildify payment verifier",
        "message": "Use /api/payments/verify to verify Razorpay payments.",
    }


@app.get("/api/health")
async def health_check() -> dict:
    return {
        "status": "ok",
        "service": "Buildify payment verifier",
        "firestore": True,
    }


@app.post("/api/payments/verify", response_model=PaymentVerificationResponse)
async def verify_payment(payload: PaymentVerificationRequest):
    razorpay_secret = os.getenv("RAZORPAY_SECRET_KEY", "")
    if not razorpay_secret:
        logger.error("RAZORPAY_SECRET_KEY is missing")
        raise HTTPException(status_code=500, detail="Payment gateway not configured.")

    if not verify_signature(
        payment_id=payload.razorpay_payment_id,
        order_id=payload.razorpay_order_id,
        signature=payload.razorpay_signature,
        secret=razorpay_secret,
    ):
        logger.warning("Invalid Razorpay signature for payment %s", payload.razorpay_payment_id)
        raise HTTPException(status_code=400, detail="Invalid payment signature.")

    payment_doc = {
        "userId": payload.user_id,
        "userEmail": payload.user_email,
        "category": payload.category,
        "amount": payload.amount,
        "transactionId": payload.razorpay_payment_id,
        "orderId": payload.razorpay_order_id or "",
        "status": "verified",
        "verified": True,
        "createdAt": firestore.SERVER_TIMESTAMP,
    }

    doc_ref = db.collection("payments").add(payment_doc)
    payment_id = payload.razorpay_payment_id
    logger.info("Saved verified payment %s to Firestore", payment_id)

    return PaymentVerificationResponse(
        status="success",
        payment_id=payment_id,
        message="Payment verified and stored successfully.",
    )
