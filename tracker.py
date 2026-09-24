#!/usr/bin/env python3
"""Dumbbell price tracker.

Checks each product in config.json, records its price history in
data/prices.json, and opens a GitHub issue when:

  * the price is under the `alert_below` threshold,
  * the product goes on sale (strike-through price, savings badge or deal badge),
  * the price drops `drop_percent` or more below its recent high.

Uses only the Python standard library so it runs anywhere with Python 3.9+.
"""

import argparse
import gzip
import html
import json
import os
import random
import re
import sys
import time
import urllib.error
import urllib.request
from datetime import datetime, timedelta, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parent
CONFIG_PATH = ROOT / "config.json"
# GitHub Actions commits its history to the repo; runs on your own PC keep a
# separate (git-ignored) history so the two never conflict.
DATA_PATH = ROOT / "data" / ("prices.json" if os.environ.get("GITHUB_ACTIONS") == "true" else "prices-local.json")

# How often to write a history entry when nothing changed (keeps a daily trace
# without committing on every hourly run).
HEARTBEAT = timedelta(hours=20)

USER_AGENTS = [
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) "
    "Chrome/128.0.0.0 Safari/537.36",
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/605.1.15 (KHTML, like Gecko) "
    "Version/17.5 Safari/605.1.15",
    "Mozilla/5.0 (X11; Linux x86_64; rv:130.0) Gecko/20100101 Firefox/130.0",
]

CAPTCHA_MARKERS = (
    "validateCaptcha",
    "Enter the characters you see below",
    "To discuss automated access to Amazon data",
)
DEAL_MARKERS = (
    "Limited time deal",
    "Lightning Deal",
    "Prime Big Deal",
    "Prime Day Deal",
    "Black Friday Deal",
)


class FetchError(Exception):
    pass


class BlockedError(FetchError):
    pass


# --------------------------------------------------------------------------
# Fetching
# --------------------------------------------------------------------------

def fetch(url, timeout=30):
    req = urllib.request.Request(
        url,
        headers={
            "User-Agent": random.choice(USER_AGENTS),
            "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
            "Accept-Language": "en-US,en;q=0.9",
            "Accept-Encoding": "gzip",
            "Cache-Control": "no-cache",
        },
    )
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            body = resp.read()
            if resp.headers.get("Content-Encoding") == "gzip":
                body = gzip.decompress(body)
            charset = resp.headers.get_content_charset() or "utf-8"
            return body.decode(charset, errors="replace")
    except urllib.error.HTTPError as e:
        if e.code in (403, 429, 503):
            raise BlockedError(f"HTTP {e.code} from {url}") from e
        raise FetchError(f"HTTP {e.code} from {url}") from e
    except (urllib.error.URLError, TimeoutError, OSError) as e:
        raise FetchError(f"Network error fetching {url}: {e}") from e


def fetch_snapshot(url, fetcher=fetch, attempts=3, sleep=time.sleep):
    """Fetch and parse a product page, retrying when blocked or unparseable."""
    last_error = None
    for attempt in range(attempts):
        if attempt:
            sleep(5 * 2 ** attempt + random.uniform(0, 3))
        try:
            return parse_page(fetcher(url), url)
        except FetchError as e:
            last_error = e
            print(f"  attempt {attempt + 1} failed: {e}")
    raise last_error


# --------------------------------------------------------------------------
# Parsing
# --------------------------------------------------------------------------

MONEY_RE = re.compile(r"\$\s*([\d,]+(?:\.\d{1,2})?)")


def parse_money(text):
    m = MONEY_RE.search(html.unescape(text or ""))
    if not m:
        return None
    value = float(m.group(1).replace(",", ""))
    return value if value > 0 else None


def _price_in_span(snippet):
    """Read the price from an Amazon `a-price` span (offscreen text or whole/fraction)."""
    m = re.search(r'class="a-offscreen"[^>]*>([^<]*)<', snippet)
    if m:
        value = parse_money(m.group(1))
        if value:
            return value
    whole = re.search(r'class="a-price-whole"[^>]*>\s*([\d,]+)', snippet)
    if whole:
        frac = re.search(r'class="a-price-fraction"[^>]*>\s*(\d+)', snippet)
        return float(whole.group(1).replace(",", "") + "." + (frac.group(1) if frac else "00"))
    return None


