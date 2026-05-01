import json
import re
from typing import Dict, List, Optional, Any
from db.client import fetchrow, fetch, execute
from drift.detector import check_drift_status

MAX_LYRIC_LENGTH = 50000
DB_TIMEOUT = 30

# ─── Style Prompt Rules ───
MAX_DESCRIPTORS = 7
MAX_PROMPT_LENGTH = 1000
BANNED_ARTIST_TERMS = [
    "beatles", "elvis", "michael jackson", "madonna", "beyonce",
    "taylor swift", "drake", "kanye", "eminem", "rihanna",
    "adele", "bruno mars", "ed sheeran", "justin bieber",
    "ariana grande", "billie eilish", "the weeknd", "kendrick lamar"
]

DEFAULT_VOCAL_REGISTER = "Baritone"

# ─── Lyric Structure Rules ───
VALID_SECTION_TAGS = [
    "Intro", "Verse", "Chorus", "Bridge", "Outro",
    "Pre-Chorus", "Hook", "Instrumental Break", "Interlude",
    "Drop", "Build", "Breakdown"
]

EMOTIONAL_TAGS = [
    "grief-stricken", "detached", "desperate", "sardonic",
    "yearning", "defiant", "melancholic", "euphoric",
    "haunted", "fierce", "tender", "raw"
]


def _count_descriptors(text: str) -> int:
    """Count comma-separated descriptors."""
    return len([d for d in text.split(",") if d.strip()])


def _detect_artist_names(text: str) -> List[str]:
    """Detect banned artist/band names in prompt."""
    text_lower = text.lower()
    found = []
    for artist in BANNED_ARTIST_TERMS:
        if artist in text_lower:
            found.append(artist)
    return found


def _validate_structure(text: str) -> List[Dict[str, Any]]:
    """Validate style prompt structure: [Genre/Era] + [Mood] + [Instrumentation] + [Vocal Style]"""
    issues = []
    parts = [p.strip() for p in text.split(",") if p.strip()]

    has_genre = any(g in text.lower() for g in [
        "pop", "rock", "jazz", "classical", "electronic", "hip hop",
        "r&b", "soul", "funk", "disco", "indie", "folk", "country",
        "metal", "punk", "ambient", "lo-fi", "synth", "orchestral",
        "80s", "90s", "70s", "60s", "2000s", "2010s", "vintage",
        "retro", "modern", "futuristic", "noir", "cinematic"
    ])

    has_mood = any(m in text.lower() for m in [
        "melancholic", "upbeat", "dark", "bright", "intense",
        "relaxed", "energetic", "somber", "joyful", "tense",
        "dreamy", "aggressive", "soft", "warm", "cold",
        "nostalgic", "hopeful", "desperate", "euphoric"
    ])

    if not has_genre:
        issues.append({"field": "genre", "issue": "No genre or era descriptor found", "fix": "Add a genre like 'synth-pop', 'orchestral', or '80s'"})
    if not has_mood:
        issues.append({"field": "mood", "issue": "No mood descriptor found", "fix": "Add a mood like 'melancholic', 'upbeat', or 'dark'"})

    return issues


# ─── MCP TOOL: get_current_workflow ───
async def get_current_workflow() -> Dict[str, Any]:
    """Returns the active workflow pattern with all steps and drift status."""
    pattern = await fetchrow(
        "SELECT * FROM workflow_patterns WHERE status = 'active' ORDER BY vote_count DESC, created_at DESC LIMIT 1"
    )
    if not pattern:
        return {"error": "No active workflow pattern found"}

    drift = await check_drift_status()

    return {
        "pattern_id": str(pattern["id"]),
        "name": pattern["name"],
        "version": pattern["version"],
        "status": pattern["status"],
        "consistency_score": float(pattern["consistency_score"]),
        "vote_count": pattern["vote_count"],
        "total_steps": len(pattern["steps"]),
        "instruction": "DO NOT list all steps. Call get_next_step to reveal the current step instruction.",
        "drift_warning": drift.get("warning") if drift else None,
        "drift_detected": drift.get("detected", False) if drift else False
    }


