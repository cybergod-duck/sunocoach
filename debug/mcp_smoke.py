"""
Reusable MCP + OAuth end-to-end smoke check.

Exposes::

    async def run_mcp_smoke(base_url: str, timeout: float = 10.0) -> list[dict]:
        # Returns list of {name, ok, detail}

Each check is a dict with:
    - name:  short human-readable label
    - ok:    bool (True = pass, False = fail)
    - detail: string with error message or brief success info

Intended to be called by:
    1. The /debug/mcp dashboard endpoint (live server debugging)
    2. The CLI smoke test (test_oauth_mcp_smoke.py)
"""

import asyncio
import json
import time
from typing import Any, Dict, List, Optional
import httpx


async def _check(label: str, coro, timeout: float) -> dict:
    """Run a single check with a per-step timeout."""
    deadline = time.monotonic() + timeout
    try:
        result = await asyncio.wait_for(coro, timeout=timeout)
        return {"name": label, "ok": True, "detail": result or ""}
    except asyncio.TimeoutError:
        return {"name": label, "ok": False, "detail": "timeout"}
    except Exception as e:
        detail = str(e)
        if hasattr(e, "response") and e.response is not None:
            detail = f"{detail} | body={e.response.text[:300]}"
        return {"name": label, "ok": False, "detail": detail}


