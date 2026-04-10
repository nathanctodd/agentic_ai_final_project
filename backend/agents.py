"""
agents.py — Real tool-calling agents using OpenAI's native function-calling API.

Each persona runs an open-ended loop: the LLM decides which tools to call and in
what order, receives tool results, and continues until it calls `purchase` or `leave`.
No fixed step list — the agent owns its own path.
"""

import json
import os
import time

from dotenv import load_dotenv
from openai import OpenAI, RateLimitError, APIError

load_dotenv()

_client: OpenAI | None = None


def _get_client() -> OpenAI:
    global _client
    if _client is None:
        api_key = os.getenv("OPENAI_API_KEY")
        if not api_key:
            raise RuntimeError("OPENAI_API_KEY is not set in the environment")
        _client = OpenAI(api_key=api_key)
    return _client


# ---------------------------------------------------------------------------
# Persona definitions
# ---------------------------------------------------------------------------

PERSONAS = [
    # Budget shoppers — price-sensitive, careful, will buy if price is right
    {"id": "budget_1", "type": "budget", "budget": 90,  "impulsiveness": 0.20,
     "goal": "find the best deal available — I will buy if the price feels fair"},
    {"id": "budget_2", "type": "budget", "budget": 110, "impulsiveness": 0.25,
     "goal": "get solid value for money; I'll purchase if it's within my range"},
    {"id": "budget_3", "type": "budget", "budget": 80,  "impulsiveness": 0.15,
     "goal": "spend as little as possible but I will buy something affordable today"},

    # Luxury shoppers — quality-focused, happy to spend freely
    {"id": "luxury_1", "type": "luxury", "budget": 500, "impulsiveness": 0.60,
     "goal": "find the highest quality premium product and buy it without hesitation"},
    {"id": "luxury_2", "type": "luxury", "budget": 400, "impulsiveness": 0.50,
     "goal": "purchase a premium product that signals quality; I'll buy if it impresses me"},
    {"id": "luxury_3", "type": "luxury", "budget": 600, "impulsiveness": 0.65,
     "goal": "buy the best version of this product — price is not a barrier"},

    # Impulsive shoppers — act fast, willing to stretch budget
    {"id": "impulsive_1", "type": "impulsive", "budget": 180, "impulsiveness": 0.90,
     "goal": "buy something right now if it catches my eye — I act on feeling"},
    {"id": "impulsive_2", "type": "impulsive", "budget": 200, "impulsiveness": 0.85,
     "goal": "I came here to buy something; the first appealing product I see, I purchase"},
    {"id": "impulsive_3", "type": "impulsive", "budget": 150, "impulsiveness": 0.95,
     "goal": "make a quick purchase decision based on gut feeling — I almost always buy"},
]

# ---------------------------------------------------------------------------
# Tool schemas (OpenAI function-calling format)
# ---------------------------------------------------------------------------

