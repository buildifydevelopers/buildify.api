from __future__ import annotations

import hmac
import hashlib
import logging
import os
from datetime import datetime

from fastapi import APIRouter, HTTPException, status
from pydantic import BaseModel

from app.services.firebase_service import firebase_service

logger = logging.getLogger(__name__)

router = APIRouter()


class PaymentVerificationRequest(BaseModel):
    razorpay_payment_id: str
    razorpay_order_id: str | None = None
    razorpay_signature: str
    amount: int
    category: str
    user_id: str
    user_email: str


class PaymentVerificationResponse(BaseModel):
    status: str
    payment_id: str
    message: str


def verify_razorpay_signature(
    payment_id: str, 
    order_id: str, 
    signature: str, 
    secret: str
) -> bool:
    """Verify Razorpay payment signature using HMAC-SHA256."""
    try:
        # For payment verification: message = "{order_id}|{payment_id}"
        # If no order_id, just use payment_id
        if order_id:
            message = f"{order_id}|{payment_id}"
        else:
            message = payment_id
        
        expected_signature = hmac.new(
            secret.encode(),
            message.encode(),
            hashlib.sha256
        ).hexdigest()
        
        # Use constant-time comparison to prevent timing attacks
        return hmac.compare_digest(expected_signature, signature)
    except Exception as exc:
        logger.error(f"Signature verification error: {exc}")
        return False


@router.post(
    "/payments/verify",
    response_model=PaymentVerificationResponse,
    status_code=status.HTTP_200_OK,
    tags=["Payments"]
)
async def verify_payment(request: PaymentVerificationRequest) -> PaymentVerificationResponse:
    """
    Verify Razorpay payment signature and save to Firestore.
    
    - **razorpay_payment_id**: Payment ID from Razorpay checkout
    - **razorpay_signature**: Signature from Razorpay checkout
    - **amount**: Payment amount in INR
    - **category**: Payment category/type
    - **user_id**: Firebase user ID
    - **user_email**: User email address
    """
    try:
        # Get secret key from environment
        secret_key = os.getenv("RAZORPAY_SECRET_KEY", "")
        
        if not secret_key:
            logger.error("RAZORPAY_SECRET_KEY not configured")
            raise HTTPException(
                status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
                detail="Payment service not configured"
            )
        
        # Verify signature
        is_valid = verify_razorpay_signature(
            payment_id=request.razorpay_payment_id,
            order_id=request.razorpay_order_id or "",
            signature=request.razorpay_signature,
            secret=secret_key
        )
        
        if not is_valid:
            logger.warning(
                f"Invalid payment signature for {request.razorpay_payment_id} "
                f"from user {request.user_id}"
            )
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="Invalid payment signature - payment verification failed"
            )
        
        # Signature verified - save to Firestore
        payment_data = {
            "userId": request.user_id,
            "userEmail": request.user_email,
            "category": request.category,
            "amount": request.amount,
            "transactionId": request.razorpay_payment_id,
            "orderId": request.razorpay_order_id or "",
            "status": "completed",
            "verified": True,
            "createdAt": datetime.now(),
        }
        
        # Save to Firestore
        doc_id = await firebase_service.save_payment(payment_data)
        
        logger.info(
            f"Payment verified and saved: {request.razorpay_payment_id} "
            f"for user {request.user_email} (Doc ID: {doc_id})"
        )
        
        return PaymentVerificationResponse(
            status="success",
            payment_id=request.razorpay_payment_id,
            message=f"Payment verified and saved successfully"
        )
    
    except HTTPException:
        raise
    except Exception as exc:
        logger.error(f"Payment verification error: {exc}", exc_info=True)
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="An error occurred while processing payment"
        ) from exc


@router.get(
    "/payments/health",
    tags=["Payments"]
)
async def payments_health_check() -> dict:
    """Check payment service health."""
    return {
        "status": "ok",
        "service": "payments",
        "razorpay_configured": bool(os.getenv("RAZORPAY_SECRET_KEY"))
    }
