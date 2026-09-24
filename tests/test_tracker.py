import sys
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import tracker  # noqa: E402

CFG = {"alert_below": 700, "drop_percent": 10, "drop_lookback_days": 30, "failure_alert_after": 3}
NOW = datetime(2026, 9, 24, 12, tzinfo=timezone.utc)
PRODUCT = {"id": "amazon-TEST", "name": "PowerBlock Elite USA 90 lb (pair)",
           "url": "https://www.amazon.com/dp/B0BSR6JN85"}


def amazon_page(price, list_price=None, savings=None, deal=False, offscreen=True):
    whole, frac = f"{price:,.2f}".split(".")
    shown = f'<span class="a-offscreen">${price:,.2f}</span>' if offscreen else '<span class="a-offscreen"> </span>'
    strike = ""
    if list_price:
        strike = (
            '<span class="a-size-small a-color-secondary aok-align-center basisPrice">List Price: '
            '<span class="a-price a-text-price" data-a-size="s" data-a-strike="true" data-a-color="secondary">'
            f'<span class="a-offscreen">${list_price:,.2f}</span><span aria-hidden="true">${list_price:,.2f}</span>'
            "</span></span>"
        )
    savings_html = (f'<span class="a-size-large a-color-price savingsPercentage">-{savings}%</span>'
                    if savings else "")
    deal_html = '<div id="dealBadge_feature_div"><span>Limited time deal</span></div>' if deal else ""
    return f"""<html><body>
    <span id="productTitle" class="a-size-large product-title-word-break">
        PowerBlock Elite USA 90 Pound Adjustable Dumbbells, Sold in Pairs, 5-90 lb.
    </span>
    {deal_html}
    <div id="corePriceDisplay_desktop_feature_div" class="celwidget">
      {savings_html}
      <span class="a-price aok-align-center reinventPricePriceToPayMargin priceToPay" data-a-size="xl">
        {shown}
        <span aria-hidden="true"><span class="a-price-symbol">$</span><span class="a-price-whole">{whole}<span class="a-price-decimal">.</span></span><span class="a-price-fraction">{frac}</span></span>
      </span>
      {strike}
    </div>
    <div id="availability"><span>In Stock</span></div>
    </body></html>"""


class FakeNotifier:
    def __init__(self):
        self.opened, self.closed = [], []

    def open(self, title, body, label, short=None):
        self.opened.append((title, body, label))
        return len(self.opened)

    def close(self, number, comment):
        self.closed.append(number)


class ParseAmazonTests(unittest.TestCase):
    def test_regular_price(self):
        snap = tracker.parse_amazon(amazon_page(849.99))
        self.assertEqual(snap["price"], 849.99)
        self.assertIsNone(snap["list_price"])
        self.assertFalse(snap["on_sale"])
        self.assertIn("PowerBlock Elite USA 90", snap["title"])

    def test_sale_with_list_price_and_savings(self):
        snap = tracker.parse_amazon(amazon_page(1049.00, list_price=1299.00, savings=19))
        self.assertEqual(snap["price"], 1049.00)
        self.assertEqual(snap["list_price"], 1299.00)
        self.assertEqual(snap["savings_pct"], 19)
        self.assertTrue(snap["on_sale"])

    def test_deal_badge_counts_as_sale(self):
        snap = tracker.parse_amazon(amazon_page(799.00, deal=True))
        self.assertTrue(snap["deal"])
        self.assertTrue(snap["on_sale"])

    def test_deal_badge_on_other_products_ignored(self):
        carousel = "<div>" + "x" * 20000 + '<span class="a-badge-text">Limited time deal</span></div>'
        snap = tracker.parse_amazon(amazon_page(849.00).replace("</body>", carousel + "</body>"))
        self.assertFalse(snap["on_sale"])

    def test_typical_price_text_fallback(self):
        page = amazon_page(899.00).replace(
            "</div>\n    <div id=\"availability\"",
            '<span class="a-size-small">Typical price: <span>$999.00</span></span></div>\n    <div id="availability"')
        snap = tracker.parse_amazon(page)
        self.assertEqual(snap["list_price"], 999.00)
        self.assertTrue(snap["on_sale"])

    def test_whole_and_fraction_when_offscreen_empty(self):
        snap = tracker.parse_amazon(amazon_page(1234.56, offscreen=False))
        self.assertEqual(snap["price"], 1234.56)

    def test_twister_fallback(self):
        page = '<input type="hidden" id="twister-plus-price-data-price" value="699.95" />'
        self.assertEqual(tracker.parse_amazon(page)["price"], 699.95)

    def test_captcha_is_blocked(self):
        page = '<form action="/errors/validateCaptcha">Enter the characters you see below</form>'
        with self.assertRaises(tracker.BlockedError):
            tracker.parse_amazon(page)

    def test_no_price_raises(self):
        with self.assertRaises(tracker.FetchError):
            tracker.parse_amazon("<html><body>nothing here</body></html>")

    def test_unavailable(self):
        page = '<div id="availability"><span class="a-color-price">Currently unavailable.</span></div>'
        snap = tracker.parse_amazon(page)
        self.assertIsNone(snap["price"])
        self.assertFalse(snap["available"])