def _core_price_region(page):
    """Return (start, end) of the main product's price block, or None."""
    for marker in (
        'id="corePriceDisplay_desktop_feature_div"',
        'id="corePrice_desktop"',
        'id="corePrice_feature_div"',
        'id="apex_desktop"',
    ):
        i = page.find(marker)
        if i != -1:
            return i, i + 10000
    return None


def parse_amazon(page):
    if any(marker in page for marker in CAPTCHA_MARKERS):
        raise BlockedError("Amazon served a CAPTCHA / robot check page")

    title_m = re.search(r'id="productTitle"[^>]*>(.*?)</span>', page, re.S)
    title = html.unescape(re.sub(r"\s+", " ", title_m.group(1))).strip() if title_m else None

    bounds = _core_price_region(page)
    region = page[bounds[0]:bounds[1]] if bounds else None
    price = list_price = savings_pct = None

    if region:
        # The "price to pay" span is the price you'd actually be charged.
        m = re.search(r'class="a-price[^"]*\b(?:priceToPay|apexPriceToPay)\b[^"]*"', region)
        if m:
            price = _price_in_span(region[m.start():m.start() + 800])
        if price is None:
            # First non-strike-through a-price in the price block.
            for m in re.finditer(r'<span class="a-price(?! a-text-price)[^"]*"([^>]*)>', region):
                if 'data-a-strike="true"' not in m.group(1):
                    price = _price_in_span(region[m.start():m.start() + 800])
                    if price:
                        break

        m = re.search(r'<span class="a-price a-text-price"[^>]*data-a-strike="true"', region)
        if m:
            list_price = _price_in_span(region[m.start():m.start() + 800])
        if list_price is None:
            m = re.search(r"(?:List Price|Typical price|Was):?[^$]{0,300}?\$\s*[\d,]+(?:\.\d{2})?",
                          region, re.S)
            if m:
                list_price = parse_money(m.group(0))

        m = re.search(r'savingsPercentage[^>]*>\s*-?\s*(\d{1,2})\s*%', region)
        if m:
            savings_pct = int(m.group(1))

    if price is None:
        for pattern in (
            r'id="twister-plus-price-data-price"[^>]*value="([\d.]+)"',
            r'"priceAmount"\s*:\s*([\d.]+)',
            r'id="priceblock_(?:deal|sale|our)price"[^>]*>([^<]+)<',
        ):
            m = re.search(pattern, page)
            if m:
                raw = m.group(1)
                price = parse_money(raw) if "$" in raw else float(raw)
                if price:
                    break

    unavailable = bool(re.search(r'id="availability".{0,400}?Currently unavailable', page, re.S))
    if price is None:
        if unavailable:
            return {"title": title, "price": None, "list_price": None, "savings_pct": None,
                    "deal": False, "on_sale": False, "available": False}
        raise FetchError("Could not find a price on the Amazon page (layout may have changed)")

    if list_price is not None and list_price <= price:
        list_price = None
    # Only look for deal badges around the main price block: carousels of other
    # products further down the page carry their own "Limited time deal" badges.
    near_price = page[max(0, bounds[0] - 4000):bounds[1]] if bounds else ""
    deal = any(marker in near_price for marker in DEAL_MARKERS)
    on_sale = bool(list_price or savings_pct or deal)
    return {
        "title": title,
        "price": round(price, 2),
        "list_price": round(list_price, 2) if list_price else None,
        "savings_pct": savings_pct,
        "deal": deal,
        "on_sale": on_sale,
        "available": not unavailable,
    }


