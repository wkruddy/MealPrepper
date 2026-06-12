from __future__ import annotations

import json
import logging
import urllib.parse
from http.server import BaseHTTPRequestHandler, HTTPServer
from typing import Any, Callable

import httpx

logger = logging.getLogger(__name__)

DEFAULT_OAUTH_PORT = 8787
DEFAULT_CALLBACK_PATH = "/slack/oauth/callback"
SLACK_OAUTH_ACCESS_URL = "https://slack.com/api/oauth.v2.access"


def build_redirect_uri(host: str, port: int, path: str = DEFAULT_CALLBACK_PATH) -> str:
    """Build a redirect URI for Slack OAuth."""
    host = host.strip() or "127.0.0.1"
    path = path if path.startswith("/") else f"/{path}"
    if host in {"localhost", "127.0.0.1"}:
        return f"http://{host}:{port}{path}"
    base = host if host.startswith("http") else f"https://{host}"
    return f"{base.rstrip('/')}{path}"


def slack_redirect_insecure_message(redirect_uri: str) -> str | None:
    """Return a warning when Slack likely rejects this redirect URI (non-HTTPS)."""
    uri = redirect_uri.strip()
    if not uri.lower().startswith("http://"):
        return None
    return (
        f"Redirect URI uses HTTP ({uri}). Slack apps reject non-HTTPS redirect URLs — "
        "use ngrok (ngrok http 8787) and set SLACK_OAUTH_REDIRECT_URI to "
        "https://YOUR-SUBDOMAIN.ngrok-free.app/slack/oauth/callback."
    )


def build_authorize_url(
    *,
    client_id: str,
    redirect_uri: str,
    scopes: list[str] | None = None,
    state: str = "",
) -> str:
    """Return the Slack OAuth v2 authorize URL with an explicit redirect_uri."""
    scope_list = scopes or [
        "chat:write",
        "channels:history",
        "groups:history",
        "app_mentions:read",
        "commands",
    ]
    params = {
        "client_id": client_id,
        "scope": ",".join(scope_list),
        "redirect_uri": redirect_uri,
    }
    if state:
        params["state"] = state
    return f"https://slack.com/oauth/v2/authorize?{urllib.parse.urlencode(params)}"


def exchange_oauth_code(
    *,
    client_id: str,
    client_secret: str,
    code: str,
    redirect_uri: str,
    timeout: float = 30.0,
) -> dict[str, Any]:
    """Exchange an OAuth authorization code for tokens via oauth.v2.access."""
    with httpx.Client(timeout=timeout) as client:
        response = client.post(
            SLACK_OAUTH_ACCESS_URL,
            data={
                "client_id": client_id,
                "client_secret": client_secret,
                "code": code,
                "redirect_uri": redirect_uri,
            },
        )
        response.raise_for_status()
        payload = response.json()
    if not payload.get("ok"):
        error = payload.get("error", "unknown_error")
        raise RuntimeError(f"Slack OAuth exchange failed: {error}")
    return payload


def format_oauth_result(payload: dict[str, Any]) -> str:
    """Human-readable summary of a successful oauth.v2.access response."""
    team = payload.get("team") or {}
    bot = payload.get("access_token") or (payload.get("bot") or {}).get("bot_access_token") or ""
    team_id = team.get("id", "")
    team_name = team.get("name", "")
    lines = [
        f"Workspace: {team_name} ({team_id})",
        f"Bot token: {bot}",
    ]
    if payload.get("incoming_webhook"):
        hook = payload["incoming_webhook"]
        lines.append(f"Webhook URL: {hook.get('url', '')}")
        lines.append(f"Webhook channel: {hook.get('channel', '')} ({hook.get('channel_id', '')})")
    return "\n".join(lines)


def run_oauth_server(
    *,
    host: str,
    port: int,
    redirect_uri: str,
    client_id: str,
    client_secret: str,
    on_success: Callable[[dict[str, Any]], str] | None = None,
    shutdown_after: int = 1,
) -> None:
    """Run a minimal HTTP server that handles the OAuth callback once."""
    results: list[dict[str, Any]] = []

    class OAuthHandler(BaseHTTPRequestHandler):
        def log_message(self, fmt: str, *args: Any) -> None:
            logger.info("oauth-server: " + fmt, *args)

        def do_GET(self) -> None:
            parsed = urllib.parse.urlparse(self.path)
            expected_path = urllib.parse.urlparse(redirect_uri).path
            if parsed.path != expected_path:
                self._respond(404, "Not found", "Unknown path.")
                return

            params = urllib.parse.parse_qs(parsed.query)
            if "error" in params:
                message = params.get("error", ["unknown"])[0]
                self._respond(400, "Authorization denied", f"Slack returned error: {message}")
                return

            code = (params.get("code") or [""])[0]
            if not code:
                self._respond(400, "Missing code", "No authorization code in callback URL.")
                return

            try:
                payload = exchange_oauth_code(
                    client_id=client_id,
                    client_secret=client_secret,
                    code=code,
                    redirect_uri=redirect_uri,
                )
            except Exception as exc:
                logger.exception("OAuth code exchange failed")
                self._respond(500, "Token exchange failed", str(exc))
                return

            results.append(payload)
            body = on_success(payload) if on_success else format_oauth_result(payload)
            self._respond(200, "MealPrepper Slack install complete", body)

            if shutdown_after and len(results) >= shutdown_after:
                raise _Shutdown

        def _respond(self, status: int, title: str, body: str) -> None:
            escaped = (
                body.replace("&", "&amp;")
                .replace("<", "&lt;")
                .replace(">", "&gt;")
            )
            html = f"""<!DOCTYPE html>
<html><head><title>{title}</title></head>
<body>
<h1>{title}</h1>
<pre>{escaped}</pre>
<p>You can close this tab and return to the terminal.</p>
</body></html>"""
            encoded = html.encode("utf-8")
            self.send_response(status)
            self.send_header("Content-Type", "text/html; charset=utf-8")
            self.send_header("Content-Length", str(len(encoded)))
            self.end_headers()
            self.wfile.write(encoded)

    class _Shutdown(Exception):
        pass

    server = HTTPServer((host, port), OAuthHandler)
    logger.info("OAuth callback listening on %s (redirect_uri=%s)", f"http://{host}:{port}", redirect_uri)
    try:
        while len(results) < shutdown_after:
            server.handle_request()
    except _Shutdown:
        pass
    finally:
        server.server_close()

    if not results:
        raise RuntimeError("OAuth server stopped without receiving a callback.")
