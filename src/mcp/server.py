import os
import json
import traceback
from typing import Any, Dict, List, Optional
from fastapi import FastAPI, Request, Response, HTTPException
from fastapi.responses import HTMLResponse, JSONResponse, RedirectResponse, StreamingResponse
from fastapi.middleware.cors import CORSMiddleware
from mcp.tools import (
    get_current_workflow, get_next_step, log_step_result, start_session,
    generate_style_prompt, build_lyric_structure, validate_prompt,
    save_style_prompt, recall_style, save_client, get_client_brief,
    submit_workflow, vote_on_pattern, get_pattern_status
)
from auth.oauth import (
    CLIENT_ID, APP_URL, create_authorization_url, exchange_code_for_token,
    refresh_access_token, validate_token, get_oauth_discovery,
    register_client, login_user
)
from billing.stripe_handler import (
    create_checkout_session, handle_webhook, get_subscription_status
)
from drift.detector import check_drift_status
from db.client import close_pool
from db.migrate import run_migrations

app = FastAPI(title="SunoCoach MCP Server")

# ─── REQUEST AUDIT LOGGER — logs every hit so we can trace Claude's OAuth flow ───
@app.middleware("http")
async def audit_log(request: Request, call_next):
    import time as _time
    start = _time.monotonic()
    print(
        f">>> {request.method} {request.url.path}"
        f" | UA={request.headers.get('user-agent','?')[:60]}"
        f" | Origin={request.headers.get('origin','none')}"
        f" | Auth={request.headers.get('authorization','none')[:30]}"
    )
    response = await call_next(request)
    elapsed = (_time.monotonic() - start) * 1000
    print(
        f"<<< {request.method} {request.url.path}"
        f" → {response.status_code}"
        f" | WWW-Auth={response.headers.get('www-authenticate','none')[:80]}"
        f" | {elapsed:.0f}ms"
    )
    return response

# ─── CATCH-ALL CORS MIDDLEWARE ───
@app.middleware("http")
async def cors_everywhere(request: Request, call_next):
    response = await call_next(request)
    response.headers["Access-Control-Allow-Origin"] = "*"
    response.headers["Access-Control-Allow-Methods"] = "DELETE, GET, HEAD, OPTIONS, PATCH, POST, PUT"
    response.headers["Access-Control-Allow-Headers"] = "*"
    response.headers["Access-Control-Expose-Headers"] = "*"
    response.headers["Access-Control-Max-Age"] = "86400"
    return response

# Also register the middleware via add_middleware for OPTIONS preflight handling
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
    expose_headers=["*"],
    max_age=86400,
)

