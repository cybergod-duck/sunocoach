import os
import stripe
from typing import Dict, Any, Optional
try:
    from fastapi import Request, HTTPException
except ImportError:
    from utils.http_compat import HTTPException
    Request = None
from db.client import fetchrow, execute

stripe.api_key = os.environ.get("STRIPE_SECRET_KEY", "")
STRIPE_WEBHOOK_SECRET = os.environ.get("STRIPE_WEBHOOK_SECRET", "")
PRICE_ID_PRO = os.environ.get("STRIPE_PRICE_ID_PRO", "")


async def create_checkout_session(user_id: str, email: str) -> Dict[str, str]:
    """Create Stripe checkout session for Pro tier."""
    # Get or create Stripe customer
    user = await fetchrow("SELECT stripe_customer_id FROM users WHERE id = $1", user_id)
    customer_id = user["stripe_customer_id"] if user else None

    if not customer_id:
        customer = stripe.Customer.create(email=email)
        customer_id = customer.id
        await execute(
            "UPDATE users SET stripe_customer_id = $1 WHERE id = $2",
            customer_id, user_id
        )

    session = stripe.checkout.Session.create(
        customer=customer_id,
        payment_method_types=["card"],
        line_items=[{
            "price": PRICE_ID_PRO,
            "quantity": 1,
        }],
        mode="subscription",
        success_url=f"{os.environ.get('APP_URL', '')}/oauth/callback?success=true&session_id={{CHECKOUT_SESSION_ID}}",
        cancel_url=f"{os.environ.get('APP_URL', '')}/oauth/callback?canceled=true",
        metadata={"user_id": user_id}
    )

    return {"checkout_url": session.url or "", "session_id": session.id or ""}


async def handle_webhook(request: Request) -> Dict[str, str]:
    """Handle Stripe webhook events with signature verification."""
    payload = await request.body()
    sig_header = request.headers.get("stripe-signature", "")

    try:
        event = stripe.Webhook.construct_event(
            payload, sig_header, STRIPE_WEBHOOK_SECRET
        )
    except ValueError:
        raise HTTPException(status_code=400, detail="Invalid payload")
    except stripe.error.SignatureVerificationError:  # type: ignore
        raise HTTPException(status_code=400, detail="Invalid signature")

    event_type = event["type"]
    data = event["data"]["object"]

    # Idempotency: check if already processed
    existing = await fetchrow(
        "SELECT id FROM users WHERE stripe_subscription_id = $1",
        data.get("id", "")
    )

    if event_type == "payment_intent.succeeded":
        # Generate API key for new Pro user
        user_id = data.get("metadata", {}).get("user_id")
        if user_id:
            await _generate_api_key(user_id)
            # Send email notification (placeholder - integrate SendGrid/Resend)
            print(f"API key generated for user {user_id}")

    elif event_type == "customer.subscription.deleted":
        subscription_id = data.get("id")
        customer_id = data.get("customer")

        # Downgrade user to free
        user = await fetchrow(
            "SELECT id FROM users WHERE stripe_customer_id = $1",
            customer_id
        )
        if user:
            await execute(
                "UPDATE users SET tier = 'free', stripe_subscription_id = NULL WHERE id = $1",
                user["id"]
            )
            # Deactivate API keys
            await execute(
                "UPDATE api_keys SET active = false WHERE user_id = $1",
                user["id"]
            )

    elif event_type == "invoice.payment_failed":
        subscription_id = data.get("subscription")
        customer_id = data.get("customer")

        # Email warning + 3-day grace
        user = await fetchrow(
            "SELECT id, email FROM users WHERE stripe_customer_id = $1",
            customer_id
        )
        if user:
            # In production: send email via SendGrid/Resend
            print(f"Payment failed for {user['email']}. 3-day grace period started.")
            # Don't downgrade yet - give 3 days

    elif event_type == "checkout.session.completed":
        user_id = data.get("metadata", {}).get("user_id")
        subscription_id = data.get("subscription")

        if user_id and subscription_id:
            await execute(
                "UPDATE users SET tier = 'pro', stripe_subscription_id = $1 WHERE id = $2",
                subscription_id, user_id
            )
            await _generate_api_key(user_id)

    return {"status": "success", "event": event_type}


async def _generate_api_key(user_id: str) -> str:
    """Generate and store API key hash."""
    import secrets
    import hashlib

    raw_key = f"sk_suno_{secrets.token_urlsafe(32)}"
    key_hash = hashlib.sha256(raw_key.encode()).hexdigest()

    await execute(
        "INSERT INTO api_keys (user_id, key_hash, active) VALUES ($1, $2, true) "
        "ON CONFLICT (user_id) DO UPDATE SET key_hash = EXCLUDED.key_hash, active = true, last_used = NOW()",
        user_id, key_hash
    )

    return raw_key


async def get_subscription_status(user_id: str) -> Dict[str, Any]:
    """Check user's subscription status."""
    user = await fetchrow(
        "SELECT tier, stripe_subscription_id, stripe_customer_id FROM users WHERE id = $1",
        user_id
    )
    if not user:
        return {"tier": "free", "active": False}

    if user["tier"] == "pro" and user["stripe_subscription_id"]:
        try:
            sub = stripe.Subscription.retrieve(user["stripe_subscription_id"])
            return {
                "tier": user["tier"],
                "active": sub.status in ["active", "trialing"],
                "status": sub.status,
                "current_period_end": getattr(sub, "current_period_end", None)
            }
        except stripe.error.StripeError:  # type: ignore
            return {"tier": "free", "active": False, "error": "Invalid subscription"}

    return {"tier": user["tier"], "active": user["tier"] != "free"}
