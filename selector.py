import json
import logging
import os

import anthropic

logger = logging.getLogger(__name__)


def select_best_article(candidates: list[dict], keywords: list[str], max_candidates: int = 30) -> dict | None:
    """
    Use Claude Haiku to select the most relevant article from the candidate list.
    Returns the selected article dict enriched with: reason, summary_es, matched_keywords.
    Returns None if selection fails or no candidates exist.
    """
    if not candidates:
        logger.warning("No candidates provided to selector.")
        return None

    pool = candidates[:max_candidates]
    keywords_string = ", ".join(keywords)

    # Build the numbered list of candidates for the prompt
    candidates_text = ""
    for i, article in enumerate(pool):
        summary_snippet = (article.get("summary") or "")[:300]
        candidates_text += (
            f"[{i}] {article['title']} — {article['source']}\n"
            f"Resumen: {summary_snippet}\n\n"
        )

    prompt = f"""Sos un curador de contenido. De la siguiente lista de artículos, seleccioná el MÁS relevante e interesante para alguien que trabaja en operaciones, business intelligence, y gestión de datos en una empresa de producción.

Keywords de interés: {keywords_string}

Artículos candidatos:
{candidates_text}
Respondé SOLO con un JSON válido, sin markdown:
{{"index": <número>, "reason": "<1 línea de por qué>", "summary_es": "<resumen de 2-3 oraciones en español>", "matched_keywords": ["kw1", "kw2"]}}"""

    try:
        client = anthropic.Anthropic(api_key=os.environ["ANTHROPIC_API_KEY"])
        message = client.messages.create(
            model="claude-haiku-4-5-20251001",
            max_tokens=400,
            messages=[{"role": "user", "content": prompt}],
        )
        raw = message.content[0].text.strip()
        logger.debug("Haiku raw response: %s", raw)
    except Exception as exc:
        logger.error("Claude API call failed: %s", exc)
        return None

    try:
        result = json.loads(raw)
    except json.JSONDecodeError:
        # Try to extract JSON block if model wrapped it anyway
        import re
        match = re.search(r"\{.*\}", raw, re.DOTALL)
        if match:
            try:
                result = json.loads(match.group())
            except json.JSONDecodeError:
                logger.error("Could not parse JSON from Haiku response: %s", raw)
                return None
        else:
            logger.error("No JSON found in Haiku response: %s", raw)
            return None

    idx = result.get("index")
    if idx is None or not isinstance(idx, int) or idx < 0 or idx >= len(pool):
        logger.error("Invalid index in Haiku response: %s", result)
        return None

    selected = pool[idx].copy()
    selected["reason"] = result.get("reason", "")
    selected["summary_es"] = result.get("summary_es", selected.get("summary", ""))
    selected["matched_keywords"] = result.get("matched_keywords", [])

    return selected
