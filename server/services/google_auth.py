"""
Google OAuth 2.0 + Service Account Credential Management for AgentK.

Hybrid mode:
  - Default: Service Account (shared company calendar, no user login needed)
  - Override: Per-user OAuth (personal calendar + Meet support)

Provides:
- Service Account credential loading (from JSON key file)
- OAuth authorization URL generation (for LINE/Web UI)
- Callback handler (exchange code → tokens)
- Credential loading for skill subprocess injection (SA or OAuth)
- Automatic token refresh

Storage:
  - Service Account: workspace/credentials/service_account.json (single file)
  - Per-user OAuth: workspace/credentials/{session_id}_google.json
"""

import json
import os
import logging
from pathlib import Path
from typing import Optional, Dict, Any

logger = logging.getLogger("MCP_Server.GoogleAuth")

# OAuth scopes for Google Workspace integration (Calendar + Meet only, no Gmail)
GOOGLE_SCOPES = [
    "https://www.googleapis.com/auth/calendar",
    "https://www.googleapis.com/auth/calendar.events",
]

# Credential storage directory
_CREDENTIALS_DIR = "workspace/credentials"


def _get_project_root() -> Path:
    """Resolve project root from environment or file location."""
    pr = os.getenv("PROJECT_ROOT")
    if pr:
        return Path(pr)
    return Path(__file__).resolve().parents[2]


def _get_credentials_dir() -> Path:
    """Get or create the credentials storage directory."""
    cred_dir = _get_project_root() / _CREDENTIALS_DIR
    cred_dir.mkdir(parents=True, exist_ok=True)
    return cred_dir


# ── Service Account ─────────────────────────────────────────────────────────

def get_service_account_path() -> Optional[Path]:
    """Get the Service Account JSON key path from env or default location."""
    # Check env var first
    sa_path_str = os.getenv("GOOGLE_SERVICE_ACCOUNT_KEY", "")
    if sa_path_str:
        # Resolve relative to project root
        sa_path = Path(sa_path_str)
        if not sa_path.is_absolute():
            sa_path = _get_project_root() / sa_path
        if sa_path.exists():
            return sa_path
    # Default location
    default = _get_credentials_dir() / "service_account.json"
    if default.exists():
        return default
    return None


def has_service_account() -> bool:
    """Check if a Service Account key file is available."""
    return get_service_account_path() is not None


# ── Per-User OAuth ──────────────────────────────────────────────────────────

def get_credential_path(session_id: str) -> Path:
    """Get the file path for a session's personal Google OAuth credentials."""
    return _get_credentials_dir() / f"{session_id}_google.json"


def has_credentials(session_id: str) -> bool:
    """Check if a session has stored personal Google OAuth credentials."""
    return get_credential_path(session_id).exists()


def build_authorize_url(session_id: str, redirect_uri: str = None) -> str:
    """
    Build Google OAuth 2.0 authorization URL for personal account binding.

    Args:
        session_id: The session ID to bind the credential to.
        redirect_uri: Override redirect URI (default from env).

    Returns:
        Authorization URL string.
    """
    from google_auth_oauthlib.flow import Flow

    client_id = os.getenv("GOOGLE_CLIENT_ID", "")
    client_secret = os.getenv("GOOGLE_CLIENT_SECRET", "")
    if not redirect_uri:
        redirect_uri = os.getenv(
            "GOOGLE_OAUTH_REDIRECT_URI",
            "http://localhost:8500/api/auth/google/workspace/callback"
        )

    if not client_id or not client_secret:
        raise RuntimeError("GOOGLE_CLIENT_ID and GOOGLE_CLIENT_SECRET must be set in .env")

    client_config = {
        "web": {
            "client_id": client_id,
            "client_secret": client_secret,
            "auth_uri": "https://accounts.google.com/o/oauth2/auth",
            "token_uri": "https://oauth2.googleapis.com/token",
            "redirect_uris": [redirect_uri],
        }
    }

    flow = Flow.from_client_config(client_config, scopes=GOOGLE_SCOPES)
    flow.redirect_uri = redirect_uri

    auth_url, state = flow.authorization_url(
        access_type="offline",
        include_granted_scopes="true",
        prompt="consent",
        state=session_id,  # Pass session_id through OAuth state
    )

    logger.info(f"[GoogleAuth] Authorization URL generated for session: {session_id}")
    return auth_url