def parse_generic(page):
    """Fallback for non-Amazon stores: schema.org JSON-LD or price meta tags."""
    price = None
    for block in re.findall(r'<script[^>]*application/ld\+json[^>]*>(.*?)</script>', page, re.S):
        m = re.search(r'"price"\s*:\s*"?([\d.]+)', block)
        if m:
            price = float(m.group(1))
            break
    if price is None:
        m = re.search(r'(?:product:price:amount|itemprop="price")[^>]*content="([\d.]+)"', page)
        if m:
            price = float(m.group(1))
    if not price:
        raise FetchError("Could not find a price on the page")
    m = re.search(r"<title>(.*?)</title>", page, re.S)
    return {"title": html.unescape(m.group(1)).strip() if m else None, "price": round(price, 2),
            "list_price": None, "savings_pct": None, "deal": False, "on_sale": False, "available": True}


def parse_page(page, url):
    if "amazon." in url:
        return parse_amazon(page)
    return parse_generic(page)


# --------------------------------------------------------------------------
# Alert rules
# --------------------------------------------------------------------------

def _parse_time(value):
    return datetime.fromisoformat(value.replace("Z", "+00:00"))


def recent_high(history, now, lookback_days):
    cutoff = now - timedelta(days=lookback_days)
    prices = [h["price"] for h in history if h.get("price") and _parse_time(h["t"]) >= cutoff]
    return max(prices) if prices else None


def evaluate(snapshot, record, cfg, now):
    """Return the alert reasons for this snapshot and update `record["state"]`.

    State keeps alerts from repeating every run: each rule fires once, and
    fires again only if the price falls further (or after it resets).
    """
    state = record.setdefault("state", {})
    price = snapshot.get("price")
    reasons = []
    if price is None:
        return reasons

    threshold = cfg["alert_below"]
    if price < threshold:
        last = state.get("below_threshold_alert_price")
        if last is None or price < last:
            reasons.append(f"Price is under ${threshold:,.0f}")
            state["below_threshold_alert_price"] = price
    else:
        state.pop("below_threshold_alert_price", None)

    if snapshot.get("on_sale"):
        if not state.get("on_sale"):
            details = []
            if snapshot.get("list_price"):
                details.append(f"was ${snapshot['list_price']:,.2f}")
            if snapshot.get("savings_pct"):
                details.append(f"{snapshot['savings_pct']}% off")
            if snapshot.get("deal"):
                details.append("deal badge on the page")
            reasons.append("On sale" + (f" ({', '.join(details)})" if details else ""))
        state["on_sale"] = True
    else:
        state["on_sale"] = False

    high = recent_high(record.get("history", []), now, cfg["drop_lookback_days"])
    if high:
        drop = (high - price) / high * 100
        if drop >= cfg["drop_percent"]:
            last = state.get("drop_alert_price")
            if last is None or price < last:
                reasons.append(
                    f"Dropped {drop:.1f}% from ${high:,.2f} "
                    f"(highest in the last {cfg['drop_lookback_days']} days)"
                )
                state["drop_alert_price"] = price
        else:
            state.pop("drop_alert_price", None)

    return reasons


def record_history(snapshot, record, now):
    history = record.setdefault("history", [])
    entry = {
        "t": now.strftime("%Y-%m-%dT%H:%M:%SZ"),
        "price": snapshot.get("price"),
        "list_price": snapshot.get("list_price"),
        "on_sale": snapshot.get("on_sale", False),
    }
    if history:
        last = history[-1]
        unchanged = all(last.get(k) == entry[k] for k in ("price", "list_price", "on_sale"))
        if unchanged and now - _parse_time(last["t"]) < HEARTBEAT:
            return
    history.append(entry)


# --------------------------------------------------------------------------
# Notifications (GitHub issues)
# --------------------------------------------------------------------------

