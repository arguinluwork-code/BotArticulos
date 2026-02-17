"""
bot_listener.py
Modo interactivo: escucha comandos de Telegram via long-polling.
Ejecutar localmente con: python main.py --listen

Comandos disponibles:
  /articulo  — envía el próximo artículo de la cola (instantáneo)
  /siguiente — alias de /articulo
  /leido     — marca el último artículo enviado como leído y lo saca de la cola
  /cola      — muestra cuántos artículos hay en cola
  /estado    — estadísticas generales
  /ayuda     — lista los comandos disponibles
"""

import json
import logging
import os
import time
from datetime import datetime

import requests

from github_state import load_state, save_state
from telegram_sender import answer_callback, send_article

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
)
logger = logging.getLogger(__name__)

POLL_TIMEOUT = 30
RETRY_SLEEP  = 5

ALLOWED_CHAT_ID = os.environ.get("TELEGRAM_CHAT_ID", "")


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
# State helpers — delegados a github_state (GitHub API o local)
# ---------------------------------------------------------------------------

def _load_state() -> dict:
    return load_state()


def _save_state(state: dict) -> None:
    save_state(state)


def _load_config() -> dict:
    with open("config.json", encoding="utf-8") as f:
        return json.load(f)


# ---------------------------------------------------------------------------
# Command handlers
# ---------------------------------------------------------------------------

def _handle_articulo(token: str, chat_id: int | str) -> None:
    """Saca el primer artículo de la cola y lo envía. Instantáneo."""
    state = _load_state()
    queue: list = state.setdefault("queue", [])

    if not queue:
        _send_text(
            token, chat_id,
            "📭 La cola está vacía. El bot la rellena automáticamente cada día.\n"
            "Si querés forzar la recarga usá /recargar."
        )
        return

    article = queue[0]  # no lo sacamos aún — se saca con /leido

    # Marcamos cuándo fue enviado
    article["sent_at"] = datetime.now().isoformat()
    _save_state(state)

    send_article(article, token, str(chat_id))

    remaining = len(queue) - 1
    _send_text(
        token, chat_id,
        f"📬 Quedan <b>{remaining}</b> artículo(s) en cola.\n"
        f"Usá /leido cuando termines de leerlo para pasar al siguiente."
    )
    logger.info("Sent from queue: %s (%d remaining)", article["title"], remaining)


def _handle_leido(token: str, chat_id: int | str) -> None:
    """Marca el primer artículo de la cola como leído y lo archiva en sent."""
    state = _load_state()
    queue: list = state.setdefault("queue", [])
    sent: list  = state.setdefault("sent", [])

    if not queue:
        _send_text(token, chat_id, "ℹ️ No hay artículo pendiente de marcar como leído.")
        return

    article = queue.pop(0)
    sent.append({
        "link":    article["link"],
        "title":   article["title"],
        "date":    article.get("published", ""),
        "sent_at": article.get("sent_at", datetime.now().isoformat()),
        "read_at": datetime.now().isoformat(),
    })
    state["sent"] = sent[-200:]
    _save_state(state)

    remaining = len(queue)
    _send_text(
        token, chat_id,
        f"✅ <i>{article['title']}</i>\nmarcado como leído.\n\n"
        f"📬 Quedan <b>{remaining}</b> artículo(s) en cola."
        + ("\n\nUsá /articulo para el siguiente." if remaining else
           "\n\n⚠️ Cola vacía. El bot la rellena mañana automáticamente.")
    )
    logger.info("Marked as read: %s (%d remaining in queue)", article["title"], remaining)


def _handle_recargar(token: str, chat_id: int | str, config: dict) -> None:
    """Fuerza una recarga de la cola ahora mismo (puede tardar ~1 min)."""
    _send_text(token, chat_id, "🔄 Recargando cola… puede tardar un minuto.")
    from main import refill_queue
    state = _load_state()
    added = refill_queue(config, state)
    state["sent"] = state.get("sent", [])[-200:]
    _save_state(state)
    queue_len = len(state.get("queue", []))
    if added:
        _send_text(
            token, chat_id,
            f"✅ Se agregaron <b>{added}</b> artículo(s) a la cola.\n"
            f"Total en cola: <b>{queue_len}</b>.\nUsá /articulo para leer."
        )
    else:
        _send_text(token, chat_id, "⚠️ No se encontraron artículos nuevos para agregar a la cola.")


