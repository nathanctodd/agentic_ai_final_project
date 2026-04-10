"""Phase 3: Screenshot capture + GPT-4o vision analysis of e-commerce pages."""

from __future__ import annotations

import base64
import json
import os

from openai import OpenAI

_client: OpenAI | None = None


def _get_client() -> OpenAI:
    global _client
    if _client is None:
        _client = OpenAI(api_key=os.getenv("OPENAI_API_KEY"))
    return _client


# ---------------------------------------------------------------------------
# Screenshot
# ---------------------------------------------------------------------------

def screenshot_page(url: str) -> bytes | None:
    """Capture a full-page screenshot using Playwright.

    Returns PNG bytes, or None if Playwright is not installed / capture fails.
    """
    try:
        from playwright.sync_api import sync_playwright  # type: ignore
    except ImportError:
        return None

    try:
        with sync_playwright() as pw:
            browser = pw.chromium.launch(headless=True)
            page = browser.new_page(viewport={"width": 1440, "height": 900})
            page.goto(url, timeout=30_000, wait_until="networkidle")
            # Scroll to trigger lazy-loads
            page.evaluate("window.scrollTo(0, document.body.scrollHeight)")
            page.wait_for_timeout(1500)
            png_bytes: bytes = page.screenshot(full_page=True)
            browser.close()
            return png_bytes
    except Exception:
        return None


# ---------------------------------------------------------------------------
# Vision analysis
# ---------------------------------------------------------------------------

_VISION_SCHEMA = """{
  "visual_first_impression": "<one sentence on what a user sees within 3 seconds>",
  "layout_clarity": "<score 1-10 and 1-sentence rationale>",
  "cta_visibility": "<are CTAs prominent? score 1-10>",
  "trust_signals": "<badges, reviews, guarantees visible? list>",
  "friction_points": ["<friction 1>", "<friction 2>"],
  "mobile_readiness_guess": "<desktop-only / responsive / unknown>",
  "visual_score": <integer 1-10>,
  "one_line_verdict": "<most important single improvement>"
}"""


def analyze_screenshot_with_vision(
    screenshot_bytes: bytes,
    site_context: dict | None = None,
) -> dict:
    """Send screenshot to GPT-4o vision and return structured UX feedback.

    Args:
        screenshot_bytes: PNG bytes from screenshot_page().
        site_context: Optional parsed site dict to give GPT-4o extra context.

    Returns:
        Dict matching _VISION_SCHEMA, or empty dict on failure.
    """
    b64 = base64.b64encode(screenshot_bytes).decode("utf-8")

    context_text = ""
    if site_context:
        headline = site_context.get("headline", "")
        cta = site_context.get("cta_text", "")
        ux_score = site_context.get("ux_score", "N/A")
        context_text = (
            f"\n\nAdditional context from HTML analysis:\n"
            f"- Headline: {headline}\n"
            f"- CTA text found: {cta}\n"
            f"- Structural UX score: {ux_score}/100"
        )

    system_prompt = (
        "You are an expert UX analyst specialising in e-commerce conversion optimisation. "
        "You will be shown a screenshot of an e-commerce page. "
        "Respond ONLY with a valid JSON object matching the schema provided — no markdown, no prose."
    )

    user_prompt = (
        f"Analyse this e-commerce page screenshot and return a JSON object with exactly these keys:\n"
        f"{_VISION_SCHEMA}"
        f"{context_text}"
    )

    try:
        client = _get_client()
        response = client.chat.completions.create(
            model="gpt-4o",
            response_format={"type": "json_object"},
            messages=[
                {"role": "system", "content": system_prompt},
                {
                    "role": "user",
                    "content": [
                        {"type": "text", "text": user_prompt},
                        {
                            "type": "image_url",
                            "image_url": {
                                "url": f"data:image/png;base64,{b64}",
                                "detail": "high",
                            },
                        },
                    ],
                },
            ],
            max_tokens=1000,
        )
        raw = response.choices[0].message.content or "{}"
        return json.loads(raw)
    except Exception:
        return {}


# ---------------------------------------------------------------------------
# Combined convenience helper
# ---------------------------------------------------------------------------

def run_visual_analysis(url: str, site_context: dict | None = None) -> dict:
    """Screenshot + vision analysis in one call.

    Returns vision analysis dict, or {} if screenshot fails.
    """
    png = screenshot_page(url)
    if not png:
        return {}
    return analyze_screenshot_with_vision(png, site_context=site_context)
