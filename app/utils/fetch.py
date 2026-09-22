import asyncio
import ipaddress
import re
import socket
from dataclasses import dataclass
from decimal import Decimal
from urllib.parse import urljoin, urlparse

import aiohttp
from aiohttp.resolver import ThreadedResolver
from bs4 import BeautifulSoup

MARKETS = {
    "amazon.co.uk",
    "amazon.com",
    "amazon.de",
    "amazon.fr",
    "amazon.it",
    "amazon.es",
    "amazon.ca",
    "amazon.com.au",
    "amazon.co.jp",
    "amazon.in",
    "amazon.pl",
    "amazon.nl",
    "amazon.se",
    "amazon.com.be",
    "amazon.ie",
}
SHORT_HOSTS = {"amzn.to", "amzn.eu", "amzn.com", "amzn.co"}
ASIN = re.compile(r"^[A-Z0-9]{10}$")
MAX_BYTES = 8_000_000


class InvalidProduct(ValueError):
    pass


@dataclass(frozen=True)
class ProductLink:
    asin: str
    url: str
    converted: bool = False


@dataclass(frozen=True)
class Offer:
    status: str
    price_pence: int | None = None
    title: str | None = None
    detail: str = ""
    availability: str = "unknown"
    seller: str | None = None


def validate_url(value: str) -> str:
    value = value.strip()
    if len(value) > 2048 or any(ord(c) < 33 for c in value) or "\\" in value:
        raise InvalidProduct("Send one Amazon product URL, without surrounding text.")
    if "://" not in value:
        value = "https://" + value
    try:
        p = urlparse(value)
        host = p.hostname or ""
        bare = host[4:] if host.startswith("www.") else host
        if p.scheme not in {"https", "http"} or p.username or p.password or p.port not in (None, 80, 443):
            raise ValueError
        if bare not in MARKETS and host not in SHORT_HOSTS:
            raise ValueError
    except ValueError as exc:
        raise InvalidProduct("Use an Amazon product URL or an amzn.to / amzn.eu short link.") from exc
    # Always use TLS, including links originally copied with http.
    return p._replace(scheme="https", netloc=host, fragment="").geturl()


def extract_asin(url: str) -> str:
    p = urlparse(validate_url(url))
    match = re.search(r"/(?:dp|gp/product|gp/aw/d)/([A-Za-z0-9]{10})(?:[/?]|$)", p.path + "/")
    if not match:
        raise InvalidProduct("The link does not identify a product. Copy its /dp/ASIN link from Amazon.")
    return match.group(1).upper()


class PublicResolver(ThreadedResolver):
    async def resolve(self, host, port=0, family=socket.AF_INET):
        addresses = await super().resolve(host, port, family)
        if not addresses or any(not ipaddress.ip_address(a["host"]).is_global for a in addresses):
            raise OSError("Refusing a non-public destination")
        return addresses


def create_session():
    return aiohttp.ClientSession(
        connector=aiohttp.TCPConnector(resolver=PublicResolver(), limit=4),
        timeout=aiohttp.ClientTimeout(total=20),
        headers={
            "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 Chrome/130.0 Safari/537.36",
            "Accept-Language": "en-GB,en;q=0.9",
        },
        cookie_jar=aiohttp.DummyCookieJar(),
        trust_env=False,
    )


async def resolve_link(value: str, session) -> ProductLink:
    url = validate_url(value)
    for _ in range(6):
        host = urlparse(url).hostname
        if host not in SHORT_HOSTS:
            asin = extract_asin(url)
            return ProductLink(
                asin, f"https://www.amazon.co.uk/dp/{asin}", host not in {"amazon.co.uk", "www.amazon.co.uk"}
            )
        async with session.get(url, allow_redirects=False) as response:
            if response.status in {301, 302, 303, 307, 308} and response.headers.get("Location"):
                url = validate_url(urljoin(url, response.headers["Location"]))
            else:
                raise InvalidProduct(
                    "This short link could not be resolved. Send the full Amazon product URL."
                )
    raise InvalidProduct("Too many redirects. Send the full Amazon product URL.")


def parse_gbp(text: str) -> int | None:
    # A single amount, never a percentage, rating, unit price, or pair of amounts.
    match = re.fullmatch(
        r"\s*(?:£|GBP\s*)\s*((?:\d{1,3}(?:,\d{3})+|\d+)(?:\.\d{2})?)\s*", text.replace("\xa0", " ")
    )
    if not match:
        return None
    value = int(Decimal(match.group(1).replace(",", "")) * 100)
    return value if 0 < value <= 100_000_000 else None


