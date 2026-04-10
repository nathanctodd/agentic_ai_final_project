import re
from urllib.parse import urljoin, urlparse
from bs4 import BeautifulSoup

# Matches: $19, $19.99, 19.99 USD, 19.99 usd
PRICE_RE = re.compile(
    r"(\$\s?\d{1,5}(?:\.\d{2})?|\d{1,5}\.\d{2}\s?(?:USD|usd))"
)

# CTA link/button text patterns
CTA_RE = re.compile(r"\b(buy|cart|checkout|shop|order|add|purchase)\b", re.IGNORECASE)

CONTEXT_WINDOW = 200


# ---------------------------------------------------------------------------
# Product extraction — 4 strategies, highest quality first
# ---------------------------------------------------------------------------

def _products_from_json_ld(json_ld: list[dict]) -> list[dict]:
    """Extract products from JSON-LD schemas (Product or ItemList types)."""
    products = []

    def _from_product_schema(schema: dict) -> dict | None:
        name = schema.get("name")
        if not name:
            return None
        offers = schema.get("offers") or {}
        if isinstance(offers, list):
            offers = offers[0] if offers else {}
        price_val = offers.get("price") or offers.get("lowPrice") or ""
        price = f"${price_val}" if price_val and not str(price_val).startswith("$") else str(price_val)
        desc = (schema.get("description") or "")[:120]
        url = schema.get("url") or offers.get("url") or ""
        return {"name": str(name)[:60], "price": price, "description": desc, "url": url}

    for schema in json_ld:
        stype = schema.get("@type", "")
        if stype in ("Product", "product"):
            p = _from_product_schema(schema)
            if p:
                products.append(p)
        elif stype in ("ItemList", "SearchResultsPage", "CollectionPage"):
            items = schema.get("itemListElement") or []
            for item in items:
                inner = item.get("item") or item
                if isinstance(inner, dict) and inner.get("@type") in ("Product", "product"):
                    p = _from_product_schema(inner)
                    if p:
                        products.append(p)
        if len(products) >= 5:
            break

    return products[:5]


def _products_from_next_data(next_data: dict | None) -> list[dict]:
    """Extract products from Next.js __NEXT_DATA__ JSON.

    Strategy:
    1. Try known e-commerce paths (Nike, Shopify, etc.) for speed and accuracy.
    2. Fall back to a recursive search that handles sibling-key product structures
       (where name and price live in different sub-objects of the same parent).
    """
    if not next_data:
        return []

    # --- Fast path: known site structures ---
    products = _next_data_known_paths(next_data)
    if products:
        return products[:5]

    # --- Generic recursive fallback ---
    return _next_data_recursive(next_data)


def _fmt_price(v: object) -> str | None:
    """Format a raw price value as '$X.XX', or return None if not a valid price."""
    if v is None:
        return None
    try:
        num = float(v)
        return f"${num:.2f}" if num > 0 else None
    except (TypeError, ValueError):
        s = str(v).strip()
        return s if s else None


def _next_data_known_paths(nd: dict) -> list[dict]:
    """Try known Next.js e-commerce data paths before falling back to recursion."""
    results: list[dict] = []

    # ---- Nike: Wall.productGroupings[*].products[*] ----
    # Structure: copy.title = name, prices.currentPrice = price,
    #            pdpUrl.url = url, displayColors.colorDescription = desc
    try:
        groupings = nd["props"]["pageProps"]["initialState"]["Wall"]["productGroupings"]
        seen_names: set[str] = set()
        for group in groupings:
            products_in_group = group.get("products", [])
            if not products_in_group:
                continue
            # Take only the first colorway per grouping so we get diverse products
            p = products_in_group[0]
            name = (p.get("copy") or {}).get("title") or (p.get("copy") or {}).get("name")
            if not name or name in seen_names:
                continue
            seen_names.add(name)
            prices_obj = p.get("prices") or {}
            price = _fmt_price(
                prices_obj.get("currentPrice")
                or prices_obj.get("salePrice")
                or prices_obj.get("initialPrice")
            )
            if name and price:
                desc = (p.get("displayColors") or {}).get("colorDescription", "")
                pdp = p.get("pdpUrl") or {}
                url = pdp.get("url") or pdp.get("path") or ""
                results.append({"name": str(name)[:60], "price": price,
                                "description": str(desc)[:120], "url": url})
                if len(results) >= 5:
                    return results
        if results:
            return results
    except (KeyError, TypeError):
        pass

    # ---- Shopify: collections / products array ----
    # Various Shopify themes expose products as pageProps.products or
    # pageProps.collection.products
    for path in [
        lambda d: d["props"]["pageProps"]["products"],
        lambda d: d["props"]["pageProps"]["collection"]["products"],
        lambda d: d["props"]["pageProps"]["initialState"]["products"],
    ]:
        try:
            items = path(nd)
            if not isinstance(items, list):
                continue
            for p in items:
                name = p.get("title") or p.get("name")
                # Shopify price is in variants[0].price (string like "89.95")
                variants = p.get("variants") or []
                price_raw = (variants[0].get("price") if variants else None) or p.get("price")
                price = _fmt_price(price_raw)
                if name and price:
                    desc = p.get("description") or ""
                    url = p.get("url") or p.get("handle") or ""
                    results.append({"name": str(name)[:60], "price": price,
                                    "description": str(desc)[:120], "url": url})
                    if len(results) >= 5:
                        return results
            if results:
                return results
        except (KeyError, TypeError, IndexError):
            continue

    return []