class GitHub:
    def __init__(self, token, repo):
        self.token = token
        self.repo = repo

    def _request(self, method, path, body=None):
        req = urllib.request.Request(
            f"https://api.github.com/repos/{self.repo}{path}",
            method=method,
            data=json.dumps(body).encode() if body is not None else None,
            headers={
                "Authorization": f"Bearer {self.token}",
                "Accept": "application/vnd.github+json",
                "X-GitHub-Api-Version": "2022-11-28",
                "Content-Type": "application/json",
            },
        )
        with urllib.request.urlopen(req, timeout=30) as resp:
            data = resp.read()
            return json.loads(data) if data else None

    def ensure_label(self, name, color, description):
        try:
            self._request("POST", "/labels", {"name": name, "color": color, "description": description})
        except urllib.error.HTTPError as e:
            if e.code != 422:  # 422 = label already exists
                raise

    def create_issue(self, title, body, label):
        return self._request("POST", "/issues", {"title": title, "body": body, "labels": [label]})["number"]

    def close_issue(self, number, comment):
        self._request("POST", f"/issues/{number}/comments", {"body": comment})
        self._request("PATCH", f"/issues/{number}", {"state": "closed", "state_reason": "completed"})


def load_env_file(path):
    """Read KEY=VALUE lines from a local .env file (for running on your own PC)."""
    if not path.exists():
        return
    for line in path.read_text().splitlines():
        line = line.strip()
        if line and not line.startswith("#") and "=" in line:
            key, value = line.split("=", 1)
            os.environ.setdefault(key.strip(), value.strip().strip('"').strip("'"))


def desktop_notify(title, message):
    """Best-effort desktop notification on Windows, macOS or Linux."""
    import platform
    import subprocess

    system = platform.system()
    try:
        if system == "Windows":
            script = (
                "Add-Type -AssemblyName System.Windows.Forms; "
                "$n = New-Object System.Windows.Forms.NotifyIcon; "
                "$n.Icon = [System.Drawing.SystemIcons]::Information; "
                "$n.BalloonTipTitle = $env:DB_TITLE; $n.BalloonTipText = $env:DB_MSG; "
                "$n.Visible = $true; $n.ShowBalloonTip(15000); Start-Sleep -Seconds 15; $n.Dispose()"
            )
            env = dict(os.environ, DB_TITLE=title[:63], DB_MSG=message[:255])
            subprocess.Popen(["powershell", "-NoProfile", "-WindowStyle", "Hidden", "-Command", script],
                             env=env)
        elif system == "Darwin":
            subprocess.run(["osascript", "-e", "on run argv\ndisplay notification (item 2 of argv) "
                            "with title (item 1 of argv) sound name \"Glass\"\nend run", title, message],
                           check=False, timeout=10)
        else:
            subprocess.run(["notify-send", "-u", "critical", title, message], check=False, timeout=10)
    except (OSError, subprocess.SubprocessError) as e:
        print(f"(desktop notification failed: {e})")


class Notifier:
    """Opens GitHub issues when a token is available, and shows a desktop
    notification when running on your own computer."""

    LABELS = {
        "price-alert": ("0e8a16", "Dumbbell price alert"),
        "tracker-error": ("d93f0b", "The price tracker could not read a price"),
    }

    def __init__(self, dry_run=False, desktop=None):
        token = os.environ.get("GITHUB_TOKEN")
        repo = os.environ.get("GITHUB_REPOSITORY")
        self.owner = os.environ.get("GITHUB_REPOSITORY_OWNER")
        self.gh = GitHub(token, repo) if token and repo and not dry_run else None
        # Desktop popups by default everywhere except GitHub Actions.
        self.desktop = (os.environ.get("GITHUB_ACTIONS") != "true") if desktop is None else desktop
        self._labels_ready = set()

    def open(self, title, body, label, short=None):
        if self.owner:
            body += f"\n\ncc @{self.owner}"
        print(f"\n=== ALERT: {title} ===\n{body}\n")
        if self.desktop:
            desktop_notify(title, short or title)
        if not self.gh:
            return None
        if label not in self._labels_ready:
            self.gh.ensure_label(label, *self.LABELS[label])
            self._labels_ready.add(label)
        number = self.gh.create_issue(title, body, label)
        print(f"Opened issue #{number}")
        return number

    def close(self, number, comment):
        print(f"Closing issue #{number}: {comment}")
        if self.gh:
            self.gh.close_issue(number, comment)


