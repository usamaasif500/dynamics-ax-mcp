
import hmac
import importlib.util
import os
import sys
from pathlib import Path

import uvicorn
from mcp.server.transport_security import TransportSecuritySettings

HERE = Path(__file__).parent
sys.path.insert(0, str(HERE))


spec = importlib.util.spec_from_file_location("my_mcp", HERE / "my-mcp.py")
my_mcp = importlib.util.module_from_spec(spec)
spec.loader.exec_module(my_mcp)

API_KEY = os.getenv("MCP_API_KEY", "").strip()
PORT = int(os.getenv("MCP_PORT", "8000"))
if len(API_KEY) < 24:
    sys.exit("Set MCP_API_KEY in .env (at least 24 random characters) before going online.")


app = my_mcp.mcp.streamable_http_app(
    streamable_http_path="/mcp",
    stateless_http=True,
    transport_security=TransportSecuritySettings(enable_dns_rebinding_protection=False),
)


def key_matches(given: str) -> bool:
    return hmac.compare_digest(given.encode(), API_KEY.encode())


class RequireKey:
    """ASGI middleware that enforces the API key."""

    def __init__(self, inner):
        self.inner = inner

    async def __call__(self, scope, receive, send):
        if scope["type"] != "http":
            return await self.inner(scope, receive, send)

        path = scope["path"]

        prefix = f"/{API_KEY}/"
        if path.startswith(prefix):
            scope = dict(scope, path="/" + path[len(prefix):])
            return await self.inner(scope, receive, send)

        # Key as Bearer token
        headers = dict(scope["headers"])
        auth = headers.get(b"authorization", b"").decode()
        if auth.startswith("Bearer ") and key_matches(auth[7:].strip()):
            return await self.inner(scope, receive, send)

        print(f"BLOCKED request to {path} from {scope.get('client')}")
        await send({"type": "http.response.start", "status": 401,
                    "headers": [(b"content-type", b"application/json")]})
        await send({"type": "http.response.body", "body": b'{"error":"unauthorized"}'})


if __name__ == "__main__":
    print(f"AX MCP web server on http://127.0.0.1:{PORT}/mcp  (key required)")
    uvicorn.run(RequireKey(app), host="127.0.0.1", port=PORT, log_level="warning")