# ─── MCP TOOL DEFINITIONS (MCP spec requires camelCase inputSchema) ───
MCP_TOOLS = [
    {
        "name": "get_current_workflow",
        "description": "Returns the active workflow pattern with all steps and drift status.",
        "inputSchema": {"type": "object", "properties": {}}
    },
    {
        "name": "get_next_step",
        "description": "Returns exact instruction for the current step in plain English.",
        "inputSchema": {
            "type": "object",
            "properties": {"session_id": {"type": "string"}},
            "required": ["session_id"]
        }
    },
    {
        "name": "log_step_result",
        "description": "Stores result, advances session state, checks for drift.",
        "inputSchema": {
            "type": "object",
            "properties": {
                "session_id": {"type": "string"},
                "step_number": {"type": "integer"},
                "quality_rating": {"type": "integer", "minimum": 1, "maximum": 5},
                "notes": {"type": "string"}
            },
            "required": ["session_id", "step_number", "quality_rating"]
        }
    },
    {
        "name": "start_session",
        "description": "Creates a new coaching session.",
        "inputSchema": {
            "type": "object",
            "properties": {
                "user_id": {"type": "string"},
                "workflow_id": {"type": "string"}
            },
            "required": ["user_id"]
        }
    },
    {
        "name": "generate_style_prompt",
        "description": "Takes plain English, returns structured style DNA prompt.",
        "inputSchema": {
            "type": "object",
            "properties": {"description": {"type": "string"}},
            "required": ["description"]
        }
    },
    {
        "name": "build_lyric_structure",
        "description": "Applies correct bracket tagging to raw lyrics.",
        "inputSchema": {
            "type": "object",
            "properties": {
                "raw_lyrics": {"type": "string"},
                "sections": {"type": "array", "items": {"type": "string"}}
            },
            "required": ["raw_lyrics"]
        }
    },
    {
        "name": "validate_prompt",
        "description": "Scores prompt against all rules, returns issues with fixes.",
        "inputSchema": {
            "type": "object",
            "properties": {"prompt_text": {"type": "string"}},
            "required": ["prompt_text"]
        }
    },
    {
        "name": "save_style_prompt",
        "description": "Validates then stores in user's style library.",
        "inputSchema": {
            "type": "object",
            "properties": {
                "name": {"type": "string"},
                "prompt_text": {"type": "string"},
                "genre_tags": {"type": "array", "items": {"type": "string"}},
                "mood_tags": {"type": "array", "items": {"type": "string"}},
                "bpm": {"type": "integer"}
            },
            "required": ["name", "prompt_text"]
        }
    },
    {
        "name": "recall_style",
        "description": "Fuzzy search user's style prompt library.",
        "inputSchema": {
            "type": "object",
            "properties": {
                "mood": {"type": "string"},
                "genre": {"type": "string"}
            }
        }
    },
    {
        "name": "save_client",
        "description": "Creates or updates client profile.",
        "inputSchema": {
            "type": "object",
            "properties": {
                "name": {"type": "string"},
                "vocal_type": {"type": "string"},
                "genres": {"type": "array", "items": {"type": "string"}},
                "bpm_range": {"type": "object", "properties": {"min": {"type": "integer"}, "max": {"type": "integer"}}},
                "emotional_register": {"type": "string"}
            },
            "required": ["name"]
        }
    },
    {
        "name": "get_client_brief",
        "description": "Returns ready-to-paste style prompt + lyric structure from client profile.",
        "inputSchema": {
            "type": "object",
            "properties": {
                "client_name": {"type": "string"},
                "concept": {"type": "string"}
            },
            "required": ["client_name", "concept"]
        }
    },
    {
        "name": "submit_workflow",
        "description": "Contributor tier: submits new workflow pattern for community scoring.",
        "inputSchema": {
            "type": "object",
            "properties": {
                "steps": {"type": "array"},
                "notes": {"type": "string"},
                "session_data": {"type": "array"}
            },
            "required": ["steps"]
        }
    },
    {
        "name": "vote_on_pattern",
        "description": "Contributor tier: vote on submitted pattern.",
        "inputSchema": {
            "type": "object",
            "properties": {
                "pattern_id": {"type": "string"},
                "rating": {"type": "integer", "minimum": 1, "maximum": 5},
                "session_evidence": {"type": "object"}
            },
            "required": ["pattern_id", "rating"]
        }
    },
    {
        "name": "get_pattern_status",
        "description": "Returns active/drifting/calibrating status + explanation.",
        "inputSchema": {"type": "object", "properties": {}}
    }
]

TOOL_DISPATCH = {
    "get_current_workflow": lambda args: get_current_workflow(),
    "get_next_step": lambda args: get_next_step(args.get("session_id")),
    "log_step_result": lambda args: log_step_result(
        args.get("session_id"), args.get("step_number"),
        args.get("quality_rating"), args.get("notes", "")
    ),
    "start_session": lambda args: start_session(args.get("user_id"), args.get("workflow_id")),
    "generate_style_prompt": lambda args: generate_style_prompt(args.get("description", "")),
    "build_lyric_structure": lambda args: build_lyric_structure(
        args.get("raw_lyrics", ""), args.get("sections")
    ),
    "validate_prompt": lambda args: validate_prompt(args.get("prompt_text", "")),
    "save_style_prompt": lambda args: save_style_prompt(
        args.get("user_id"), args.get("name"), args.get("prompt_text"),
        args.get("genre_tags"), args.get("mood_tags"), args.get("bpm")
    ),
    "recall_style": lambda args: recall_style(args.get("user_id"), args.get("mood"), args.get("genre")),
    "save_client": lambda args: save_client(
        args.get("user_id"), args.get("name"), args.get("vocal_type"),
        args.get("genres"), args.get("bpm_range"), args.get("emotional_register")
    ),
    "get_client_brief": lambda args: get_client_brief(args.get("user_id"), args.get("client_name"), args.get("concept", "")),
    "submit_workflow": lambda args: submit_workflow(
        args.get("user_id"), args.get("steps", []), args.get("notes", ""), args.get("session_data")
    ),
    "vote_on_pattern": lambda args: vote_on_pattern(
        args.get("user_id"), args.get("pattern_id"), args.get("rating"), args.get("session_evidence")
    ),
    "get_pattern_status": lambda args: get_pattern_status(),
}


# ─── STARTUP VALIDATION: assert every tool in MCP_TOOLS has a TOOL_DISPATCH entry ───
_defined_tool_names = {t["name"] for t in MCP_TOOLS}
_dispatched_tool_names = set(TOOL_DISPATCH.keys())
_missing_from_dispatch = _defined_tool_names - _dispatched_tool_names
_extra_in_dispatch = _dispatched_tool_names - _defined_tool_names

