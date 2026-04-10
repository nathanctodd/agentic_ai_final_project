STOPWORDS = {
    "the", "a", "an", "is", "it", "i", "my", "me", "this", "that", "to",
    "of", "and", "or", "for", "in", "on", "at", "are", "was", "not", "no",
    "be", "as", "by", "we", "so", "do", "if", "up", "but", "its", "has",
    "with", "from", "they", "have", "had", "will", "just", "been", "also",
    "very", "what", "when", "who", "how", "would", "could", "should",
}

PERSONA_TYPES = ["budget", "luxury", "impulsive"]

# ---------------------------------------------------------------------------
# Theme classification
# ---------------------------------------------------------------------------

_THEME_RULES: list[tuple[str, list[str]]] = [
    ("price",      ["price", "expens", "afford", "cost", "budget", "cheap", "overpriced",
                    "too high", "too much", "pricey", "value"]),
    ("selection",  ["no product", "nothing", "selection", "limited", "few ", "variety",
                    "doesn't have", "not available", "out of stock", "no option", "lack"]),
    ("ux",         ["confus", "unclear", "hard to", "difficult", "trust", "clutter",
                    "messy", "layout", "navigate", "find", "overwhelming", "poor design"]),
    ("relevance",  ["not what", "relevant", "looking for", "match", "need", "different",
                    "wrong", "doesn't fit", "not suitable", "goal"]),
]


def _classify_theme(reason: str) -> str:
    """Classify a drop-off reason into one of: price, selection, ux, relevance, other."""
    r = reason.lower()
    for theme, keywords in _THEME_RULES:
        if any(kw in r for kw in keywords):
            return theme
    return "other"


def _extract_top_complaints(logs: list[dict], top_n: int = 5) -> list[str]:
    """Return the top-N most frequent meaningful words from drop-off reasons."""
    left_reasons = []
    for log in logs:
        if log["result"] == "left" and log["steps"]:
            last_step = log["steps"][-1]
            reason = last_step.get("reason", "")
            if reason:
                left_reasons.append(reason)

    word_counts: dict[str, int] = {}
    for reason in left_reasons:
        for word in reason.lower().split():
            word = word.strip(".,!?\"'();:-")
            if word and word not in STOPWORDS and len(word) >= 4:
                word_counts[word] = word_counts.get(word, 0) + 1

    sorted_words = sorted(word_counts, key=lambda w: word_counts[w], reverse=True)
    return sorted_words[:top_n]


def _build_agent_insights(logs: list[dict]) -> tuple[list[dict], list[dict], dict]:
    """Build per-agent drop-off and purchase reason lists, plus theme counts.

    Returns:
        dropoff_reasons: one entry per agent that left
        purchase_reasons: one entry per agent that bought
        drop_themes: {theme: count} across all drop-offs
    """
    dropoff_reasons: list[dict] = []
    purchase_reasons: list[dict] = []

    for log in logs:
        if not log["steps"]:
            continue

        last_step = log["steps"][-1]
        reason = last_step.get("reason", "")
        step_count = len(log["steps"])

        # Collect reasoning journey: one sentence per step
        journey = [
            {
                "step": s["step"],
                "action": s["action"],
                "target": s.get("target", ""),
                "reason": s.get("reason", ""),
            }
            for s in log["steps"]
        ]

        entry = {
            "agent_id": log["agent_id"],
            "persona_type": log["persona_type"],
            "budget": log["budget"],
            "step_count": step_count,
            "exit_reason": reason,
            "journey": journey,
        }

        if log["result"] == "left":
            entry["theme"] = _classify_theme(reason)
            dropoff_reasons.append(entry)
        elif log["result"] == "purchased":
            target = last_step.get("target", "")
            entry["purchased_product"] = target
            purchase_reasons.append(entry)

    drop_themes: dict[str, int] = {}
    for d in dropoff_reasons:
        t = d["theme"]
        drop_themes[t] = drop_themes.get(t, 0) + 1

    return dropoff_reasons, purchase_reasons, drop_themes


def compute_analytics(logs: list[dict], ux_score: int) -> dict:
    """Compute dashboard metrics from simulation logs.

    Args:
        logs: Output from simulation.run_simulation()
        ux_score: Integer 0-100 from parser.parse()

    Returns:
        Analytics dict ready for the API response.
    """
    total = len(logs)
    if total == 0:
        return {
            "conversion_rate": 0.0,
            "dropoff_rate": 1.0,
            "avg_steps": 0.0,
            "ux_score": ux_score,
            "top_complaints": [],
            "agent_breakdown": {},
            "total_agents": 0,
            "purchased_count": 0,
            "left_count": 0,
        }

    purchased_logs = [l for l in logs if l["result"] == "purchased"]
    left_logs = [l for l in logs if l["result"] == "left"]

    purchased_count = len(purchased_logs)
    left_count = len(left_logs)
    conversion_rate = purchased_count / total
    dropoff_rate = left_count / total
    avg_steps = sum(len(l["steps"]) for l in logs) / total

    top_complaints = _extract_top_complaints(logs)
    dropoff_reasons, purchase_reasons, drop_themes = _build_agent_insights(logs)

    # Per-persona breakdown
    agent_breakdown: dict[str, dict] = {}
    for ptype in PERSONA_TYPES:
        group = [l for l in logs if l["persona_type"] == ptype]
        if not group:
            continue
        group_purchased = [l for l in group if l["result"] == "purchased"]
        group_left = [l for l in group if l["result"] == "left"]

        # Most common drop theme for this persona type
        theme_counts: dict[str, int] = {}
        for d in dropoff_reasons:
            if d["persona_type"] == ptype:
                t = d["theme"]
                theme_counts[t] = theme_counts.get(t, 0) + 1
        top_theme = max(theme_counts, key=theme_counts.get) if theme_counts else None

        agent_breakdown[ptype] = {
            "conversion_rate": round(len(group_purchased) / len(group), 3),
            "avg_steps": round(sum(len(l["steps"]) for l in group) / len(group), 2),
            "count": len(group),
            "top_drop_theme": top_theme,
        }

    return {
        "conversion_rate": round(conversion_rate, 3),
        "dropoff_rate": round(dropoff_rate, 3),
        "avg_steps": round(avg_steps, 2),
        "ux_score": ux_score,
        "top_complaints": top_complaints,
        "agent_breakdown": agent_breakdown,
        "total_agents": total,
        "purchased_count": purchased_count,
        "left_count": left_count,
        "dropoff_reasons": dropoff_reasons,
        "purchase_reasons": purchase_reasons,
        "drop_themes": drop_themes,
    }
