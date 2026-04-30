import os
import json
import secrets
import hashlib
import time
import base64
from typing import Dict, Any, Optional
from fastapi import Request, HTTPException
from db.client import fetchrow, fetch, execute

CLIENT_ID = os.environ.get("OAUTH_CLIENT_ID", "sunocoach-claude")
CLIENT_SECRET = os.environ.get("OAUTH_CLIENT_SECRET", "")
TOKEN_EXPIRY = 3600  # 1 hour
APP_URL = os.environ.get("APP_URL", "https://sunocoach.onrender.com")

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

    # Coerce empty strings to None so the DB stores NULL (not an empty challenge)
    code_challenge = code_challenge or None
    code_challenge_method = code_challenge_method or None

    # Validate PKCE (required for Claude)
    if code_challenge_method and code_challenge_method != "S256":
        raise HTTPException(status_code=400, detail="Only S256 code_challenge_method is supported")

    code = _generate_code()
    now = time.time()

    # Store in database (persistent across cold starts)
    await execute(
        """INSERT INTO oauth_codes (code, redirect_uri, scope, state, code_challenge, code_challenge_method, created_at, used)
           VALUES ($1, $2, $3, $4, $5, $6, $7, false)""",
        code, redirect_uri, scope, state, code_challenge, code_challenge_method, now
    )

    return {
        "authorization_url": f"{APP_URL}/oauth/authorize?response_type=code&client_id={CLIENT_ID}&redirect_uri={redirect_uri}&scope={scope}&state={state}",
        "code": code  # For testing/dev only
    }


async def login_user(email: str, password: str, redirect_uri: str, scope: str, state: str, code_challenge: Optional[str] = None, code_challenge_method: Optional[str] = None) -> Dict[str, Any]:
    """Validate credentials, upsert user, generate auth code, return redirect info."""
    # Validate redirect URI
    if redirect_uri not in ALLOWED_REDIRECT_URIS:
        raise HTTPException(status_code=400, detail=f"Redirect URI not whitelisted: {redirect_uri}")

    # Coerce empty strings to None so the DB stores NULL (not an empty challenge)
    code_challenge = code_challenge or None
    code_challenge_method = code_challenge_method or None

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

    # Generate auth code and store in database
    code = _generate_code()
    now = time.time()
    
    await execute(
        """INSERT INTO oauth_codes (code, redirect_uri, scope, state, code_challenge, code_challenge_method, created_at, used, user_id)
           VALUES ($1, $2, $3, $4, $5, $6, $7, false, $8)""",
        code, redirect_uri, scope, state, code_challenge, code_challenge_method, now, user_id
    )

    redirect_url = f"{redirect_uri}?code={code}&state={state}" if state else f"{redirect_uri}?code={code}"
    return {"redirect_url": redirect_url}


async def exchange_code_for_token(code: str, client_id: str, client_secret: str, redirect_uri: str, code_verifier: Optional[str] = None) -> Dict[str, Any]:
    """Exchange authorization code for access token with PKCE verification."""
    # Coerce empty string to None so the PKCE check below works correctly
    code_verifier = code_verifier or None
    # Check if client is dynamically registered
    client_row = await fetchrow("SELECT client_secret, redirect_uris FROM oauth_clients WHERE client_id = $1", client_id)
    
    if client_row:
        if client_row["client_secret"] != client_secret:
            raise HTTPException(status_code=401, detail="Invalid client credentials")
        # Log redirect_uri validation against stored client redirect_uris
        # asyncpg returns JSONB as a raw str — must parse with json.loads()
        raw_uris = client_row["redirect_uris"]
        if isinstance(raw_uris, list):
            stored_uris = raw_uris
        elif isinstance(raw_uris, str):
            try:
                stored_uris = json.loads(raw_uris)
            except (ValueError, TypeError):
                stored_uris = []
        else:
            stored_uris = []
        print(f"[oauth] registered client '{client_id}' stored redirect_uris: {stored_uris}")
        print(f"[oauth] received redirect_uri: {redirect_uri}")
        if redirect_uri not in stored_uris:
            print(f"[oauth] WARNING: redirect_uri '{redirect_uri}' NOT in client's registered redirect_uris: {stored_uris}")
            raise HTTPException(status_code=400, detail=f"Redirect URI mismatch with registered client's redirect_uris")
    elif client_id != CLIENT_ID or client_secret != CLIENT_SECRET:
        raise HTTPException(status_code=401, detail="Invalid client credentials")

    # Fetch auth code from database
    auth_row = await fetchrow(
        "SELECT code, redirect_uri, scope, state, code_challenge, code_challenge_method, created_at, used, user_id FROM oauth_codes WHERE code = $1",
        code
    )
    
    if not auth_row:
        print(f"[oauth] ERROR: auth code '{code[:12]}...' NOT FOUND in database")
        raise HTTPException(status_code=400, detail="Invalid authorization code")

    print(f"[oauth] auth_code redirect_uri (stored): {auth_row['redirect_uri']}")
    print(f"[oauth] auth_code redirect_uri (received): {redirect_uri}")

    if auth_row["used"]:
        print(f"[oauth] ERROR: auth code already used")
        raise HTTPException(status_code=400, detail="Authorization code already used")

    if auth_row["redirect_uri"] != redirect_uri:
        print(f"[oauth] ERROR: redirect_uri mismatch — stored='{auth_row['redirect_uri']}' vs received='{redirect_uri}'")
        raise HTTPException(status_code=400, detail="Redirect URI mismatch")

    now = time.time()
    if now - auth_row["created_at"] > 600:  # 10 min expiry
        print(f"[oauth] ERROR: auth code expired")
        raise HTTPException(status_code=400, detail="Authorization code expired")

    # Verify PKCE if code_challenge was provided
    if auth_row.get("code_challenge"):
        if not code_verifier:
            raise HTTPException(status_code=400, detail="code_verifier required for PKCE")
        if not _verify_pkce(code_verifier, auth_row["code_challenge"]):
            raise HTTPException(status_code=400, detail="Invalid code_verifier")

    # Mark auth code as used
    await execute("UPDATE oauth_codes SET used = true WHERE code = $1", code)

    # Generate tokens
    access_token = _generate_token()
    refresh_token = _generate_token()
    token_hash = _hash_token(access_token)

    # Store tokens in database
    await execute(
        """INSERT INTO oauth_tokens (token_hash, access_token, refresh_token, created_at, expires_at, scope, user_id)
           VALUES ($1, $2, $3, $4, $5, $6, $7)""",
        token_hash, access_token, refresh_token, now, now + TOKEN_EXPIRY,
        auth_row["scope"], auth_row.get("user_id")
    )

    print(f"[oauth] token issued — hash={token_hash[:16]}... user_id={auth_row.get('user_id')}")

    return {
        "access_token": access_token,
        "token_type": "Bearer",
        "expires_in": TOKEN_EXPIRY,
        "refresh_token": refresh_token,
        "scope": auth_row["scope"]
    }