if _missing_from_dispatch:
    raise RuntimeError(
        f"STARTUP VALIDATION FAILED — tools defined in MCP_TOOLS but MISSING from TOOL_DISPATCH: {_missing_from_dispatch}"
    )
if _extra_in_dispatch:
    print(f"STARTUP WARNING — TOOL_DISPATCH has tools not in MCP_TOOLS: {_extra_in_dispatch}")

print(f"STARTUP VALIDATION PASSED — {len(MCP_TOOLS)} tools fully wired in TOOL_DISPATCH")
# ─── END STARTUP VALIDATION ───


# ─── SSE WRAPPER (MCP Streamable HTTP transport) ───
def sse_response(data: dict, status_code: int = 200, headers: Optional[dict] = None) -> StreamingResponse:
    """Wrap JSON payload in SSE format for MCP Streamable HTTP."""
    payload = f"data: {json.dumps(data)}\n\n"
    response_headers = {"Content-Type": "text/event-stream", "Cache-Control": "no-cache"}
    if headers:
        response_headers.update(headers)
    return StreamingResponse(
        iter([payload]),
        status_code=status_code,
        headers=response_headers
    )


# ─── SHARED JSON-RPC 2.0 DISPATCHER ───
# Extracted so both /mcp and / routes use the same logic without duplication.
async def _dispatch_jsonrpc(request: Request, body: dict, log_label: str = "MCP") -> Response:
    """Authenticate and dispatch a JSON-RPC 2.0 request.

    MCP handshake flow:
      1. initialize     → no auth required, returns protocol version + capabilities
      2. initialized    → no auth required, returns 204
      3. tools/list     → auth required
      4. tools/call     → auth required
    """
    headers = dict(request.headers)
    print(f"=== {log_label} HIT ===")
    print(f"METHOD: {body.get('method')}")
    print(f"BODY: {json.dumps(body)}")
    print(f"AUTH: {headers.get('authorization', 'MISSING')}")
    print(f"ACCEPT: {headers.get('accept', 'MISSING')}")

    method = body.get("method", "")
    params = body.get("params", {})
    req_id = body.get("id")

    # ─── HANDSHAKE: initialize / notifications pass through WITHOUT auth ───
    if method == "initialize":
        data = {
            "jsonrpc": "2.0",
            "id": req_id,
            "result": {
                "protocolVersion": "2025-11-25",
                "serverInfo": {"name": "SunoCoach", "version": "1.0.0"},
                "capabilities": {"tools": {}}
            }
        }
        print(f"RESPONSE [initialize]: {json.dumps(data)}")
        return sse_response(data)

    if method == "notifications/initialized":
        print(f"RESPONSE [notifications/initialized]: 204")
        return Response(status_code=204)

    # Catch any other notifications/ methods silently
    if method and method.startswith("notifications/"):
        print(f"RESPONSE [notifications/*]: 204")
        return Response(status_code=204)

    # ─── AUTH GATE: everything beyond here requires valid Bearer token ───
    # CRITICAL: 401 MUST be plain HTTP (not SSE) so Claude can parse
    # the WWW-Authenticate header and initiate the OAuth flow.
    auth_header = request.headers.get("Authorization", "")
    if not auth_header.startswith("Bearer "):
        print(f"RESPONSE [401-challenge]: no Bearer token")
        return JSONResponse(
            status_code=401,
            headers={
                "WWW-Authenticate": (
                    f'Bearer realm="sunocoach"'
                    f', resource_metadata="{APP_URL}/.well-known/oauth-protected-resource"'
                    f', error="insufficient_scope"'
                    f', scope="read write contribute"'
                )
            },
            content={
                "jsonrpc": "2.0",
                "id": req_id,
                "error": {"code": -32001, "message": "Unauthorized — Bearer token required"}
            }
        )

    try:
        # Token validation with debug logging (logs token hash + lookup result)
        print(f"[auth-gate] validating Bearer token: {auth_header[:40]}...")
        await validate_token(request)
    except HTTPException as e:
        print(f"RESPONSE [401-invalid-token]: {e.detail}")
        return JSONResponse(
            status_code=401,
            headers={
                "WWW-Authenticate": (
                    f'Bearer realm="sunocoach"'
                    f', resource_metadata="{APP_URL}/.well-known/oauth-protected-resource"'
                    f', error="invalid_token"'
                    f', scope="read write contribute"'
                )
            },
            content={
                "jsonrpc": "2.0",
                "id": req_id,
                "error": {"code": -32001, "message": "Unauthorized — invalid or expired token"}
            }
        )

    # ─── JSON-RPC 2.0 Method Dispatch (authenticated) ───
    if method == "tools/list":
        data = {
            "jsonrpc": "2.0",
            "id": req_id,
            "result": {"tools": MCP_TOOLS}
        }
        print(f"RESPONSE [tools/list]: {json.dumps(data)}")
        return sse_response(data)

    if method == "tools/call":
        tool_name = params.get("name")
        arguments = params.get("arguments", {})

        if tool_name not in TOOL_DISPATCH:
            data = {
                "jsonrpc": "2.0",
                "id": req_id,
                "error": {"code": -32601, "message": f"Tool not found: {tool_name}"}
            }
            print(f"RESPONSE [tool-not-found]: {json.dumps(data)}")
            return sse_response(data)

        try:
            result = await TOOL_DISPATCH[tool_name](arguments)
            data = {
                "jsonrpc": "2.0",
                "id": req_id,
                "result": {"content": [{"type": "text", "text": json.dumps(result) if not isinstance(result, str) else result}]}
            }
            print(f"RESPONSE [tools/call-{tool_name}]: {json.dumps(data)}")
            return sse_response(data)
        except Exception as e:
            data = {
                "jsonrpc": "2.0",
                "id": req_id,
                "error": {"code": -32603, "message": str(e)}
            }
            print(f"RESPONSE [tools/call-error]: {json.dumps(data)}")
            return sse_response(data, status_code=500)

    # Unknown method
    data = {
        "jsonrpc": "2.0",
        "id": req_id,
        "error": {"code": -32601, "message": f"Method not found: {method}"}
    }
    print(f"RESPONSE [unknown-method]: {json.dumps(data)}")
    return sse_response(data)


