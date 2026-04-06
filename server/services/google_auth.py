"""
Google OAuth 2.0 Credential Management for AgentK.

Provides:
- OAuth authorization URL generation (for LINE/Web UI)
- Callback handler (exchange code → tokens)
- Credential loading for skill subprocess injection
- Automatic token refresh

Storage: workspace/credentials/{session_id}_google.json
"""

import json
import os
import logging
from pathlib import Path
from typing import Optional, Dict, Any

logger = logging.getLogger("MCP_Server.GoogleAuth")

# OAuth scopes for Google Workspace integration
GOOGLE_SCOPES = [
    "https://www.googleapis.com/auth/calendar",
    "https://www.googleapis.com/auth/calendar.events",
    "https://www.googleapis.com/auth/gmail.readonly",
    "https://www.googleapis.com/auth/gmail.send",
    "https://www.googleapis.com/auth/gmail.modify",
]

# Credential storage directory
_CREDENTIALS_DIR = "workspace/credentials"


def _get_project_root() -> Path:
    """Resolve project root from environment or file location."""
    # Try PROJECT_ROOT env var first
    pr = os.getenv("PROJECT_ROOT")
    if pr:
        return Path(pr)
    # Fallback: 3 levels up from this file
    return Path(__file__).resolve().parents[2]


def _get_credentials_dir() -> Path:
    """Get or create the credentials storage directory."""
    cred_dir = _get_project_root() / _CREDENTIALS_DIR
    cred_dir.mkdir(parents=True, exist_ok=True)
    return cred_dir


def get_credential_path(session_id: str) -> Path:
    """Get the file path for a session's Google credentials."""
    return _get_credentials_dir() / f"{session_id}_google.json"


def has_credentials(session_id: str) -> bool:
    """Check if a session has stored Google credentials."""
    return get_credential_path(session_id).exists()


def build_authorize_url(session_id: str, redirect_uri: str = None) -> str:
    """
    Build Google OAuth 2.0 authorization URL.

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
            "http://localhost:8500/api/auth/google/callback"
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
            "http://localhost:8500/api/auth/google/callback"
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

    logger.info(f"[GoogleAuth] Credentials stored for session: {session_id}")

    return {
        "status": "success",
        "session_id": session_id,
        "message": "Google 帳號已成功綁定",
    }


def get_credentials_env(session_id: str) -> Optional[Dict[str, str]]:
    """
    Get environment variables to inject into skill subprocess.
    Returns {"GOOGLE_CREDENTIALS_PATH": "/path/to/cred.json"} or None.

    This is called by UMA execute_tool_call() for Google skills.
    """
    cred_path = get_credential_path(session_id)
    if not cred_path.exists():
        return None
    return {"GOOGLE_CREDENTIALS_PATH": str(cred_path)}
