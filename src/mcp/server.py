import os
import json
import traceback
from typing import Any, Dict, List, Optional
from fastapi import FastAPI, Request, HTTPException
from fastapi.responses import JSONResponse
from fastapi.middleware.cors import CORSMiddleware
from mcp.tools import (
    get_current_workflow, get_next_step, log_step_result, start_session,
    generate_style_prompt, build_lyric_structure, validate_prompt,
    save_style_prompt, recall_style, save_client, get_client_brief,
    submit_workflow, vote_on_pattern, get_pattern_status
)
from auth.oauth import (
    create_authorization_url, exchange_code_for_token, refresh_access_token,
    validate_token, get_oauth_discovery, register_client
)
from billing.stripe_handler import (
    create_checkout_session, handle_webhook, get_subscription_status
)
from drift.detector import check_drift_status
from db.client import close_pool

app = FastAPI(title="SunoCoach MCP Server")

# ─── CATCH-ALL CORS MIDDLEWARE ───
# FastAPI's CORSMiddleware only adds headers when Origin matches.
# Claude's MCP connector may not always send Origin, so we inject
# CORS on EVERY response to be safe.
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

    # ─── INCOMING REQUEST LOG ───
    body = await request.json()
    headers = dict(request.headers)
    print(f"=== MCP HIT ===")
    print(f"METHOD: {body.get('method')}")
    print(f"BODY: {json.dumps(body)}")
    print(f"AUTH: {headers.get('authorization', 'MISSING')}")
    print(f"ACCEPT: {headers.get('accept', 'MISSING')}")

    # ─── OAuth 2.1: ALL POST requests require valid Bearer token ───
    auth_header = request.headers.get("Authorization", "")
    base_url = os.environ.get("APP_URL", "https://sunocoach.onrender.com")

    if not auth_header.startswith("Bearer "):
        data = {
            "jsonrpc": "2.0",
            "id": body.get("id"),
            "error": {
                "code": -32001,
                "message": "Unauthorized. Use OAuth 2.1 with PKCE to authenticate.",
                "authorization_url": f"{base_url}/oauth/authorize"
            }
        }
        print(f"RESPONSE [401-challenge]: {json.dumps(data)}")
        return JSONResponse(
            data,
            status_code=401,
            headers={
                "WWW-Authenticate": f'Bearer realm="sunocoach", authorization_server="{base_url}/.well-known/oauth-authorization-server"'
            }
        )

    try:
        await validate_token(request)
    except HTTPException:
        data = {
            "jsonrpc": "2.0",
            "id": body.get("id"),
            "error": {"code": -32001, "message": "Unauthorized: Invalid or expired token"}
        }
        print(f"RESPONSE [401-invalid-token]: {json.dumps(data)}")
        return JSONResponse(
            data,
            status_code=401,
            headers={"WWW-Authenticate": 'Bearer error="invalid_token"'}
        )

    # ─── JSON-RPC 2.0 Method Dispatch ───
    method = body.get("method", "")
    params = body.get("params", {})
    req_id = body.get("id")

    if method == "initialize":
        data = {
            "jsonrpc": "2.0",
            "id": req_id,
            "result": {
                "protocolVersion": "2024-11-05",
                "serverInfo": {"name": "SunoCoach", "version": "1.0.0"},
                "capabilities": {"tools": {}}
            }
        }
        print(f"RESPONSE [initialize]: {json.dumps(data)}")
        return JSONResponse(data)

    if method == "tools/list":
        data = {
            "jsonrpc": "2.0",
            "id": req_id,
            "result": {"tools": MCP_TOOLS}
        }
        print(f"RESPONSE [tools/list]: {json.dumps(data)}")
        return JSONResponse(data)

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
            return JSONResponse(data)

        try:
            result = await TOOL_DISPATCH[tool_name](arguments)
            data = {
                "jsonrpc": "2.0",
                "id": req_id,
                "result": {"content": [{"type": "text", "text": json.dumps(result) if not isinstance(result, str) else result}]}
            }
            print(f"RESPONSE [tools/call-{tool_name}]: {json.dumps(data)}")
            return JSONResponse(data)
        except Exception as e:
            data = {
                "jsonrpc": "2.0",
                "id": req_id,
                "error": {"code": -32603, "message": str(e)}
            }
            print(f"RESPONSE [tools/call-error]: {json.dumps(data)}")
            return JSONResponse(data, status_code=500)

    # Unknown method
    data = {
        "jsonrpc": "2.0",
        "id": req_id,
        "error": {"code": -32601, "message": f"Method not found: {method}"}
    }
    print(f"RESPONSE [unknown-method]: {json.dumps(data)}")
    return JSONResponse(data)

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