# ─── /mcp ENDPOINT (Claude Streamable HTTP / OAuth 2.1) ───
# Claude requires:
#   POST /mcp → JSON-RPC 2.0 dispatcher for initialize, tools/list, tools/call
#   GET /mcp  → MCP manifest (same as GET /)
#
# OAuth flow:
#   1. Claude fetches discovery → finds registration_endpoint
#   2. POST /oauth/register → gets client_id, client_secret
#   3. GET /oauth/authorize → user signs in → gets auth code
#   4. POST /oauth/token → exchanges code for Bearer token
#   5. POST /mcp with Authorization: Bearer <token> → access granted
@app.post("/mcp")
@app.get("/mcp")
async def mcp_handler(request: Request):
    if request.method == "GET":
        return await root()
    body = await request.json()
    return await _dispatch_jsonrpc(request, body, log_label="MCP")

# ─── MCP MANIFEST (root) ───
@app.get("/")
async def root():
    return {
        "name": "SunoCoach",
        "version": "1.0.0",
        "description": "AI music creation workflow coach for Suno and any AI music generator. Style prompt engineering, lyric structure tagging, client profile management, and self-updating pattern detection.",
        "protocol": "mcp",
        "tools": [
            {
                "name": "get_current_workflow",
                "description": "Returns the active workflow pattern with all steps and drift status.",
                "inputSchema": {"type": "object", "properties": {}}
            },
            {
                "name": "get_next_step",
                "description": "Returns exact instruction for the current step in plain English.",
                "inputSchema": {
                    "type": "object",
                    "properties": {"session_id": {"type": "string"}},
                    "required": ["session_id"]
                }
            },
            {
                "name": "log_step_result",
                "description": "Stores result, advances session state, checks for drift.",
                "inputSchema": {
                    "type": "object",
                    "properties": {
                        "session_id": {"type": "string"},
                        "step_number": {"type": "integer"},
                        "quality_rating": {"type": "integer", "minimum": 1, "maximum": 5},
                        "notes": {"type": "string"}
                    },
                    "required": ["session_id", "step_number", "quality_rating"]
                }
            },
            {
                "name": "start_session",
                "description": "Creates a new coaching session.",
                "inputSchema": {
                    "type": "object",
                    "properties": {
                        "user_id": {"type": "string"},
                        "workflow_id": {"type": "string"}
                    },
                    "required": ["user_id"]
                }
            },
            {
                "name": "generate_style_prompt",
                "description": "Takes plain English, returns structured style DNA prompt.",
                "inputSchema": {
                    "type": "object",
                    "properties": {"description": {"type": "string"}},
                    "required": ["description"]
                }
            },
            {
                "name": "build_lyric_structure",
                "description": "Applies correct bracket tagging to raw lyrics.",
                "inputSchema": {
                    "type": "object",
                    "properties": {
                        "raw_lyrics": {"type": "string"},
                        "sections": {"type": "array", "items": {"type": "string"}}
                    },
                    "required": ["raw_lyrics"]
                }
            },
            {
                "name": "validate_prompt",
                "description": "Scores prompt against all rules, returns issues with fixes.",
                "inputSchema": {
                    "type": "object",
                    "properties": {"prompt_text": {"type": "string"}},
                    "required": ["prompt_text"]
                }
            },
            {
                "name": "save_style_prompt",
                "description": "Validates then stores in user's style library.",
                "inputSchema": {
                    "type": "object",
                    "properties": {
                        "name": {"type": "string"},
                        "prompt_text": {"type": "string"},
                        "genre_tags": {"type": "array", "items": {"type": "string"}},
                        "mood_tags": {"type": "array", "items": {"type": "string"}},
                        "bpm": {"type": "integer"}
                    },
                    "required": ["name", "prompt_text"]
                }
            },
            {
                "name": "recall_style",
                "description": "Fuzzy search user's style prompt library.",
                "inputSchema": {
                    "type": "object",
                    "properties": {
                        "mood": {"type": "string"},
                        "genre": {"type": "string"}
                    }
                }
            },
            {
                "name": "save_client",
                "description": "Creates or updates client profile.",
                "inputSchema": {
                    "type": "object",
                    "properties": {
                        "name": {"type": "string"},
                        "vocal_type": {"type": "string"},
                        "genres": {"type": "array", "items": {"type": "string"}},
                        "bpm_range": {"type": "object", "properties": {"min": {"type": "integer"}, "max": {"type": "integer"}}},
                        "emotional_register": {"type": "string"}
                    },
                    "required": ["name"]
                }
            },
            {
                "name": "get_client_brief",
                "description": "Returns ready-to-paste style prompt + lyric structure from client profile.",
                "inputSchema": {
                    "type": "object",
                    "properties": {
                        "client_name": {"type": "string"},
                        "concept": {"type": "string"}
                    },
                    "required": ["client_name", "concept"]
                }
            },
            {
                "name": "submit_workflow",
                "description": "Contributor tier: submits new workflow pattern for community scoring.",
                "inputSchema": {
                    "type": "object",
                    "properties": {
                        "steps": {"type": "array"},
                        "notes": {"type": "string"},
                        "session_data": {"type": "array"}
                    },
                    "required": ["steps"]
                }
            },
            {
                "name": "vote_on_pattern",
                "description": "Contributor tier: vote on submitted pattern.",
                "inputSchema": {
                    "type": "object",
                    "properties": {
                        "pattern_id": {"type": "string"},
                        "rating": {"type": "integer", "minimum": 1, "maximum": 5},
                        "session_evidence": {"type": "object"}
                    },
                    "required": ["pattern_id", "rating"]
                }
            },
            {
                "name": "get_pattern_status",
                "description": "Returns active/drifting/calibrating status + explanation.",
                "inputSchema": {"type": "object", "properties": {}}
            }
        ],
        "endpoints": {
            "health": "/health",
            "mcp_streamable_http": "/mcp",
            "oauth_discovery": "/.well-known/oauth-authorization-server",
            "billing": "/billing/*"
        },
        "transport": "streamable-http",
        "mcp_endpoint": "/mcp"
    }