def alert_body(product, snapshot, reasons, high):
    lines = [f"**{product['name']}**", "", f"### Current price: ${snapshot['price']:,.2f}", ""]
    lines += [f"- {r}" for r in reasons]
    lines.append("")
    if snapshot.get("list_price"):
        lines.append(f"List / typical price: ${snapshot['list_price']:,.2f}")
    if high:
        lines.append(f"Highest price in the tracking window: ${high:,.2f}")
    lines += ["", f"[Open the product page]({product['url']})",
              "", "_Prices change quickly — check the page before buying._"]
    return "\n".join(lines)


# --------------------------------------------------------------------------
# Main
# --------------------------------------------------------------------------

def load_json(path, default):
    if path.exists():
        return json.loads(path.read_text())
    return default


def check_product(product, record, cfg, notifier, now, fetcher=fetch, sleep=time.sleep):
    record["name"] = product["name"]
    record["url"] = product["url"]
    state = record.setdefault("state", {})
    print(f"Checking {product['name']}")

    try:
        snapshot = fetch_snapshot(product["url"], fetcher=fetcher, sleep=sleep)
    except FetchError as e:
        failures = state.get("consecutive_failures", 0) + 1
        state["consecutive_failures"] = failures
        print(f"  FAILED ({failures} in a row): {e}")
        if failures >= cfg["failure_alert_after"] and not state.get("error_issue"):
            state["error_issue"] = notifier.open(
                f"⚠️ Price tracker can't read {product['name']}",
                f"The last {failures} checks failed. Latest error:\n\n```\n{e}\n```\n\n"
                "Amazon sometimes blocks automated requests; the tracker keeps retrying every run "
                "and will close this issue automatically once it reads a price again.\n\n"
                f"[Product page]({product['url']})",
                "tracker-error",
                short=f"{failures} checks in a row failed: {e}",
            ) or True
        return

    state["consecutive_failures"] = 0
    if state.get("error_issue"):
        if isinstance(state["error_issue"], int):
            notifier.close(state["error_issue"], "✅ The tracker is reading prices again.")
        del state["error_issue"]

    if snapshot["price"] is None:
        print("  Currently unavailable")
    else:
        sale = " (ON SALE)" if snapshot["on_sale"] else ""
        was = f", list ${snapshot['list_price']:,.2f}" if snapshot.get("list_price") else ""
        print(f"  ${snapshot['price']:,.2f}{was}{sale}")

    # Compare against the high *before* adding this reading.
    high = recent_high(record.get("history", []), now, cfg["drop_lookback_days"])
    reasons = evaluate(snapshot, record, cfg, now)
    record_history(snapshot, record, now)

    if reasons:
        summary = "; ".join(r.split(" (")[0] for r in reasons)
        notifier.open(
            f"💰 {product['name'].split('(')[0].strip()}: ${snapshot['price']:,.2f} — {summary}",
            alert_body(product, snapshot, reasons, high),
            "price-alert",
            short=f"${snapshot['price']:,.2f} — " + "; ".join(reasons),
        )


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--dry-run", action="store_true",
                        help="print alerts instead of opening issues, and don't save history")
    parser.add_argument("--test-alert", action="store_true",
                        help="open a test alert issue to confirm notifications reach you")
    parser.add_argument("--no-desktop", action="store_true", help="don't show desktop notifications")
    args = parser.parse_args(argv)

    load_env_file(ROOT / ".env")
    cfg = load_json(CONFIG_PATH, None)
    notifier = Notifier(dry_run=args.dry_run, desktop=False if args.no_desktop else None)

    if args.test_alert:
        notifier.open(
            "🔔 Test alert from the dumbbell price tracker",
            "Notifications are working. Real alerts will look like this issue.",
            "price-alert",
        )
        return 0

    data = load_json(DATA_PATH, {"products": {}})
    now = datetime.now(timezone.utc)
    for product in cfg["products"]:
        record = data["products"].setdefault(product["id"], {})
        check_product(product, record, cfg, notifier, now)

    if not args.dry_run:
        DATA_PATH.parent.mkdir(parents=True, exist_ok=True)
        DATA_PATH.write_text(json.dumps(data, indent=2) + "\n")
    return 0


if __name__ == "__main__":
    sys.exit(main())
