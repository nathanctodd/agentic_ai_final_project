"""
eval_script.py — Quantitative evaluation script.

Runs the full simulation (9 personas) AND a generic no-persona baseline
against a list of URLs, then writes results to ../eval_results.txt.

Usage:
    cd backend
    python eval_script.py
"""

import asyncio
import os
import sys
from datetime import datetime

from dotenv import load_dotenv

load_dotenv()

from scraper import scrape, ScraperError
from parser import parse
from simulation import run_simulation
from analytics import compute_analytics
from report import generate_ux_report

# ---------------------------------------------------------------------------
# URLs to test — edit this list
# ---------------------------------------------------------------------------

TEST_URLS = [
    "https://www.nike.com/w/mens-running-shoes",
    "https://www.allbirds.com/collections/mens-shoes",
    "https://kith.com/collections/footwear",
]

# ---------------------------------------------------------------------------
# Baseline: single generic persona with no budget/goal framing
# ---------------------------------------------------------------------------

BASELINE_PERSONAS = [
    {
        "id": "generic_1",
        "type": "generic",
        "budget": 200,
        "impulsiveness": 0.5,
        "goal": "browse the site and decide whether to buy something",
    },
    {
        "id": "generic_2",
        "type": "generic",
        "budget": 200,
        "impulsiveness": 0.5,
        "goal": "browse the site and decide whether to buy something",
    },
    {
        "id": "generic_3",
        "type": "generic",
        "budget": 200,
        "impulsiveness": 0.5,
        "goal": "browse the site and decide whether to buy something",
    },
]

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _persona_variance(logs: list[dict]) -> float:
    """Std dev of per-archetype conversion rates. 0 if only one type present."""
    from analytics import PERSONA_TYPES
    import math

    rates = []
    for ptype in PERSONA_TYPES:
        group = [l for l in logs if l["persona_type"] == ptype]
        if group:
            rate = sum(1 for l in group if l["result"] == "purchased") / len(group)
            rates.append(rate)

    if len(rates) < 2:
        return 0.0
    mean = sum(rates) / len(rates)
    variance = sum((r - mean) ** 2 for r in rates) / len(rates)
    return round(math.sqrt(variance), 3)


def _avg_reason_length(logs: list[dict]) -> float:
    """Average word count of exit/purchase reasons — proxy for reasoning specificity."""
    lengths = []
    for log in logs:
        if log["steps"]:
            reason = log["steps"][-1].get("reason", "")
            if reason:
                lengths.append(len(reason.split()))
    return round(sum(lengths) / len(lengths), 1) if lengths else 0.0


def _run_one(url: str, personas: list[dict] | None, label: str) -> dict:
    """Scrape, parse, simulate, and compute analytics for one URL + persona set."""
    print(f"  [{label}] Scraping {url}...")
    try:
        raw = scrape(url)
    except ScraperError as e:
        return {"error": str(e)}

    parsed = parse(raw, base_url=url)
    print(f"  [{label}] Running simulation ({len(personas or []) or 9} agents)...")

    logs = run_simulation(parsed, price_modifier=1.0, base_url=url, custom_personas=personas)
    analytics = compute_analytics(logs, parsed["ux_score"])

    print(f"  [{label}] Generating UX report...")
    try:
        report = asyncio.get_event_loop().run_until_complete(
            asyncio.to_thread(generate_ux_report, parsed, logs, analytics)
        )
    except Exception:
        report = {}

    return {
        "label": label,
        "url": url,
        "ux_score": parsed["ux_score"],
        "conversion_rate": analytics["conversion_rate"],
        "dropoff_rate": analytics["dropoff_rate"],
        "avg_steps": analytics["avg_steps"],
        "purchased_count": analytics["purchased_count"],
        "left_count": analytics["left_count"],
        "total_agents": analytics["total_agents"],
        "drop_themes": analytics.get("drop_themes", {}),
        "agent_breakdown": analytics.get("agent_breakdown", {}),
        "top_complaints": analytics.get("top_complaints", []),
        "persona_variance": _persona_variance(logs),
        "avg_reason_length": _avg_reason_length(logs),
        "executive_summary": report.get("executive_summary", ""),
        "critical_issues": report.get("critical_issues", []),
        "quick_wins": report.get("quick_wins", []),
        "overall_score": report.get("overall_score", 0),
    }


