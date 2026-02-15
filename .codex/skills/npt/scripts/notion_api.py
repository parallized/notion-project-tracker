#!/usr/bin/env python3
"""NPT helper for Notion OAuth and exact data source queries.

Commands:
  - oauth-login: open browser and complete OAuth exchange automatically.
  - oauth-start: build OAuth authorization URL and persist temporary state.
  - oauth-exchange: exchange OAuth code for tokens and persist token bundle.
  - oauth-refresh: refresh a stored OAuth token.
  - oauth-token: print a valid access token (auto-refresh when possible).
  - query-active: exact query against /v1/data_sources/{id}/query.
"""

from __future__ import annotations

import argparse
import base64
import datetime as dt
import http.server
import json
import os
import pathlib
import shutil
import subprocess
import sys
import threading
import urllib.error
import urllib.parse
import urllib.request
import uuid
import webbrowser
from typing import Any, Dict, List, Optional, Tuple


DEFAULT_NOTION_VERSION = "2025-09-03"
DEFAULT_INCLUDE_STATUSES = ["待办", "队列中", "进行中", "需要更多信息", "已阻塞"]
DEFAULT_ACTIVE_STATUSES = ["待办", "队列中", "进行中", "需要更多信息"]
DEFAULT_BLOCKED_STATUS = "已阻塞"
AUTH_URL = "https://api.notion.com/v1/oauth/authorize"
TOKEN_URL = "https://api.notion.com/v1/oauth/token"
KEYCHAIN_SERVICE = "npt.notion.oauth"
KEYCHAIN_ACCOUNT = "default"
DEFAULT_OAUTH_TIMEOUT_SECONDS = 180


class NptError(Exception):
    """User-facing, non-stacktrace error."""


class HttpError(Exception):
    """HTTP error wrapper with response details."""

    def __init__(self, status: int, body: str):
        super().__init__(f"HTTP {status}: {body}")
        self.status = status
        self.body = body


def utc_now() -> dt.datetime:
    return dt.datetime.now(dt.timezone.utc)


def to_iso_z(value: dt.datetime) -> str:
    return value.astimezone(dt.timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z")


def parse_iso(value: str) -> Optional[dt.datetime]:
    if not value:
        return None
    text = value.strip()
    if text.endswith("Z"):
        text = text[:-1] + "+00:00"
    try:
        parsed = dt.datetime.fromisoformat(text)
    except ValueError:
        return None
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=dt.timezone.utc)
    return parsed.astimezone(dt.timezone.utc)


def ensure_parent(path: pathlib.Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)


def dump_json(data: Dict[str, Any]) -> str:
    return json.dumps(data, ensure_ascii=False, indent=2)


def read_json_file(path: pathlib.Path) -> Optional[Dict[str, Any]]:
    if not path.exists():
        return None
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except json.JSONDecodeError as exc:
        raise NptError(f"Invalid JSON file: {path} ({exc})") from exc


def write_json_file(path: pathlib.Path, data: Dict[str, Any], secure: bool = True) -> None:
    ensure_parent(path)
    path.write_text(dump_json(data) + "\n", encoding="utf-8")
    if secure:
        os.chmod(path, 0o600)


def command_exists(name: str) -> bool:
    return shutil.which(name) is not None


def choose_store_mode() -> str:
    requested = (os.getenv("NPT_TOKEN_STORE") or "auto").strip().lower()
    if requested not in {"auto", "file", "keychain"}:
        raise NptError("NPT_TOKEN_STORE must be one of: auto, file, keychain")
    if requested == "file":
        return "file"
    if requested == "keychain":
        if sys.platform != "darwin" or not command_exists("security"):
            raise NptError("NPT_TOKEN_STORE=keychain requires macOS `security` CLI")
        return "keychain"
    if sys.platform == "darwin" and command_exists("security"):
        return "keychain"
    return "file"