def _next_data_recursive(nd: dict) -> list[dict]:
    """Generic recursive fallback.

    Handles products where name and price may be in SIBLING sub-objects of the
    same parent dict (e.g. copy.title + prices.currentPrice on Nike).
    """
    # Keys that directly hold a numeric/string price
    PRICE_KEYS = {
        "price", "currentPrice", "salePrice", "retailPrice", "msrp",
        "listPrice", "fullPrice", "discountedPrice", "amount",
    }
    # Sub-dicts that may contain a price one level down
    PRICE_CONTAINERS = {"offers", "pricing", "priceWithCurrency", "priceInfo", "prices"}
    # Sub-dicts that may contain a name one level down
    NAME_CONTAINERS = {"copy", "content", "details", "info", "product", "item"}
    NAME_KEYS = ["name", "title", "productName", "displayName", "label"]

    found: list[dict] = []

    def _get_price(obj: dict) -> str | None:
        for k in PRICE_KEYS:
            p = _fmt_price(obj.get(k))
            if p:
                return p
        for ck in PRICE_CONTAINERS:
            sub = obj.get(ck)
            if isinstance(sub, dict):
                for k in PRICE_KEYS:
                    p = _fmt_price(sub.get(k))
                    if p:
                        return p
            elif isinstance(sub, list) and sub and isinstance(sub[0], dict):
                for k in PRICE_KEYS:
                    p = _fmt_price(sub[0].get(k))
                    if p:
                        return p
        return None

    def _get_name(obj: dict) -> str | None:
        # Direct
        for k in NAME_KEYS:
            v = obj.get(k)
            if isinstance(v, str) and len(v) > 3:
                return v
        # One level into name containers
        for ck in NAME_CONTAINERS:
            sub = obj.get(ck)
            if isinstance(sub, dict):
                for k in NAME_KEYS:
                    v = sub.get(k)
                    if isinstance(v, str) and len(v) > 3:
                        return v
        return None

    def _get_url(obj: dict) -> str:
        direct = obj.get("url") or obj.get("href") or obj.get("path") or ""
        if direct and isinstance(direct, str):
            return direct
        for ck in ("pdpUrl", "link", "canonical"):
            sub = obj.get(ck)
            if isinstance(sub, dict):
                u = sub.get("url") or sub.get("path") or sub.get("href") or ""
                if u:
                    return str(u)
            elif isinstance(sub, str) and sub:
                return sub
        return ""

    def _get_desc(obj: dict) -> str:
        candidates = [
            obj.get("description"), obj.get("subtitle"),
            (obj.get("displayColors") or {}).get("colorDescription"),
            (obj.get("copy") or {}).get("subTitle"),
        ]
        return str(next((c for c in candidates if c), ""))[:120]

    def _recurse(obj: object, depth: int = 0) -> None:
        if depth > 15 or len(found) >= 5:
            return
        if isinstance(obj, dict):
            name = _get_name(obj)
            if name:
                price = _get_price(obj)
                if price:
                    found.append({
                        "name": name[:60],
                        "price": price,
                        "description": _get_desc(obj),
                        "url": _get_url(obj),
                    })
                    return  # captured — don't recurse further into this product
            for v in obj.values():
                if len(found) >= 5:
                    return
                _recurse(v, depth + 1)
        elif isinstance(obj, list):
            for item in obj:
                if len(found) >= 5:
                    return
                _recurse(item, depth + 1)

    _recurse(nd)
    return found


def _products_from_og(og_data: dict, title: str) -> list[dict]:
    """Build a single product entry from Open Graph / product meta tags."""
    price = (
        og_data.get("og:price:amount")
        or og_data.get("product:price:amount")
        or ""
    )
    if not price:
        return []
    name = og_data.get("og:title") or title or "Product"
    desc = og_data.get("og:description") or ""
    return [{"name": name[:60], "price": f"${price}" if not price.startswith("$") else price,
             "description": desc[:120], "url": og_data.get("og:url", "")}]


def _products_from_text(text: str) -> list[dict]:
    """Fallback: regex price detection with surrounding context heuristics."""
    products = []
    seen_prices: set[str] = set()

    for match in PRICE_RE.finditer(text):
        price_str = match.group(0).strip()
        if price_str in seen_prices:
            continue
        seen_prices.add(price_str)

        start = max(0, match.start() - CONTEXT_WINDOW)
        before_price = text[start: match.start()]
        title_case_words = re.findall(r"\b[A-Z][a-z]+(?:\s+[A-Z][a-z]+)*\b", before_price)
        name = max(title_case_words, key=len) if title_case_words else "Product"

        after_price = text[match.end(): match.end() + 120].replace("\n", " ").strip()

        products.append({
            "name": name[:60],
            "price": price_str,
            "description": after_price[:120],
            "url": "",
        })
        if len(products) >= 5:
            break

    return products


