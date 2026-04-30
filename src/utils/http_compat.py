"""
HTTP compatibility layer for Cloudflare Workers Python.
Replaces FastAPI Request and HTTPException with plain Python.
"""


class HTTPException(Exception):
    """Simple HTTP exception compatible with FastAPI's HTTPException."""
    def __init__(self, status_code: int = 400, detail: str = ""):
        self.status_code = status_code
        self.detail = detail
        super().__init__(detail)


class MockRequest:
    """Mock request for local testing."""
    def __init__(self, headers=None, body=b""):
        self.headers = headers or {}
        self._body = body

    async def json(self):
        import json
        return json.loads(self._body)

    async def body(self):
        return self._body