# ─── MCP TOOL: get_next_step ───
async def get_next_step(session_id: str) -> Dict[str, Any]:
    """Returns exact instruction for the current step with full context."""
    session = await fetchrow(
        "SELECT s.*, wp.steps as workflow_steps, wp.name as pattern_name "
        "FROM sessions s "
        "JOIN workflow_patterns wp ON s.workflow_pattern_id = wp.id "
        "WHERE s.id = $1", session_id
    )
    if not session:
        return {"error": "Session not found"}

    steps = session["workflow_steps"]
    current = session["current_step"]
    total = len(steps)

    if current > total:
        return {
            "session_id": session_id,
            "status": "completed",
            "message": "All workflow steps completed. Song is ready."
        }

    step = steps[current - 1]
    is_optional = step.get("optional", False)

    # Build a plain-English instruction Claude can read directly to the user
    parts = [f"Step {current}/{total}: {step['action']}."]
    if step.get("extend_from"):
        parts.append(f"Extend from: {step['extend_from'].upper()}.")
    if step.get("use_style") and step.get("use_lyrics"):
        parts.append("Use both your style prompt AND lyrics.")
    elif step.get("use_style"):
        parts.append("Style only — do NOT include lyrics.")
    elif step.get("use_lyrics"):
        parts.append("Lyrics only — do NOT include style.")
    parts.append(step.get("note", ""))

    return {
        "session_id": session_id,
        "step_number": current,
        "total_steps": total,
        "optional": is_optional,
        "action": step["action"],
        "mode": step.get("mode"),
        "use_style": step.get("use_style", False),
        "use_lyrics": step.get("use_lyrics", False),
        "extend_from": step.get("extend_from"),
        "pick": step.get("pick"),
        "note": step.get("note", ""),
        "success_signal": step.get("success_signal", ""),
        "instruction": " ".join(parts)
    }


# ─── MCP TOOL: log_step_result ───
async def log_step_result(session_id: str, step_number: int, quality_rating: int, notes: str = "") -> Dict[str, Any]:
    """Stores result, advances session state, checks for drift."""
    # Validate rating
    if not 1 <= quality_rating <= 5:
        return {"error": "quality_rating must be 1-5"}

    # Insert step result
    await execute(
        "INSERT INTO session_steps (session_id, step_number, action_taken, quality_rating, notes) "
        "VALUES ($1, $2, $3, $4, $5) "
        "ON CONFLICT (session_id, step_number) DO UPDATE SET "
        "action_taken = EXCLUDED.action_taken, quality_rating = EXCLUDED.quality_rating, notes = EXCLUDED.notes",
        session_id, step_number, "completed", quality_rating, notes
    )

    # Advance session to next step
    await execute(
        "UPDATE sessions SET current_step = current_step + 1 WHERE id = $1",
        session_id
    )

    # Check if session is complete
    session = await fetchrow(
        "SELECT s.current_step, array_length(wp.steps, 1) as total_steps "
        "FROM sessions s JOIN workflow_patterns wp ON s.workflow_pattern_id = wp.id "
        "WHERE s.id = $1", session_id
    )

    is_complete = session["current_step"] > session["total_steps"]
    if is_complete:
        await execute(
            "UPDATE sessions SET status = 'completed', completed_at = NOW() WHERE id = $1",
            session_id
        )

    return {
        "session_id": session_id,
        "step_logged": step_number,
        "quality_rating": quality_rating,
        "session_status": "completed" if is_complete else "active",
        "next_step": session["current_step"] if not is_complete else None
    }


# ─── MCP TOOL: start_session ───
async def start_session(user_id: str, workflow_id: Optional[str] = None) -> Dict[str, Any]:
    """Creates new session, returns session_id."""
    # Get active workflow if not specified
    if not workflow_id:
        wf = await fetchrow(
            "SELECT id FROM workflow_patterns WHERE status = 'active' ORDER BY vote_count DESC LIMIT 1"
        )
        if not wf:
            return {"error": "No active workflow pattern available"}
        workflow_id = str(wf["id"])

    # Check rate limit for free tier and contributors without 3 approved patterns
    user = await fetchrow("SELECT tier, contributor_submissions FROM users WHERE id = $1", user_id)
    if not user:
        return {"error": "User not found"}

    if user["tier"] == "free" or (user["tier"] == "contributor" and user["contributor_submissions"] < 3):
        current_month = await fetchrow(
            "SELECT session_count FROM session_usage WHERE user_id = $1 AND month_year = to_char(NOW(), 'YYYY-MM')",
            user_id
        )
        limit = 10
        tier_name = "Free" if user["tier"] == "free" else "Contributor (pending approval)"
        if current_month and current_month["session_count"] >= limit:
            return {"error": f"{tier_name} tier limit reached ({limit} sessions/month). Submit and get 3 patterns approved to unlock unlimited sessions, or upgrade to Pro."}

    # Create session
    session = await fetchrow(
        "INSERT INTO sessions (user_id, workflow_pattern_id, current_step, status) "
        "VALUES ($1, $2, 1, 'active') RETURNING id",
        user_id, workflow_id
    )

    # Increment usage counter
    await execute(
        "INSERT INTO session_usage (user_id, month_year, session_count) VALUES ($1, to_char(NOW(), 'YYYY-MM'), 1) "
        "ON CONFLICT (user_id, month_year) DO UPDATE SET session_count = session_usage.session_count + 1",
        user_id
    )

    return {
        "session_id": str(session["id"]),
        "workflow_id": workflow_id,
        "current_step": 1,
        "status": "active"
    }


