import json
import logging
import os
from datetime import datetime

from feeds import fetch_all_feeds
from selector import select_best_article
from telegram_sender import send_article

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
)
logger = logging.getLogger(__name__)


def load_json(path: str) -> dict:
    with open(path, encoding="utf-8") as f:
        return json.load(f)


def save_json(path: str, data: dict) -> None:
    with open(path, "w", encoding="utf-8") as f:
        json.dump(data, f, indent=2, ensure_ascii=False)


def main() -> None:
    # 1. Load config and state
    config = load_json("config.json")
    state = load_json("state.json")

    # 2. Fetch candidate articles
    candidates = fetch_all_feeds(config, state)
    logger.info("Candidates found: %d", len(candidates))

    if not candidates:
        logger.warning("No candidates found. Exiting.")
        _notify_no_articles()
        return

    # 3. Select the best article via Claude Haiku
    selected = select_best_article(
        candidates,
        config["keywords"],
        max_candidates=config.get("max_candidates_to_llm", 30),
    )

    if not selected:
        logger.warning("Could not select an article. Exiting.")
        return

    logger.info("Selected: %s", selected["title"])

    # 4. Send via Telegram
    bot_token = os.environ["TELEGRAM_BOT_TOKEN"]
    chat_id = os.environ["TELEGRAM_CHAT_ID"]
    send_article(selected, bot_token, chat_id)

    # 5. Update state (keep last 200 entries)
    state.setdefault("sent", []).append({
        "link": selected["link"],
        "title": selected["title"],
        "date": selected.get("published", ""),
        "sent_at": datetime.now().isoformat(),
    })
    state["sent"] = state["sent"][-200:]
    save_json("state.json", state)
    logger.info("State updated.")


def _notify_no_articles() -> None:
    """Optionally send a Telegram notification when no articles are found."""
    bot_token = os.environ.get("TELEGRAM_BOT_TOKEN")
    chat_id = os.environ.get("TELEGRAM_CHAT_ID")
    if not bot_token or not chat_id:
        return
    import requests
    try:
        requests.post(
            f"https://api.telegram.org/bot{bot_token}/sendMessage",
            json={
                "chat_id": chat_id,
                "text": "⚠️ Daily Article Bot: No se encontraron artículos candidatos hoy.",
                "parse_mode": "HTML",
            },
            timeout=15,
        )
    except Exception as exc:
        logger.error("Failed to send no-articles notification: %s", exc)


if __name__ == "__main__":
    main()