def _fmt_breakdown(breakdown: dict) -> str:
    lines = []
    for ptype, stats in breakdown.items():
        conv = f"{stats['conversion_rate']*100:.0f}%"
        steps = stats["avg_steps"]
        theme = stats.get("top_drop_theme") or "none"
        lines.append(f"    {ptype:10s}  conv={conv:4s}  avg_steps={steps:.1f}  top_drop={theme}")
    return "\n".join(lines) if lines else "    (none)"


def _write_results(all_results: list[dict], output_path: str) -> None:
    lines = []
    lines.append("=" * 70)
    lines.append("AGENTIC AI SHOPPER SIMULATOR — EVALUATION RESULTS")
    lines.append(f"Generated: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}")
    lines.append("=" * 70)

    # ---- Summary table ----
    lines.append("\nSUMMARY TABLE")
    lines.append("-" * 70)
    header = f"{'URL':<35} {'Mode':<10} {'UX':>4} {'Conv':>5} {'Steps':>5} {'PVar':>5} {'Spec':>5}"
    lines.append(header)
    lines.append("-" * 70)

    for r in all_results:
        if "error" in r:
            lines.append(f"{r.get('url','?'):<35} ERROR: {r['error']}")
            continue
        url_short = r["url"].replace("https://www.", "").replace("https://", "")[:34]
        conv = f"{r['conversion_rate']*100:.0f}%"
        lines.append(
            f"{url_short:<35} {r['label']:<10} {r['ux_score']:>4} "
            f"{conv:>5} {r['avg_steps']:>5.1f} {r['persona_variance']:>5.3f} {r['avg_reason_length']:>5.1f}"
        )

    lines.append("-" * 70)
    lines.append("Columns: UX=structural UX score (0-100), Conv=conversion rate,")
    lines.append("         Steps=avg tool calls, PVar=persona variance (std dev of")
    lines.append("         per-type conversion rates), Spec=avg exit reason word count")

    # ---- Per-URL detail ----
    lines.append("\n\n" + "=" * 70)
    lines.append("DETAILED RESULTS BY URL")
    lines.append("=" * 70)

    for r in all_results:
        if "error" in r:
            continue
        lines.append(f"\nURL:   {r['url']}")
        lines.append(f"Mode:  {r['label']}")
        lines.append(f"UX Score:        {r['ux_score']}/100")
        lines.append(f"Conversion rate: {r['conversion_rate']*100:.0f}%  ({r['purchased_count']}/{r['total_agents']} agents purchased)")
        lines.append(f"Drop-off rate:   {r['dropoff_rate']*100:.0f}%  ({r['left_count']}/{r['total_agents']} agents left)")
        lines.append(f"Avg steps:       {r['avg_steps']}")
        lines.append(f"Persona variance:{r['persona_variance']}  (0=no differentiation, 0.5=max)")
        lines.append(f"Avg reason len:  {r['avg_reason_length']} words")
        lines.append(f"Drop themes:     {r['drop_themes'] or 'none'}")
        lines.append(f"Top complaints:  {', '.join(r['top_complaints']) or 'none'}")

        if r["agent_breakdown"]:
            lines.append("Per-persona breakdown:")
            lines.append(_fmt_breakdown(r["agent_breakdown"]))

        if r["overall_score"]:
            lines.append(f"GPT-4o UX score: {r['overall_score']}/10")

        if r["executive_summary"]:
            lines.append(f"Executive summary:")
            lines.append(f"  {r['executive_summary']}")

        if r["critical_issues"]:
            lines.append("Critical issues:")
            for issue in r["critical_issues"]:
                lines.append(f"  - {issue}")

        if r["quick_wins"]:
            lines.append("Quick wins:")
            for win in r["quick_wins"]:
                lines.append(f"  - {win}")

        lines.append("-" * 70)

    output = "\n".join(lines)
    with open(output_path, "w") as f:
        f.write(output)

    print(f"\nResults written to: {output_path}")


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

def main():
    output_path = os.path.join(os.path.dirname(__file__), "..", "eval_results.txt")
    output_path = os.path.normpath(output_path)

    all_results = []

    for url in TEST_URLS:
        print(f"\n{'='*50}")
        print(f"Testing: {url}")
        print(f"{'='*50}")

        # Full system — 9 personas
        result_full = _run_one(url, personas=None, label="full")
        all_results.append(result_full)

        # Baseline — 3 generic personas
        result_base = _run_one(url, personas=BASELINE_PERSONAS, label="baseline")
        all_results.append(result_base)

    _write_results(all_results, output_path)


if __name__ == "__main__":
    main()