def _extract_products(raw: dict, title: str) -> list[dict]:
    """Try extraction strategies in quality order, stopping at the first success."""
    # 1. JSON-LD structured data (ideal)
    products = _products_from_json_ld(raw.get("json_ld", []))
    if products:
        return products

    # 2. Next.js embedded JSON (__NEXT_DATA__)
    products = _products_from_next_data(raw.get("next_data"))
    if products:
        return products

    # 3. Open Graph / product meta tags
    products = _products_from_og(raw.get("og_data", {}), title)
    if products:
        return products

    # 4. Regex on page text (last resort)
    products = _products_from_text(raw.get("text", ""))
    if products:
        return products

    # Absolute fallback
    return [{"name": "General product", "price": "unknown",
             "description": "No clearly priced products detected on this page.", "url": ""}]


# ---------------------------------------------------------------------------
# Product URL map
# ---------------------------------------------------------------------------

def _build_product_url_map(products: list[dict], product_links: list[dict], base_url: str) -> dict[str, str]:
    """Return {product_name: absolute_url} for use by the simulation's view_product."""
    url_map: dict[str, str] = {}

    # First pass: URLs embedded in the product records themselves
    for p in products:
        if p.get("url"):
            abs_url = urljoin(base_url, p["url"]) if not p["url"].startswith("http") else p["url"]
            url_map[p["name"]] = abs_url

    # Second pass: fuzzy-match product names against scraped link texts
    if len(url_map) < len(products):
        for p in products:
            if p["name"] in url_map:
                continue
            p_name_lower = p["name"].lower()
            for link in product_links:
                link_text = link.get("text", "").lower()
                if p_name_lower in link_text or link_text in p_name_lower:
                    url_map[p["name"]] = link["href"]
                    break

    return url_map


# ---------------------------------------------------------------------------
# UX Score
# ---------------------------------------------------------------------------

def _compute_ux_score(raw: dict, soup: BeautifulSoup, products: list[dict]) -> tuple[int, dict]:
    breakdown = {
        "headline": False,
        "cta": False,
        "price_clarity": False,
        "product_count": False,
        "image_alts": False,
    }

    h1 = soup.find("h1")
    if h1 and len(h1.get_text(strip=True)) > 10:
        breakdown["headline"] = True

    # Also accept og:title as "headline present"
    if not breakdown["headline"] and raw.get("og_data", {}).get("og:title"):
        breakdown["headline"] = True

    for tag in soup.find_all(["button", "a"]):
        if CTA_RE.search(tag.get_text(strip=True)):
            breakdown["cta"] = True
            break

    # Price clarity: 2+ price matches in text OR structured data had prices
    price_matches = PRICE_RE.findall(raw.get("text", ""))
    has_structured_prices = any(p.get("price") and p["price"] != "unknown" for p in products)
    if len(price_matches) >= 2 or has_structured_prices:
        breakdown["price_clarity"] = True

    real_products = [p for p in products if p.get("name") != "General product"]
    if len(real_products) >= 3:
        breakdown["product_count"] = True

    images = raw.get("images", [])
    if images:
        quality_alts = sum(1 for alt in images if len(alt) > 5)
        if quality_alts / len(images) >= 0.5:
            breakdown["image_alts"] = True
    else:
        breakdown["image_alts"] = True  # no images = not penalised

    score = sum(20 for v in breakdown.values() if v)
    return score, breakdown


# ---------------------------------------------------------------------------
# Main parse function
# ---------------------------------------------------------------------------

def parse(raw: dict, base_url: str = "") -> dict:
    """Convert raw scrape dict into structured site data with UX score.

    Args:
        raw: Output from scraper.scrape()
        base_url: Original URL, used to resolve relative product links.

    Returns:
        Structured dict with headline, cta_text, products, ux_score, ux_breakdown,
        product_urls, and raw_text_truncated.
    """
    # Use full HTML for soup (was limited to 2000 chars before — this was the Nike bug)
    soup = BeautifulSoup(raw.get("full_html", raw.get("html_snippet", "")), "html.parser")

    # Headline: prefer <h1>, fall back to OG title, then page title
    h1 = soup.find("h1")
    headline = (
        h1.get_text(strip=True)
        if h1
        else raw.get("og_data", {}).get("og:title")
        or raw.get("title", "")
    )

    # CTA text
    cta_text = ""
    for tag in soup.find_all(["button", "a"]):
        txt = tag.get_text(strip=True)
        if CTA_RE.search(txt):
            cta_text = txt[:80]
            break

    products = _extract_products(raw, headline)
    ux_score, ux_breakdown = _compute_ux_score(raw, soup, products)

    product_urls = _build_product_url_map(
        products, raw.get("product_links", []), base_url
    )

    return {
        "headline": headline,
        "cta_text": cta_text,
        "products": products,
        "ux_score": ux_score,
        "ux_breakdown": ux_breakdown,
        "product_urls": product_urls,   # {name: url} for simulation to follow
        "raw_text_truncated": raw.get("text", "")[:1500],
    }
