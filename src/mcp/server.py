import os
import json
from typing import Any, Dict
try:
    from fastapi import FastAPI, Request, HTTPException
    from fastapi.responses import JSONResponse
    from fastapi.middleware.cors import CORSMiddleware
except ImportError:
    from utils.http_compat import HTTPException
    Request = None
    FastAPI = None
    JSONResponse = None
    CORSMiddleware = None
from mcp.tools import (
    get_current_workflow, get_next_step, log_step_result, start_session,
    generate_style_prompt, build_lyric_structure, validate_prompt,
    save_style_prompt, recall_style, save_client, get_client_brief,
    submit_workflow, vote_on_pattern, get_pattern_status
)
from auth.oauth import (
    create_authorization_url, exchange_code_for_token, refresh_access_token,
    validate_token, get_oauth_discovery
)
from billing.stripe_handler import (
    create_checkout_session, handle_webhook, get_subscription_status
)
from drift.detector import check_drift_status
from db.client import close_pool

app = FastAPI(title="SunoCoach MCP Server")

# CORS for Claude connector
app.add_middleware(
    CORSMiddleware,
    allow_origins=["https://claude.ai", "https://www.claude.ai"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# ─── HEALTH ENDPOINT ───
@app.get("/health")
async def health():
    pattern = await get_pattern_status()
    return {
        "status": "ok",
        "version": "1.0.0",
        "pattern_status": pattern.get("system_status", "unknown"),
        "active_pattern": pattern.get("active_pattern", {}).get("name")
    }

# ─── OAUTH DISCOVERY ───
@app.get("/.well-known/oauth-authorization-server")
async def oauth_discovery():
    return await get_oauth_discovery()

# ─── OAUTH AUTHORIZE ───
@app.get("/oauth/authorize")
async def oauth_authorize(response_type: str, client_id: str, redirect_uri: str, scope: str = "read", state: str = ""):
    if response_type != "code":
        raise HTTPException(status_code=400, detail="response_type must be 'code'")
    result = await create_authorization_url(redirect_uri, scope, state)
    return JSONResponse({
        "authorization_url": result["authorization_url"],
        "login_url": f"/oauth/login?redirect_uri={redirect_uri}&scope={scope}&state={state}"
    })

# ─── OAUTH TOKEN ───
@app.post("/oauth/token")
async def oauth_token(request: Request):
    body = await request.json()
    grant_type = body.get("grant_type", "authorization_code")

    if grant_type == "authorization_code":
        return await exchange_code_for_token(
            body.get("code"),
            body.get("client_id"),
            body.get("client_secret"),
            body.get("redirect_uri")
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

@app.post("/mcp/get_current_workflow")
async def mcp_get_current_workflow(request: Request):
    await validate_token(request)
    return await get_current_workflow()

@app.post("/mcp/get_next_step")
async def mcp_get_next_step(request: Request):
    await validate_token(request)
    body = await request.json()
    return await get_next_step(body.get("session_id"))

@app.post("/mcp/log_step_result")
async def mcp_log_step_result(request: Request):
    await validate_token(request)
    body = await request.json()
    return await log_step_result(
        body.get("session_id"),
        body.get("step_number"),
        body.get("quality_rating"),
        body.get("notes", "")
    )

@app.post("/mcp/start_session")
async def mcp_start_session(request: Request):
    token_data = await validate_token(request)
    body = await request.json()
    user_id = token_data.get("user_id")
    if not user_id:
        raise HTTPException(status_code=401, detail="User not authenticated")
    return await start_session(user_id, body.get("workflow_id"))

@app.post("/mcp/generate_style_prompt")
async def mcp_generate_style_prompt(request: Request):
    await validate_token(request)
    body = await request.json()
    return await generate_style_prompt(body.get("description", ""))

@app.post("/mcp/build_lyric_structure")
async def mcp_build_lyric_structure(request: Request):
    await validate_token(request)
    body = await request.json()
    return await build_lyric_structure(body.get("raw_lyrics", ""), body.get("sections"))

@app.post("/mcp/validate_prompt")
async def mcp_validate_prompt(request: Request):
    await validate_token(request)
    body = await request.json()
    return await validate_prompt(body.get("prompt_text", ""))

@app.post("/mcp/save_style_prompt")
async def mcp_save_style_prompt(request: Request):
    token_data = await validate_token(request)
    body = await request.json()
    user_id = token_data.get("user_id")
    if not user_id:
        raise HTTPException(status_code=401, detail="User not authenticated")
    return await save_style_prompt(
        user_id,
        body.get("name"),
        body.get("prompt_text"),
        body.get("genre_tags"),
        body.get("mood_tags"),
        body.get("bpm")
    )

@app.post("/mcp/recall_style")
async def mcp_recall_style(request: Request):
    token_data = await validate_token(request)
    body = await request.json()
    user_id = token_data.get("user_id")
    if not user_id:
        raise HTTPException(status_code=401, detail="User not authenticated")
    return await recall_style(
        user_id,
        body.get("mood"),
        body.get("genre")
    )

@app.post("/mcp/save_client")
async def mcp_save_client(request: Request):
    token_data = await validate_token(request)
    body = await request.json()
    user_id = token_data.get("user_id")
    if not user_id:
        raise HTTPException(status_code=401, detail="User not authenticated")
    return await save_client(
        user_id,
        body.get("name"),
        body.get("vocal_type"),
        body.get("genres"),
        body.get("bpm_range"),
        body.get("emotional_register")
    )

@app.post("/mcp/get_client_brief")
async def mcp_get_client_brief(request: Request):
    token_data = await validate_token(request)
    body = await request.json()
    user_id = token_data.get("user_id")
    if not user_id:
        raise HTTPException(status_code=401, detail="User not authenticated")
    return await get_client_brief(
        user_id,
        body.get("client_name"),
        body.get("concept", "")
    )

@app.post("/mcp/submit_workflow")
async def mcp_submit_workflow(request: Request):
    token_data = await validate_token(request)
    body = await request.json()
    user_id = token_data.get("user_id")
    if not user_id:
        raise HTTPException(status_code=401, detail="User not authenticated")
    return await submit_workflow(
        user_id,
        body.get("steps", []),
        body.get("notes", ""),
        body.get("session_data")
    )

@app.post("/mcp/vote_on_pattern")
async def mcp_vote_on_pattern(request: Request):
    token_data = await validate_token(request)
    body = await request.json()
    user_id = token_data.get("user_id")
    if not user_id:
        raise HTTPException(status_code=401, detail="User not authenticated")
    return await vote_on_pattern(
        user_id,
        body.get("pattern_id"),
        body.get("rating"),
        body.get("session_evidence")
    )

@app.post("/mcp/get_pattern_status")
async def mcp_get_pattern_status(request: Request):
    await validate_token(request)
    return await get_pattern_status()

# ─── SHUTDOWN ───
@app.on_event("shutdown")
async def shutdown():
    await close_pool()