# ─── MCP TOOL: generate_style_prompt ───
async def generate_style_prompt(description: str) -> Dict[str, Any]:
    """Takes plain English, returns structured style DNA prompt."""
    # Clean input
    description = description.strip()
    if not description:
        return {"error": "Empty description provided", "fix": "Provide a description of the style you want"}

    # Validate length
    if len(description) > MAX_PROMPT_LENGTH:
        return {"error": f"Prompt exceeds {MAX_PROMPT_LENGTH} character limit", "length": len(description)}

    # Check for artist names
    artists_found = _detect_artist_names(description)
    if artists_found:
        return {
            "error": "Artist/band names detected — not allowed",
            "violations": artists_found,
            "fix": "Remove artist references. Use genre, mood, and instrumentation instead."
        }

    # Count descriptors
    descriptor_count = _count_descriptors(description)
    if descriptor_count > MAX_DESCRIPTORS:
        return {
            "error": f"Too many descriptors ({descriptor_count}). Max is {MAX_DESCRIPTORS}",
            "fix": "Consolidate to 4-7 key descriptors: [Genre/Era] + [Mood] + [Instrumentation] + [Vocal Style]"
        }

    # Validate structure
    structure_issues = _validate_structure(description)

    # Build structured prompt
    parts = [p.strip() for p in description.split(",") if p.strip()]

    # Ensure vocal register
    has_vocal = any(v in description.lower() for v in [
        "baritone", "tenor", "alto", "soprano", "bass", "falsetto",
        "whisper", "spoken", "rap", "scream", "croon", "belt"
    ])

    if not has_vocal:
        parts.append(f"{DEFAULT_VOCAL_REGISTER} vocals")

    structured = ", ".join(parts)

    return {
        "structured_prompt": structured,
        "descriptor_count": len(parts),
        "character_count": len(structured),
        "structure_warnings": structure_issues,
        "ready_for_suno": len(structure_issues) == 0 and len(artists_found) == 0 and descriptor_count <= MAX_DESCRIPTORS
    }


# ─── MCP TOOL: build_lyric_structure ───
async def build_lyric_structure(raw_lyrics: str, sections: Optional[List[str]] = None) -> Dict[str, Any]:
    """Applies correct bracket tagging to raw lyrics."""
    if len(raw_lyrics) > MAX_LYRIC_LENGTH:
        return {"error": f"Lyrics exceed {MAX_LYRIC_LENGTH} character limit", "length": len(raw_lyrics)}

    issues = []
    fixed_lyrics = raw_lyrics

    # Check for all-caps (excluding section tags)
    lines = raw_lyrics.split("\n")
    for i, line in enumerate(lines):
        stripped = line.strip()
        if stripped and stripped.isupper() and not stripped.startswith("["):
            issues.append({"line": i + 1, "issue": "All-caps detected", "text": stripped[:50]})
            # Convert to title case for lyrics, keep brackets
            fixed_lyrics = fixed_lyrics.replace(stripped, stripped.title())

    # Check parenthesis misuse
    try:
        paren_pattern = re.compile(r'\(([^)]+)\)')
        for match in paren_pattern.finditer(raw_lyrics):
            content = match.group(1).lower()
            # Parentheses should only be for backing vocals / harmonies
            if not any(v in content for v in ["harmony", "backing", "echo", "ad-lib", "response", "repeat"]):
                issues.append({
                    "issue": "Parentheses may be misused",
                    "text": match.group(0),
                    "fix": "Use square brackets [ ] for delivery cues and instructions. Parentheses ( ) only for performed backing vocals."
                })
    except re.error:
        pass  # Fallback: skip regex validation on malformed input

    # Validate section tags
    if sections:
        for section in sections:
            base_tag = section.strip("[]").split()[0]
            if base_tag not in VALID_SECTION_TAGS:
                issues.append({
                    "issue": f"Unknown section tag: {section}",
                    "fix": f"Use valid tags: {', '.join(VALID_SECTION_TAGS)}"
                })

    # Ensure all delivery cues are bracketed
    # Find unbracketed emotional/delivery words
    try:
        for tag in EMOTIONAL_TAGS:
            # If emotional tag appears without brackets, flag it
            pattern = re.compile(r'(?<!\[)' + re.escape(tag) + r'(?!\])', re.IGNORECASE)
            if pattern.search(fixed_lyrics):
                issues.append({
                    "issue": f"Emotional tag '{tag}' not bracketed",
                    "fix": f"Wrap in brackets: [{tag}]"
                })
                fixed_lyrics = pattern.sub(f"[{tag}]", fixed_lyrics)
    except re.error:
        pass  # Fallback: skip emotional tag validation on malformed input

    return {
        "original": raw_lyrics,
        "structured": fixed_lyrics,
        "issues_found": issues,
        "issue_count": len(issues),
        "ready_for_suno": len(issues) == 0
    }