# ─── MCP JSON-RPC ENDPOINT (POST /) — delegates to shared dispatcher ───
@app.post("/")
async def mcp_root_handler(request: Request):
    body = await request.json()
    return await _dispatch_jsonrpc(request, body, log_label="MCP ROOT")


# ─── DEBUG: MCP + OAuth Live Dashboard ───
@app.get("/debug/mcp")
async def debug_mcp_dashboard():
    """
    Live MCP + OAuth end-to-end smoke check dashboard.

    Runs all 11 smoke test steps against the live server and renders
    a dark-themed HTML page with pass/fail indicators.

    NOTE: This is a development/debugging tool. Do not expose publicly
    in production without adding auth.
    """
    from debug.mcp_smoke import run_mcp_smoke
    import time

    start = time.time()
    checks = await run_mcp_smoke(APP_URL, timeout=10.0)
    elapsed = time.time() - start

    total = len(checks)
    passed = sum(1 for c in checks if c["ok"])
    failed = total - passed

    rows_html = ""
    for c in checks:
        icon = "✅" if c["ok"] else "❌"
        color = "#4ade80" if c["ok"] else "#ff6b6b"
        detail = c.get("detail", "")
        # Escape HTML in detail
        detail = detail.replace("&", "&").replace("<", "<").replace(">", ">")
        rows_html += f"""
        <tr>
            <td style="font-size:20px;text-align:center;padding:8px 12px;">{icon}</td>
            <td style="padding:8px 12px;color:#e0d6ff;font-weight:500;">{c['name']}</td>
            <td style="padding:8px 12px;color:{color};font-size:13px;word-break:break-word;">{detail}</td>
        </tr>"""

    html = f"""<!DOCTYPE html>
<html lang="en">
<head>
  <meta charset="UTF-8">
  <meta name="viewport" content="width=device-width, initial-scale=1.0">
  <title>SunoCoach — MCP Debug Dashboard</title>
  <style>
    * {{ box-sizing: border-box; margin: 0; padding: 0; }}
    body {{
      font-family: -apple-system, BlinkMacSystemFont, 'Segoe UI', Roboto, monospace;
      background: linear-gradient(135deg, #0f0c29, #302b63, #24243e);
      min-height: 100vh; padding: 40px 20px;
      color: #c8c0e0;
    }}
    .container {{ max-width: 900px; margin: 0 auto; }}
    h1 {{ color: #e0d6ff; font-size: 26px; margin-bottom: 6px; }}
    .warning {{
      background: #3a2a00; border: 1px solid #665500; border-radius: 8px;
      padding: 12px 16px; margin: 16px 0; color: #ffcc66; font-size: 13px;
    }}
    .summary {{
      display: flex; gap: 16px; margin: 20px 0;
    }}
    .summary-box {{
      flex: 1; border-radius: 12px; padding: 20px; text-align: center;
    }}
    .summary-box.pass {{ background: #0a2e1a; border: 1px solid #1a6b3a; }}
    .summary-box.fail {{ background: #2e0a0a; border: 1px solid #6b1a1a; }}
    .summary-box .num {{ font-size: 36px; font-weight: 700; }}
    .summary-box .num.green {{ color: #4ade80; }}
    .summary-box .num.red {{ color: #ff6b6b; }}
    .summary-box .label {{ font-size: 13px; color: #888; margin-top: 4px; }}
    table {{
      width: 100%; border-collapse: collapse; background: #1a1a2e;
      border-radius: 12px; overflow: hidden; margin-top: 16px;
    }}
    th {{
      text-align: left; padding: 12px; background: #12122a;
      color: #888; font-size: 12px; text-transform: uppercase; letter-spacing: 1px;
    }}
    td {{ border-bottom: 1px solid #2a2a4a; }}
    tr:last-child td {{ border-bottom: none; }}
    .footer {{ margin-top: 20px; font-size: 12px; color: #555; text-align: center; }}
  </style>
</head>
<body>
  <div class="container">
    <h1>🔬 MCP + OAuth Debug Dashboard</h1>
    <p style="color:#888;font-size:14px;">Live end-to-end check against <code style="color:#7c5cfc;">{APP_URL}</code></p>

    <div class="warning">
      ⚠️ <strong>Development/debugging tool only.</strong> Do not expose this publicly in production without adding authentication.
      This endpoint runs live OAuth token exchange and MCP tool calls against the actual database.
    </div>

    <div class="summary">
      <div class="summary-box pass">
        <div class="num green">{passed}</div>
        <div class="label">Passed</div>
      </div>
      <div class="summary-box fail">
        <div class="num red">{failed}</div>
        <div class="label">Failed</div>
      </div>
      <div class="summary-box" style="background:#1a1a2e;border:1px solid #3a3560;">
        <div class="num" style="color:#e0d6ff;">{elapsed:.1f}s</div>
        <div class="label">Duration</div>
      </div>
    </div>

    <table>
      <thead>
        <tr><th style="width:40px;">&nbsp;</th><th>Check</th><th>Detail</th></tr>
      </thead>
      <tbody>
        {rows_html}
      </tbody>
    </table>

    <div class="footer">
      Ran {total} checks in {elapsed:.1f}s &mdash; SunoCoach v1.0.0
    </div>
  </div>
</body>
</html>"""
    return HTMLResponse(content=html)


