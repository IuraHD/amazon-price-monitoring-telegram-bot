from unittest.mock import AsyncMock, patch
import pytest
from app.utils.fetch import (
    validate_url,
    InvalidProduct,
    extract_asin,
    parse_gbp,
    parse_offer,
    resolve_link,
    PublicResolver,
)
from app.utils.graph import build_price_graph


@pytest.mark.parametrize(
    "url",
    [
        "http://amazon.co.uk@127.0.0.1/private",
        "https://amazon.co.uk.evil.test/dp/B012345678",
        "https://evilamazon.com/",
        "https://amzn.evil.test/x",
        "file:///etc/passwd",
        "https://amazon.co.uk:8080/",
        "https://amazon.co.uk\\@127.0.0.1/",
        "https://amazon.co.uk/a b",
    ],
)
def test_rejects_unsafe_urls(url):
    with pytest.raises(InvalidProduct):
        validate_url(url)


@pytest.mark.parametrize(
    "text,expected",
    [
        ("£1,299", 129900),
        ("£1,299.99", 129999),
        ("GBP 10.01", 1001),
        ("4.5 out of 5 stars", None),
        ("Only 3 left", None),
        ("£19.99 (£2.00 / 100 g)", None),
        ("€10.00", None),
        ("£1.299", None),
    ],
)
def test_price_amounts(text, expected):
    assert parse_gbp(text) == expected


def page(content, asin="B012345678"):
    return f'<input id="ASIN" value="{asin}"><span id="productTitle">Example</span>{content}'


def test_no_arbitrary_number_fallback():
    offer = parse_offer(page("<span>4.5 out of 5 stars</span><span>Only 3 left</span>"), "B012345678")
    assert offer.price_pence is None and offer.status == "unrecognized"


def test_primary_price_excludes_unit_price():
    offer = parse_offer(
        page(
            '<div id="corePrice_feature_div"><span class="priceToPay"><span class="a-offscreen">£24.00</span></span><span class="a-price"><span class="a-offscreen">£2.00</span></span></div>'
        ),
        "B012345678",
    )
    assert offer.price_pence == 2400


def test_ambiguous_prices_fail_closed():
    offer = parse_offer(
        page(
            '<div id="corePrice_feature_div"><span class="priceToPay"><span class="a-offscreen">£24.00</span><span class="a-offscreen">£20.00</span></span></div>'
        ),
        "B012345678",
    )
    assert offer.status == "ambiguous" and offer.price_pence is None


def test_identity_captcha_and_unavailable():
    assert parse_offer(page("", asin="B012345679"), "B012345678").status == "unrecognized"
    assert parse_offer('<input id="captchacharacters">', "B012345678").status == "blocked"
    assert (
        parse_offer(page('<div id="availability">Currently unavailable</div>'), "B012345678").status
        == "unavailable"
    )


@pytest.mark.parametrize("url", ["https://amazon.co.uk/", "https://amazon.co.uk/dp/123"])
def test_invalid_asins(url):
    with pytest.raises(InvalidProduct):
        extract_asin(url)


async def test_marketplace_is_explicit():
    link = await resolve_link("https://www.amazon.de/dp/B012345678", None)
    assert link.converted and link.url == "https://www.amazon.co.uk/dp/B012345678"


async def test_short_link_redirect_validated():
    class Response:
        status = 302
        headers = {"Location": "http://127.0.0.1/private"}

        async def __aenter__(self):
            return self

        async def __aexit__(self, *args):
            pass

    class Session:
        def get(self, *args, **kwargs):
            assert kwargs["allow_redirects"] is False
            return Response()

    with pytest.raises(InvalidProduct):
        await resolve_link("https://amzn.to/abc", Session())


async def test_dns_private_address_rejected():
    resolver = PublicResolver()
    try:
        with patch(
            "aiohttp.resolver.ThreadedResolver.resolve", new=AsyncMock(return_value=[{"host": "127.0.0.1"}])
        ):
            with pytest.raises(OSError):
                await resolver.resolve("amazon.co.uk")
    finally:
        await resolver.close()


def test_empty_chart_and_valid_chart():
    assert build_price_graph([], "Empty") is None
    data = build_price_graph([{"ts": "2026-01-01T00:00:00+00:00", "price_pence": 1000}], "Title")
    assert data.startswith(b"\x89PNG")


def test_current_accordion_keeps_selected_one_time_offer():
    content = '<div id="buyBoxAccordion">'
    content += '<div id="newAccordionRow_0" class="a-accordion-active"><div id="corePrice_feature_div"><span class="apex-pricetopay-value"><span class="a-offscreen">£419.99</span></span></div></div>'
    content += '<div id="newAccordionRow_1"><span class="apex-pricetopay-value"><span class="a-offscreen">£422.18</span></span></div>'
    content += '<div id="subscriptionPrice"><span class="apex-pricetopay-value"><span class="a-offscreen">£399.99</span></span></div></div>'
    assert parse_offer(page(content), "B012345678").price_pence == 41999
