from typing import Optional
import asyncio
import random
import re
from bs4 import BeautifulSoup
import aiohttp
from urllib.parse import urlparse

_USER_AGENTS = [
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/129.0.0.0 Safari/537.36",
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64; rv:127.0) Gecko/20100101 Firefox/127.0",
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/605.1.15 (KHTML, like Gecko) Version/17.0 Safari/605.1.15",
]

def _headers() -> dict:
    return {
        "User-Agent": random.choice(_USER_AGENTS),
        "Accept-Language": "en-GB,en;q=0.9",
        "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
        "Connection": "close",
    }


async def fetch_price(url: str, session: aiohttp.ClientSession | None = None) -> Optional[float]:
    attempts = 0
    last_exc: Exception | None = None
    while attempts < 3:
        attempts += 1
        try:
            # Prefer a stable marketplace to avoid regional price variance
            target_url = url
            try:
                parsed = urlparse(url)
                host = parsed.netloc.lower()
                if "amazon.co.uk" not in host:
                    # Try to canonicalize to UK marketplace using ASIN
                    asin = await resolve_asin(url, session=session)
                    if asin and len(asin) >= 8:
                        target_url = f"https://www.amazon.co.uk/dp/{asin}"
            except Exception:
                target_url = url

            s = session or aiohttp.ClientSession()
            async with s.get(target_url, headers=_headers(), timeout=aiohttp.ClientTimeout(total=25)) as resp:
                if resp.status >= 400:
                    raise RuntimeError(f"HTTP {resp.status}")
                text = await resp.text()
            if session is None:
                await s.close()
            soup = BeautifulSoup(text, "html.parser")
            price = _extract_price(soup)
            if price is not None:
                return price
        except Exception as e:
            last_exc = e
            await asyncio.sleep(1 * attempts)
    return None


def _extract_price(soup: BeautifulSoup) -> Optional[float]:
    # First, try to find all eligible prices within the main price container and pick the lowest
    main_container = (
        soup.select_one("#corePrice_feature_div")
        or soup.select_one("#apex_desktop")
        or soup.select_one("#ppd")
        or soup.select_one("#centerCol")
    )
    candidates: list[float] = []
    if main_container is not None:
        for el in main_container.select("span.a-price:not(.a-text-price) span.a-offscreen"):
            if _is_struckthrough(el):
                continue
            txt = el.get_text(strip=True)
            p = _parse_price_text(txt)
            if p is not None:
                candidates.append(p)

    # Also consider the AOD (All Offers) ingress price if present (often the lowest offer)
    aod_el = soup.select_one("#aod-ingress-link .a-price .a-offscreen, #dynamic-aod-ingress-box .a-price .a-offscreen")
    if aod_el and not _is_struckthrough(aod_el):
        p = _parse_price_text(aod_el.get_text(strip=True))
        if p is not None:
            candidates.append(p)

    if candidates:
        try:
            return min(candidates)
        except Exception:
            pass

    # Prefer discounted/"price to pay" selectors first, then fall back to regular price.
    preferred_selectors = [
        "#priceblock_dealprice",
        "#priceblock_saleprice",
        "span.priceToPay span.a-offscreen",
        "#corePrice_feature_div span.a-price[data-a-color='price'] span.a-offscreen",
        "#corePrice_feature_div span.a-offscreen",
        "#corePrice_desktop span.a-offscreen",
        "#apex_desktop span.a-price[data-a-color='price'] span.a-offscreen",
        "#apex_desktop span.a-offscreen",
        "span.a-price[data-a-color='price'] span.a-offscreen",
        "div[data-feature-name='corePrice'] span.a-offscreen",
    ]
    fallback_selectors = [
        "#priceblock_ourprice",
        "#priceblock_price",
        "span.a-price span.a-offscreen",
    ]

    for sel in preferred_selectors + fallback_selectors:
        el = soup.select_one(sel)
        if el:
            # Skip if this element is inside a strikethrough/list price container
            if _is_struckthrough(el):
                continue
            txt = el.get_text(strip=True)
            if txt:
                p = _parse_price_text(txt)
                if p is not None:
                    return p

    # As a broader fallback, scan common price containers for any a-offscreen values
    for el in soup.select("#corePrice_feature_div .a-offscreen, #apex_desktop .a-offscreen, .a-price .a-offscreen"):
        if _is_struckthrough(el):
            continue
        txt = el.get_text(strip=True)
        p = _parse_price_text(txt)
        if p is not None:
            return p

    # Ultimate fallback: scan all spans for any currency/number looking text
    for span in soup.find_all("span"):
        if _is_struckthrough(span):
            continue
        txt = span.get_text(strip=True)
        if any(sym in txt for sym in ("£", "€", "$", "zł", "PLN", "₹", "¥")) or re.search(r"\d[\d\.,\s]*", txt):
            p = _parse_price_text(txt)
            if p is not None:
                return p
    return None

    # legacy sync removed
    return None


