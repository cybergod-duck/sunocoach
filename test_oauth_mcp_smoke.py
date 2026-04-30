"""
End-to-end smoke test for MCP + OAuth flow.

Delegates all check logic to the reusable ``debug.mcp_smoke.run_mcp_smoke()``
module so there's a single source of truth for both the CLI test and the
``/debug/mcp`` live dashboard.

Run: python test_oauth_mcp_smoke.py
"""

import asyncio
import sys
from debug.mcp_smoke import run_mcp_smoke

BASE = "http://localhost:8000"
# BASE = "https://sunocoach.onrender.com"  # Uncomment for staging


async def main():
    print("\n═══════════════════════════════════════════")
    print("  MCP + OAuth End-to-End Smoke Test")
    print(f"  Target: {BASE}")
    print("═══════════════════════════════════════════")

    checks = await run_mcp_smoke(BASE, timeout=30.0)

    passed = sum(1 for c in checks if c["ok"])
    failed = len(checks) - passed

    for c in checks:
        icon = "✅" if c["ok"] else "❌"
        detail = c.get("detail", "")
        if icon == "✅":
            print(f"  {icon} {c['name']}")
        else:
            print(f"  {icon} {c['name']} — {detail}")

    print("\n═══════════════════════════════════════════")
    total = len(checks)
    print(f"  Results: {passed}/{total} passed, {failed} failed")
    if failed > 0:
        print("  ❌ SOME TESTS FAILED — investigate before deploying")
        sys.exit(1)
    else:
        print("  ✅ ALL TESTS PASSED — ready to deploy!")
    print("═══════════════════════════════════════════\n")


if __name__ == "__main__":
    asyncio.run(main())