# ─── HEALTH ENDPOINT ───
@app.get("/health")
async def health():
    import traceback
    diagnostics = {
        "database_url_set": bool(os.environ.get("DATABASE_URL")),
        "app_url": os.environ.get("APP_URL", "not_set")
    }
    try:
        pattern = await get_pattern_status()
        return {
            "status": "ok",
            "version": "1.0.0",
            "pattern_status": pattern.get("system_status", "unknown"),
            "active_pattern": pattern.get("active_pattern", {}).get("name"),
            "diagnostics": diagnostics
        }
    except Exception as e:
        diagnostics["error"] = str(e)
        diagnostics["traceback"] = traceback.format_exc()
        return JSONResponse(
            status_code=500,
            content={
                "status": "error",
                "message": str(e),
                "diagnostics": diagnostics
            }
        )

# ─── OAUTH DISCOVERY ───
@app.get("/.well-known/oauth-authorization-server")
async def oauth_discovery():
    return await get_oauth_discovery()

# ─── OAUTH PROTECTED RESOURCE ───
@app.get("/.well-known/oauth-protected-resource")
async def oauth_protected_resource():
    base_url = APP_URL
    return JSONResponse({
        "resource": base_url,
        "authorization_servers": [base_url],
        "bearer_methods_supported": ["Authorization"],
        "scopes_supported": ["read", "write", "contribute"],
        "resource_documentation": f"{base_url}/"
    })

