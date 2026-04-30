import os
import secrets
import hashlib
import time
import base64
from typing import Dict, Any, Optional
from fastapi import Request, HTTPException
from db.client import fetchrow, execute

# In-memory stores (replace with Redis/Upstash in production)
_token_store: Dict[str, Dict[str, Any]] = {}
_auth_codes: Dict[str, Dict[str, Any]] = {}
_registered_clients: Dict[str, Dict[str, Any]] = {}  # Dynamic client registration

CLIENT_ID = os.environ.get("OAUTH_CLIENT_ID", "sunocoach-claude")
CLIENT_SECRET = os.environ.get("OAUTH_CLIENT_SECRET", "")
TOKEN_EXPIRY = 3600  # 1 hour

# Claude's allowed redirect URIs
ALLOWED_REDIRECT_URIS = [
    "https://claude.ai/oauth/callback",
    "https://claude.ai/api/mcp/auth_callback"
]


def _hash_token(token: str) -> str:
    return hashlib.sha256(token.encode()).hexdigest()


def _generate_token() -> str:
    return secrets.token_urlsafe(32)


def _generate_code() -> str:
    return secrets.token_urlsafe(24)


def _hash_password(password: str) -> str:
    """Return SHA-256 hash of password."""
    return hashlib.sha256(password.encode()).hexdigest()


def _verify_pkce(code_verifier: str, code_challenge: str) -> bool:
    """Verify PKCE S256 challenge."""
    computed = hashlib.sha256(code_verifier.encode()).digest()
    computed_b64 = base64.urlsafe_b64encode(computed).decode().rstrip('=')
    return computed_b64 == code_challenge


async def create_authorization_url(redirect_uri: str, scope: str = "read", state: str = "", code_challenge: Optional[str] = None, code_challenge_method: Optional[str] = None) -> Dict[str, str]:
    """Generate OAuth authorization URL with PKCE support."""
    # Validate redirect URI
    if redirect_uri not in ALLOWED_REDIRECT_URIS:
        raise HTTPException(status_code=400, detail=f"Redirect URI not whitelisted: {redirect_uri}")
    
    # Validate PKCE (required for Claude)
    if code_challenge_method and code_challenge_method != "S256":
        raise HTTPException(status_code=400, detail="Only S256 code_challenge_method is supported")
    
    code = _generate_code()
    _auth_codes[code] = {
        "redirect_uri": redirect_uri,
        "scope": scope,
        "state": state,
        "code_challenge": code_challenge,
        "code_challenge_method": code_challenge_method,
        "created_at": time.time(),
        "used": False
    }

    # In real deployment, this would be a hosted login page
    return {
        "authorization_url": f"/oauth/authorize?response_type=code&client_id={CLIENT_ID}&redirect_uri={redirect_uri}&scope={scope}&state={state}",
        "code": code  # For testing/dev only
    }


async def login_user(email: str, password: str, redirect_uri: str, scope: str, state: str, code_challenge: Optional[str] = None, code_challenge_method: Optional[str] = None) -> Dict[str, Any]:
    """Validate credentials, upsert user, generate auth code, return redirect info."""
    # Validate redirect URI
    if redirect_uri not in ALLOWED_REDIRECT_URIS:
        raise HTTPException(status_code=400, detail=f"Redirect URI not whitelisted: {redirect_uri}")

    if code_challenge_method and code_challenge_method != "S256":
        raise HTTPException(status_code=400, detail="Only S256 code_challenge_method is supported")

    password_hash = _hash_password(password)

    # Upsert user — auto-register on first login
    existing = await fetchrow("SELECT id, password_hash FROM users WHERE email = $1", email)

    if existing:
        if existing["password_hash"] and existing["password_hash"] != password_hash:
            raise HTTPException(status_code=401, detail="Invalid email or password")
        user_id = existing["id"]
    else:
        row = await fetchrow(
            "INSERT INTO users (email, password_hash) VALUES ($1, $2) RETURNING id",
            email, password_hash
        )
        user_id = row["id"]

    # Generate auth code
    code = _generate_code()
    _auth_codes[code] = {
        "redirect_uri": redirect_uri,
        "scope": scope,
        "state": state,
        "code_challenge": code_challenge,
        "code_challenge_method": code_challenge_method,
        "created_at": time.time(),
        "used": False,
        "user_id": user_id
    }

    redirect_url = f"{redirect_uri}?code={code}&state={state}" if state else f"{redirect_uri}?code={code}"
    return {"redirect_url": redirect_url}


async def exchange_code_for_token(code: str, client_id: str, client_secret: str, redirect_uri: str, code_verifier: Optional[str] = None) -> Dict[str, Any]:
    """Exchange authorization code for access token with PKCE verification."""
    # Check if client is dynamically registered
    client = _registered_clients.get(client_id)
    if client:
        if client["client_secret"] != client_secret:
            raise HTTPException(status_code=401, detail="Invalid client credentials")
    elif client_id != CLIENT_ID or client_secret != CLIENT_SECRET:
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

    # Verify PKCE if code_challenge was provided
    if auth_data.get("code_challenge"):
        if not code_verifier:
            raise HTTPException(status_code=400, detail="code_verifier required for PKCE")
        if not _verify_pkce(code_verifier, auth_data["code_challenge"]):
            raise HTTPException(status_code=400, detail="Invalid code_verifier")

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
        "user_id": auth_data.get("user_id")  # Set from login flow
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


async def register_client(client_metadata: Dict[str, Any]) -> Dict[str, Any]:
    """Dynamic Client Registration (OAuth 2.1) for Claude."""
    client_id = f"claude-{secrets.token_urlsafe(16)}"
    client_secret = secrets.token_urlsafe(32)
    
    _registered_clients[client_id] = {
        "client_id": client_id,
        "client_secret": client_secret,
        "client_name": client_metadata.get("client_name", "Claude"),
        "redirect_uris": client_metadata.get("redirect_uris", ALLOWED_REDIRECT_URIS),
        "created_at": time.time()
    }
    
    return {
        "client_id": client_id,
        "client_secret": client_secret,
        "client_id_issued_at": int(time.time()),
        "client_secret_expires_at": 0  # Never expires
    }


async def get_oauth_discovery() -> Dict[str, Any]:
    """OAuth 2.1 discovery endpoint with PKCE support."""
    base_url = os.environ.get("APP_URL", "https://sunocoach.onrender.com")
    return {
        "issuer": base_url,
        "authorization_endpoint": f"{base_url}/oauth/authorize",
        "token_endpoint": f"{base_url}/oauth/token",
        "registration_endpoint": f"{base_url}/oauth/register",
        "token_endpoint_auth_methods_supported": ["client_secret_post"],
        "grant_types_supported": ["authorization_code", "refresh_token"],
        "scopes_supported": ["read", "write", "contribute"],
        "response_types_supported": ["code"],
        "code_challenge_methods_supported": ["S256"]
    }
