import logging

import requests

logger = logging.getLogger(__name__)

TELEGRAM_API    = "https://api.telegram.org/bot{token}/sendMessage"
TELEGRAM_ANSWER = "https://api.telegram.org/bot{token}/answerCallbackQuery"
MAX_MESSAGE_LENGTH = 4096


def _build_message(article: dict) -> str:
    keywords    = ", ".join(article.get("matched_keywords") or [])
    reading_min = article.get("estimated_reading_min", "?")
    source      = article.get("source", "")
    link        = article.get("link", "")
    title       = article.get("title", "")
    summary_es  = article.get("summary_es", "")
    reason      = article.get("reason", "")

    return (
        f"📰 <b>{title}</b>\n\n"
        f"📝 {summary_es}\n\n"
        f"🏷 {keywords}\n"
        f"⏱ ~{reading_min} min de lectura\n"
        f"📌 {source}\n"
        f"🔗 <a href=\"{link}\">Leer artículo completo</a>\n\n"
        f"💡 <i>{reason}</i>"
    )


def _truncate_message(article: dict) -> str:
    msg = _build_message(article)
    if len(msg) <= MAX_MESSAGE_LENGTH:
        return msg

    summary = article.get("summary_es", "")
    while len(summary) > 0 and len(msg) > MAX_MESSAGE_LENGTH:
        summary = summary[: int(len(summary) * 0.85)].rsplit(" ", 1)[0] + "…"
        article = {**article, "summary_es": summary}
        msg = _build_message(article)

    return msg[:MAX_MESSAGE_LENGTH]


def send_article(article: dict, bot_token: str, chat_id: str) -> bool:
    """
    Envía el artículo con un inline keyboard:
      [✅ Marcar como leído]  [📋 Ver cola]
    Retorna True si el envío fue exitoso.
    """
    message = _truncate_message(article)
    url     = TELEGRAM_API.format(token=bot_token)

    payload = {
        "chat_id":    chat_id,
        "text":       message,
        "parse_mode": "HTML",
        "disable_web_page_preview": False,
        "reply_markup": {
            "inline_keyboard": [[
                {"text": "✅ Marcar como leído", "callback_data": "leido"},
                {"text": "📋 Ver cola",          "callback_data": "cola"},
            ]]
        },
    }

    try:
        response = requests.post(url, json=payload, timeout=15)
        response.raise_for_status()
        data = response.json()
        if not data.get("ok"):
            logger.error("Telegram API returned not-ok: %s", data)
            return False
        logger.info("Message sent successfully to chat_id=%s", chat_id)
        return True
    except requests.HTTPError as exc:
        logger.error("HTTP error sending Telegram message: %s — %s", exc, exc.response.text if exc.response else "")
        return False
    except Exception as exc:
        logger.error("Unexpected error sending Telegram message: %s", exc)
        return False


def answer_callback(bot_token: str, callback_query_id: str, text: str = "") -> None:
    """Cierra el spinner del botón en el cliente de Telegram."""
    try:
        requests.post(
            TELEGRAM_ANSWER.format(token=bot_token),
            json={"callback_query_id": callback_query_id, "text": text},
            timeout=10,
        )
    except Exception as exc:
        logger.warning("Failed to answer callback query: %s", exc)