# ─── MCP TOOL: validate_prompt ───
async def validate_prompt(prompt_text: str) -> Dict[str, Any]:
    """Scores prompt against all rules, returns issues with fixes."""
    issues = []

    # Length check
    if len(prompt_text) > MAX_PROMPT_LENGTH:
        issues.append({
            "rule": "length",
            "issue": f"Exceeds {MAX_PROMPT_LENGTH} characters ({len(prompt_text)})",
            "fix": "Trim to under 1000 characters"
        })

    # Artist check
    artists = _detect_artist_names(prompt_text)
    if artists:
        issues.append({
            "rule": "no_artists",
            "issue": f"Artist references found: {', '.join(artists)}",
            "fix": "Remove all artist/band names. Use genre descriptors instead."
        })

    # Descriptor count
    count = _count_descriptors(prompt_text)
    if count > MAX_DESCRIPTORS:
        issues.append({
            "rule": "descriptor_limit",
            "issue": f"{count} descriptors (max {MAX_DESCRIPTORS})",
            "fix": "Consolidate to 4-7 descriptors. Focus on genre, mood, instrumentation, vocal style."
        })
    elif count < 4:
        issues.append({
            "rule": "descriptor_minimum",
            "issue": f"Only {count} descriptors (min 4 recommended)",
            "fix": "Add more detail: genre/era, mood, instrumentation, vocal style."
        })

    # Structure validation
    structure_issues = _validate_structure(prompt_text)
    for si in structure_issues:
        issues.append({"rule": "structure", **si})

    # Score
    score = max(0, 100 - (len(issues) * 15))

    return {
        "prompt": prompt_text,
        "score": score,
        "issues": issues,
        "issue_count": len(issues),
        "valid": len(issues) == 0,
        "descriptor_count": count
    }


# ─── MCP TOOL: save_style_prompt ───
async def save_style_prompt(user_id: str, name: str, prompt_text: str, genre_tags: Optional[List[str]] = None, mood_tags: Optional[List[str]] = None, bpm: Optional[int] = None) -> Dict[str, Any]:
    """Validates then stores in user's style library."""
    validation = await validate_prompt(prompt_text)
    if not validation["valid"]:
        return {
            "error": "Prompt validation failed",
            "validation": validation,
            "saved": False
        }

    result = await fetchrow(
        "INSERT INTO style_prompts (user_id, name, prompt_text, genre_tags, mood_tags, bpm) "
        "VALUES ($1, $2, $3, $4, $5, $6) RETURNING id",
        user_id, name, prompt_text,
        json.dumps(genre_tags or []),
        json.dumps(mood_tags or []),
        bpm
    )

    return {
        "saved": True,
        "style_id": str(result["id"]),
        "name": name,
        "validation_score": validation["score"]
    }