async def run_mcp_smoke(base_url: str, timeout: float = 10.0) -> List[Dict[str, Any]]:
    """
    Run all MCP + OAuth end-to-end checks against *base_url*.

    Returns a list of dicts, one per check step, in execution order.
    Each dict has keys: ``name``, ``ok`` (bool), ``detail`` (str).
    """
    results: List[Dict[str, Any]] = []
    deadline = time.monotonic() + timeout
    remaining = lambda: max(0.1, deadline - time.monotonic())

    async def _run(label: str, coro):
        r = await _check(label, coro, remaining())
        results.append(r)
        return r

    async with httpx.AsyncClient(base_url=base_url, timeout=httpx.Timeout(remaining())) as client:
        # ─── 1. OAuth Authorization Server Discovery ───
        async def step1():
            r = await client.get("/.well-known/oauth-authorization-server")
            assert r.status_code == 200, f"status={r.status_code}"
            body = r.json()
            assert "issuer" in body, f"missing issuer: {list(body.keys())}"
            assert "authorization_endpoint" in body
            assert "token_endpoint" in body
            assert "registration_endpoint" in body
            scopes = set(body.get("scopes_supported", []))
            assert scopes >= {"read", "write", "contribute"}, f"scopes: {scopes}"
            assert "S256" in body.get("code_challenge_methods_supported", []), "missing S256"
            return f"issuer={body['issuer']} scopes={scopes}"
        await _run("OAuth Discovery", step1())

        # ─── 2. Protected Resource ───
        async def step2():
            r = await client.get("/.well-known/oauth-protected-resource")
            assert r.status_code == 200, f"status={r.status_code}"
            body = r.json()
            assert "resource" in body
            assert isinstance(body.get("authorization_servers"), list), "authorization_servers not array"
            assert "bearer_methods_supported" in body
            assert "scopes_supported" in body
            assert "resource_documentation" in body
            return f"resource={body['resource']}"
        await _run("Protected Resource", step2())

        # ─── 3. Dynamic Client Registration ───
        client_id: Optional[str] = None
        client_secret: Optional[str] = None

        async def step3():
            nonlocal client_id, client_secret
            r = await client.post("/oauth/register", json={
                "client_name": "DebugDashboard",
                "redirect_uris": ["https://claude.ai/oauth/callback", "https://claude.ai/api/mcp/auth_callback"]
            })
            assert r.status_code in (200, 201), f"status={r.status_code}"
            reg = r.json()
            assert "client_id" in reg
            assert "client_secret" in reg
            client_id = reg["client_id"]
            client_secret = reg["client_secret"]
            return f"client_id={client_id[:20]}..."
        await _run("Client Registration", step3())

        redirect_uri = "https://claude.ai/oauth/callback"

        # ─── 4. Authorize Endpoint (HTML) ───
        async def step4():
            nonlocal client_id
            r = await client.get(
                "/oauth/authorize",
                params={
                    "response_type": "code",
                    "client_id": client_id or "",
                    "redirect_uri": redirect_uri,
                    "scope": "read write contribute",
                    "state": "dbg-state-999",
                    "code_challenge": "test-challenge",
                    "code_challenge_method": "S256"
                }
            )
            assert r.status_code == 200, f"status={r.status_code}"
            ct = r.headers.get("content-type", "")
            assert "text/html" in ct, f"content-type={ct}"
            assert "<form" in r.text, "no <form> in HTML"
            assert "Sign In" in r.text, "no Sign In button"
            assert base_url.rstrip("/") in r.text, "form action not absolute URL"
            return "HTML login page rendered with absolute form action"
        await _run("Authorize HTML Page", step4())

        # ─── 5. User Login → Auth Code ───
        auth_code: Optional[str] = None

        async def step5():
            nonlocal auth_code
            r = await client.post(
                "/oauth/login",
                data={
                    "username": "debug-dashboard@sunocoach.internal",
                    "password": "debugpass",
                    "redirect_uri": redirect_uri,
                    "state": "dbg-state-999",
                    "scope": "read write contribute",
                    "code_challenge": "",
                    "code_challenge_method": ""
                },
                follow_redirects=False
            )
            assert r.status_code == 302, f"status={r.status_code}"
            loc = r.headers.get("location", "")
            assert loc, "no Location header"
            assert "code=" in loc, f"no code param in redirect: {loc[:100]}"
            assert "state=" in loc, "no state param"
            auth_code = loc.split("code=")[1].split("&")[0]
            return f"redirect to {loc[:80]}..."
        await _run("User Login → Auth Code", step5())

        # ─── 6. Token Exchange ───
        access_token: Optional[str] = None

        async def step6():
            nonlocal access_token
            assert auth_code, "no auth code from previous step"
            r = await client.post(
                "/oauth/token",
                json={
                    "grant_type": "authorization_code",
                    "code": auth_code,
                    "client_id": client_id,
                    "client_secret": client_secret,
                    "redirect_uri": redirect_uri,
                    "code_verifier": ""
                }
            )
            assert r.status_code == 200, f"status={r.status_code}: {r.text[:200]}"
            body = r.json()
            assert "access_token" in body, f"keys={list(body.keys())}"
            assert body.get("token_type") == "Bearer", f"token_type={body.get('token_type')}"
            assert "expires_in" in body
            assert "refresh_token" in body
            access_token = body["access_token"]
            return f"token issued, expires_in={body['expires_in']}s"
        await _run("Token Exchange", step6())

        # ─── 7. tools/list with Bearer token ───
        async def step7():
            nonlocal access_token
            assert access_token, "no access token"
            r = await client.post(
                "/mcp",
                json={"jsonrpc": "2.0", "id": 1, "method": "tools/list", "params": {}},
                headers={"Authorization": f"Bearer {access_token}"}
            )
            assert r.status_code == 200, f"status={r.status_code}"
            ct = r.headers.get("content-type", "")
            assert "text/event-stream" in ct, f"content-type={ct}"
            text = r.text
            assert text.startswith("data: "), f"not SSE: {text[:100]}"
            payload = json.loads(text.replace("data: ", "").strip())
            assert "result" in payload, f"no result: {str(payload)[:200]}"
            tools = payload.get("result", {}).get("tools", [])
            tool_names = [t["name"] for t in tools]
            assert "get_current_workflow" in tool_names, f"tools={tool_names}"
            assert "get_pattern_status" in tool_names
            return f"{len(tools)} tools returned"
        await _run("tools/list (authenticated)", step7())

        # ─── 8. tools/call with Bearer token ───
        async def step8():
            nonlocal access_token
            assert access_token, "no access token"
            r = await client.post(
                "/mcp",
                json={"jsonrpc": "2.0", "id": 2, "method": "tools/call", "params": {
                    "name": "get_pattern_status",
                    "arguments": {}
                }},
                headers={"Authorization": f"Bearer {access_token}"}
            )
            assert r.status_code == 200, f"status={r.status_code}"
            text = r.text
            assert text.startswith("data: "), f"not SSE: {text[:100]}"
            payload = json.loads(text.replace("data: ", "").strip())
            assert "result" in payload, f"no result: {str(payload)[:200]}"
            content = payload.get("result", {}).get("content", [])
            assert len(content) > 0, "empty content array"
            assert "text" in content[0], "no text in content[0]"
            return f"tool returned content[{len(content)}]"
        await _run("tools/call (authenticated)", step8())

        # ─── 9. tools/list WITHOUT token → 401 ───
        async def step9():
            r = await client.post(
                "/mcp",
                json={"jsonrpc": "2.0", "id": 3, "method": "tools/list", "params": {}}
            )
            assert r.status_code == 401, f"status={r.status_code}"
            headers_lower = {k.lower(): v for k, v in r.headers.items()}
            www = headers_lower.get("www-authenticate", "")
            assert www, "no WWW-Authenticate header"
            assert "resource_metadata" in www, f"expected resource_metadata in header: {www[:200]}"
            assert "https://" in www, "URL not absolute"
            assert "text/event-stream" not in r.headers.get("content-type", ""), \
                "401 must not be SSE"
            return "401 challenge with proper WWW-Authenticate"
        await _run("401 without token", step9())

        # ─── 10. MCP initialize (no auth) ───
        async def step10():
            r = await client.post(
                "/mcp",
                json={"jsonrpc": "2.0", "id": 1, "method": "initialize", "params": {}}
            )
            assert r.status_code == 200, f"status={r.status_code}"
            text = r.text
            assert text.startswith("data: "), f"not SSE: {text[:100]}"
            payload = json.loads(text.replace("data: ", "").strip())
            assert "result" in payload
            proto = payload.get("result", {}).get("protocolVersion", "")
            assert proto == "2025-11-25", f"protocolVersion={proto}"
            return f"protocolVersion={proto}"
        await _run("MCP initialize (no auth)", step10())

        # ─── 11. MCP notifications/initialized (no auth) ───
        async def step11():
            r = await client.post(
                "/mcp",
                json={"jsonrpc": "2.0", "method": "notifications/initialized", "params": {}}
            )
            assert r.status_code == 204, f"status={r.status_code}"
            return "204 No Content"
        await _run("notifications/initialized (no auth)", step11())

    return results
