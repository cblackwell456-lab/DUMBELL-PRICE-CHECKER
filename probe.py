"""Temporary: measure which ways of requesting the Amazon page get past the robot check."""
import gzip, http.cookiejar, random, re, time, urllib.request
import tracker

ASIN = "B0BSR6JN85"
DESKTOP = tracker.USER_AGENTS[0]
MOBILE = ("Mozilla/5.0 (iPhone; CPU iPhone OS 17_5 like Mac OS X) AppleWebKit/605.1.15 "
          "(KHTML, like Gecko) Version/17.5 Mobile/15E148 Safari/604.1")
BROWSER_HEADERS = {
    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,image/avif,image/webp,*/*;q=0.8",
    "Accept-Language": "en-US,en;q=0.9", "Accept-Encoding": "gzip",
    "Upgrade-Insecure-Requests": "1", "Sec-Fetch-Dest": "document", "Sec-Fetch-Mode": "navigate",
    "Sec-Fetch-Site": "none", "Sec-Fetch-User": "?1",
    "sec-ch-ua": '"Chromium";v="128", "Not;A=Brand";v="24", "Google Chrome";v="128"',
    "sec-ch-ua-mobile": "?0", "sec-ch-ua-platform": '"Windows"',
}


def get(opener, url, ua, extra=None):
    h = dict(BROWSER_HEADERS, **{"User-Agent": ua}, **(extra or {}))
    try:
        with opener.open(urllib.request.Request(url, headers=h), timeout=30) as r:
            b = r.read()
            if r.headers.get("Content-Encoding") == "gzip":
                b = gzip.decompress(b)
            return r.status, b.decode("utf-8", "replace")
    except urllib.error.HTTPError as e:
        return e.code, ""
    except Exception as e:
        return f"ERR {e}", ""


def plain(url, ua):
    return get(urllib.request.build_opener(), url, ua)


def session(url, ua):
    opener = urllib.request.build_opener(urllib.request.HTTPCookieProcessor(http.cookiejar.CookieJar()))
    get(opener, "https://www.amazon.com/", ua)
    time.sleep(2)
    return get(opener, url, ua, {"Referer": "https://www.amazon.com/", "Sec-Fetch-Site": "same-origin"})


DP = f"https://www.amazon.com/dp/{ASIN}?psc=1"
STRATEGIES = {
    "A plain dp": lambda: plain(DP, DESKTOP),
    "B session dp": lambda: session(DP, DESKTOP),
    "C mobile dp": lambda: plain(DP, MOBILE),
    "D session mobile dp": lambda: session(DP, MOBILE),
    "E aodAjaxMain": lambda: plain(f"https://www.amazon.com/gp/product/ajax/aodAjaxMain/?asin={ASIN}&pc=dp", DESKTOP),
    "F aod ajax": lambda: plain(f"https://www.amazon.com/gp/aod/ajax/?asin={ASIN}&pc=dp", DESKTOP),
    "G gp/product": lambda: plain(f"https://www.amazon.com/gp/product/{ASIN}?psc=1", DESKTOP),
}

results = {k: [] for k in STRATEGIES}
samples = {}
order = [k for k in STRATEGIES for _ in range(5)]
random.shuffle(order)
for name in order:
    status, page = STRATEGIES[name]()
    captcha = any(m in page for m in tracker.CAPTCHA_MARKERS)
    try:
        price = tracker.parse_amazon(page)["price"]
    except Exception as e:
        price = type(e).__name__
    results[name].append((status, len(page), "CAPTCHA" if captcha else "ok", price))
    if not captcha and page and name not in samples:
        samples[name] = page
    time.sleep(random.uniform(3, 8))

print("\n===== RESULTS =====")
for name, rows in results.items():
    good = sum(1 for r in rows if isinstance(r[3], float))
    print(f"{name:22s} price read {good}/{len(rows)}  {rows}")

for name, page in samples.items():
    if name.startswith(("E", "F")):
        print(f"\n===== {name} sample =====")
        for m in re.finditer(r'aod-price-\d|apex-pinned|a-offscreen|aod-offer-soldBy|aod-pinned-offer', page):
            print(m.group(0), "|", re.sub(r"\s+", " ", page[m.start():m.start() + 220]))
            if m.start() > 40000:
                break
