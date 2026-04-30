import os
import json
import traceback
from typing import Any, Dict
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

# ─── /mcp ALIAS (Claude Streamable HTTP compatibility) ───
# Claude requires POST /mcp for Streamable HTTP MCP transport.
# This aliases /mcp to the working JSON-RPC handlers at POST / and GET /.
@app.post("/mcp")
@app.get("/mcp")
async def mcp_endpoint(request: Request):
    """Handle MCP requests at /mcp for Claude compatibility.
    
    POST /mcp → JSON-RPC messages (initialize, tools/list, tools/call)
    GET /mcp  → MCP manifest (same as GET /)
    
    Bearer token validation: Claude sends Authorization: Bearer <token> header.
    """
    if request.method == "GET":
        return await root()
    
    # Validate Bearer token for POST requests (Claude OAuth)
    auth_header = request.headers.get("Authorization", "")
    if auth_header.startswith("Bearer "):
        try:
            await validate_token(request)
        except HTTPException:
            # Token invalid — return JSON-RPC error
            body = await request.json()
            return JSONResponse({
                "jsonrpc": "2.0",
                "id": body.get("id"),
                "error": {"code": -32001, "message": "Unauthorized: Invalid or expired token"}
            }, status_code=401)
    
    # For POST, delegate to the JSON-RPC handler
    return await mcp_rpc(request)

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
                "input_schema": {"type": "object", "properties": {}}
            },
            {
                "name": "get_next_step",
                "description": "Returns exact instruction for the current step in plain English.",
                "input_schema": {
                    "type": "object",
                    "properties": {"session_id": {"type": "string"}},
                    "required": ["session_id"]
                }
            },
            {
                "name": "log_step_result",
                "description": "Stores result, advances session state, checks for drift.",
                "input_schema": {
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
                "input_schema": {
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
                "input_schema": {
                    "type": "object",
                    "properties": {"description": {"type": "string"}},
                    "required": ["description"]
                }
            },
            {
                "name": "build_lyric_structure",
                "description": "Applies correct bracket tagging to raw lyrics.",
                "input_schema": {
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
                "input_schema": {
                    "type": "object",
                    "properties": {"prompt_text": {"type": "string"}},
                    "required": ["prompt_text"]
                }
            },
            {
                "name": "save_style_prompt",
                "description": "Validates then stores in user's style library.",
                "input_schema": {
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
                "input_schema": {
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
                "input_schema": {
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
                "input_schema": {
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
                "input_schema": {
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
                "input_schema": {
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
                "input_schema": {"type": "object", "properties": {}}
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

# ─── MCP JSON-RPC ENDPOINT (POST /) ───
@app.post("/")
async def mcp_rpc(request: Request):
    body = await request.json()
    method = body.get("method", "")
    params = body.get("params", {})
    req_id = body.get("id", None)

    # Tool dispatch table
    tools = {
        "get_current_workflow": lambda: get_current_workflow(),
        "get_next_step": lambda: get_next_step(params.get("session_id")),
        "log_step_result": lambda: log_step_result(
            params.get("session_id"),
            params.get("step_number"),
            params.get("quality_rating"),
            params.get("notes", "")
        ),
        "start_session": lambda: start_session(
            params.get("user_id"),
            params.get("workflow_id")
        ),
        "generate_style_prompt": lambda: generate_style_prompt(params.get("description", "")),
        "build_lyric_structure": lambda: build_lyric_structure(
            params.get("raw_lyrics", ""),
            params.get("sections")
        ),
        "validate_prompt": lambda: validate_prompt(params.get("prompt_text", "")),
        "save_style_prompt": lambda: save_style_prompt(
            params.get("user_id"),
            params.get("name"),
            params.get("prompt_text"),
            params.get("genre_tags"),
            params.get("mood_tags"),
            params.get("bpm")
        ),
        "recall_style": lambda: recall_style(
            params.get("user_id"),
            params.get("mood"),
            params.get("genre")
        ),
        "save_client": lambda: save_client(
            params.get("user_id"),
            params.get("name"),
            params.get("vocal_type"),
            params.get("genres"),
            params.get("bpm_range"),
            params.get("emotional_register")
        ),
        "get_client_brief": lambda: get_client_brief(
            params.get("user_id"),
            params.get("client_name"),
            params.get("concept", "")
        ),
        "submit_workflow": lambda: submit_workflow(
            params.get("user_id"),
            params.get("steps", []),
            params.get("notes", ""),
            params.get("session_data")
        ),
        "vote_on_pattern": lambda: vote_on_pattern(
            params.get("user_id"),
            params.get("pattern_id"),
            params.get("rating"),
            params.get("session_evidence")
        ),
        "get_pattern_status": lambda: get_pattern_status(),
    }

    if method == "initialize":
        return JSONResponse({
            "jsonrpc": "2.0",
            "id": req_id,
            "result": {
                "protocolVersion": "2024-11-05",
                "capabilities": {},
                "serverInfo": {
                    "name": "SunoCoach",
                    "version": "1.0.0"
                }
            }
        })

    if method == "tools/list":
        manifest = await root()
        return JSONResponse({
            "jsonrpc": "2.0",
            "id": req_id,
            "result": {"tools": manifest.get("tools", [])}
        })

    if method in tools:
        try:
            result = await tools[method]()
            return JSONResponse({
                "jsonrpc": "2.0",
                "id": req_id,
                "result": result
            })
        except Exception as e:
            return JSONResponse({
                "jsonrpc": "2.0",
                "id": req_id,
                "error": {"code": -32603, "message": str(e)}
            }, status_code=500)

    return JSONResponse({
        "jsonrpc": "2.0",
        "id": req_id,
        "error": {"code": -32601, "message": f"Method not found: {method}"}
    }, status_code=404)

# ─── MCP DISCOVERY ENDPOINT ───
@app.get("/mcp")
async def mcp_discovery():
    return await root()

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

# ─── MCP TOOL ENDPOINTS ───

# ─── SHUTDOWN ───
@app.on_event("shutdown")
async def shutdown():
    await close_pool()