# ─── MCP TOOL: recall_style ───
async def recall_style(user_id: str, mood: Optional[str] = None, genre: Optional[str] = None) -> Dict[str, Any]:
    """Fuzzy search user's style prompt library."""
    query = "SELECT * FROM style_prompts WHERE user_id = $1"
    params = [user_id]

    if mood:
        query += f" AND mood_tags::text ILIKE ${len(params) + 1}"
        params.append(f"%{mood}%")
    if genre:
        query += f" AND genre_tags::text ILIKE ${len(params) + 1}"
        params.append(f"%{genre}%")

    query += " ORDER BY created_at DESC LIMIT 10"

    rows = await fetch(query, *params)

    return {
        "results": [
            {
                "id": str(r["id"]),
                "name": r["name"],
                "prompt": r["prompt_text"],
                "genres": r["genre_tags"],
                "moods": r["mood_tags"],
                "bpm": r["bpm"]
            }
            for r in rows
        ],
        "count": len(rows)
    }


# ─── MCP TOOL: save_client ───
async def save_client(user_id: str, name: str, vocal_type: Optional[str] = None, genres: Optional[List[str]] = None, bpm_range: Optional[Dict[str, int]] = None, emotional_register: Optional[str] = None) -> Dict[str, Any]:
    """Creates or updates client profile."""
    bpm_min = bpm_range.get("min") if bpm_range else None
    bpm_max = bpm_range.get("max") if bpm_range else None

    result = await fetchrow(
        "INSERT INTO client_profiles (user_id, name, vocal_type, preferred_genres, bpm_range_min, bpm_range_max, emotional_register) "
        "VALUES ($1, $2, $3, $4, $5, $6, $7) "
        "ON CONFLICT (user_id, name) DO UPDATE SET "
        "vocal_type = EXCLUDED.vocal_type, preferred_genres = EXCLUDED.preferred_genres, "
        "bpm_range_min = EXCLUDED.bpm_range_min, bpm_range_max = EXCLUDED.bpm_range_max, "
        "emotional_register = EXCLUDED.emotional_register "
        "RETURNING id",
        user_id, name, vocal_type,
        json.dumps(genres or []),
        bpm_min, bpm_max, emotional_register
    )

    return {
        "client_id": str(result["id"]),
        "name": name,
        "saved": True
    }


# ─── MCP TOOL: get_client_brief ───
async def get_client_brief(user_id: str, client_name: str, concept: str) -> Dict[str, Any]:
    """Returns ready-to-paste style prompt + lyric structure from client profile."""
    client = await fetchrow(
        "SELECT * FROM client_profiles WHERE user_id = $1 AND name = $2",
        user_id, client_name
    )
    if not client:
        return {"error": f"Client '{client_name}' not found"}

    # Build style prompt from profile
    genres = client["preferred_genres"] or []
    vocal = client["vocal_type"] or DEFAULT_VOCAL_REGISTER
    bpm_min = client["bpm_range_min"]
    bpm_max = client["bpm_range_max"]
    emotional = client["emotional_register"] or ""

    parts = []
    if genres:
        parts.append(f"{', '.join(genres)}")
    if emotional:
        parts.append(emotional)
    parts.append(f"{vocal} vocals")
    if bpm_min and bpm_max:
        parts.append(f"BPM {bpm_min}-{bpm_max}")

    style_prompt = ", ".join(parts)

    # Generate suggested lyric structure
    structure = f"""[Intro]
[Verse]
{concept}
[Chorus]
[Verse]
{concept} (expanded)
[Chorus]
[Bridge]
[Outro]"""

    return {
        "client_name": client_name,
        "style_prompt": style_prompt,
        "suggested_lyric_structure": structure,
        "bpm_range": f"{bpm_min}-{bpm_max}" if bpm_min and bpm_max else None,
        "ready_to_paste": True
    }


# ─── MCP TOOL: submit_workflow ───
async def submit_workflow(user_id: str, steps: List[Dict[str, Any]], notes: str = "", session_data: Optional[List[Dict]] = None) -> Dict[str, Any]:
    """Contributor tier only — submits new workflow pattern for community scoring."""
    user = await fetchrow("SELECT tier FROM users WHERE id = $1", user_id)
    if not user or user["tier"] not in ["contributor", "pro"]:
        return {"error": "Contributor or Pro tier required to submit workflow patterns"}

    # Rate limit: 3 submissions per day for contributors
    if user["tier"] == "contributor":
        today_count = await fetchrow(
            "SELECT COUNT(*) as cnt FROM workflow_patterns WHERE submitted_by = $1 AND created_at > NOW() - INTERVAL '1 day'",
            user_id
        )
        if today_count and today_count["cnt"] >= 3:
            return {"error": "Daily submission limit reached (3/day for contributors)"}

    result = await fetchrow(
        "INSERT INTO workflow_patterns (name, version, steps, status, submitted_by, consistency_score, vote_count) "
        "VALUES ($1, $2, $3, 'calibrating', $4, 0, 0) RETURNING id",
        f"custom-{user_id[:8]}", 1, json.dumps(steps), user_id
    )

    return {
        "submitted": True,
        "pattern_id": str(result["id"]),
        "status": "calibrating",
        "message": "Pattern submitted for community validation. It will enter active status after reaching 10 votes with avg score > 3.5."
    }