TOOLS = [
    {
        "type": "function",
        "function": {
            "name": "view_product",
            "description": (
                "Navigate to a product's detail page to read its full specs, materials, "
                "sizing info, and any on-page details. Use this when you need more "
                "information before deciding."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "product_name": {
                        "type": "string",
                        "description": "The exact product name as listed on the page",
                    }
                },
                "required": ["product_name"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "compare_products",
            "description": (
                "Compare two or more products side-by-side on price, description, and "
                "value relative to your budget and goal. Use when torn between options."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "product_names": {
                        "type": "array",
                        "items": {"type": "string"},
                        "description": "List of product names to compare (2–4 products)",
                    }
                },
                "required": ["product_names"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "search_reviews",
            "description": (
                "Search the web for reviews, ratings, and real user opinions about a product. "
                "Use before committing to a purchase if you want outside validation."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "query": {
                        "type": "string",
                        "description": "Search query, e.g. 'Nike Air Max 270 review comfort durability'",
                    }
                },
                "required": ["query"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "check_return_policy",
            "description": (
                "Read the site's return and refund policy. Use when you're nervous about "
                "committing to a purchase and want to know if you can return it easily."
            ),
            "parameters": {"type": "object", "properties": {}, "required": []},
        },
    },
    {
        "type": "function",
        "function": {
            "name": "navigate_to_page",
            "description": (
                "Navigate to a different section of the website — such as the shopping cart, "
                "checkout, shipping info, size guide, or a different product category. "
                "Use this to explore the full site beyond the product listing. "
                "Do NOT use this for individual product detail pages — use view_product instead."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "destination": {
                        "type": "string",
                        "description": (
                            "Where you want to go, e.g. 'checkout', 'shopping cart', "
                            "'shipping info', 'size guide', 'sale section', 'men\\'s running shoes'"
                        ),
                    }
                },
                "required": ["destination"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "purchase",
            "description": "Add a product to cart and complete the purchase. Call this when you have decided to buy.",
            "parameters": {
                "type": "object",
                "properties": {
                    "product_name": {
                        "type": "string",
                        "description": "Name of the product you are buying",
                    },
                    "reason": {
                        "type": "string",
                        "description": "Why you decided to buy this product (1-2 sentences in first person)",
                    },
                },
                "required": ["product_name", "reason"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "leave",
            "description": "Leave the site without purchasing. Call this only when you have a clear reason not to buy.",
            "parameters": {
                "type": "object",
                "properties": {
                    "reason": {
                        "type": "string",
                        "description": "Why you are leaving without buying (1-2 sentences in first person)",
                    }
                },
                "required": ["reason"],
            },
        },
    },
]

MAX_TURNS = 8  # max tool calls per agent before forcing a leave

# ---------------------------------------------------------------------------
# System prompt builder
# ---------------------------------------------------------------------------

def _build_system_prompt(persona: dict, page_data: dict, price_modifier: float) -> str:
    price_note = ""
    if price_modifier < 1.0:
        price_note = f" (prices reduced {round((1-price_modifier)*100)}% in this scenario)"
    elif price_modifier > 1.0:
        price_note = f" (prices increased {round((price_modifier-1)*100)}% in this scenario)"

    products_lines = []
    for p in page_data.get("products", []):
        raw = p.get("price", "unknown")
        if price_modifier != 1.0 and raw != "unknown":
            cleaned = raw.replace("$", "").replace("USD", "").replace("usd", "").strip()
            try:
                raw = f"${float(cleaned) * price_modifier:.2f}"
            except ValueError:
                pass
        desc = p.get("description", "")
        products_lines.append(
            f"  • {p['name']}: {raw}" + (f"  — {desc}" if desc else "")
        )

    return f"""You are simulating a real customer browsing an e-commerce website. \
Behave authentically based on your persona.

YOUR PERSONA:
  Type: {persona['type']} shopper
  Budget: ${persona['budget']}{price_note}
  Impulsiveness: {persona['impulsiveness']}/1.0  (1.0 = extremely impulsive)
  Goal: {persona['goal']}

CURRENT SITE:
  Headline: {page_data.get('headline', 'Unknown')}
  Call-to-action: {page_data.get('cta_text', 'none detected')}
  UX Score: {page_data.get('ux_score', '?')}/100

PRODUCTS ON THIS PAGE:
{chr(10).join(products_lines) or '  (none detected)'}

HOW TO SHOP:
Use your tools as a real shopper would — in any order, as many times as makes sense:
  • view_product   → get full details on a specific item before deciding
  • compare_products     → weigh multiple options side-by-side
  • search_reviews       → check outside opinions if unsure about quality or value
  • check_return_policy  → read the returns policy if purchase risk worries you
  • purchase             → buy when you've found what you want (REQUIRED to end session)
  • leave                → leave without buying only if you have a genuine reason

Stay true to your persona. Impulsive shoppers act fast. Budget shoppers check prices carefully. \
Luxury shoppers prioritize quality. You MUST eventually call either purchase or leave."""


# ---------------------------------------------------------------------------
# Tool implementations
# ---------------------------------------------------------------------------

def _tool_view_product(product_name: str, context: dict) -> str:
    """Scrape the product's detail page and return a readable summary."""
    from scraper import scrape_product_page

    product_urls: dict = context.get("product_urls", {})
    cache: dict = context.get("product_page_cache", {})

    # Find URL — exact then fuzzy
    url = product_urls.get(product_name)
    if not url:
        name_lower = product_name.lower()
        for k, v in product_urls.items():
            if name_lower in k.lower() or k.lower() in name_lower:
                url = v
                break

    if not url:
        # Fall back to in-memory product data
        products = context.get("page_data", {}).get("products", [])
        match = next((p for p in products if product_name.lower() in p["name"].lower()), None)
        if match:
            return (
                f"Product: {match['name']}\n"
                f"Price: {match['price']}\n"
                f"Description: {match.get('description', 'No description available.')}\n"
                "(No dedicated product page found — showing listing data only)"
            )
        return f"Could not find product matching '{product_name}'."

    if url not in cache:
        cache[url] = scrape_product_page(url) or {}

    detail = cache[url]
    if not detail:
        return f"Could not load product page for '{product_name}'."

    specs = "\n".join(f"  • {s}" for s in (detail.get("specs") or [])[:6])
    return (
        f"Product: {detail.get('name') or product_name}\n"
        f"Price: {detail.get('price') or 'unknown'}\n"
        f"Description: {detail.get('description') or detail.get('page_text', '')[:300]}\n"
        f"Specs/Features:\n{specs or '  (none listed)'}\n"
        f"Page: {url}"
    )


def _tool_compare_products(product_names: list[str], context: dict) -> str:
    """Return a side-by-side comparison of the requested products."""
    products = context.get("page_data", {}).get("products", [])
    price_modifier = context.get("price_modifier", 1.0)

    rows = []
    for name in product_names[:4]:
        match = next(
            (p for p in products if name.lower() in p["name"].lower()
             or p["name"].lower() in name.lower()),
            None,
        )
        if not match:
            rows.append(f"  ✗ '{name}' — not found in product list")
            continue

        raw = match.get("price", "unknown")
        if price_modifier != 1.0 and raw != "unknown":
            cleaned = raw.replace("$", "").replace("USD", "").strip()
            try:
                raw = f"${float(cleaned) * price_modifier:.2f}"
            except ValueError:
                pass

        rows.append(
            f"  {match['name']}\n"
            f"    Price:       {raw}\n"
            f"    Description: {match.get('description', '—')}"
        )

    return "COMPARISON:\n" + "\n\n".join(rows)


def _tool_search_reviews(query: str, context: dict) -> str:  # noqa: ARG001
    """Search DuckDuckGo for real reviews and return top snippets."""
    try:
        from ddgs import DDGS

        results = list(DDGS().text(query, max_results=4))
        if not results:
            return "No review results found for that query."

        lines = []
        for r in results:
            title = r.get("title", "")
            body = r.get("body", "")[:200]
            href = r.get("href", "")
            lines.append(f"[{title}]\n{body}\n({href})")

        return "REVIEW SEARCH RESULTS:\n\n" + "\n\n---\n\n".join(lines)

    except ImportError:
        return "Web search unavailable (duckduckgo-search not installed)."
    except Exception as e:
        return f"Review search failed: {e}"


def _tool_check_return_policy(context: dict) -> str:
    """Try to find and scrape the site's return policy page."""
    from scraper import scrape, ScraperError

    base_url: str = context.get("base_url", "")
    if not base_url:
        return "Could not determine the site's base URL to look up return policy."

    from urllib.parse import urlparse, urljoin

    parsed = urlparse(base_url)
    root = f"{parsed.scheme}://{parsed.netloc}"

    candidates = [
        "/returns", "/return-policy", "/refund-policy",
        "/help/returns", "/support/returns", "/customer-service/returns",
        "/pages/returns", "/pages/refund-policy",
    ]

    for path in candidates:
        url = urljoin(root, path)
        try:
            raw = scrape(url)
            text = raw.get("text", "").strip()
            if len(text) > 100:
                return f"RETURN POLICY (from {url}):\n\n{text[:800]}"
        except ScraperError:
            continue

    return (
        "Could not locate a return policy page automatically. "
        "The site may not have a publicly accessible returns page, "
        "or it may require login."
    )


def _tool_navigate_to_page(destination: str, context: dict) -> str:
    """Navigate to a different section of the site and return a summary of what's there."""
    from scraper import scrape, ScraperError
    from parser import parse
    from urllib.parse import urljoin, urlparse

    base_url: str = context.get("base_url", "")
    if not base_url:
        return "Cannot navigate — base URL unknown."

    parsed_base = urlparse(base_url)
    root = f"{parsed_base.scheme}://{parsed_base.netloc}"
    dest_lower = destination.lower()

    # Keyword → candidate URL paths
    _DEST_MAP = {
        "cart":     ["/cart", "/bag", "/shopping-bag", "/shopping-cart", "/checkout/cart"],
        "checkout": ["/checkout", "/order", "/buy"],
        "shipping": ["/shipping", "/delivery", "/help/shipping", "/pages/shipping"],
        "returns":  ["/returns", "/return-policy", "/refund-policy", "/pages/returns"],
        "sale":     ["/sale", "/clearance", "/outlet", "/w/sale"],
        "size":     ["/size-guide", "/size-chart", "/help/size-guide", "/pages/size-guide"],
        "men":      ["/w/mens-shoes", "/collections/mens", "/mens"],
        "women":    ["/w/womens-shoes", "/collections/womens", "/womens"],
        "running":  ["/w/running-shoes", "/collections/running", "/running"],
    }

    # Find the best candidate list
    candidates: list[str] = []
    for keyword, paths in _DEST_MAP.items():
        if keyword in dest_lower:
            candidates = [urljoin(root, p) for p in paths]
            break

    # Also try to find matching links from the page's scraped link list
    page_links: list[str] = context.get("page_data", {}).get("links", []) or []
    for link in page_links:
        link_lower = link.lower()
        if any(word in link_lower for word in dest_lower.split()):
            abs_link = link if link.startswith("http") else urljoin(root, link)
            if abs_link not in candidates:
                candidates.append(abs_link)

    # Fallback: try root + destination slug directly
    if not candidates:
        slug = dest_lower.replace(" ", "-").replace("_", "-")
        candidates = [urljoin(root, f"/{slug}"), urljoin(root, f"/w/{slug}")]

    # Try each candidate until one succeeds
    for url in candidates[:4]:
        try:
            raw = scrape(url)
            # Skip pages that look like they redirected back to home
            if len(raw.get("text", "")) < 80:
                continue
            page = parse(raw, base_url=url)
            # Update mutable context so further tools (compare, etc.) use this page
            context["current_page_data"] = page
            context["current_url"] = url

            summary_lines = [
                f"NAVIGATED TO: {destination.title()} ({url})",
                f"Page headline: {page.get('headline', 'Unknown')}",
            ]
            if page.get("products"):
                prod_lines = [
                    f"  • {p['name']} — {p['price']}"
                    for p in page["products"]
                ]
                summary_lines.append("Products/items on this page:")
                summary_lines.extend(prod_lines)
            else:
                text_snippet = page.get("raw_text_truncated", "")[:400]
                summary_lines.append(f"Page content: {text_snippet}")
            return "\n".join(summary_lines)

        except ScraperError:
            continue

    return (
        f"Could not navigate to '{destination}'. "
        "The page may require login or doesn't exist at common URL paths. "
        "Consider using view_product for product pages, or search_reviews for outside info."
    )


def _execute_tool(name: str, args: dict, context: dict) -> str:
    """Dispatch a tool call to its implementation."""
    if name == "view_product":
        return _tool_view_product(args.get("product_name", ""), context)
    if name == "compare_products":
        return _tool_compare_products(args.get("product_names", []), context)
    if name == "search_reviews":
        return _tool_search_reviews(args.get("query", ""), context)
    if name == "check_return_policy":
        return _tool_check_return_policy(context)
    if name == "navigate_to_page":
        return _tool_navigate_to_page(args.get("destination", ""), context)
    return f"Unknown tool: {name}"


# ---------------------------------------------------------------------------
# Main agent loop
# ---------------------------------------------------------------------------

def run_agent_loop(
    persona: dict,
    page_data: dict,
    price_modifier: float = 1.0,
    product_page_cache: dict | None = None,
    base_url: str = "",
    on_step: "callable | None" = None,
) -> tuple[list[dict], str]:
    """Run a single persona through an open-ended tool-calling shopping session.

    The agent decides its own sequence of tool calls. The loop ends when the
    agent calls `purchase` or `leave`, or after MAX_TURNS tool calls.

    Args:
        on_step: Optional callback invoked after every tool call with the step record.
                 Used by the streaming endpoint to emit real-time events.

    Returns:
        (steps, result) where result is "purchased" or "left".
    """
    if product_page_cache is None:
        product_page_cache = {}

    context = {
        "page_data": page_data,
        "current_page_data": page_data,   # updated by navigate_to_page
        "current_url": base_url,
        "price_modifier": price_modifier,
        "product_urls": page_data.get("product_urls", {}),
        "product_page_cache": product_page_cache,
        "base_url": base_url,
        "links": page_data.get("links", []),
    }

    messages = [
        {"role": "system", "content": _build_system_prompt(persona, page_data, price_modifier)},
    ]

    steps: list[dict] = []
    result = "left"
    client = _get_client()

    for _turn in range(MAX_TURNS):
        # Call the API
        try:
            response = client.chat.completions.create(
                model="gpt-4o-mini",
                messages=messages,
                tools=TOOLS,
                tool_choice="required",  # agent MUST call a tool each turn
                temperature=0.7,
                max_tokens=400,
            )
        except RateLimitError:
            time.sleep(2)
            try:
                response = client.chat.completions.create(
                    model="gpt-4o-mini",
                    messages=messages,
                    tools=TOOLS,
                    tool_choice="required",
                    temperature=0.7,
                    max_tokens=400,
                )
            except Exception:
                break
        except APIError:
            break
        except Exception:
            break

        msg = response.choices[0].message
        messages.append(msg)

        if not msg.tool_calls:
            break

        # Process the first tool call (primary decision)
        tool_call = msg.tool_calls[0]
        tool_name = tool_call.function.name

        try:
            args = json.loads(tool_call.function.arguments or "{}")
        except json.JSONDecodeError:
            args = {}

        # Build step record
        step: dict = {
            "step": len(steps) + 1,
            "agent_id": persona["id"],
            "action": tool_name,
            "target": (
                args.get("product_name")
                or (args.get("product_names") or [""])[0]
                or args.get("query")
                or ""
            )[:100],
            "reason": args.get("reason", ""),
        }

        # ---- Terminal actions ----
        if tool_name in ("purchase", "leave"):
            if tool_name == "purchase":
                result = "purchased"
            steps.append(step)
            if on_step:
                on_step(step)
            # Must still return a tool result to keep the message thread valid
            messages.append({
                "role": "tool",
                "tool_call_id": tool_call.id,
                "content": "Session ended.",
            })
            # Acknowledge any parallel tool calls the model may have emitted
            for extra in msg.tool_calls[1:]:
                messages.append({
                    "role": "tool",
                    "tool_call_id": extra.id,
                    "content": "Session ended.",
                })
            break

        # ---- Non-terminal: execute and feed result back ----
        tool_result = _execute_tool(tool_name, args, context)
        step["tool_result_preview"] = tool_result[:400]

        # Enrich step with navigation/product metadata
        if tool_name == "view_product":
            url = context["product_urls"].get(step["target"])
            if url and url in product_page_cache and product_page_cache[url]:
                step["product_url"] = url
                step["product_detail_fetched"] = True
        elif tool_name == "navigate_to_page":
            step["navigated_url"] = context.get("current_url", "")

        steps.append(step)
        if on_step:
            on_step(step)

        messages.append({
            "role": "tool",
            "tool_call_id": tool_call.id,
            "content": tool_result,
        })

        # Acknowledge any extra parallel tool calls
        for extra in msg.tool_calls[1:]:
            messages.append({
                "role": "tool",
                "tool_call_id": extra.id,
                "content": "(handled sequentially)",
            })

    # Hit MAX_TURNS without a terminal decision → agent left by timeout
    if not steps or steps[-1]["action"] not in ("purchase", "leave"):
        steps.append({
            "step": len(steps) + 1,
            "agent_id": persona["id"],
            "action": "leave",
            "target": "site",
            "reason": "Ran out of time browsing without reaching a decision.",
        })

    return steps, result
