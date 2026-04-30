"""
End-to-end smoke test for MCP + OAuth flow.

Tests:
  1. GET /.well-known/oauth-authorization-server
  2. GET /.well-known/oauth-protected-resource
  3. POST /oauth/register (dynamic client registration)
  4. GET /oauth/authorize (HTML login page)
  5. POST /oauth/login (simulate user login → auth code)
  6. POST /oauth/token (exchange code for Bearer token)
  7. POST /mcp with Bearer token → tools/list
  8. POST /mcp with Bearer token → tools/call
  9. POST /mcp without Bearer token → 401

Run: python test_oauth_mcp_smoke.py
"""

import httpx
import sys
import json

BASE = "http://localhost:8000"
# BASE = "https://sunocoach.onrender.com"  # Uncomment for staging

passed = 0
failed = 0

def check(label: str, condition: bool, detail: str = ""):
    global passed, failed
    if condition:
        passed += 1
        print(f"  ✅ {label}")
    else:
        failed += 1
        print(f"  ❌ {label} — {detail}")


def main():
    global passed, failed
    client = httpx.Client(base_url=BASE, timeout=15)

    print("\n═══════════════════════════════════════════")
    print("  MCP + OAuth End-to-End Smoke Test")
    print("═══════════════════════════════════════════")

    # ─── 1. OAuth Authorization Server Discovery ───
    print("\n─── 1. OAuth Discovery ───")
    r = client.get("/.well-known/oauth-authorization-server")
    check("Status 200", r.status_code == 200)
    body = r.json()
    check("Has issuer", "issuer" in body, str(body))
    check("Has authorization_endpoint", "authorization_endpoint" in body)
    check("Has token_endpoint", "token_endpoint" in body)
    check("Has registration_endpoint", "registration_endpoint" in body)
    check("scopes_supported includes read/write/contribute",
          set(body.get("scopes_supported", [])) >= {"read", "write", "contribute"})
    check("code_challenge_methods_supported includes S256",
          "S256" in body.get("code_challenge_methods_supported", []))

    # ─── 2. Protected Resource ───
    print("\n─── 2. Protected Resource ───")
    r = client.get("/.well-known/oauth-protected-resource")
    check("Status 200", r.status_code == 200)
    body = r.json()
    check("Has resource", "resource" in body, str(body))
    check("authorization_servers is array", isinstance(body.get("authorization_servers"), list))
    check("Has bearer_methods_supported", "bearer_methods_supported" in body)
    check("Has scopes_supported", "scopes_supported" in body)
    check("Has resource_documentation", "resource_documentation" in body)

    # ─── 3. Dynamic Client Registration ───
    print("\n─── 3. Client Registration ───")
    r = client.post("/oauth/register", json={
        "client_name": "SmokeTest",
        "redirect_uris": ["https://claude.ai/oauth/callback", "https://claude.ai/api/mcp/auth_callback"]
    })
    check("Status 200/201", r.status_code in (200, 201))
    reg = r.json()
    check("Has client_id", "client_id" in reg, str(reg))
    check("Has client_secret", "client_secret" in reg)
    CLIENT_ID = reg["client_id"]
    CLIENT_SECRET = reg["client_secret"]
    print(f"     client_id: {CLIENT_ID[:20]}...")
    print(f"     client_secret: {CLIENT_SECRET[:20]}...")

    # ─── 4. Authorize (HTML page) ───
    print("\n─── 4. Authorize Endpoint (HTML) ───")
    redirect_uri = "https://claude.ai/oauth/callback"
    r = client.get(
        "/oauth/authorize",
        params={
            "response_type": "code",
            "client_id": CLIENT_ID,
            "redirect_uri": redirect_uri,
            "scope": "read write contribute",
            "state": "test-state-123",
            "code_challenge": "test-challenge",
            "code_challenge_method": "S256"
        }
    )
    check("Status 200", r.status_code == 200)
    check("Response is HTML", "text/html" in r.headers.get("content-type", ""), r.headers.get("content-type", ""))
    check("Contains login form", '<form' in r.text)
    check("Contains Sign In button", 'Sign In' in r.text)
    check("Form action is absolute URL",
          f"{BASE}/oauth/login" in r.text or "https://sunocoach.onrender.com/oauth/login" in r.text)

    # ─── 5. Login (simulate user) → get auth code ───
    print("\n─── 5. User Login → Auth Code ───")
    r = client.post(
        "/oauth/login",
        data={
            "username": "smoke-test@example.com",
            "password": "testpass123",
            "redirect_uri": redirect_uri,
            "state": "test-state-123",
            "scope": "read write contribute",
            "code_challenge": "",
            "code_challenge_method": ""
        },
        follow_redirects=False
    )
    check("Status 302 (redirect)", r.status_code == 302, f"got {r.status_code}")
    redirect_url = r.headers.get("location", "")
    check("Has redirect location", bool(redirect_url), redirect_url[:100])
    check("Redirect has code param", "code=" in redirect_url, redirect_url[:100])
    check("Redirect has state param", "state=" in redirect_url)
    auth_code = None
    if "code=" in redirect_url:
        auth_code = redirect_url.split("code=")[1].split("&")[0]
        print(f"     auth_code: {auth_code[:16]}...")

    # ─── 6. Token Exchange ───
    print("\n─── 6. Token Exchange ───")
    if not auth_code:
        check("Cannot test token exchange — no auth code", False)
        global passed, failed  # noqa
    else:
        r = client.post(
            "/oauth/token",
            json={
                "grant_type": "authorization_code",
                "code": auth_code,
                "client_id": CLIENT_ID,
                "client_secret": CLIENT_SECRET,
                "redirect_uri": redirect_uri,
                "code_verifier": ""
            }
        )
        check("Status 200", r.status_code == 200, f"got {r.status_code}: {r.text[:200]}")
        token_body = r.json()
        check("Has access_token", "access_token" in token_body, str(list(token_body.keys())))
        check("Has token_type Bearer", token_body.get("token_type") == "Bearer")
        check("Has expires_in", "expires_in" in token_body)
        check("Has refresh_token", "refresh_token" in token_body)
        ACCESS_TOKEN = token_body.get("access_token", "")
        print(f"     access_token: {ACCESS_TOKEN[:20]}...")

        # ─── 7. tools/list with Bearer token ───
        print("\n─── 7. tools/list (authenticated) ───")
        r = client.post(
            "/mcp",
            json={"jsonrpc": "2.0", "id": 1, "method": "tools/list", "params": {}},
            headers={"Authorization": f"Bearer {ACCESS_TOKEN}"}
        )
        check("Status 200", r.status_code == 200, f"got {r.status_code}")
        check("Content-Type text/event-stream",
              "text/event-stream" in r.headers.get("content-type", ""),
              r.headers.get("content-type", ""))
        # Parse SSE
        body_text = r.text
        check("Response is non-empty", bool(body_text.strip()))
        if body_text.startswith("data: "):
            try:
                payload = json.loads(body_text.replace("data: ", "").strip())
                check("JSON-RPC result present", "result" in payload, str(payload)[:200])
                check("Has tools array", "tools" in payload.get("result", {}))
                tools = payload.get("result", {}).get("tools", [])
                tool_names = [t["name"] for t in tools]
                print(f"     tools returned: {len(tools)}")
                for name in tool_names:
                    print(f"       - {name}")
                check("get_current_workflow in tools", "get_current_workflow" in tool_names)
                check("get_pattern_status in tools", "get_pattern_status" in tool_names)
            except json.JSONDecodeError as e:
                check("Valid JSON in SSE", False, str(e))
        else:
            check("SSE data: prefix", False, f"Unexpected format: {body_text[:200]}")

        # ─── 8. tools/call with Bearer token ───
        print("\n─── 8. tools/call (authenticated) ───")
        r = client.post(
            "/mcp",
            json={"jsonrpc": "2.0", "id": 2, "method": "tools/call", "params": {
                "name": "get_pattern_status",
                "arguments": {}
            }},
            headers={"Authorization": f"Bearer {ACCESS_TOKEN}"}
        )
        check("Status 200", r.status_code == 200, f"got {r.status_code}")
        body_text = r.text
        if body_text.startswith("data: "):
            try:
                payload = json.loads(body_text.replace("data: ", "").strip())
                check("JSON-RPC result present", "result" in payload, str(payload)[:200])
                content = payload.get("result", {}).get("content", [])
                check("Has content array", len(content) > 0)
                if content:
                    check("Content has text", "text" in content[0])
            except json.JSONDecodeError as e:
                check("Valid JSON in SSE", False, str(e))
        else:
            check("SSE data: prefix", False, f"Unexpected format: {body_text[:200]}")

        # ─── 9. tools/list WITHOUT token → 401 ───
        print("\n─── 9. Unauthenticated Request (401) ───")
        r = client.post(
            "/mcp",
            json={"jsonrpc": "2.0", "id": 3, "method": "tools/list", "params": {}}
        )
        check("Status 401", r.status_code == 401, f"got {r.status_code}")
        check("Has WWW-Authenticate header", "www-authenticate" in {k.lower(): v for k, v in r.headers.items()})
        www_auth = {k.lower(): v for k, v in r.headers.items()}.get("www-authenticate", "")
        check("WWW-Authenticate includes scope",
              'scope="read write contribute"' in www_auth, www_auth[:200])
        check("WWW-Authenticate includes authorization_server",
              "authorization_server" in www_auth)
        check("WWW-Authenticate URL is absolute",
              "https://" in www_auth, www_auth[:200])
        check("Response is JSON (not SSE)",
              "text/event-stream" not in r.headers.get("content-type", ""),
              r.headers.get("content-type", ""))

        # ─── 10. MCP initialize (no auth) ───
        print("\n─── 10. MCP initialize (no auth required) ───")
        r = client.post(
            "/mcp",
            json={"jsonrpc": "2.0", "id": 1, "method": "initialize", "params": {}}
        )
        check("Status 200", r.status_code == 200, f"got {r.status_code}")
        body_text = r.text
        if body_text.startswith("data: "):
            try:
                payload = json.loads(body_text.replace("data: ", "").strip())
                check("Has result", "result" in payload)
                check("protocolVersion is 2025-11-25",
                      payload.get("result", {}).get("protocolVersion") == "2025-11-25",
                      str(payload.get("result", {})))
            except json.JSONDecodeError as e:
                check("Valid JSON in SSE", False, str(e))

        # ─── 11. MCP notifications/initialized (no auth) ───
        print("\n─── 11. MCP notifications/initialized (no auth) ───")
        r = client.post(
            "/mcp",
            json={"jsonrpc": "2.0", "method": "notifications/initialized", "params": {}}
        )
        check("Status 204", r.status_code == 204, f"got {r.status_code}")

    # ─── Summary ───
    print("\n═══════════════════════════════════════════")
    total = passed + failed
    print(f"  Results: {passed}/{total} passed, {failed} failed")
    if failed > 0:
        print("  ❌ SOME TESTS FAILED — investigate before deploying")
        sys.exit(1)
    else:
        print("  ✅ ALL TESTS PASSED — ready to deploy!")
    print("═══════════════════════════════════════════\n")


if __name__ == "__main__":
    main()