class TokenStore:
    def __init__(self) -> None:
        config_dir = pathlib.Path(os.getenv("NPT_CONFIG_DIR", "~/.config/npt")).expanduser()
        self.token_path = pathlib.Path(
            os.getenv("NPT_OAUTH_TOKEN_PATH", str(config_dir / "notion-oauth.json"))
        ).expanduser()
        self.state_path = pathlib.Path(
            os.getenv("NPT_OAUTH_STATE_PATH", str(config_dir / "notion-oauth-state.json"))
        ).expanduser()
        self.mode = choose_store_mode()

    def save_state(self, state: str, redirect_uri: str) -> None:
        payload = {
            "state": state,
            "redirect_uri": redirect_uri,
            "created_at": to_iso_z(utc_now()),
        }
        write_json_file(self.state_path, payload, secure=True)

    def load_state(self) -> Optional[Dict[str, Any]]:
        return read_json_file(self.state_path)

    def load_token(self) -> Optional[Dict[str, Any]]:
        if self.mode == "keychain":
            return self._keychain_load()
        return read_json_file(self.token_path)

    def save_token(self, token_bundle: Dict[str, Any]) -> None:
        if self.mode == "keychain":
            self._keychain_save(token_bundle)
            return
        write_json_file(self.token_path, token_bundle, secure=True)

    def _keychain_load(self) -> Optional[Dict[str, Any]]:
        proc = subprocess.run(
            [
                "security",
                "find-generic-password",
                "-s",
                KEYCHAIN_SERVICE,
                "-a",
                KEYCHAIN_ACCOUNT,
                "-w",
            ],
            capture_output=True,
            text=True,
        )
        if proc.returncode != 0:
            return None
        payload = proc.stdout.strip()
        if not payload:
            return None
        try:
            return json.loads(payload)
        except json.JSONDecodeError as exc:
            raise NptError("Stored keychain token payload is invalid JSON") from exc

    def _keychain_save(self, token_bundle: Dict[str, Any]) -> None:
        payload = dump_json(token_bundle)
        proc = subprocess.run(
            [
                "security",
                "add-generic-password",
                "-U",
                "-s",
                KEYCHAIN_SERVICE,
                "-a",
                KEYCHAIN_ACCOUNT,
                "-w",
                payload,
            ],
            capture_output=True,
            text=True,
        )
        if proc.returncode != 0:
            message = (proc.stderr or proc.stdout).strip() or "unknown keychain error"
            raise NptError(f"Failed to save token to macOS Keychain: {message}")


def get_oauth_credentials(redirect_override: Optional[str] = None) -> Tuple[str, str, str]:
    client_id = os.getenv("NOTION_OAUTH_CLIENT_ID") or os.getenv("NPT_NOTION_CLIENT_ID")
    client_secret = os.getenv("NOTION_OAUTH_CLIENT_SECRET") or os.getenv("NPT_NOTION_CLIENT_SECRET")
    redirect_uri = redirect_override or os.getenv("NOTION_OAUTH_REDIRECT_URI") or os.getenv(
        "NPT_NOTION_REDIRECT_URI"
    )
    if not client_id:
        raise NptError("Missing NOTION_OAUTH_CLIENT_ID (or NPT_NOTION_CLIENT_ID)")
    if not client_secret:
        raise NptError("Missing NOTION_OAUTH_CLIENT_SECRET (or NPT_NOTION_CLIENT_SECRET)")
    if not redirect_uri:
        raise NptError("Missing NOTION_OAUTH_REDIRECT_URI (or NPT_NOTION_REDIRECT_URI)")
    return client_id.strip(), client_secret.strip(), redirect_uri.strip()


def request_json(
    method: str,
    url: str,
    headers: Optional[Dict[str, str]] = None,
    body: Optional[Dict[str, Any]] = None,
) -> Dict[str, Any]:
    payload = None
    merged_headers: Dict[str, str] = {"Accept": "application/json"}
    if headers:
        merged_headers.update(headers)
    if body is not None:
        payload = json.dumps(body).encode("utf-8")
        merged_headers.setdefault("Content-Type", "application/json")
    request = urllib.request.Request(url=url, data=payload, headers=merged_headers, method=method)
    try:
        with urllib.request.urlopen(request) as resp:
            raw = resp.read().decode("utf-8")
            if not raw:
                return {}
            return json.loads(raw)
    except urllib.error.HTTPError as exc:
        err_body = exc.read().decode("utf-8")
        raise HttpError(exc.code, err_body) from exc
    except urllib.error.URLError as exc:
        raise NptError(f"Network error: {exc}") from exc
    except json.JSONDecodeError as exc:
        raise NptError(f"Invalid JSON response from {url}: {exc}") from exc


