# Dumbbell Price Checker

Tracks the price of the **PowerBlock Elite USA 90 lb Adjustable Dumbbells (pair, 5–90 lb)**
on [Amazon](https://www.amazon.com/dp/B0BSR6JN85) every hour and alerts you when:

| Alert | Rule |
|---|---|
| **Under $700** | Price is below $700. Fires on the first check that sees it. |
| **On sale** | Amazon shows a strike-through list/typical price, a "-XX%" savings badge, or a deal badge (Limited time deal, Lightning Deal, Prime Day, etc.). |
| **Dropped 10%+** | Price is 10% or more below the highest price seen in the last 30 days. |

Alerts don't repeat every hour. Each one fires once and fires again only if the price
drops further, or after the price recovers and drops again.

## How it runs: GitHub Actions (on)

`.github/workflows/price-check.yml` runs the check on GitHub's servers **every hour**,
even when your computer is off. When an alert fires, it opens a GitHub issue that
@mentions you, and GitHub emails you (or pushes it to the GitHub mobile app).

- It's free: GitHub Actions has no minute limit for public repositories.
- GitHub pauses scheduled workflows in public repos after 60 days with no activity.
  The tracker saves its price history at least once a day, which counts as activity,
  so it stays on.
- To run a check now, or to send a test alert: *Actions → Dumbbell price check →
  Run workflow*.
- To stop it: *Actions → Dumbbell price check → ⋯ → Disable workflow*.
- If Amazon blocks the tracker for 6 checks in a row, it opens a "tracker can't read
  price" issue. It closes that issue automatically once it reads a price again.

## Optional: run it on your PC instead

Amazon rarely blocks home internet connections, so this is a fallback if GitHub gets
blocked a lot. Checks only happen while the PC is on, and alerts appear as desktop
pop-ups. If you switch to this, disable the GitHub workflow to avoid duplicate alerts.

1. Install **Python 3** from <https://www.python.org/downloads/>. On Windows, tick
   *"Add python.exe to PATH"* during install.
2. Download this repo (green **Code** button → *Download ZIP*, then unzip it).
3. Schedule the hourly check. This also runs one check straight away:
   - **Windows:** open PowerShell in the folder and run
     `powershell -ExecutionPolicy Bypass -File .\setup_windows.ps1`
   - **Mac / Linux:** open a terminal in the folder and run `sh setup_mac_linux.sh`
4. Optional: copy `.env.example` to `.env` and add a GitHub token to also get
   GitHub-issue alerts from your PC.

Manual commands: `python tracker.py` (check now), `python tracker.py --dry-run`
(check without saving), `python tracker.py --test-alert` (test notification).

To stop:
- **Windows:** `Unregister-ScheduledTask -TaskName "Dumbbell Price Tracker"`
- **Mac / Linux:** `crontab -l | grep -v tracker.py | crontab -`

## Settings & more products

Edit `config.json`:

```jsonc
{
  "alert_below": 700,        // alert when the price is under this
  "drop_percent": 10,        // alert on a drop of this % from the recent high
  "drop_lookback_days": 30,  // "recent high" window
  "failure_alert_after": 6,  // failed checks in a row before an error alert
  "products": [{ "id": "...", "name": "...", "url": "..." }]
}
```

To track another store's listing (for example the same dumbbells on powerblock.com or
Dick's), add another entry to `products` with a unique `id`. Amazon links use the Amazon
parser. Other stores are read from their standard product price tags (schema.org / Open
Graph), which most big retailers publish.

Price history is saved to `data/prices-local.json` on your PC, or to `data/prices.json`
on GitHub.

## Tests

```sh
python -m unittest discover -s tests
```