def _parse_price_text(text: str) -> Optional[float]:
    # Normalize whitespace and NBSPs
    t = text.replace("\xa0", " ").strip()
    # Remove currency codes/symbols but keep digits and separators for parsing logic
    # Keep digits, commas, dots and spaces only
    cleaned = "".join(ch for ch in t if ch.isdigit() or ch in ",. ")
    if not cleaned:
        return None
    # Remove spaces (thousand separators in some locales)
    cleaned = cleaned.replace(" ", "")
    # Determine decimal separator as the last occurrence of ',' or '.'
    last_dot = cleaned.rfind('.')
    last_comma = cleaned.rfind(',')
    last_idx = max(last_dot, last_comma)
    if last_idx != -1:
        dec_sep = cleaned[last_idx]
        int_part = cleaned[:last_idx].replace('.', '').replace(',', '')
        frac_part = cleaned[last_idx + 1:]
        if not int_part and not frac_part:
            return None
        num_str = f"{int_part}.{frac_part}" if frac_part else int_part
    else:
        # No obvious decimal separator, assume whole number
        num_str = cleaned.replace('.', '').replace(',', '')
    try:
        return float(num_str)
    except Exception:
        return None


def _is_struckthrough(el) -> bool:
    try:
        # Walk up a few levels to see if any ancestor marks this as list/strike price
        node = el
        depth = 0
        while node is not None and depth < 6:
            classes = set(node.get("class", [])) if hasattr(node, 'get') else set()
            if (
                ("a-text-price" in classes)  # typical strikethrough container
                or ("priceBlockStrikePriceString" in classes)
                or node.get("data-a-strike") is not None
                or node.get("aria-hidden") == "true" and ("a-offscreen" in classes)
            ):
                return True
            node = getattr(node, 'parent', None)
            depth += 1
    except Exception:
        return False
    return False


def extract_asin(url: str) -> str:
    parts = url.split("/")
    if "dp" in parts:
        i = parts.index("dp")
        if i + 1 < len(parts):
            return parts[i + 1][:10]
    if "product" in parts:
        i = parts.index("product")
        if i + 1 < len(parts):
            return parts[i + 1][:10]
    return url  # fallback


async def resolve_asin(url: str, session: aiohttp.ClientSession | None = None) -> str:
    """Resolve short amazon URLs like amzn.eu/d/... or amzn.to/... to extract ASIN.
    Falls back to original extract if redirect fails.
    """
    parsed = urlparse(url)
    host = parsed.netloc.lower()
    # Only attempt redirect resolution for known short hosts
    short_hosts = {"amzn.to", "amzn.eu", "amzn.co", "amzn.com"}
    if host in short_hosts or host.startswith("amzn."):
        try:
            s = session or aiohttp.ClientSession()
            async with s.get(url, headers=_headers(), allow_redirects=True, timeout=15) as resp:
                final_url = str(resp.url)
            if session is None:
                await s.close()
            asin = extract_asin(final_url)
            return asin
        except Exception:
            return extract_asin(url)
    return extract_asin(url)


async def fetch_title(url: str, session: aiohttp.ClientSession | None = None) -> Optional[str]:
    attempts = 0
    while attempts < 3:
        attempts += 1
        try:
            target_url = url
            try:
                parsed = urlparse(url)
                host = parsed.netloc.lower()
                if "amazon.co.uk" not in host:
                    asin = await resolve_asin(url, session=session)
                    if asin and len(asin) >= 8:
                        target_url = f"https://www.amazon.co.uk/dp/{asin}"
            except Exception:
                target_url = url
            s = session or aiohttp.ClientSession()
            async with s.get(target_url, headers=_headers(), timeout=aiohttp.ClientTimeout(total=25)) as resp:
                if resp.status >= 400:
                    raise RuntimeError(f"HTTP {resp.status}")
                text = await resp.text()
            if session is None:
                await s.close()
            soup = BeautifulSoup(text, "html.parser")
            sel = soup.select_one('#productTitle') or soup.select_one('h1#title span') or soup.select_one('span#title')
            if not sel:
                og = soup.select_one('meta[property="og:title"]')
                if og and og.get('content'):
                    return og.get('content').strip()
            if sel:
                t = sel.get_text(strip=True)
                if t:
                    return t
        except Exception:
            await asyncio.sleep(1 * attempts)
    return None