def make_basic_auth_header(client_id: str, client_secret: str) -> str:
    token = base64.b64encode(f"{client_id}:{client_secret}".encode("utf-8")).decode("ascii")
    return f"Basic {token}"


def build_auth_url(client_id: str, redirect_uri: str, state: str, owner: str) -> str:
    query = urllib.parse.urlencode(
        {
            "client_id": client_id,
            "redirect_uri": redirect_uri,
            "response_type": "code",
            "owner": owner,
            "state": state,
        }
    )
    return f"{AUTH_URL}?{query}"


def enrich_token_bundle(raw: Dict[str, Any], source: str) -> Dict[str, Any]:
    now = utc_now()
    out: Dict[str, Any] = dict(raw)
    out["source"] = source
    out["acquired_at"] = to_iso_z(now)
    expires_in = raw.get("expires_in")
    if isinstance(expires_in, int) and expires_in > 0:
        out["expires_at"] = to_iso_z(now + dt.timedelta(seconds=expires_in))
    return out


def exchange_code(client_id: str, client_secret: str, code: str, redirect_uri: str) -> Dict[str, Any]:
    headers = {"Authorization": make_basic_auth_header(client_id, client_secret)}
    body = {
        "grant_type": "authorization_code",
        "code": code,
        "redirect_uri": redirect_uri,
    }
    raw = request_json("POST", TOKEN_URL, headers=headers, body=body)
    if "access_token" not in raw:
        raise NptError(f"OAuth exchange returned no access_token: {raw}")
    return enrich_token_bundle(raw, source="oauth_exchange")


def refresh_token(client_id: str, client_secret: str, refresh: str) -> Dict[str, Any]:
    headers = {"Authorization": make_basic_auth_header(client_id, client_secret)}
    body = {"grant_type": "refresh_token", "refresh_token": refresh}
    raw = request_json("POST", TOKEN_URL, headers=headers, body=body)
    if "access_token" not in raw:
        raise NptError(f"Token refresh returned no access_token: {raw}")
    return enrich_token_bundle(raw, source="oauth_refresh")


def token_expiring_soon(token_bundle: Dict[str, Any], within_seconds: int = 120) -> bool:
    expires_at = parse_iso(str(token_bundle.get("expires_at", "")))
    if expires_at is None:
        return False
    return expires_at <= (utc_now() + dt.timedelta(seconds=within_seconds))


def parse_redirect_url(url: str) -> Dict[str, str]:
    parsed = urllib.parse.urlparse(url)
    query = urllib.parse.parse_qs(parsed.query)
    out: Dict[str, str] = {}
    for key in ("code", "state", "error", "error_description"):
        value = query.get(key)
        if value and value[0]:
            out[key] = value[0]
    return out


def parse_local_callback(redirect_uri: str) -> Tuple[str, int, str]:
    parsed = urllib.parse.urlparse(redirect_uri)
    if parsed.scheme != "http":
        raise NptError("Auto OAuth callback requires http redirect URI (for local callback server).")
    host = parsed.hostname or ""
    if host not in {"localhost", "127.0.0.1", "::1"}:
        raise NptError(
            "Auto OAuth callback requires localhost redirect URI (localhost/127.0.0.1/::1). "
            "Use manual oauth-start/oauth-exchange for remote redirect URIs."
        )
    if parsed.port is None:
        raise NptError("Redirect URI must include an explicit port for auto OAuth callback.")
    path = parsed.path or "/"
    return host, parsed.port, path