class ParseGenericTests(unittest.TestCase):
    def test_json_ld(self):
        page = ('<title>Elite USA 90</title><script type="application/ld+json">'
                '{"@type":"Product","offers":{"@type":"Offer","price":"729.00","priceCurrency":"USD"}}</script>')
        self.assertEqual(tracker.parse_page(page, "https://www.powerblock.com/x")["price"], 729.00)

    def test_meta_tag(self):
        page = '<meta property="product:price:amount" content="689.50">'
        self.assertEqual(tracker.parse_generic(page)["price"], 689.50)


def snap(price, on_sale=False, list_price=None):
    return {"price": price, "on_sale": on_sale, "list_price": list_price, "savings_pct": None, "deal": False}


def hist(*prices, start=NOW - timedelta(days=5)):
    return [{"t": (start + timedelta(hours=i)).strftime("%Y-%m-%dT%H:%M:%SZ"), "price": p,
             "list_price": None, "on_sale": False} for i, p in enumerate(prices)]


class EvaluateTests(unittest.TestCase):
    def test_no_alert_at_normal_price(self):
        record = {"history": hist(849.0)}
        self.assertEqual(tracker.evaluate(snap(849.0), record, CFG, NOW), [])

    def test_under_700_alerts_immediately_even_without_history(self):
        reasons = tracker.evaluate(snap(699.99), {}, CFG, NOW)
        self.assertTrue(any("under $700" in r for r in reasons))

    def test_exactly_700_is_not_under(self):
        self.assertEqual(tracker.evaluate(snap(700.0), {}, CFG, NOW), [])

    def test_under_700_does_not_repeat_unless_lower(self):
        record = {}
        tracker.evaluate(snap(690.0), record, CFG, NOW)
        self.assertEqual(tracker.evaluate(snap(690.0), record, CFG, NOW), [])
        self.assertEqual(tracker.evaluate(snap(695.0), record, CFG, NOW), [])
        self.assertTrue(tracker.evaluate(snap(650.0), record, CFG, NOW))

    def test_under_700_rearms_after_going_back_up(self):
        record = {}
        tracker.evaluate(snap(690.0), record, CFG, NOW)
        tracker.evaluate(snap(760.0), record, CFG, NOW)
        self.assertTrue(tracker.evaluate(snap(690.0), record, CFG, NOW))

    def test_drop_of_10_percent_alerts(self):
        record = {"history": hist(1000.0, 1000.0)}
        reasons = tracker.evaluate(snap(900.0), record, CFG, NOW)
        self.assertTrue(any("Dropped 10.0%" in r for r in reasons))

    def test_drop_under_10_percent_does_not_alert(self):
        record = {"history": hist(1000.0)}
        self.assertEqual(tracker.evaluate(snap(901.0), record, CFG, NOW), [])

    def test_gradual_drop_measured_from_recent_high(self):
        record = {"history": hist(1000.0, 960.0, 930.0)}
        self.assertTrue(tracker.evaluate(snap(895.0), record, CFG, NOW))

    def test_old_high_outside_window_ignored(self):
        record = {"history": hist(1000.0, start=NOW - timedelta(days=45)) + hist(900.0)}
        self.assertEqual(tracker.evaluate(snap(890.0), record, CFG, NOW), [])

    def test_sale_alerts_once_per_sale(self):
        record = {"history": hist(1000.0)}
        self.assertTrue(tracker.evaluate(snap(950.0, on_sale=True, list_price=1000.0), record, CFG, NOW))
        self.assertEqual(tracker.evaluate(snap(950.0, on_sale=True, list_price=1000.0), record, CFG, NOW), [])
        tracker.evaluate(snap(1000.0), record, CFG, NOW)
        self.assertTrue(tracker.evaluate(snap(960.0, on_sale=True, list_price=1000.0), record, CFG, NOW))

    def test_all_reasons_combined(self):
        record = {"history": hist(850.0)}
        reasons = tracker.evaluate(snap(650.0, on_sale=True, list_price=850.0), record, CFG, NOW)
        self.assertEqual(len(reasons), 3)


