import json
import re

import requests
from bs4 import BeautifulSoup


class ScraperError(Exception):
    pass


HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
        "AppleWebKit/537.36 (KHTML, like Gecko) "
        "Chrome/120.0.0.0 Safari/537.36"
    ),
    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
    "Accept-Language": "en-US,en;q=0.5",
}

# Paths that indicate a utility page, not a product
_NON_PRODUCT_PATH_RE = re.compile(
    r"/(login|logout|account|cart|checkout|search|help|faq|about|contact|"
    r"privacy|terms|returns|shipping|blog|news|press|careers|sitemap)",
    re.IGNORECASE,
)

# Paths that strongly suggest a product detail page
_PRODUCT_PATH_RE = re.compile(
    r"/(?:t|pd|product|p|dp|item|shop|buy|goods)/",
    re.IGNORECASE,
)


# ---------------------------------------------------------------------------
# Structured data helpers
# ---------------------------------------------------------------------------

def _extract_json_ld(soup: BeautifulSoup) -> list[dict]:
    """Parse all JSON-LD <script> tags and return a flat list of schema objects."""
    schemas = []
    for script in soup.find_all("script", type="application/ld+json"):
        try:
            data = json.loads(script.string or "")
            if isinstance(data, list):
                schemas.extend(data)
            elif isinstance(data, dict):
                schemas.append(data)
        except (json.JSONDecodeError, TypeError):
            pass
    return schemas


def _extract_og_data(soup: BeautifulSoup) -> dict:
    """Extract Open Graph meta tags into a flat dict (key = property after 'og:')."""
    og = {}
    for tag in soup.find_all("meta"):
        prop = tag.get("property", "") or tag.get("name", "")
        if prop.startswith("og:") or prop.startswith("product:"):
            og[prop] = tag.get("content", "").strip()
    return og


def _extract_next_data(soup: BeautifulSoup) -> dict | None:
    """Return parsed __NEXT_DATA__ JSON from Next.js pages, or None."""
    tag = soup.find("script", id="__NEXT_DATA__")
    if tag and tag.string:
        try:
            return json.loads(tag.string)
        except (json.JSONDecodeError, TypeError):
            pass
    return None


def _extract_product_links(soup: BeautifulSoup, base_url: str) -> list[dict]:
    """Return up to 20 links that look like product detail pages.

    Each entry is {"text": str, "href": str} where href is absolute.
    """
    from urllib.parse import urljoin, urlparse

    parsed_base = urlparse(base_url)
    base_domain = parsed_base.netloc

    candidates: list[dict] = []
    seen_hrefs: set[str] = set()

    for a in soup.find_all("a", href=True):
        href: str = a["href"].strip()
        if not href or href.startswith("#") or href.startswith("javascript:"):
            continue

        abs_href = urljoin(base_url, href)
        parsed = urlparse(abs_href)

        # Stay on the same domain
        if parsed.netloc and parsed.netloc != base_domain:
            continue

        path = parsed.path.rstrip("/")
        if not path or path == "/" or _NON_PRODUCT_PATH_RE.search(path):
            continue

        # Prefer explicit product paths; also accept any deeper path
        is_product = bool(_PRODUCT_PATH_RE.search(path)) or path.count("/") >= 2

        if not is_product:
            continue

        if abs_href in seen_hrefs:
            continue
        seen_hrefs.add(abs_href)

        text = a.get_text(strip=True)[:80]
        candidates.append({"text": text, "href": abs_href})

        if len(candidates) >= 20:
            break

    return candidates


# ---------------------------------------------------------------------------
# Main scrape function
# ---------------------------------------------------------------------------

