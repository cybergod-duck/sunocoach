"""
SunoCoach - Cloudflare Workers entry point
Pure Python - no FastAPI (not supported in Workers Python runtime)
"""
import os
import json
from urllib.parse import parse_qs, urlparse

# Import tool handlers
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
from db.client import get_pool, close_pool


def json_response(data, status=200):
    return Response(json.dumps(data), status=status, headers={"Content-Type": "application/json"})


def error_response(message, status=400):
    return Response(json.dumps({"error": message}), status=status, headers={"Content-Type": "application/json"})


class Response:
    def __init__(self, body, status=200, headers=None):
        self.body = body
        self.status = status
        self.headers = headers or {}


async def on_fetch(request, env):
    """Cloudflare Workers fetch handler."""
    # Set environment from bindings
    os.environ["DATABASE_URL"] = env.DATABASE_URL
    os.environ["REDIS_URL"] = env.REDIS_URL
    os.environ["STRIPE_SECRET_KEY"] = env.STRIPE_SECRET_KEY
    os.environ["STRIPE_WEBHOOK_SECRET"] = env.STRIPE_WEBHOOK_SECRET
    os.environ["STRIPE_PRICE_ID_PRO"] = env.STRIPE_PRICE_ID_PRO
    os.environ["OAUTH_CLIENT_SECRET"] = env.OAUTH_CLIENT_SECRET
    os.environ["APP_URL"] = env.APP_URL
    os.environ["OAUTH_CLIENT_ID"] = "sunocoach-claude"
    os.environ["SESSION_CACHE"] = env.SESSION_CACHE

    # Initialize DB pool
    await get_pool()

    url = urlparse(request.url)
    path = url.path
    method = request.method

    try:
        # ─── HEALTH ───
        if path == "/health" and method == "GET":
            pattern = await get_pattern_status()
            return json_response({
                "status": "ok",
                "version": "1.0.0",
                "pattern_status": pattern.get("system_status", "unknown"),
                "active_pattern": pattern.get("active_pattern", {}).get("name")
            })

        # ─── OAUTH DISCOVERY ───
        if path == "/.well-known/oauth-authorization-server" and method == "GET":
            return json_response(await get_oauth_discovery())

        # ─── OAUTH AUTHORIZE ───
        if path == "/oauth/authorize" and method == "GET":
            query = parse_qs(url.query)
            response_type = query.get("response_type", [""])[0]
            if response_type != "code":
                return error_response("response_type must be 'code'", 400)
            redirect_uri = query.get("redirect_uri", [""])[0]
            scope = query.get("scope", ["read"])[0]
            state = query.get("state", [""])[0]
            result = await create_authorization_url(redirect_uri, scope, state)
            return json_response({
                "authorization_url": result["authorization_url"],
                "login_url": f"/oauth/login?redirect_uri={redirect_uri}&scope={scope}&state={state}"
            })

        # ─── OAUTH TOKEN ───
        if path == "/oauth/token" and method == "POST":
            body = await request.json()
            grant_type = body.get("grant_type", "authorization_code")
            if grant_type == "authorization_code":
                return json_response(await exchange_code_for_token(
                    body.get("code"), body.get("client_id"),
                    body.get("client_secret"), body.get("redirect_uri")
                ))
            elif grant_type == "refresh_token":
                return json_response(await refresh_access_token(body.get("refresh_token")))
            else:
                return error_response("Unsupported grant_type", 400)

        # ─── STRIPE WEBHOOK ───
        if path == "/webhooks/stripe" and method == "POST":
            return await handle_webhook(request)

        # ─── STRIPE CHECKOUT ───
        if path == "/billing/checkout" and method == "POST":
            token_data = await validate_token(request)
            body = await request.json()
            return json_response(await create_checkout_session(token_data["user_id"], body.get("email", "")))

        # ─── MCP TOOL ENDPOINTS ───
        if path == "/mcp/get_current_workflow" and method == "POST":
            await validate_token(request)
            return json_response(await get_current_workflow())

        if path == "/mcp/get_next_step" and method == "POST":
            await validate_token(request)
            body = await request.json()
            return json_response(await get_next_step(body.get("session_id")))

        if path == "/mcp/log_step_result" and method == "POST":
            await validate_token(request)
            body = await request.json()
            return json_response(await log_step_result(
                body.get("session_id"), body.get("step_number"),
                body.get("quality_rating"), body.get("notes", "")
            ))

        if path == "/mcp/start_session" and method == "POST":
            token_data = await validate_token(request)
            body = await request.json()
            user_id = token_data.get("user_id")
            if not user_id:
                return error_response("User not authenticated", 401)
            return json_response(await start_session(user_id, body.get("workflow_id")))

        if path == "/mcp/generate_style_prompt" and method == "POST":
            await validate_token(request)
            body = await request.json()
            return json_response(await generate_style_prompt(body.get("description", "")))

        if path == "/mcp/build_lyric_structure" and method == "POST":
            await validate_token(request)
            body = await request.json()
            return json_response(await build_lyric_structure(body.get("raw_lyrics", ""), body.get("sections")))

        if path == "/mcp/validate_prompt" and method == "POST":
            await validate_token(request)
            body = await request.json()
            return json_response(await validate_prompt(body.get("prompt_text", "")))

        if path == "/mcp/save_style_prompt" and method == "POST":
            token_data = await validate_token(request)
            body = await request.json()
            user_id = token_data.get("user_id")
            if not user_id:
                return error_response("User not authenticated", 401)
            return json_response(await save_style_prompt(
                user_id, body.get("name"), body.get("prompt_text"),
                body.get("genre_tags"), body.get("mood_tags"), body.get("bpm")
            ))

        if path == "/mcp/recall_style" and method == "POST":
            token_data = await validate_token(request)
            body = await request.json()
            user_id = token_data.get("user_id")
            if not user_id:
                return error_response("User not authenticated", 401)
            return json_response(await recall_style(user_id, body.get("mood"), body.get("genre")))

        if path == "/mcp/save_client" and method == "POST":
            token_data = await validate_token(request)
            body = await request.json()
            user_id = token_data.get("user_id")
            if not user_id:
                return error_response("User not authenticated", 401)
            return json_response(await save_client(
                user_id, body.get("name"), body.get("vocal_type"),
                body.get("genres"), body.get("bpm_range"), body.get("emotional_register")
            ))

        if path == "/mcp/get_client_brief" and method == "POST":
            token_data = await validate_token(request)
            body = await request.json()
            user_id = token_data.get("user_id")
            if not user_id:
                return error_response("User not authenticated", 401)
            return json_response(await get_client_brief(user_id, body.get("client_name"), body.get("concept", "")))

        if path == "/mcp/submit_workflow" and method == "POST":
            token_data = await validate_token(request)
            body = await request.json()
            user_id = token_data.get("user_id")
            if not user_id:
                return error_response("User not authenticated", 401)
            return json_response(await submit_workflow(
                user_id, body.get("steps", []), body.get("notes", ""), body.get("session_data")
            ))

        if path == "/mcp/vote_on_pattern" and method == "POST":
            token_data = await validate_token(request)
            body = await request.json()
            user_id = token_data.get("user_id")
            if not user_id:
                return error_response("User not authenticated", 401)
            return json_response(await vote_on_pattern(
                user_id, body.get("pattern_id"), body.get("rating"), body.get("session_evidence")
            ))

        if path == "/mcp/get_pattern_status" and method == "POST":
            await validate_token(request)
            return json_response(await get_pattern_status())

        # ─── 404 ───
        return error_response("Not found", 404)

    except Exception as e:
        return error_response(str(e), 500)
    finally:
        await close_pool()
