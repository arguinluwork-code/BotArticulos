"""
bot_listener.py
Modo interactivo: escucha comandos de Telegram via long-polling.
Ejecutar localmente con: python bot_listener.py

Comandos disponibles:
  /articulo  — busca y envía el artículo más relevante del día
  /estado    — muestra cuántos artículos fueron enviados en total
  /ayuda     — lista los comandos disponibles
"""

import json
import logging
import os
import time
from datetime import datetime

import requests

from feeds import fetch_all_feeds
from selector import select_best_article
from telegram_sender import send_article

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
)
logger = logging.getLogger(__name__)

POLL_TIMEOUT = 30       # segundos de long-polling por request
RETRY_SLEEP  = 5        # segundos a esperar si falla getUpdates


# ---------------------------------------------------------------------------
# Telegram API helpers
# ---------------------------------------------------------------------------

def _api(token: str, method: str, **kwargs) -> dict:
    url = f"https://api.telegram.org/bot{token}/{method}"
    resp = requests.post(url, json=kwargs, timeout=POLL_TIMEOUT + 5)
    resp.raise_for_status()
    return resp.json()


def _send_text(token: str, chat_id: int | str, text: str) -> None:
    try:
        _api(token, "sendMessage", chat_id=chat_id, text=text, parse_mode="HTML")
    except Exception as exc:
        logger.error("Error sending text: %s", exc)


def _get_updates(token: str, offset: int) -> list[dict]:
    try:
        data = _api(token, "getUpdates", offset=offset, timeout=POLL_TIMEOUT)
        return data.get("result", [])
    except Exception as exc:
        logger.warning("getUpdates failed: %s", exc)
        time.sleep(RETRY_SLEEP)
        return []


# ---------------------------------------------------------------------------
# State helpers
# ---------------------------------------------------------------------------

def _load_json(path: str) -> dict:
    with open(path, encoding="utf-8") as f:
        return json.load(f)


def _save_json(path: str, data: dict) -> None:
    with open(path, "w", encoding="utf-8") as f:
        json.dump(data, f, indent=2, ensure_ascii=False)


# ---------------------------------------------------------------------------
# Command handlers
# ---------------------------------------------------------------------------

def _handle_articulo(token: str, chat_id: int | str, config: dict) -> None:
    """Fetch feeds, select best article, send it, update state."""
    _send_text(token, chat_id, "🔍 Buscando el mejor artículo… puede tardar un minuto.")

    state = _load_json("state.json")

    candidates = fetch_all_feeds(config, state)
    logger.info("Candidates found: %d", len(candidates))

    if not candidates:
        _send_text(token, chat_id, "⚠️ No encontré artículos candidatos en este momento. Probá de nuevo más tarde.")
        return

    selected = select_best_article(
        candidates,
        config["keywords"],
        max_candidates=config.get("max_candidates_to_llm", 30),
    )

    if not selected:
        _send_text(token, chat_id, "⚠️ No pude seleccionar un artículo. Intentá de nuevo.")
        return

    logger.info("Selected: %s", selected["title"])
    send_article(selected, token, str(chat_id))

    # Update state
    state.setdefault("sent", []).append({
        "link": selected["link"],
        "title": selected["title"],
        "date": selected.get("published", ""),
        "sent_at": datetime.now().isoformat(),
    })
    state["sent"] = state["sent"][-200:]
    _save_json("state.json", state)
    logger.info("State updated.")


def _handle_estado(token: str, chat_id: int | str) -> None:
    """Report how many articles have been sent so far."""
    state = _load_json("state.json")
    count = len(state.get("sent", []))
    last = state["sent"][-1]["title"] if count else "—"
    _send_text(
        token, chat_id,
        f"📊 <b>Estado del bot</b>\n\n"
        f"📬 Artículos enviados: <b>{count}</b>\n"
        f"📄 Último: <i>{last}</i>",
    )


def _handle_ayuda(token: str, chat_id: int | str) -> None:
    _send_text(
        token, chat_id,
        "🤖 <b>Daily Article Bot — Comandos</b>\n\n"
        "/articulo — Busca y envía el artículo más relevante ahora\n"
        "/estado   — Muestra estadísticas del bot\n"
        "/ayuda    — Muestra este mensaje",
    )


# ---------------------------------------------------------------------------
# Main polling loop
# ---------------------------------------------------------------------------

ALLOWED_CHAT_ID = os.environ.get("TELEGRAM_CHAT_ID", "")


def _is_authorized(chat_id: int | str) -> bool:
    """Only respond to the configured chat ID to avoid abuse."""
    return str(chat_id) == str(ALLOWED_CHAT_ID)


def run_listener() -> None:
    token = os.environ["TELEGRAM_BOT_TOKEN"]
    config = _load_json("config.json")

    logger.info("Bot listener started. Waiting for commands…")
    _send_text(token, ALLOWED_CHAT_ID, "✅ Bot iniciado. Usá /ayuda para ver los comandos disponibles.")

    offset = 0

    while True:
        updates = _get_updates(token, offset)

        for update in updates:
            offset = update["update_id"] + 1

            message = update.get("message") or update.get("edited_message")
            if not message:
                continue

            chat_id = message["chat"]["id"]
            text = (message.get("text") or "").strip()

            if not _is_authorized(chat_id):
                logger.warning("Unauthorized chat_id=%s tried to use the bot.", chat_id)
                continue

            logger.info("Received: %r from chat_id=%s", text, chat_id)

            cmd = text.split()[0].lower().split("@")[0] if text else ""

            if cmd == "/articulo":
                _handle_articulo(token, chat_id, config)
            elif cmd == "/estado":
                _handle_estado(token, chat_id)
            elif cmd in ("/ayuda", "/start", "/help"):
                _handle_ayuda(token, chat_id)
            else:
                if text.startswith("/"):
                    _send_text(token, chat_id, "❓ Comando no reconocido. Usá /ayuda.")


if __name__ == "__main__":
    run_listener()
