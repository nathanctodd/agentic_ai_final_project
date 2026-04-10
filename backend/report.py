"""
report.py — LLM-powered UX consultant report.

After all agents have run, sends a single GPT-4o call that synthesizes
the simulation results into an actionable executive report.
"""

import json
import os

from dotenv import load_dotenv
from openai import OpenAI

load_dotenv()

_client: OpenAI | None = None


def _get_client() -> OpenAI:
    global _client
    if _client is None:
        _client = OpenAI(api_key=os.getenv("OPENAI_API_KEY"))
    return _client


_SYSTEM_PROMPT = (
    "You are a senior UX researcher and conversion rate optimization expert. "
    "You receive simulation data from AI customer agents that browsed an e-commerce site "
    "and you produce a structured, actionable report. "
    "Respond ONLY with valid JSON matching the schema provided. Be specific — "
    "reference actual agent behavior, product names, and metrics."
)

_REPORT_SCHEMA = """{
  "executive_summary": "<2-3 sentence plain-English overview of site performance>",
  "overall_score": <integer 1-10 representing overall site effectiveness>,
  "score_rationale": "<one sentence explaining the score>",
  "critical_issues": [
    "<specific issue 1 with evidence from agent behavior>",
    "<specific issue 2>",
    ...
  ],
  "quick_wins": [
    "<concrete actionable improvement #1>",
    "<concrete actionable improvement #2>",
    ...
  ],
  "persona_insights": {
    "budget": "<what specifically drove budget shoppers' behavior — reference their reasons>",
    "luxury": "<what drove luxury shoppers — did they find quality signals?>",
    "impulsive": "<what drove impulsive shoppers — were there urgency or friction blockers?>"
  },
  "tools_used_insights": "<what does the pattern of tool calls (reviews searched, policies checked, products compared) reveal about trust or UX gaps?>",
  "redesign_priorities": [
    "<highest priority change with expected impact>",
    "<second priority>",
    "<third priority>"
  ]
}"""


def _summarize_logs(logs: list[dict]) -> str:
    """Convert agent logs into a compact narrative for the report prompt."""
    lines = []
    for log in logs:
        aid = log["agent_id"]
        ptype = log["persona_type"]
        budget = log["budget"]
        result = log["result"]
        steps = log.get("steps", [])

        journey = []
        for s in steps:
            action = s["action"]
            target = s.get("target", "")
            reason = s.get("reason", "")
            preview = s.get("tool_result_preview", "")

            entry = f"    [{action}]"
            if target:
                entry += f" → {target}"
            if reason:
                entry += f'\n      Reason: "{reason}"'
            if preview and action not in ("purchase", "leave"):
                # Only include a snippet of tool output to keep prompt size reasonable
                entry += f"\n      Tool output snippet: {preview[:120]}..."
            journey.append(entry)

        lines.append(
            f"{aid} ({ptype}, ${budget} budget) → {result.upper()} "
            f"after {len(steps)} tool calls:\n" + "\n".join(journey)
        )

    return "\n\n".join(lines)


def generate_ux_report(
    parsed_site: dict,
    logs: list[dict],
    analytics: dict,
) -> dict:
    """Generate a GPT-4o powered UX consultant report from simulation results.

    Args:
        parsed_site: Output from parser.parse() — has headline, products, ux_breakdown, etc.
        logs: Full agent logs from simulation.run_simulation()
        analytics: Output from analytics.compute_analytics()

    Returns:
        Structured report dict. On any failure, returns a minimal fallback dict.
    """
    products_summary = ", ".join(
        f"{p['name']} ({p['price']})"
        for p in parsed_site.get("products", [])
    ) or "none detected"

    ux_breakdown = parsed_site.get("ux_breakdown", {})
    ux_flags = ", ".join(
        f"{'✓' if v else '✗'} {k}" for k, v in ux_breakdown.items()
    )

    agent_log_text = _summarize_logs(logs)

    # Build theme summary for the prompt
    drop_themes = analytics.get("drop_themes", {})
    theme_text = (
        ", ".join(f"{k}: {v} agents" for k, v in drop_themes.items())
        if drop_themes
        else "none"
    )

    # Count how often each tool was used across all agents
    tool_usage: dict[str, int] = {}
    for log in logs:
        for step in log.get("steps", []):
            t = step.get("action", "")
            if t not in ("purchase", "leave"):
                tool_usage[t] = tool_usage.get(t, 0) + 1
    tool_usage_text = (
        ", ".join(f"{t}: {n}x" for t, n in sorted(tool_usage.items(), key=lambda x: -x[1]))
        if tool_usage
        else "none"
    )

    prompt = f"""SIMULATION REPORT DATA

SITE OVERVIEW:
  Headline: {parsed_site.get('headline', 'Unknown')}
  Products detected: {products_summary}
  UX Score: {parsed_site.get('ux_score', '?')}/100
  UX Checks: {ux_flags}

SIMULATION METRICS:
  Total agents: {analytics.get('total_agents', 9)}
  Conversion rate: {analytics.get('conversion_rate', 0) * 100:.0f}%
  Drop-off rate: {analytics.get('dropoff_rate', 0) * 100:.0f}%
  Average tool calls before decision: {analytics.get('avg_steps', 0):.1f}
  Drop-off themes: {theme_text}
  Tool usage across all agents: {tool_usage_text}

AGENT JOURNEYS (full detail):
{agent_log_text}

Now produce a UX consultant report using EXACTLY this JSON schema:
{_REPORT_SCHEMA}

Important: Be specific. Reference actual agent quotes, product names, and the tools they \
called. A generic report is not useful. Tie every claim to observed behavior."""

    try:
        response = _get_client().chat.completions.create(
            model="gpt-4o",
            response_format={"type": "json_object"},
            messages=[
                {"role": "system", "content": _SYSTEM_PROMPT},
                {"role": "user", "content": prompt},
            ],
            temperature=0.3,
            max_tokens=1200,
        )
        raw = response.choices[0].message.content or "{}"
        return json.loads(raw)

    except Exception as e:
        return {
            "executive_summary": f"Report generation failed: {e}",
            "overall_score": 0,
            "score_rationale": "Could not generate report.",
            "critical_issues": [],
            "quick_wins": [],
            "persona_insights": {"budget": "", "luxury": "", "impulsive": ""},
            "tools_used_insights": "",
            "redesign_priorities": [],
        }