def scrape(url: str) -> dict:
    """Fetch a URL and extract raw + structured page data.

    Returns a dict with:
      title, meta_description, text, links, images,
      full_html (full response text for rich parsing),
      json_ld (list of parsed JSON-LD schemas),
      og_data (Open Graph / product meta tags),
      next_data (parsed __NEXT_DATA__ or None),
      product_links (list of {text, href} for product-like URLs).

    Raises ScraperError if the page cannot be reached.
    """
    try:
        response = requests.get(url, timeout=(5, 15), headers=HEADERS)
    except requests.exceptions.Timeout:
        raise ScraperError(f"Request timed out for {url}")
    except requests.exceptions.ConnectionError as e:
        raise ScraperError(f"Could not connect to {url}: {e}")
    except requests.exceptions.RequestException as e:
        raise ScraperError(f"Request failed for {url}: {e}")

    if not response.ok and not response.text.strip():
        raise ScraperError(
            f"Received HTTP {response.status_code} from {url} with no content"
        )

    full_html = response.text

    try:
        soup = BeautifulSoup(full_html, "html.parser")
    except Exception as e:
        raise ScraperError(f"Failed to parse HTML from {url}: {e}")

    # Basic text fields
    title_tag = soup.find("title")
    title = title_tag.get_text(strip=True) if title_tag else ""

    meta_desc_tag = soup.find("meta", attrs={"name": "description"})
    meta_description = ""
    if meta_desc_tag and meta_desc_tag.get("content"):
        meta_description = meta_desc_tag["content"].strip()

    page_text = soup.get_text(separator=" ", strip=True)
    page_text = " ".join(page_text.split())
    text = page_text[:3000]

    # Links (deduplicated, max 50, non-fragment)
    seen: set[str] = set()
    links: list[str] = []
    for a in soup.find_all("a", href=True):
        href = a["href"].strip()
        if href and href not in seen and not href.startswith("#"):
            seen.add(href)
            links.append(href)
            if len(links) >= 50:
                break

    # Image alt texts (max 20)
    images: list[str] = []
    for img in soup.find_all("img"):
        images.append(img.get("alt", "").strip())
        if len(images) >= 20:
            break

    # Structured data
    json_ld = _extract_json_ld(soup)
    og_data = _extract_og_data(soup)
    next_data = _extract_next_data(soup)
    product_links = _extract_product_links(soup, url)

    return {
        "title": title,
        "meta_description": meta_description,
        "text": text,
        "links": links,
        "images": images,
        "full_html": full_html,          # replaces html_snippet — full page for parser
        "json_ld": json_ld,
        "og_data": og_data,
        "next_data": next_data,
        "product_links": product_links,
    }


def scrape_product_page(url: str) -> dict | None:
    """Scrape a single product page and return a compact detail dict.

    Returns None on any error (callers treat missing detail as non-fatal).
    """
    try:
        raw = scrape(url)
    except ScraperError:
        return None

    soup = BeautifulSoup(raw["full_html"], "html.parser")

    # Pull the richest available description
    detail: dict = {
        "url": url,
        "title": raw["title"],
        "description": raw["meta_description"],
        "price": None,
        "name": None,
        "specs": [],
    }

    # Prefer JSON-LD Product schema
    for schema in raw["json_ld"]:
        if schema.get("@type") in ("Product", "product"):
            detail["name"] = schema.get("name") or detail["name"]
            offers = schema.get("offers") or {}
            if isinstance(offers, list):
                offers = offers[0] if offers else {}
            detail["price"] = str(offers.get("price") or offers.get("lowPrice") or "")
            detail["description"] = schema.get("description") or detail["description"]
            break

    # OG fallback for price
    if not detail["price"]:
        detail["price"] = (
            raw["og_data"].get("og:price:amount")
            or raw["og_data"].get("product:price:amount")
            or ""
        )

    # Extract a few bullet-point specs (li items near the product description)
    for li in soup.find_all("li")[:30]:
        txt = li.get_text(strip=True)
        if 10 < len(txt) < 150:
            detail["specs"].append(txt)

    detail["specs"] = detail["specs"][:8]

    # Truncated page text as fallback detail
    detail["page_text"] = raw["text"][:800]

    return detail
