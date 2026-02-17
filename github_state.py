"""
github_state.py
Lee y escribe state.json directamente en el repo de GitHub via API.
Esto permite que Railway (sin filesystem persistente) mantenga el state.
Requiere la variable de entorno GITHUB_TOKEN con permisos de repo contents.
"""

import base64
import json
import logging
import os

import requests

logger = logging.getLogger(__name__)

GITHUB_TOKEN = os.environ.get("GITHUB_TOKEN", "")
GITHUB_REPO  = os.environ.get("GITHUB_REPO", "")   # formato: "usuario/repo"
STATE_PATH   = "state.json"
API_BASE     = "https://api.github.com"

HEADERS = {
    "Authorization": f"Bearer {GITHUB_TOKEN}",
    "Accept": "application/vnd.github+json",
    "X-GitHub-Api-Version": "2022-11-28",
}


def _get_file_meta() -> tuple[str, str]:
    """Retorna (contenido_base64, sha) del state.json en GitHub."""
    url = f"{API_BASE}/repos/{GITHUB_REPO}/contents/{STATE_PATH}"
    r = requests.get(url, headers=HEADERS, timeout=15)
    r.raise_for_status()
    data = r.json()
    return data["content"], data["sha"]


def load_state() -> dict:
    """Lee state.json desde GitHub. Fallback a archivo local si no hay token."""
    if not GITHUB_TOKEN or not GITHUB_REPO:
        logger.debug("No GITHUB_TOKEN/REPO set, reading local state.json")
        with open(STATE_PATH, encoding="utf-8") as f:
            return json.load(f)
    try:
        content_b64, _ = _get_file_meta()
        content = base64.b64decode(content_b64).decode("utf-8")
        return json.loads(content)
    except Exception as exc:
        logger.error("Failed to load state from GitHub: %s", exc)
        # Fallback a local si existe
        with open(STATE_PATH, encoding="utf-8") as f:
            return json.load(f)


def save_state(state: dict) -> None:
    """Escribe state.json en GitHub. Fallback a archivo local si no hay token."""
    if not GITHUB_TOKEN or not GITHUB_REPO:
        logger.debug("No GITHUB_TOKEN/REPO set, writing local state.json")
        with open(STATE_PATH, "w", encoding="utf-8") as f:
            json.dump(state, f, indent=2, ensure_ascii=False)
        return
    try:
        _, sha = _get_file_meta()
        content = json.dumps(state, indent=2, ensure_ascii=False)
        content_b64 = base64.b64encode(content.encode("utf-8")).decode("utf-8")
        url = f"{API_BASE}/repos/{GITHUB_REPO}/contents/{STATE_PATH}"
        payload = {
            "message": "Update state.json [skip ci]",
            "content": content_b64,
            "sha": sha,
            "committer": {
                "name": "railway-bot",
                "email": "bot@railway.app",
            },
        }
        r = requests.put(url, headers=HEADERS, json=payload, timeout=15)
        r.raise_for_status()
        logger.info("state.json pushed to GitHub successfully.")
    except Exception as exc:
        logger.error("Failed to save state to GitHub: %s", exc)
        # Fallback a local
        with open(STATE_PATH, "w", encoding="utf-8") as f:
            json.dump(state, f, indent=2, ensure_ascii=False)
