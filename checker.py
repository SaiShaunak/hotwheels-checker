import os
import time
import json
import logging
import schedule
import requests
from datetime import datetime
from twilio.rest import Client
from bs4 import BeautifulSoup

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s"
)
log = logging.getLogger(__name__)

# ── Config from environment variables (set these in Railway dashboard) ────────
TWILIO_ACCOUNT_SID    = os.environ["TWILIO_ACCOUNT_SID"]
TWILIO_AUTH_TOKEN     = os.environ["TWILIO_AUTH_TOKEN"]
YOUR_WHATSAPP_NUMBER  = os.environ["YOUR_WHATSAPP_NUMBER"]   # e.g. +919876543210
CHECK_INTERVAL_MINUTES = int(os.environ.get("CHECK_INTERVAL_MINUTES", "15"))

TWILIO_WHATSAPP_FROM  = "whatsapp:+14155238886"

HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Linux; Android 13; Pixel 7) "
        "AppleWebKit/537.36 (KHTML, like Gecko) "
        "Chrome/124.0.6367.82 Mobile Safari/537.36"
    ),
    "Accept-Language": "en-IN,en;q=0.9",
    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
}

PLATFORMS = [
    {
        "name": "Blinkit",
        "url": "https://blinkit.com/s/?q=hot+wheels",
        "keywords": ["hot wheels", "hotwheels", "matchbox"],
        "oos_markers": ["out of stock", "notify me", "sold out"],
    },
    {
        "name": "BigBasket",
        "url": "https://www.bigbasket.com/ps/?q=hot+wheels&tab=prd",
        "keywords": ["hot wheels", "hotwheels", "matchbox"],
        "oos_markers": ["out of stock", "notify me", "sold out"],
    },
    {
        "name": "Instamart",
        "url": "https://www.swiggy.com/instamart/search?query=hot+wheels",
        "keywords": ["hot wheels", "hotwheels", "matchbox"],
        "oos_markers": ["out of stock", "sold out"],
    },
]

STATE_FILE = "/tmp/hw_state.json"

# ─────────────────────────────────────────────────────────────────────────────

def load_state():
    try:
        with open(STATE_FILE) as f:
            return json.load(f)
    except Exception:
        return {}

def save_state(state):
    with open(STATE_FILE, "w") as f:
        json.dump(state, f)

def send_whatsapp(platform, products):
    lines = [
        f"🚗 *Hot Wheels IN STOCK on {platform}!*",
        "",
        *[f"✅ {p}" for p in products[:8]],
        *(["…and more"] if len(products) > 8 else []),
        "",
        "🛒 Order before it sells out!",
    ]
    try:
        client = Client(TWILIO_ACCOUNT_SID, TWILIO_AUTH_TOKEN)
        msg = client.messages.create(
            from_=TWILIO_WHATSAPP_FROM,
            to=f"whatsapp:{YOUR_WHATSAPP_NUMBER}",
            body="\n".join(lines),
        )
        log.info(f"WhatsApp sent! SID: {msg.sid}")
    except Exception as e:
        log.error(f"WhatsApp failed: {e}")

def check_platform(platform):
    name = platform["name"]
    try:
        resp = requests.get(platform["url"], headers=HEADERS, timeout=20)
        soup = BeautifulSoup(resp.text, "html.parser")
        text = soup.get_text(separator=" ").lower()

        in_stock = []
        # Look for product names near Hot Wheels keywords
        for tag in soup.find_all(["h2", "h3", "span", "div", "a"]):
            t = tag.get_text(strip=True)
            if not t or len(t) > 120:
                continue
            tl = t.lower()
            if any(kw in tl for kw in platform["keywords"]):
                # Check if nearby text suggests out of stock
                parent_text = (tag.parent.get_text(separator=" ") if tag.parent else "").lower()
                if any(oos in parent_text for oos in platform["oos_markers"]):
                    log.debug(f"  OOS: {t}")
                    continue
                if t not in in_stock:
                    in_stock.append(t)

        log.info(f"{name}: {len(in_stock)} in-stock item(s) found.")
        return in_stock

    except Exception as e:
        log.error(f"{name} error: {e}")
        return []

def run_check():
    log.info(f"=== Check started at {datetime.now().strftime('%Y-%m-%d %H:%M:%S')} ===")
    state = load_state()
    new_state = {}

    for platform in PLATFORMS:
        found = check_platform(platform)
        new_state[platform["name"]] = found

        prev = set(state.get(platform["name"], []))
        newly_found = [p for p in found if p not in prev]

        if newly_found:
            log.info(f"NEW stock on {platform['name']}: {newly_found}")
            send_whatsapp(platform["name"], newly_found)
        elif found:
            log.info(f"{platform['name']}: Same items still in stock, no new alert.")
        else:
            log.info(f"{platform['name']}: Nothing in stock.")

    save_state(new_state)
    log.info("=== Check complete ===\n")

# ─────────────────────────────────────────────────────────────────────────────

if __name__ == "__main__":
    log.info("🚗 Hot Wheels Stock Checker starting up…")
    log.info(f"Checking every {CHECK_INTERVAL_MINUTES} minutes.")
    log.info(f"Alerts → WhatsApp {YOUR_WHATSAPP_NUMBER}")

    run_check()  # immediate first check

    schedule.every(CHECK_INTERVAL_MINUTES).minutes.do(run_check)
    while True:
        schedule.run_pending()
        time.sleep(30)