def _handle_cola(token: str, chat_id: int | str) -> None:
    """Muestra los títulos en cola."""
    state = _load_state()
    queue: list = state.get("queue", [])

    if not queue:
        _send_text(token, chat_id, "📭 La cola está vacía.")
        return

    lines = [f"📋 <b>Cola de artículos ({len(queue)})</b>\n"]
    for i, art in enumerate(queue, 1):
        lines.append(f"{i}. <i>{art['title']}</i> — {art.get('source', '')}")
    _send_text(token, chat_id, "\n".join(lines))


def _handle_estado(token: str, chat_id: int | str) -> None:
    state = _load_state()
    sent  = state.get("sent", [])
    queue = state.get("queue", [])
    last  = sent[-1]["title"] if sent else "—"
    _send_text(
        token, chat_id,
        f"📊 <b>Estado del bot</b>\n\n"
        f"📬 Artículos leídos: <b>{len(sent)}</b>\n"
        f"📋 En cola: <b>{len(queue)}</b>\n"
        f"📄 Último leído: <i>{last}</i>",
    )


def _handle_ayuda(token: str, chat_id: int | str) -> None:
    _send_text(
        token, chat_id,
        "🤖 <b>Daily Article Bot — Comandos</b>\n\n"
        "/articulo  — Envía el próximo artículo de la cola\n"
        "/leido     — Marca el artículo actual como leído\n"
        "/cola      — Ver artículos en cola\n"
        "/recargar  — Forzar recarga de la cola ahora\n"
        "/estado    — Estadísticas generales\n"
        "/ayuda     — Este mensaje",
    )


# ---------------------------------------------------------------------------
# Auth
# ---------------------------------------------------------------------------

def _is_authorized(chat_id: int | str) -> bool:
    return str(chat_id) == str(ALLOWED_CHAT_ID)


# ---------------------------------------------------------------------------
# Main polling loop
# ---------------------------------------------------------------------------

def run_listener() -> None:
    token  = os.environ["TELEGRAM_BOT_TOKEN"]
    config = _load_config()

    logger.info("Bot listener started. Waiting for commands…")
    _send_text(token, ALLOWED_CHAT_ID, "✅ Bot iniciado. Usá /ayuda para ver los comandos.")

    offset = 0

    while True:
        updates = _get_updates(token, offset)

        for update in updates:
            offset = update["update_id"] + 1

            # --- Botón pulsado (inline keyboard) ---
            if "callback_query" in update:
                cq      = update["callback_query"]
                cq_id   = cq["id"]
                chat_id = cq["message"]["chat"]["id"]
                data    = cq.get("data", "")

                if not _is_authorized(chat_id):
                    answer_callback(token, cq_id, "⛔ No autorizado.")
                    continue

                logger.info("Callback: %r from chat_id=%s", data, chat_id)

                if data == "leido":
                    answer_callback(token, cq_id, "✅ Marcado como leído")
                    _handle_leido(token, chat_id)
                elif data == "cola":
                    answer_callback(token, cq_id)
                    _handle_cola(token, chat_id)
                elif data == "articulo":
                    answer_callback(token, cq_id)
                    _handle_articulo(token, chat_id)
                else:
                    answer_callback(token, cq_id, "❓ Acción desconocida.")
                continue

            # --- Mensaje de texto normal ---
            message = update.get("message") or update.get("edited_message")
            if not message:
                continue

            chat_id = message["chat"]["id"]
            text    = (message.get("text") or "").strip()

            if not _is_authorized(chat_id):
                logger.warning("Unauthorized chat_id=%s", chat_id)
                continue

            logger.info("Received: %r from chat_id=%s", text, chat_id)
            cmd = text.split()[0].lower().split("@")[0] if text else ""

            if cmd in ("/articulo", "/siguiente"):
                _handle_articulo(token, chat_id)
            elif cmd == "/leido":
                _handle_leido(token, chat_id)
            elif cmd == "/recargar":
                _handle_recargar(token, chat_id, config)
            elif cmd == "/cola":
                _handle_cola(token, chat_id)
            elif cmd == "/estado":
                _handle_estado(token, chat_id)
            elif cmd in ("/ayuda", "/start", "/help"):
                _handle_ayuda(token, chat_id)
            elif text.startswith("/"):
                _send_text(token, chat_id, "❓ Comando no reconocido. Usá /ayuda.")


if __name__ == "__main__":
    run_listener()
