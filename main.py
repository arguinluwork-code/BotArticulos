import json
import logging
import os
import sys
from datetime import datetime

from feeds import fetch_all_feeds
from selector import select_best_article
from telegram_sender import send_article

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
)
logger = logging.getLogger(__name__)

QUEUE_TARGET = 5   # artículos que queremos tener en la cola siempre


def load_json(path: str) -> dict:
    with open(path, encoding="utf-8") as f:
        return json.load(f)


def save_json(path: str, data: dict) -> None:
    with open(path, "w", encoding="utf-8") as f:
        json.dump(data, f, indent=2, ensure_ascii=False)


def refill_queue(config: dict, state: dict) -> int:
    """
    Fetchea feeds y usa el LLM para agregar artículos a la cola
    hasta llegar a QUEUE_TARGET. Devuelve cuántos se agregaron.
    """
    queue: list = state.setdefault("queue", [])
    sent: list  = state.setdefault("sent", [])

    slots = QUEUE_TARGET - len(queue)
    if slots <= 0:
        logger.info("Queue already full (%d articles). Nothing to do.", len(queue))
        return 0

    # Links ya vistos = enviados + en cola (para no duplicar)
    seen_links = {item["link"] for item in sent}
    seen_links |= {item["link"] for item in queue}

    # Construimos un state temporal con todos los links vistos
    state_for_fetch = {"sent": [{"link": l} for l in seen_links]}

    candidates = fetch_all_feeds(config, state_for_fetch)
    logger.info("Candidates found: %d", len(candidates))

    if not candidates:
        logger.warning("No candidates found to fill queue.")
        return 0

    added = 0
    remaining_candidates = candidates

    while added < slots and remaining_candidates:
        selected = select_best_article(
            remaining_candidates,
            config["keywords"],
            max_candidates=config.get("max_candidates_to_llm", 30),
        )
        if not selected:
            break

        queue.append({
            **selected,
            "queued_at": datetime.now().isoformat(),
        })

        # Sacar el seleccionado de los candidatos para la próxima iteración
        remaining_candidates = [
            c for c in remaining_candidates if c["link"] != selected["link"]
        ]
        seen_links.add(selected["link"])
        added += 1
        logger.info("Queued [%d/%d]: %s", added, slots, selected["title"])

    return added


def main() -> None:
    config = load_json("config.json")
    state  = load_json("state.json")

    queue = state.setdefault("queue", [])
    logger.info("Queue before refill: %d articles", len(queue))

    added = refill_queue(config, state)
    logger.info("Added %d articles to queue. Queue now: %d", added, len(queue))

    if added == 0 and not queue:
        logger.warning("Queue empty and nothing added. Notifying.")
        _notify_no_articles()
    else:
        logger.info("Queue refilled successfully. No article sent (use /articulo in Telegram).")

    # Guardar state actualizado
    state["sent"] = state.get("sent", [])[-200:]
    save_json("state.json", state)
    logger.info("State saved.")


def _notify_no_articles() -> None:
    bot_token = os.environ.get("TELEGRAM_BOT_TOKEN")
    chat_id   = os.environ.get("TELEGRAM_CHAT_ID")
    if not bot_token or not chat_id:
        return
    import requests
    try:
        requests.post(
            f"https://api.telegram.org/bot{bot_token}/sendMessage",
            json={
                "chat_id": chat_id,
                "text": "⚠️ Daily Article Bot: No se encontraron artículos nuevos para encolar hoy.",
                "parse_mode": "HTML",
            },
            timeout=15,
        )
    except Exception as exc:
        logger.error("Failed to send notification: %s", exc)


if __name__ == "__main__":
    if "--listen" in sys.argv:
        from bot_listener import run_listener
        run_listener()
    else:
        main()
