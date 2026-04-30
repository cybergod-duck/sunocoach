# MCP Registry Submission Guide

Your server is live at: **https://sunocoach.onrender.com**

All files are ready. Just copy-paste into each registry tomorrow.

---

## 1. Smithery.ai

**URL:** https://smithery.ai

**Steps:**
1. Sign in with GitHub
2. Click "Add Server"
3. Paste: `https://sunocoach.onrender.com`
4. Smithery auto-detects from `/.well-known/oauth-authorization-server`
5. Submit

**File ready:** [`smithery.yaml`](smithery.yaml:1)

---

## 2. MCP Registry

**URL:** https://mcp-registry.com

**Steps:**
1. Click "Submit Server"
2. Upload or paste contents of [`mcp-registry.json`](mcp-registry.json:1)
3. Submit

**Key fields already filled:**
- Name: `sunocoach`
- URL: `https://sunocoach.onrender.com`
- Auth: `oauth2`
- 14 tools listed with descriptions

---

## 3. PulseMCP

**URL:** https://pulsemcp.com

**Steps:**
1. Click "Submit"
2. Fill form:
   - **Name:** SunoCoach
   - **URL:** https://sunocoach.onrender.com
   - **Description:** AI music creation workflow coach with community-validated patterns, style prompt engineering, and lyric structure tools.
   - **Categories:** music, creativity, audio
   - **Auth Type:** OAuth 2.0
3. Submit

---

## 4. MCP.so

**URL:** https://mcp.so

**Steps:**
1. Go to community submit form
2. Paste server URL: `https://sunocoach.onrender.com`
3. Add description and categories
4. Submit

**Note:** Biggest directory — 19,700+ servers listed.

---

## 5. Glama

**URL:** https://glama.ai/mcp

**Steps:**
1. Go to submit form
2. Enter server URL and metadata
3. Glama auto-detects capabilities from your endpoints
4. Submit

**Note:** Broad automated coverage, good for discovery.

---

## 6. MCP.directory

**URL:** https://mcp.directory/submit

**Steps:**
1. Enter your GitHub repo URL: `https://github.com/cybergod-duck/sunocoach`
2. MCP.directory auto-pulls metadata from `mcp-registry.json`
3. Goes live in ~24 hours

**Note:** Zero manual work — just paste repo URL.

---

## 7. MCPMarket

**URL:** https://mcpmarket.com

**Steps:**
1. Click "Submit Server"
2. Fill form with URL and description
3. Submit

**Note:** Has a trending leaderboard — good for visibility.

---

## 8. GitHub MCP Registry

**URL:** https://github.blog/mcp-registry

**Steps:**
1. Submit for Copilot + agent discovery
2. Paste server URL and OAuth details
3. Submit

**Note:** Official GitHub registry — high trust signal.

---

## 9. MCPServers.org

**URL:** https://mcpservers.org

**Steps:**
1. Go to submit page
2. Enter server URL: `https://sunocoach.onrender.com`
3. Add description and tags
4. Submit

**Note:** Good for community/unofficial servers.

---

## Quick Reference

| Registry | URL | How to Submit | Notes |
|----------|-----|---------------|-------|
| Smithery | https://smithery.ai | Paste server URL | Auto-detects OAuth config |
| MCP Registry | https://mcp-registry.com | Upload `mcp-registry.json` | 14 tools pre-documented |
| PulseMCP | https://pulsemcp.com | Fill web form | Good for SEO |
| MCP.so | https://mcp.so | Community submit form | **Biggest directory** — 19,700+ servers |
| Glama | https://glama.ai/mcp | Submit via form | Broad automated coverage |
| MCP.directory | https://mcp.directory/submit | Paste GitHub repo URL | Auto-pulls metadata, live in 24hrs |
| MCPMarket | https://mcpmarket.com | Submit form | Trending leaderboard |
| GitHub MCP Registry | https://github.blog/mcp-registry | Submit for Copilot | Official, high trust |
| MCPServers.org | https://mcpservers.org | Submit page | Community-focused |

---

## Verification Checklist

Before submitting, verify:
- [ ] `https://sunocoach.onrender.com/` returns JSON
- [ ] `https://sunocoach.onrender.com/health` returns status
- [ ] `https://sunocoach.onrender.com/.well-known/oauth-authorization-server` returns OAuth config

All registry files are in the repo root and already contain the correct production URL.
