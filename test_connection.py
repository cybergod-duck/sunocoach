"""Test MCP connection end-to-end."""
import urllib.request
import urllib.error
import json
import time
import ssl

url = "https://sunocoach.onrender.com"
ctx = ssl.create_default_context()

# 1. Check cold start time
print("=" * 60)
print("1. TESTING COLD START / HEALTH")
print("=" * 60)
start = time.time()
try:
    r = urllib.request.urlopen(f"{url}/health", timeout=60, context=ctx)
    print(f"  Time: {time.time()-start:.2f}s")
    print(f"  Status: {r.status}")
    print(f"  Server: {r.headers.get('server')}")
    print(f"  CF-Ray: {r.headers.get('cf-ray')}")
    data = json.loads(r.read().decode())
    print(f"  Data: {json.dumps(data, indent=2)}")
except Exception as e:
    print(f"  FAILED: {e}")

# 2. Check CORS headers
print()
print("=" * 60)
print("2. TESTING CORS (OPTIONS preflight)")
print("=" * 60)
try:
    req = urllib.request.Request(f"{url}/", method="OPTIONS")
    req.add_header("Origin", "https://claude.ai")
    req.add_header("Access-Control-Request-Method", "POST")
    r = urllib.request.urlopen(req, timeout=30, context=ctx)
    print(f"  Status: {r.status}")
    cors_headers = [
        "access-control-allow-origin",
        "access-control-allow-methods",
        "access-control-allow-headers",
        "access-control-expose-headers",
    ]
    for h in cors_headers:
        val = r.headers.get(h, "MISSING")
        print(f"  {h}: {val}")
except Exception as e:
    print(f"  Error: {e}")

# 3. Test GET /
print()
print("=" * 60)
print("3. TESTING GET / (ROOT MANIFEST)")
print("=" * 60)
try:
    r = urllib.request.urlopen(f"{url}/", timeout=30, context=ctx)
    print(f"  Status: {r.status}")
    print(f"  Content-Type: {r.headers.get('content-type')}")
    cors = r.headers.get("access-control-allow-origin", "MISSING")
    print(f"  CORS Allow-Origin: {cors}")
    data = json.loads(r.read().decode())
    print(f"  Name: {data.get('name')}")
    print(f"  Protocol: {data.get('protocol')}")
    print(f"  Tools count: {len(data.get('tools', []))}")
except Exception as e:
    print(f"  Error: {e}")

# 4. Test JSON-RPC initialize
print()
print("=" * 60)
print("4. TESTING POST / (JSON-RPC initialize)")
print("=" * 60)
try:
    req = urllib.request.Request(
        f"{url}/",
        data=json.dumps({
            "jsonrpc": "2.0",
            "method": "initialize",
            "id": 1
        }).encode(),
        headers={
            "Content-Type": "application/json",
            "Origin": "https://claude.ai"
        },
        method="POST"
    )
    r = urllib.request.urlopen(req, timeout=30, context=ctx)
    print(f"  Status: {r.status}")
    cors = r.headers.get("access-control-allow-origin", "MISSING")
    print(f"  CORS Allow-Origin: {cors}")
    data = json.loads(r.read().decode())
    print(f"  Response: {json.dumps(data, indent=2)}")
except Exception as e:
    print(f"  Error: {e}")

# 5. Test JSON-RPC tools/list
print()
print("=" * 60)
print("5. TESTING POST / (JSON-RPC tools/list)")
print("=" * 60)
try:
    req = urllib.request.Request(
        f"{url}/",
        data=json.dumps({
            "jsonrpc": "2.0",
            "method": "tools/list",
            "id": 2
        }).encode(),
        headers={
            "Content-Type": "application/json",
            "Origin": "https://claude.ai"
        },
        method="POST"
    )
    r = urllib.request.urlopen(req, timeout=30, context=ctx)
    print(f"  Status: {r.status}")
    data = json.loads(r.read().decode())
    tools = data.get("result", {}).get("tools", [])
    print(f"  Tools count: {len(tools)}")
    for t in tools:
        print(f"  - {t['name']}")
except Exception as e:
    print(f"  Error: {e}")

# 6. Check OAuth discovery
print()
print("=" * 60)
print("6. TESTING OAuth discovery endpoints")
print("=" * 60)
try:
    r = urllib.request.urlopen(f"{url}/.well-known/oauth-authorization-server", timeout=30, context=ctx)
    print(f"  oauth-authorization-server: {r.status}")
    data = json.loads(r.read().decode())
    print(f"  Issuer: {data.get('issuer')}")
except Exception as e:
    print(f"  Error: {e}")

try:
    r = urllib.request.urlopen(f"{url}/.well-known/oauth-protected-resource", timeout=30, context=ctx)
    print(f"  oauth-protected-resource: {r.status}")
    data = json.loads(r.read().decode())
    print(f"  Resource: {data.get('resource')}")
except Exception as e:
    print(f"  Error: {e}")

print()
print("=" * 60)
print("ALL CHECKS COMPLETE")
print("=" * 60)