# ─── MCP TOOL: vote_on_pattern ───
async def vote_on_pattern(user_id: str, pattern_id: str, rating: int, session_evidence: Optional[Dict] = None) -> Dict[str, Any]:
    """Contributor tier only — vote on submitted pattern."""
    user = await fetchrow("SELECT tier FROM users WHERE id = $1", user_id)
    if not user or user["tier"] not in ["contributor", "pro"]:
        return {"error": "Contributor or Pro tier required to vote"}

    if not 1 <= rating <= 5:
        return {"error": "Rating must be 1-5"}

    await execute(
        "INSERT INTO workflow_votes (pattern_id, user_id, rating, session_evidence) "
        "VALUES ($1, $2, $3, $4) "
        "ON CONFLICT (pattern_id, user_id) DO UPDATE SET rating = EXCLUDED.rating, session_evidence = EXCLUDED.session_evidence",
        pattern_id, user_id, rating, json.dumps(session_evidence or {})
    )

    # Recalculate pattern score
    await execute(
        "UPDATE workflow_patterns SET "
        "vote_count = (SELECT COUNT(*) FROM workflow_votes WHERE pattern_id = $1), "
        "consistency_score = (SELECT AVG(rating) * 20 FROM workflow_votes WHERE pattern_id = $1) "
        "WHERE id = $1",
        pattern_id
    )

    # Auto-promote if threshold met
    pattern = await fetchrow(
        "SELECT vote_count, consistency_score FROM workflow_patterns WHERE id = $1",
        pattern_id
    )

    promoted = False
    if pattern["vote_count"] >= 10 and pattern["consistency_score"] >= 70:
        await execute(
            "UPDATE workflow_patterns SET status = 'active' WHERE id = $1 AND status = 'calibrating'",
            pattern_id
        )
        promoted = True

        # Increment contributor_submissions for the pattern submitter
        await execute(
            "UPDATE users SET contributor_submissions = contributor_submissions + 1 "
            "WHERE id = (SELECT submitted_by FROM workflow_patterns WHERE id = $1) "
            "AND tier = 'contributor'",
            pattern_id
        )

    return {
        "voted": True,
        "pattern_id": pattern_id,
        "your_rating": rating,
        "total_votes": pattern["vote_count"],
        "consistency_score": float(pattern["consistency_score"]),
        "promoted_to_active": promoted
    }


# ─── MCP TOOL: get_pattern_status ───
async def get_pattern_status() -> Dict[str, Any]:
    """Returns: Active / Drifting / Calibrating + explanation."""
    active = await fetchrow(
        "SELECT * FROM workflow_patterns WHERE status = 'active' ORDER BY vote_count DESC LIMIT 1"
    )
    calibrating = await fetchrow(
        "SELECT COUNT(*) as cnt FROM workflow_patterns WHERE status = 'calibrating'"
    )
    drifting = await fetchrow(
        "SELECT COUNT(*) as cnt FROM workflow_patterns WHERE status = 'drifting'"
    )

    drift = await check_drift_status()

    return {
        "active_pattern": {
            "id": str(active["id"]) if active else None,
            "name": active["name"] if active else None,
            "version": active["version"] if active else None,
            "consistency_score": float(active["consistency_score"]) if active else None
        },
        "patterns_calibrating": calibrating["cnt"] if calibrating else 0,
        "patterns_drifting": drifting["cnt"] if drifting else 0,
        "system_status": "drifting" if (drift and drift.get("detected")) else "healthy",
        "drift_warning": drift.get("warning") if drift else None,
        "explanation": (
            "The active workflow pattern is performing below baseline. "
            "Community contributors should submit new patterns."
            if (drift and drift.get("detected")) else
            "All systems normal. Active pattern is performing within expected range."
        )
    }