class CheckProductTests(unittest.TestCase):
    def test_alert_issue_opened_and_history_recorded(self):
        record, notifier = {}, FakeNotifier()
        tracker.check_product(PRODUCT, record, CFG, notifier, NOW,
                              fetcher=lambda url: amazon_page(679.00), sleep=lambda s: None)
        self.assertEqual(len(notifier.opened), 1)
        title, body, label = notifier.opened[0]
        self.assertIn("$679.00", title)
        self.assertEqual(label, "price-alert")
        self.assertEqual(record["history"][-1]["price"], 679.00)

    def test_failures_open_then_close_error_issue(self):
        record, notifier = {}, FakeNotifier()

        def blocked(url):
            raise tracker.BlockedError("HTTP 503")

        for _ in range(3):
            tracker.check_product(PRODUCT, record, CFG, notifier, NOW, fetcher=blocked, sleep=lambda s: None)
        self.assertEqual([o[2] for o in notifier.opened], ["tracker-error"])
        # A fourth failure must not open a duplicate issue.
        tracker.check_product(PRODUCT, record, CFG, notifier, NOW, fetcher=blocked, sleep=lambda s: None)
        self.assertEqual(len(notifier.opened), 1)

        tracker.check_product(PRODUCT, record, CFG, notifier, NOW,
                              fetcher=lambda url: amazon_page(849.00), sleep=lambda s: None)
        self.assertEqual(notifier.closed, [1])
        self.assertNotIn("error_issue", record["state"])

    def test_retry_recovers_from_one_captcha(self):
        pages = iter(["validateCaptcha", amazon_page(849.00)])
        record, notifier = {}, FakeNotifier()
        tracker.check_product(PRODUCT, record, CFG, notifier, NOW,
                              fetcher=lambda url: next(pages), sleep=lambda s: None)
        self.assertEqual(record["history"][-1]["price"], 849.00)
        self.assertEqual(record["state"]["consecutive_failures"], 0)


class HistoryTests(unittest.TestCase):
    def test_unchanged_price_not_recorded_until_heartbeat(self):
        record = {}
        tracker.record_history(snap(849.0), record, NOW)
        tracker.record_history(snap(849.0), record, NOW + timedelta(hours=1))
        self.assertEqual(len(record["history"]), 1)
        tracker.record_history(snap(849.0), record, NOW + timedelta(hours=21))
        self.assertEqual(len(record["history"]), 2)

    def test_changed_price_recorded(self):
        record = {}
        tracker.record_history(snap(849.0), record, NOW)
        tracker.record_history(snap(829.0), record, NOW + timedelta(hours=1))
        self.assertEqual(len(record["history"]), 2)


if __name__ == "__main__":
    unittest.main()