# ─── OAUTH DYNAMIC CLIENT REGISTRATION ───
@app.post("/oauth/register")
async def oauth_register(request: Request):
    """Dynamic Client Registration (OAuth 2.1) for Claude."""
    body = await request.json()
    result = await register_client(body)
    return JSONResponse(result)

# ─── OAUTH AUTHORIZE — serve HTML login page ───
@app.get("/oauth/authorize")
async def oauth_authorize(
    response_type: str,
    client_id: str,
    redirect_uri: str,
    scope: str = "read",
    state: str = "",
    code_challenge: Optional[str] = None,
    code_challenge_method: Optional[str] = None
):
    if response_type != "code":
        raise HTTPException(status_code=400, detail="response_type must be 'code'")

    import html
    safe_redirect = html.escape(redirect_uri)
    safe_state = html.escape(state)
    safe_scope = html.escape(scope)
    safe_client = html.escape(client_id)
    safe_challenge = html.escape(code_challenge or "")
    safe_challenge_method = html.escape(code_challenge_method or "")

    html_content = f"""<!DOCTYPE html>
<html lang="en">
<head>
  <meta charset="UTF-8">
  <meta name="viewport" content="width=device-width, initial-scale=1.0">
  <title>SunoCoach — Sign In</title>
  <style>
    * {{ box-sizing: border-box; margin: 0; padding: 0; }}
    body {{
      font-family: -apple-system, BlinkMacSystemFont, 'Segoe UI', Roboto, sans-serif;
      background: linear-gradient(135deg, #0f0c29, #302b63, #24243e);
      min-height: 100vh;
      display: flex; align-items: center; justify-content: center;
    }}
    .card {{
      background: #1a1a2e;
      border-radius: 16px; padding: 40px; width: 380px;
      box-shadow: 0 20px 60px rgba(0,0,0,0.5);
    }}
    h2 {{ color: #e0d6ff; font-size: 24px; margin-bottom: 8px; text-align: center; }}
    .sub {{ color: #888; font-size: 14px; margin-bottom: 28px; text-align: center; }}
    label {{ color: #b8b0d0; font-size: 14px; display: block; margin-bottom: 6px; }}
    input[type="text"], input[type="password"] {{
      width: 100%; padding: 12px 16px; border-radius: 8px; border: 1px solid #3a3560;
      background: #16213e; color: #e0d6ff; font-size: 15px; margin-bottom: 18px;
      outline: none; transition: border 0.2s;
    }}
    input:focus {{ border-color: #7c5cfc; }}
    button {{
      width: 100%; padding: 12px; border-radius: 8px; border: none;
      background: linear-gradient(135deg, #7c5cfc, #5c3cfc);
      color: #fff; font-size: 16px; font-weight: 600; cursor: pointer;
      transition: transform 0.15s, box-shadow 0.15s;
    }}
    button:hover {{ transform: translateY(-1px); box-shadow: 0 8px 24px rgba(124,92,252,0.4); }}
    .error {{ color: #ff6b6b; font-size: 13px; margin-top: 12px; text-align: center; display: none; }}
  </style>
</head>
<body>
  <div class="card">
    <h2>🔐 SunoCoach</h2>
    <p class="sub">Sign in to authorize Claude Desktop</p>
    <form method="POST" action="{APP_URL}/oauth/login">
      <input type="hidden" name="state" value="{safe_state}" />
      <input type="hidden" name="redirect_uri" value="{safe_redirect}" />
      <input type="hidden" name="client_id" value="{safe_client}" />
      <input type="hidden" name="scope" value="{safe_scope}" />
      <input type="hidden" name="code_challenge" value="{safe_challenge}" />
      <input type="hidden" name="code_challenge_method" value="{safe_challenge_method}" />
      <label for="username">Email</label>
      <input type="text" id="username" name="username" placeholder="you@example.com" required />
      <label for="password">Password</label>
      <input type="password" id="password" name="password" placeholder="Enter your password" required />
      <button type="submit">Sign In</button>
      <p class="error" id="error-msg"></p>
    </form>
  </div>
  <script>
    const params = new URLSearchParams(window.location.search);
    if (params.get('error')) {{
      document.getElementById('error-msg').textContent = params.get('error');
      document.getElementById('error-msg').style.display = 'block';
    }}
  </script>
</body>
</html>"""
    return HTMLResponse(content=html_content)