async def refresh_access_token(refresh_token: str) -> Dict[str, Any]:
    """Refresh expired access token."""
    # Search database for matching refresh_token
    rows = await fetch(
        "SELECT token_hash, access_token, refresh_token, created_at, expires_at, scope, user_id FROM oauth_tokens WHERE refresh_token = $1",
        refresh_token
    )

    if not rows:
        raise HTTPException(status_code=401, detail="Invalid refresh token")

    row = rows[0]
    now = time.time()

    if now - row["created_at"] > 30 * 24 * 3600:  # 30 days
        raise HTTPException(status_code=401, detail="Refresh token expired")

    # Delete old token
    await execute("DELETE FROM oauth_tokens WHERE token_hash = $1", row["token_hash"])

    # Generate new access token
    new_access = _generate_token()
    new_hash = _hash_token(new_access)

    await execute(
        """INSERT INTO oauth_tokens (token_hash, access_token, refresh_token, created_at, expires_at, scope, user_id)
           VALUES ($1, $2, $3, $4, $5, $6, $7)""",
        new_hash, new_access, row["refresh_token"], now, now + TOKEN_EXPIRY,
        row["scope"], row["user_id"]
    )

    return {
        "access_token": new_access,
        "token_type": "Bearer",
        "expires_in": TOKEN_EXPIRY,
        "scope": row["scope"]
    }


async def validate_token(request: Request) -> Dict[str, Any]:
    """Validate Bearer token from request headers."""
    auth_header = request.headers.get("Authorization", "")
    if not auth_header.startswith("Bearer "):
        raise HTTPException(status_code=401, detail="Missing or invalid Authorization header")

    token = auth_header[7:]
    token_hash = _hash_token(token)

    print(f"[oauth/validate] raw token[:16]={token[:16]}... hashed={token_hash[:20]}...")

    # Fetch token from database
    row = await fetchrow(
        "SELECT token_hash, access_token, refresh_token, created_at, expires_at, scope, user_id FROM oauth_tokens WHERE token_hash = $1",
        token_hash
    )

    if not row:
        print(f"[oauth/validate] TOKEN NOT FOUND in database — hash={token_hash[:20]}...")
        raise HTTPException(status_code=401, detail="Invalid token")

    print(f"[oauth/validate] token FOUND — user_id={row['user_id']} scope={row['scope']} expires_at={row['expires_at']}")

    now = time.time()
    if now > row["expires_at"]:
        print(f"[oauth/validate] token EXPIRED — now={now} expires_at={row['expires_at']}")
        raise HTTPException(status_code=401, detail="Token expired")

    # Build token_data dict like the old in-memory store returned
    token_data = {
        "access_token": row["access_token"],
        "refresh_token": row["refresh_token"],
        "created_at": row["created_at"],
        "expires_at": row["expires_at"],
        "scope": row["scope"],
        "user_id": row["user_id"]
    }

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
    now = time.time()
    
    redirect_uris = client_metadata.get("redirect_uris", ALLOWED_REDIRECT_URIS)

    print(f"[oauth/register] storing client '{client_id}' with redirect_uris: {redirect_uris}")

    # Store in database
    await execute(
        """INSERT INTO oauth_clients (client_id, client_secret, client_name, redirect_uris, created_at)
           VALUES ($1, $2, $3, $4::jsonb, $5)""",
        client_id, client_secret, client_metadata.get("client_name", "Claude"),
        json.dumps(redirect_uris), now
    )

    return {
        "client_id": client_id,
        "client_secret": client_secret,
        "client_id_issued_at": int(now),
        "client_secret_expires_at": 0  # Never expires
    }


async def get_oauth_discovery() -> Dict[str, Any]:
    """OAuth 2.1 discovery endpoint with PKCE support."""
    base_url = APP_URL
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
