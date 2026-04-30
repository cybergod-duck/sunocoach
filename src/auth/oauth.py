import os
import secrets
import hashlib
import time
from typing import Dict, Any, Optional
from fastapi import Request, HTTPException
from db.client import fetchrow, execute

# In-memory token store (replace with Redis/Upstash in production)
_token_store: Dict[str, Dict[str, Any]] = {}
_auth_codes: Dict[str, Dict[str, Any]] = {}

CLIENT_ID = os.environ.get("OAUTH_CLIENT_ID", "sunocoach-claude")
CLIENT_SECRET = os.environ.get("OAUTH_CLIENT_SECRET", "")
TOKEN_EXPIRY = 3600  # 1 hour


def _hash_token(token: str) -> str:
    return hashlib.sha256(token.encode()).hexdigest()


def _generate_token() -> str:
    return secrets.token_urlsafe(32)


def _generate_code() -> str:
    return secrets.token_urlsafe(24)


async def create_authorization_url(redirect_uri: str, scope: str = "read", state: str = "") -> Dict[str, str]:
    """Generate OAuth authorization URL."""
    code = _generate_code()
    _auth_codes[code] = {
        "redirect_uri": redirect_uri,
        "scope": scope,
        "state": state,
        "created_at": time.time(),
        "used": False
    }

    # In real deployment, this would be a hosted login page
    return {
        "authorization_url": f"/oauth/authorize?response_type=code&client_id={CLIENT_ID}&redirect_uri={redirect_uri}&scope={scope}&state={state}",
        "code": code  # For testing/dev only
    }


async def exchange_code_for_token(code: str, client_id: str, client_secret: str, redirect_uri: str) -> Dict[str, Any]:
    """Exchange authorization code for access token."""
    if client_id != CLIENT_ID or client_secret != CLIENT_SECRET:
        raise HTTPException(status_code=401, detail="Invalid client credentials")

    auth_data = _auth_codes.get(code)
    if not auth_data:
        raise HTTPException(status_code=400, detail="Invalid authorization code")

    if auth_data["used"]:
        raise HTTPException(status_code=400, detail="Authorization code already used")

    if auth_data["redirect_uri"] != redirect_uri:
        raise HTTPException(status_code=400, detail="Redirect URI mismatch")

    if time.time() - auth_data["created_at"] > 600:  # 10 min expiry
        raise HTTPException(status_code=400, detail="Authorization code expired")

    auth_data["used"] = True

    # Create user if not exists (email from auth context - simplified)
    # In production, this comes from Google/email OAuth provider
    access_token = _generate_token()
    refresh_token = _generate_token()

    token_hash = _hash_token(access_token)

    # Store token
    _token_store[token_hash] = {
        "access_token": access_token,
        "refresh_token": refresh_token,
        "created_at": time.time(),
        "expires_at": time.time() + TOKEN_EXPIRY,
        "scope": auth_data["scope"],
        "user_id": None  # Set after user creation/login
    }

    return {
        "access_token": access_token,
        "token_type": "Bearer",
        "expires_in": TOKEN_EXPIRY,
        "refresh_token": refresh_token,
        "scope": auth_data["scope"]
    }


async def refresh_access_token(refresh_token: str) -> Dict[str, Any]:
    """Refresh expired access token."""
    for token_hash, data in _token_store.items():
        if data.get("refresh_token") == refresh_token:
            if time.time() - data["created_at"] > 30 * 24 * 3600:  # 30 days
                raise HTTPException(status_code=401, detail="Refresh token expired")

            new_access = _generate_token()
            new_hash = _hash_token(new_access)

            _token_store[new_hash] = {
                "access_token": new_access,
                "refresh_token": refresh_token,
                "created_at": time.time(),
                "expires_at": time.time() + TOKEN_EXPIRY,
                "scope": data["scope"],
                "user_id": data["user_id"]
            }

            del _token_store[token_hash]

            return {
                "access_token": new_access,
                "token_type": "Bearer",
                "expires_in": TOKEN_EXPIRY,
                "scope": data["scope"]
            }

    raise HTTPException(status_code=401, detail="Invalid refresh token")


async def validate_token(request: Request) -> Dict[str, Any]:
    """Validate Bearer token from request headers."""
    auth_header = request.headers.get("Authorization", "")
    if not auth_header.startswith("Bearer "):
        raise HTTPException(status_code=401, detail="Missing or invalid Authorization header")

    token = auth_header[7:]
    token_hash = _hash_token(token)

    token_data = _token_store.get(token_hash)
    if not token_data:
        raise HTTPException(status_code=401, detail="Invalid token")

    if time.time() > token_data["expires_at"]:
        raise HTTPException(status_code=401, detail="Token expired")

    # Check user tier and gate access
    if token_data.get("user_id"):
        user = await fetchrow("SELECT tier, stripe_subscription_id FROM users WHERE id = $1", token_data["user_id"])
        if user:
            token_data["tier"] = user["tier"]
            token_data["stripe_subscription_id"] = user["stripe_subscription_id"]

    return token_data


async def get_oauth_discovery() -> Dict[str, Any]:
    """OAuth 2.0 discovery endpoint."""
    base_url = os.environ.get("APP_URL", "https://sunocoach.onrender.com")
    return {
        "issuer": base_url,
        "authorization_endpoint": f"{base_url}/oauth/authorize",
        "token_endpoint": f"{base_url}/oauth/token",
        "token_endpoint_auth_methods_supported": ["client_secret_post"],
        "grant_types_supported": ["authorization_code", "refresh_token"],
        "scopes_supported": ["read", "write", "contribute"],
        "response_types_supported": ["code"]
    }