def parse_offer(markup: str, asin: str) -> Offer:
    soup = BeautifulSoup(markup, "html.parser")
    if (
        soup.select_one('#captchacharacters, form[action*="validateCaptcha"]')
        or "enter the characters you see below" in soup.get_text(" ", strip=True).lower()
    ):
        return Offer("blocked", detail="Amazon returned a verification page")
    identity = soup.select_one('input#ASIN, input[name="ASIN"]')
    if not identity or identity.get("value", "").upper() != asin:
        return Offer("unrecognized", detail="Could not verify the requested product variant")
    title_node = soup.select_one("#productTitle")
    title = title_node.get_text(" ", strip=True)[:300] if title_node else None
    if not title:
        return Offer("unrecognized", detail="Product title missing")
    availability_node = soup.select_one("#availability")
    availability_text = availability_node.get_text(" ", strip=True).lower() if availability_node else ""
    if any(
        s in availability_text for s in ("currently unavailable", "temporarily out of stock", "out of stock")
    ):
        return Offer(
            "unavailable",
            title=title,
            detail="Amazon reports this product unavailable",
            availability="unavailable",
        )
    selectors = [
        '#buyBoxAccordion [id^="newAccordionRow"].a-accordion-active .apex-pricetopay-value .a-offscreen',
        "#corePrice_feature_div .priceToPay .a-offscreen",
        "#corePriceDisplay_desktop_feature_div .priceToPay .a-offscreen",
        "#apex_desktop .priceToPay .a-offscreen",
        '#corePrice_feature_div .a-price[data-a-color="price"] .a-offscreen',
        "#priceblock_dealprice, #priceblock_saleprice, #priceblock_ourprice",
    ]
    for selector in selectors:
        values = set()
        for el in soup.select(selector):
            ancestors = list(el.parents)
            if any(
                parent.get("id", "").startswith(("sns-", "usedAccordionRow"))
                or parent.get("id") == "subscriptionPrice"
                or (
                    parent.get("id", "").startswith("newAccordionRow")
                    and "a-accordion-active" not in parent.get("class", [])
                )
                for parent in ancestors
            ):
                continue
            if any(
                "a-text-price" in parent.get("class", []) or parent.has_attr("data-a-strike")
                for parent in [el, *list(el.parents)[:5]]
            ):
                continue
            price = parse_gbp(el.get_text(" ", strip=True))
            if price is not None:
                values.add(price)
        if len(values) > 1:
            return Offer("ambiguous", title=title, detail="Multiple primary prices; no price recorded")
        if values:
            seller_node = soup.select_one("#sellerProfileTriggerId")
            seller = seller_node.get_text(" ", strip=True)[:120] if seller_node else None
            stock = "available" if "in stock" in availability_text else "unknown"
            return Offer(
                "ok",
                values.pop(),
                title,
                "Displayed UK price; shipping and conditional discounts excluded",
                stock,
                seller,
            )
    return Offer("unrecognized", title=title, detail="No unambiguous primary GBP offer found")


async def fetch_offer(asin: str, session) -> Offer:
    if not ASIN.fullmatch(asin):
        return Offer("invalid", detail="Invalid legacy ASIN; remove and add a valid product URL")
    url = f"https://www.amazon.co.uk/dp/{asin}"
    for attempt in range(3):
        try:
            async with session.get(url, allow_redirects=False) as response:
                if response.status in {403, 429, 503}:
                    return Offer("blocked", detail=f"Amazon HTTP {response.status}; retry deferred")
                if response.status >= 500 and attempt < 2:
                    await asyncio.sleep(attempt + 1)
                    continue
                if response.status != 200:
                    return Offer("http_error", detail=f"Amazon HTTP {response.status}; no price recorded")
                body = bytearray()
                async for chunk in response.content.iter_chunked(65536):
                    body.extend(chunk)
                    if len(body) > MAX_BYTES:
                        return Offer("unrecognized", detail="Page exceeds the response size limit")
                return await asyncio.to_thread(parse_offer, body.decode("utf-8", errors="replace"), asin)
        except (aiohttp.ClientError, asyncio.TimeoutError, OSError):
            if attempt == 2:
                return Offer("network_error", detail="Could not reach Amazon after three attempts")
            await asyncio.sleep(attempt + 1)
    return Offer("network_error", detail="Request failed")