# ─── MCP JSON-RPC ENDPOINT (POST /) — same logic as /mcp handler ───
@app.post("/")
async def mcp_root_handler(request: Request):
    # ─── INCOMING REQUEST LOG ───
    body = await request.json()
    headers = dict(request.headers)
    print(f"=== MCP ROOT HIT ===")
    print(f"METHOD: {body.get('method')}")
    print(f"BODY: {json.dumps(body)}")
    print(f"AUTH: {headers.get('authorization', 'MISSING')}")
    print(f"ACCEPT: {headers.get('accept', 'MISSING')}")

    # ─── OAuth 2.1: ALL POST requests require valid Bearer token ───
    auth_header = request.headers.get("Authorization", "")
    base_url = os.environ.get("APP_URL", "https://sunocoach.onrender.com")

    if not auth_header.startswith("Bearer "):
        data = {
            "jsonrpc": "2.0",
            "id": body.get("id"),
            "error": {
                "code": -32001,
                "message": "Unauthorized. Use OAuth 2.1 with PKCE to authenticate.",
                "authorization_url": f"{base_url}/oauth/authorize"
            }
        }
        print(f"RESPONSE [401-challenge]: {json.dumps(data)}")
        return JSONResponse(
            data,
            status_code=401,
            headers={
                "WWW-Authenticate": f'Bearer realm="sunocoach", authorization_server="{base_url}/.well-known/oauth-authorization-server"'
            }
        )

    try:
        await validate_token(request)
    except HTTPException:
        data = {
            "jsonrpc": "2.0",
            "id": body.get("id"),
            "error": {"code": -32001, "message": "Unauthorized: Invalid or expired token"}
        }
        print(f"RESPONSE [401-invalid-token]: {json.dumps(data)}")
        return JSONResponse(
            data,
            status_code=401,
            headers={"WWW-Authenticate": 'Bearer error="invalid_token"'}
        )

    # ─── JSON-RPC 2.0 Method Dispatch ───
    method = body.get("method", "")
    params = body.get("params", {})
    req_id = body.get("id")

    if method == "initialize":
        data = {
            "jsonrpc": "2.0",
            "id": req_id,
            "result": {
                "protocolVersion": "2024-11-05",
                "serverInfo": {"name": "SunoCoach", "version": "1.0.0"},
                "capabilities": {"tools": {}}
            }
        }
        print(f"RESPONSE [initialize]: {json.dumps(data)}")
        return JSONResponse(data)

    if method == "tools/list":
        data = {
            "jsonrpc": "2.0",
            "id": req_id,
            "result": {"tools": MCP_TOOLS}
        }
        print(f"RESPONSE [tools/list]: {json.dumps(data)}")
        return JSONResponse(data)

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
            return JSONResponse(data)

        try:
            result = await TOOL_DISPATCH[tool_name](arguments)
            data = {
                "jsonrpc": "2.0",
                "id": req_id,
                "result": {"content": [{"type": "text", "text": json.dumps(result) if not isinstance(result, str) else result}]}
            }
            print(f"RESPONSE [tools/call-{tool_name}]: {json.dumps(data)}")
            return JSONResponse(data)
        except Exception as e:
            data = {
                "jsonrpc": "2.0",
                "id": req_id,
                "error": {"code": -32603, "message": str(e)}
            }
            print(f"RESPONSE [tools/call-error]: {json.dumps(data)}")
            return JSONResponse(data, status_code=500)

    # Unknown method
    data = {
        "jsonrpc": "2.0",
        "id": req_id,
        "error": {"code": -32601, "message": f"Method not found: {method}"}
    }
    print(f"RESPONSE [unknown-method]: {json.dumps(data)}")
    return JSONResponse(data)


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
    base_url = os.environ.get("APP_URL", "https://sunocoach.onrender.com")
    return JSONResponse({
        "resource": base_url,
        "authorization_servers": [base_url]
    })

# ─── OAUTH DYNAMIC CLIENT REGISTRATION ───
@app.post("/oauth/register")
async def oauth_register(request: Request):
    """Dynamic Client Registration (OAuth 2.1) for Claude."""
    body = await request.json()
    result = await register_client(body)
    return JSONResponse(result)

# ─── OAUTH AUTHORIZE (with PKCE support) ───
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
    result = await create_authorization_url(redirect_uri, scope, state, code_challenge, code_challenge_method)
    return JSONResponse({
        "authorization_url": result["authorization_url"],
        "login_url": f"/oauth/login?redirect_uri={redirect_uri}&scope={scope}&state={state}"
    })

# ─── OAUTH TOKEN (with PKCE verification) ───
@app.post("/oauth/token")
async def oauth_token(request: Request):
    body = await request.json()
    grant_type = body.get("grant_type", "authorization_code")

    if grant_type == "authorization_code":
        return await exchange_code_for_token(
            body.get("code"),
            body.get("client_id"),
            body.get("client_secret"),
            body.get("redirect_uri"),
            body.get("code_verifier")  # PKCE verifier
        )
    elif grant_type == "refresh_token":
        return await refresh_access_token(body.get("refresh_token"))
    else:
        raise HTTPException(status_code=400, detail="Unsupported grant_type")

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


# ─── SHUTDOWN ───
@app.on_event("shutdown")
async def shutdown():
    await close_pool()