def handle_callback(code: str, session_id: str, redirect_uri: str = None) -> Dict[str, Any]:
    """
    Exchange authorization code for tokens and store credentials.

    Args:
        code: Authorization code from Google.
        session_id: The session ID to bind the credential to.
        redirect_uri: Override redirect URI.

    Returns:
        Dict with user info and status.
    """
    from google_auth_oauthlib.flow import Flow

    client_id = os.getenv("GOOGLE_CLIENT_ID", "")
    client_secret = os.getenv("GOOGLE_CLIENT_SECRET", "")
    if not redirect_uri:
        redirect_uri = os.getenv(
            "GOOGLE_OAUTH_REDIRECT_URI",
            "http://localhost:8500/api/auth/google/workspace/callback"
        )

    client_config = {
        "web": {
            "client_id": client_id,
            "client_secret": client_secret,
            "auth_uri": "https://accounts.google.com/o/oauth2/auth",
            "token_uri": "https://oauth2.googleapis.com/token",
            "redirect_uris": [redirect_uri],
        }
    }

    flow = Flow.from_client_config(client_config, scopes=GOOGLE_SCOPES)
    flow.redirect_uri = redirect_uri

    flow.fetch_token(code=code)
    creds = flow.credentials

    # Store credentials
    cred_data = {
        "token": creds.token,
        "refresh_token": creds.refresh_token,
        "token_uri": creds.token_uri,
        "client_id": creds.client_id,
        "client_secret": creds.client_secret,
        "scopes": list(creds.scopes) if creds.scopes else GOOGLE_SCOPES,
    }

    cred_path = get_credential_path(session_id)
    with open(cred_path, "w", encoding="utf-8") as f:
        json.dump(cred_data, f, ensure_ascii=False, indent=2)

    logger.info(f"[GoogleAuth] Personal OAuth credentials stored for session: {session_id}")

    return {
        "status": "success",
        "session_id": session_id,
        "message": "Google 帳號已成功綁定",
    }


def refresh_if_expired(session_id: str) -> bool:
    """
    Check and refresh personal OAuth token if expired.
    Returns True if credential is valid (after refresh if needed).
    """
    cred_path = get_credential_path(session_id)
    if not cred_path.exists():
        return False

    try:
        cred_data = json.loads(cred_path.read_text(encoding="utf-8"))
        from google.oauth2.credentials import Credentials
        creds = Credentials(
            token=cred_data.get("token"),
            refresh_token=cred_data.get("refresh_token"),
            token_uri=cred_data.get("token_uri", "https://oauth2.googleapis.com/token"),
            client_id=cred_data.get("client_id"),
            client_secret=cred_data.get("client_secret"),
            scopes=cred_data.get("scopes"),
        )

        if creds.expired and creds.refresh_token:
            from google.auth.transport.requests import Request
            creds.refresh(Request())
            # Persist refreshed token
            cred_data["token"] = creds.token
            cred_path.write_text(json.dumps(cred_data, ensure_ascii=False, indent=2), encoding="utf-8")
            logger.info(f"[GoogleAuth] Token refreshed for session: {session_id}")

        return creds.valid or creds.token is not None
    except Exception as e:
        logger.warning(f"[GoogleAuth] Token refresh failed for session {session_id}: {e}")
        return False


# ── Hybrid Credential Resolution ────────────────────────────────────────────

def get_credentials_env(session_id: str) -> Optional[Dict[str, str]]:
    """
    Get environment variables to inject into skill subprocess.

    Resolution order:
      1. Personal OAuth credential (if user has bound their Google account)
      2. Service Account (shared company credential)

    Returns:
      {"GOOGLE_CREDENTIALS_PATH": "...", "GOOGLE_CREDENTIAL_TYPE": "oauth|service_account"}
      or None if no credentials available.
    """
    # Priority 1: Personal OAuth
    personal_path = get_credential_path(session_id)
    if personal_path.exists():
        # Try refresh before returning
        refresh_if_expired(session_id)
        return {
            "GOOGLE_CREDENTIALS_PATH": str(personal_path),
            "GOOGLE_CREDENTIAL_TYPE": "oauth",
        }

    # Priority 2: Service Account
    sa_path = get_service_account_path()
    if sa_path:
        return {
            "GOOGLE_CREDENTIALS_PATH": str(sa_path),
            "GOOGLE_CREDENTIAL_TYPE": "service_account",
        }

    return None


def needs_personal_oauth(skill_name: str) -> bool:
    """
    Check if a skill requires personal OAuth (cannot use Service Account).
    Google Meet requires personal OAuth because SA cannot create Meet links.
    """
    return skill_name in ("mcp-google-meet",)