def open_authorization_url(url: str) -> bool:
    try:
        if webbrowser.open(url, new=2, autoraise=True):
            return True
    except Exception:
        pass
    launcher: Optional[List[str]] = None
    if sys.platform == "darwin" and command_exists("open"):
        launcher = ["open", url]
    elif sys.platform.startswith("linux") and command_exists("xdg-open"):
        launcher = ["xdg-open", url]
    elif sys.platform.startswith("win"):
        launcher = ["cmd", "/c", "start", "", url]
    if not launcher:
        return False
    try:
        subprocess.Popen(launcher, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        return True
    except Exception:
        return False


def wait_for_callback(redirect_uri: str, expected_state: str, timeout_seconds: int) -> Dict[str, str]:
    host, port, callback_path = parse_local_callback(redirect_uri)
    callback_data: Dict[str, str] = {}
    callback_received = threading.Event()

    class CallbackHandler(http.server.BaseHTTPRequestHandler):
        def do_GET(self) -> None:  # noqa: N802
            parsed = urllib.parse.urlparse(self.path)
            if parsed.path != callback_path:
                self.send_response(404)
                self.send_header("Content-Type", "text/plain; charset=utf-8")
                self.end_headers()
                self.wfile.write(b"Not Found")
                return
            query = urllib.parse.parse_qs(parsed.query)
            for key in ("code", "state", "error", "error_description"):
                values = query.get(key)
                if values and values[0]:
                    callback_data[key] = values[0]
            if callback_data.get("error"):
                body = "NPT OAuth failed. You can close this tab."
            else:
                body = "NPT OAuth success. You can close this tab and return to terminal."
            self.send_response(200)
            self.send_header("Content-Type", "text/html; charset=utf-8")
            self.end_headers()
            html = "<!doctype html><html><body><p>" + body + "</p></body></html>"
            self.wfile.write(html.encode("utf-8"))
            callback_received.set()

        def log_message(self, fmt: str, *args: Any) -> None:  # noqa: A003
            return

    try:
        server = http.server.ThreadingHTTPServer((host, port), CallbackHandler)
    except OSError as exc:
        raise NptError(f"Cannot bind local callback server at {host}:{port}: {exc}") from exc

    thread = threading.Thread(target=server.serve_forever, kwargs={"poll_interval": 0.2}, daemon=True)
    thread.start()
    callback_received.wait(timeout=timeout_seconds)
    server.shutdown()
    thread.join(timeout=2)
    server.server_close()

    if not callback_received.is_set():
        raise NptError(
            f"Timed out waiting for OAuth callback after {timeout_seconds}s at {redirect_uri}. "
            "Fallback: run oauth-start then oauth-exchange manually."
        )
    if callback_data.get("error"):
        detail = callback_data.get("error_description", callback_data["error"])
        raise NptError(f"OAuth authorization failed: {detail}")
    returned_state = callback_data.get("state", "")
    if expected_state and returned_state != expected_state:
        raise NptError("OAuth state mismatch. Re-run oauth-login.")
    if not callback_data.get("code"):
        raise NptError("OAuth callback missing code parameter.")
    return callback_data


def flatten_text(rich: List[Dict[str, Any]]) -> str:
    parts: List[str] = []
    for node in rich:
        plain = node.get("plain_text")
        if plain:
            parts.append(str(plain))
            continue
        text = node.get("text", {}).get("content")
        if text:
            parts.append(str(text))
    return "".join(parts).strip()


def extract_status(page: Dict[str, Any], prop_name: str) -> str:
    props = page.get("properties", {})
    if not isinstance(props, dict):
        return ""
    node = props.get(prop_name)
    if not isinstance(node, dict):
        return ""
    status = node.get("status")
    if isinstance(status, dict) and status.get("name"):
        return str(status["name"])
    select = node.get("select")
    if isinstance(select, dict) and select.get("name"):
        return str(select["name"])
    return ""


def extract_title(page: Dict[str, Any], preferred_prop: str) -> str:
    props = page.get("properties", {})
    if not isinstance(props, dict):
        return "(untitled)"
    preferred = props.get(preferred_prop)
    if isinstance(preferred, dict):
        title_nodes = preferred.get("title")
        if isinstance(title_nodes, list):
            text = flatten_text(title_nodes)
            if text:
                return text
    for _, prop in props.items():
        if not isinstance(prop, dict):
            continue
        if prop.get("type") == "title" and isinstance(prop.get("title"), list):
            text = flatten_text(prop["title"])
            if text:
                return text
    return "(untitled)"


def simplify_page(page: Dict[str, Any], status_property: str, title_property: str) -> Dict[str, Any]:
    return {
        "id": page.get("id", ""),
        "url": page.get("url", ""),
        "created_time": page.get("created_time", ""),
        "last_edited_time": page.get("last_edited_time", ""),
        "status": extract_status(page, status_property),
        "title": extract_title(page, title_property),
    }


def query_data_source(
    access_token: str,
    notion_version: str,
    data_source_id: str,
    status_property: str,
    include_statuses: List[str],
    page_size: int,
) -> List[Dict[str, Any]]:
    endpoint = f"https://api.notion.com/v1/data_sources/{data_source_id}/query"
    headers = {
        "Authorization": f"Bearer {access_token}",
        "Notion-Version": notion_version,
        "Content-Type": "application/json",
    }
    filter_or = [{"property": status_property, "select": {"equals": value}} for value in include_statuses]
    pages: List[Dict[str, Any]] = []
    cursor: Optional[str] = None
    while True:
        body: Dict[str, Any] = {
            "page_size": page_size,
            "result_type": "page",
            "filter": {"or": filter_or},
        }
        if cursor:
            body["start_cursor"] = cursor
        response = request_json("POST", endpoint, headers=headers, body=body)
        results = response.get("results", [])
        if isinstance(results, list):
            pages.extend([item for item in results if isinstance(item, dict)])
        has_more = bool(response.get("has_more"))
        next_cursor = response.get("next_cursor")
        if not has_more or not next_cursor:
            break
        cursor = str(next_cursor)
    return pages


def split_text_chunks(text: str, max_chars: int = 1800) -> List[str]:
    cleaned = text.replace("\r\n", "\n")
    if not cleaned:
        return []
    chunks: List[str] = []
    start = 0
    while start < len(cleaned):
        chunks.append(cleaned[start : start + max_chars])
        start += max_chars
    return chunks


def build_comment_rich_text(text: str) -> List[Dict[str, Any]]:
    chunks = split_text_chunks(text)
    if not chunks:
        raise NptError("Comment text is empty.")
    return [{"type": "text", "text": {"content": chunk}} for chunk in chunks]


def create_page_comment(
    access_token: str,
    notion_version: str,
    page_id: str,
    text: str,
) -> Dict[str, Any]:
    endpoint = "https://api.notion.com/v1/comments"
    headers = {
        "Authorization": f"Bearer {access_token}",
        "Notion-Version": notion_version,
        "Content-Type": "application/json",
    }
    body = {
        "parent": {"page_id": page_id},
        "rich_text": build_comment_rich_text(text),
    }
    return request_json("POST", endpoint, headers=headers, body=body)


def maybe_refresh_store_token(store: TokenStore, token_bundle: Dict[str, Any]) -> Dict[str, Any]:
    if not token_expiring_soon(token_bundle):
        return token_bundle
    refresh = token_bundle.get("refresh_token")
    if not refresh:
        return token_bundle
    client_id, client_secret, _ = get_oauth_credentials()
    refreshed = refresh_token(client_id, client_secret, str(refresh))
    if "refresh_token" not in refreshed:
        refreshed["refresh_token"] = refresh
    store.save_token(refreshed)
    return refreshed


def resolve_query_token(
    store: TokenStore,
    explicit_token: Optional[str],
) -> Tuple[str, str, Optional[Dict[str, Any]]]:
    if explicit_token:
        return explicit_token, "explicit_token", None
    api_key = os.getenv("NOTION_API_KEY")
    if api_key:
        return api_key, "notion_api_key", None
    raise NptError(
        "No query token found. Set NOTION_API_KEY."
    )


def cmd_oauth_start(args: argparse.Namespace, store: TokenStore) -> None:
    owner = args.owner or "user"
    if owner not in {"user", "workspace"}:
        raise NptError("--owner must be user or workspace")
    client_id, _, redirect_uri = get_oauth_credentials(args.redirect_uri)
    state = args.state or uuid.uuid4().hex
    auth_url = build_auth_url(client_id, redirect_uri, state, owner)
    if not args.no_store_state:
        store.save_state(state, redirect_uri)
    output = {
        "authorization_url": auth_url,
        "state": state,
        "redirect_uri": redirect_uri,
        "owner": owner,
        "state_saved": not args.no_store_state,
        "store_mode": store.mode,
    }
    if args.json:
        print(dump_json(output))
        return
    print("Open this URL in your browser and approve access:")
    print(auth_url)
    print("")
    print(f"State: {state}")
    print(f"Redirect URI: {redirect_uri}")
    print("After redirect, run oauth-exchange with --redirect-url or --code.")


def cmd_oauth_login(args: argparse.Namespace, store: TokenStore) -> None:
    owner = args.owner or "user"
    if owner not in {"user", "workspace"}:
        raise NptError("--owner must be user or workspace")
    if args.timeout < 10:
        raise NptError("--timeout must be at least 10 seconds")

    client_id, client_secret, redirect_uri = get_oauth_credentials(args.redirect_uri)
    state = args.state or uuid.uuid4().hex
    auth_url = build_auth_url(client_id, redirect_uri, state, owner)
    if not args.no_store_state:
        store.save_state(state, redirect_uri)

    opened = False
    if not args.no_open:
        opened = open_authorization_url(auth_url)

    if not args.json:
        if opened:
            print("Opened browser for Notion OAuth authorization.")
        else:
            print("Could not auto-open browser. Open this URL manually:")
            print(auth_url)
        print(f"Waiting for callback at {redirect_uri} (timeout: {args.timeout}s)...")

    callback = wait_for_callback(redirect_uri=redirect_uri, expected_state=state, timeout_seconds=args.timeout)
    token_bundle = exchange_code(client_id, client_secret, callback["code"], redirect_uri)
    store.save_token(token_bundle)

    output = {
        "ok": True,
        "mode": "oauth_login",
        "store_mode": store.mode,
        "opened_browser": opened,
        "authorization_url": auth_url if args.json else "",
        "workspace_name": token_bundle.get("workspace_name", ""),
        "workspace_id": token_bundle.get("workspace_id", ""),
        "owner_type": token_bundle.get("owner", {}).get("type", ""),
        "has_refresh_token": bool(token_bundle.get("refresh_token")),
        "expires_at": token_bundle.get("expires_at", ""),
    }
    print(dump_json(output))


def cmd_oauth_exchange(args: argparse.Namespace, store: TokenStore) -> None:
    redirect_info: Dict[str, str] = {}
    if args.redirect_url:
        redirect_info = parse_redirect_url(args.redirect_url)
        if redirect_info.get("error"):
            detail = redirect_info.get("error_description", redirect_info["error"])
            raise NptError(f"OAuth authorization failed: {detail}")
    code = args.code or redirect_info.get("code")
    if not code:
        raise NptError("Missing OAuth code. Provide --code or --redirect-url.")
    returned_state = redirect_info.get("state")
    expected_state = args.state
    if expected_state is None and not args.skip_state_check:
        cached = store.load_state()
        if cached and cached.get("state"):
            expected_state = str(cached["state"])
    if not args.skip_state_check and expected_state and returned_state and expected_state != returned_state:
        raise NptError("OAuth state mismatch. Re-run oauth-start and retry.")
    client_id, client_secret, redirect_uri = get_oauth_credentials(args.redirect_uri)
    token_bundle = exchange_code(client_id, client_secret, code, redirect_uri)
    store.save_token(token_bundle)
    print(
        dump_json(
            {
                "ok": True,
                "store_mode": store.mode,
                "workspace_name": token_bundle.get("workspace_name", ""),
                "workspace_id": token_bundle.get("workspace_id", ""),
                "owner_type": token_bundle.get("owner", {}).get("type", ""),
                "has_refresh_token": bool(token_bundle.get("refresh_token")),
                "expires_at": token_bundle.get("expires_at", ""),
            }
        )
    )


def cmd_oauth_refresh(_: argparse.Namespace, store: TokenStore) -> None:
    token_bundle = store.load_token()
    if not token_bundle:
        raise NptError("No stored OAuth token. Run oauth-start/oauth-exchange first.")
    refresh = token_bundle.get("refresh_token")
    if not refresh:
        raise NptError("Stored token has no refresh_token. Re-authorize via oauth-start/oauth-exchange.")
    client_id, client_secret, _ = get_oauth_credentials()
    refreshed = refresh_token(client_id, client_secret, str(refresh))
    if "refresh_token" not in refreshed:
        refreshed["refresh_token"] = refresh
    store.save_token(refreshed)
    print(
        dump_json(
            {
                "ok": True,
                "store_mode": store.mode,
                "workspace_name": refreshed.get("workspace_name", ""),
                "workspace_id": refreshed.get("workspace_id", ""),
                "expires_at": refreshed.get("expires_at", ""),
                "has_refresh_token": bool(refreshed.get("refresh_token")),
            }
        )
    )


def cmd_oauth_token(_: argparse.Namespace, store: TokenStore) -> None:
    token_bundle = store.load_token()
    if not token_bundle:
        raise NptError("No stored OAuth token. Run oauth-start/oauth-exchange first.")
    token_bundle = maybe_refresh_store_token(store, token_bundle)
    access_token = token_bundle.get("access_token")
    if not access_token:
        raise NptError("Stored token bundle has no access_token.")
    print(access_token)


def cmd_query_active(args: argparse.Namespace, store: TokenStore) -> None:
    include_statuses = args.include_statuses or DEFAULT_INCLUDE_STATUSES
    active_statuses = args.active_statuses or DEFAULT_ACTIVE_STATUSES
    blocked_status = args.blocked_status or DEFAULT_BLOCKED_STATUS
    notion_version = args.notion_version or os.getenv("NOTION_VERSION") or DEFAULT_NOTION_VERSION

    access_token, source, _ = resolve_query_token(store, args.access_token)
    while True:
        try:
            pages = query_data_source(
                access_token=access_token,
                notion_version=notion_version,
                data_source_id=args.data_source_id,
                status_property=args.status_property,
                include_statuses=include_statuses,
                page_size=args.page_size,
            )
            break
        except HttpError as exc:
            raise

    simplified = [simplify_page(page, args.status_property, args.title_property) for page in pages]
    active = [item for item in simplified if item.get("status") in set(active_statuses)]
    blocked = [item for item in simplified if item.get("status") == blocked_status]
    skipped = len(simplified) - len(active) - len(blocked)

    output = {
        "query_confidence": "high",
        "source": source,
        "data_source_id": args.data_source_id,
        "status_property": args.status_property,
        "counts": {
            "total": len(simplified),
            "active": len(active),
            "blocked": len(blocked),
            "skipped": skipped,
        },
        "active": active,
        "blocked": blocked,
        "all": simplified if args.include_all else [],
    }
    print(dump_json(output))


def cmd_create_comment(args: argparse.Namespace, store: TokenStore) -> None:
    notion_version = args.notion_version or os.getenv("NOTION_VERSION") or DEFAULT_NOTION_VERSION
    access_token, source, _ = resolve_query_token(store, args.access_token)

    text = ""
    if args.text is not None:
        text = args.text
    elif args.text_file is not None:
        path = pathlib.Path(args.text_file).expanduser()
        if not path.exists():
            raise NptError(f"Comment text file not found: {path}")
        text = path.read_text(encoding="utf-8")
    elif args.stdin:
        text = sys.stdin.read()

    if not text.strip():
        raise NptError("Comment text is empty. Provide --text, --text-file, or --stdin.")

    response = create_page_comment(
        access_token=access_token,
        notion_version=notion_version,
        page_id=args.page_id,
        text=text,
    )
    output = {
        "ok": True,
        "source": source,
        "page_id": args.page_id,
        "comment_id": response.get("id", ""),
        "url": response.get("url", ""),
    }
    print(dump_json(output))


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="NPT Notion OAuth and data source query helper")
    sub = parser.add_subparsers(dest="command", required=True)

    p_login = sub.add_parser("oauth-login", help="Open browser and complete OAuth exchange automatically")
    p_login.add_argument("--owner", default="user", help="OAuth owner value: user or workspace")
    p_login.add_argument("--redirect-uri", help="Override redirect URI")
    p_login.add_argument("--state", help="Explicit state value")
    p_login.add_argument(
        "--timeout",
        type=int,
        default=DEFAULT_OAUTH_TIMEOUT_SECONDS,
        help="Callback wait timeout in seconds",
    )
    p_login.add_argument("--no-open", action="store_true", help="Do not auto-open browser")
    p_login.add_argument("--no-store-state", action="store_true", help="Do not persist generated state")
    p_login.add_argument("--json", action="store_true", help="Output JSON only")
    p_login.set_defaults(func=cmd_oauth_login)

    p_start = sub.add_parser("oauth-start", help="Generate OAuth authorization URL")
    p_start.add_argument("--owner", default="user", help="OAuth owner value: user or workspace")
    p_start.add_argument("--redirect-uri", help="Override redirect URI")
    p_start.add_argument("--state", help="Explicit state value")
    p_start.add_argument("--no-store-state", action="store_true", help="Do not persist generated state")
    p_start.add_argument("--json", action="store_true", help="Output JSON only")
    p_start.set_defaults(func=cmd_oauth_start)

    p_exchange = sub.add_parser("oauth-exchange", help="Exchange OAuth code for token bundle")
    p_exchange.add_argument("--code", help="Authorization code")
    p_exchange.add_argument("--redirect-url", help="Full redirect URL containing code/state")
    p_exchange.add_argument("--state", help="Expected state override")
    p_exchange.add_argument("--redirect-uri", help="Override redirect URI")
    p_exchange.add_argument("--skip-state-check", action="store_true", help="Skip state validation")
    p_exchange.set_defaults(func=cmd_oauth_exchange)

    p_refresh = sub.add_parser("oauth-refresh", help="Refresh stored OAuth token")
    p_refresh.set_defaults(func=cmd_oauth_refresh)

    p_token = sub.add_parser("oauth-token", help="Print valid access token from store")
    p_token.set_defaults(func=cmd_oauth_token)

    p_query = sub.add_parser("query-active", help="Exact query for NPT statuses via data_sources/query")
    p_query.add_argument("--data-source-id", required=True, help="Notion data source UUID")
    p_query.add_argument("--status-property", default="状态", help="Status property name")
    p_query.add_argument("--title-property", default="任务", help="Title property name")
    p_query.add_argument("--include-statuses", action="append", help="Status to include (repeatable)")
    p_query.add_argument("--active-statuses", action="append", help="Statuses treated as active")
    p_query.add_argument("--blocked-status", default=DEFAULT_BLOCKED_STATUS, help="Blocked status label")
    p_query.add_argument("--page-size", type=int, default=100, help="Query page size (1-100)")
    p_query.add_argument("--notion-version", help="Notion-Version header")
    p_query.add_argument("--access-token", help="Explicit bearer token")
    p_query.add_argument(
        "--include-all",
        action="store_true",
        help="Include complete simplified result list under `all`",
    )
    p_query.set_defaults(func=cmd_query_active)

    p_comment = sub.add_parser("create-comment", help="Create a page comment via comments API")
    p_comment.add_argument("--page-id", required=True, help="Notion page UUID")
    comment_source = p_comment.add_mutually_exclusive_group(required=True)
    comment_source.add_argument("--text", help="Comment content")
    comment_source.add_argument("--text-file", help="Read comment content from file")
    comment_source.add_argument("--stdin", action="store_true", help="Read comment content from stdin")
    p_comment.add_argument("--notion-version", help="Notion-Version header")
    p_comment.add_argument("--access-token", help="Explicit bearer token")
    p_comment.set_defaults(func=cmd_create_comment)

    return parser


def main() -> int:
    parser = build_parser()
    args = parser.parse_args()
    store = TokenStore()
    if hasattr(args, "page_size") and args.page_size:
        if args.page_size < 1 or args.page_size > 100:
            raise NptError("--page-size must be between 1 and 100")
    try:
        args.func(args, store)
    except HttpError as exc:
        raise NptError(str(exc)) from exc
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except NptError as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        raise SystemExit(1)