# ─── OAUTH LOGIN — validate credentials, redirect back with code ───
@app.post("/oauth/login")
async def oauth_login(request: Request):
    form = await request.form()
    username = str(form.get("username", ""))
    password = str(form.get("password", ""))
    redirect_uri = str(form.get("redirect_uri", ""))
    state = str(form.get("state", ""))
    scope = str(form.get("scope", "read"))
    code_challenge_raw = form.get("code_challenge")
    code_challenge = str(code_challenge_raw) if code_challenge_raw else None
    code_challenge_method_raw = form.get("code_challenge_method")
    code_challenge_method = str(code_challenge_method_raw) if code_challenge_method_raw else None

    if not username or not password:
        raise HTTPException(status_code=400, detail="Email and password required")

    try:
        result = await login_user(
            email=username,
            password=password,
            redirect_uri=redirect_uri,
            scope=scope,
            state=state,
            code_challenge=code_challenge,
            code_challenge_method=code_challenge_method
        )
        return RedirectResponse(url=result["redirect_url"], status_code=302)
    except HTTPException:
        import html as _html
        safe_redirect = _html.escape(redirect_uri)
        safe_state = _html.escape(state)
        safe_scope = _html.escape(scope)
        error_msg = "Invalid+email+or+password"
        return RedirectResponse(
            url=f"{APP_URL}/oauth/authorize?response_type=code&client_id={CLIENT_ID}&redirect_uri={safe_redirect}&scope={safe_scope}&state={safe_state}&error={error_msg}",
            status_code=302
        )

# ─── OAUTH TOKEN (with PKCE verification) ───
# Accepts both application/json (smoke test / curl) and
# application/x-www-form-urlencoded (Claude OAuth 2.1 RFC 6749 standard).
@app.post("/oauth/token")
async def oauth_token(request: Request):
    content_type = request.headers.get("content-type", "")
    if "application/x-www-form-urlencoded" in content_type or "multipart/form-data" in content_type:
        form = await request.form()
        body = dict(form)
    else:
        body = await request.json()

    grant_type = body.get("grant_type", "authorization_code")

    try:
        if grant_type == "authorization_code":
            return await exchange_code_for_token(
                body.get("code"),
                body.get("client_id"),
                body.get("client_secret"),
                body.get("redirect_uri"),
                body.get("code_verifier") or None  # PKCE verifier (empty string → None)
            )
        elif grant_type == "refresh_token":
            return await refresh_access_token(body.get("refresh_token"))
        else:
            raise HTTPException(status_code=400, detail="Unsupported grant_type")
    except HTTPException:
        raise  # Let FastAPI handle known HTTP errors normally
    except Exception as exc:
        # Surface the real exception so /debug/mcp shows the actual crash cause
        import traceback as _tb
        detail = f"{type(exc).__name__}: {exc}"
        print(f"[oauth/token] UNCAUGHT EXCEPTION: {detail}\n{_tb.format_exc()}")
        raise HTTPException(status_code=500, detail=detail)

# ─── STRIPE WEBHOOK ───
@app.post("/webhooks/stripe")
async def stripe_webhook(request: Request):
    return await handle_webhook(request)

# ─── STRIPE CHECKOUT ───
@app.post("/billing/checkout")
async def billing_checkout(request: Request):
    token_data = await validate_token(request)
    body = await request.json()
    result = await create_checkout_session(token_data["user_id"], body.get("email", ""))
    return result


# ─── STARTUP: run idempotent DB migrations ───
@app.on_event("startup")
async def startup():
    import logging
    logging.basicConfig(level=logging.INFO)
    log = logging.getLogger("sunocoach.startup")
    log.info("[startup] Running DB migrations …")
    try:
        await run_migrations()
        log.info("[startup] DB migrations complete ✅")
    except Exception as exc:
        log.error(f"[startup] DB migration FAILED: {exc}")
        # Re-raise so Render marks the deployment as failed — better than
        # serving requests against a broken schema.
        raise


# ─── SHUTDOWN ───
@app.on_event("shutdown")
async def shutdown():
    await close_pool()

