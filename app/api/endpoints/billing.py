from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException, Request

from app.schemas.billing import (
    SubscribeRequest,
    SubscribeResponse,
    VerifyPaymentRequest,
    VerifyPaymentResponse,
)
from app.services.billing_service import billing_service
from app.services.razorpay_service import razorpay_service
from app.utils.auth import CurrentUser, get_current_user, require_super_admin

billing_router = APIRouter()


@billing_router.get("/plans")
async def get_plans():
    return billing_service.list_active_plans()


@billing_router.post("/subscribe", response_model=SubscribeResponse)
async def subscribe(payload: SubscribeRequest, user: CurrentUser = Depends(get_current_user)):
    try:
        return billing_service.start_subscription(user, payload.plan_id)
    except ValueError as error:
        raise HTTPException(status_code=400, detail=str(error)) from error
    except Exception as error:
        raise HTTPException(status_code=502, detail=f"Unable to start subscription: {error}") from error


@billing_router.post("/verify", response_model=VerifyPaymentResponse)
async def verify(payload: VerifyPaymentRequest, user: CurrentUser = Depends(get_current_user)):
    ok = billing_service.verify_payment(
        user,
        payload.razorpay_payment_id,
        payload.razorpay_subscription_id,
        payload.razorpay_signature,
    )
    if not ok:
        raise HTTPException(status_code=400, detail="Payment signature verification failed.")
    return VerifyPaymentResponse(success=True)


@billing_router.get("/me")
async def me(user: CurrentUser = Depends(get_current_user)):
    return billing_service.get_my_billing(user)


@billing_router.get("/admin/overview")
async def admin_overview(_: CurrentUser = Depends(require_super_admin)):
    return billing_service.get_admin_overview()


@billing_router.post("/webhook")
async def webhook(request: Request):
    # Signature verification needs the exact raw bytes Razorpay signed —
    # reading .json() first (which re-serializes) would invalidate it.
    raw_body = await request.body()
    signature = request.headers.get("x-razorpay-signature", "")

    if not razorpay_service.verify_webhook_signature(raw_body=raw_body, signature=signature):
        raise HTTPException(status_code=400, detail="Invalid webhook signature.")

    payload = await request.json()
    event_id = request.headers.get("x-razorpay-event-id")

    billing_service.process_webhook(raw_body, payload, event_id)
    return {"status": "ok"}
