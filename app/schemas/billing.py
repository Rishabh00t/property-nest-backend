from __future__ import annotations

from typing import Any

from pydantic import BaseModel


class SubscribeRequest(BaseModel):
    plan_id: str


class SubscribeResponse(BaseModel):
    subscription_id: str
    razorpay_subscription_id: str
    razorpay_key_id: str


class VerifyPaymentRequest(BaseModel):
    razorpay_payment_id: str
    razorpay_subscription_id: str
    razorpay_signature: str


class VerifyPaymentResponse(BaseModel):
    success: bool


class MeResponse(BaseModel):
    subscription: dict[str, Any] | None
    invoices: list[dict[str, Any]]


class AdminOverviewResponse(BaseModel):
    mrr_paise: int
    active_subscriptions: int
    failed_invoices_recent: int
    invoices: list[dict[str, Any]]
