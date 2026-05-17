import os
import time
import json
import logging
import threading
import schedule
from datetime import datetime
from http.server import HTTPServer, BaseHTTPRequestHandler
from twilio.rest import Client
from playwright.sync_api import sync_playwright

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s"
)
log = logging.getLogger(__name__)

# ── Config ────────────────────────────────────────────────────────────────────
TWILIO_ACCOUNT_SID     = os.environ["TWILIO_ACCOUNT_SID"]
TWILIO_AUTH_TOKEN      = os.environ["TWILIO_AUTH_TOKEN"]
YOUR_WHATSAPP_NUMBER   = os.environ["YOUR_WHATSAPP_NUMBER"]
CHECK_INTERVAL_MINUTES = int(os.environ.get("CHECK_INTERVAL_MINUTES", "65"))
PORT                   = int(os.environ.get("PORT", "8080"))
TWILIO_WHATSAPP_FROM   = "whatsapp:+14155238886"

# ── Location: Sannidhi Road, Basavanagudi, Bangalore 560004 ──────────────────
LAT = 12.9422
LON = 77.5739

KEYWORDS     = ["hot wheels", "hotwheels", "hot-wheels"]
OOS_KEYWORDS = ["out of stock", "sold out", "notify me"]

PLATFORMS = [
    {
        "name": "Blinkit",
        "url": f"https://blinkit.com/s/?q=hot+wheels",
        "set_location": "blinkit",
    },
    {
        "name": "Instamart",
        "url": f"https://www.swiggy.com/instamart/search?query=hot+wheels",
        "set_location": "swiggy",
    },
    {
        "name": "BigBasket",
        "url": f"https://www.bigbasket.com/ps/?q=hot+wheels&tab=prd",
        "set_location": None,
    },
]

STATE_FILE = "/tmp/hw_state.json"
status = {"last_check": "Not yet", "results": {}}

# ── Web server ────────────────────────────────────────────────────────────────

class Handler(BaseHTTPRequestHandler):
    def do_GET(self):
        self.send_response(200)
        self.send_header("Content-type", "text/html")
        self.end_headers()
        rows = "".join(
            f"<p><b>{k}:</b> {len(v)} in stock — {', '.join(v[:3]) or 'none'}</p>"
            for k, v in status["results"].items()
        )
        html = f"""<html><body style="font-family:sans-serif;padding:20px;max-width:500px">
        <h2>🚗 Hot Wheels Checker ✅</h2>
        <p><b>Location:</b> Sannidhi Road, Basavanagudi 560004</p>
        <p><b>Last check:</b> {status['last_check']}</p>
        <p><b>Interval:</b> every {CHECK_INTERVAL_MINUTES} min</p>
        <p><b>Alerting:</b> {YOUR_WHATSAPP_NUMBER}</p>
        <hr/>{rows}
        </body></html>""".encode()
        self.wfile.write(html)

    def log_message(self, format, *args):
        pass

def start_web_server():
    HTTPServer(("0.0.0.0", PORT), Handler).serve_forever()

# ── Helpers ───────────────────────────────────────────────────────────────────

def load_state():
    try:
        with open(STATE_FILE) as f:
            return json.load(f)
    except Exception:
        return {}

def save_state(s):
    with open(STATE_FILE, "w") as f:
        json.dump(s, f)

def send_whatsapp(platform, products):
    lines = [
        f"🚗 *Hot Wheels IN STOCK on {platform}!*", "",
        *[f"✅ {p}" for p in products[:8]],
        *(["…and more"] if len(products) > 8 else []),
        "", "🛒 Order before it sells out!",
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

# ── Scraper using real browser + GPS spoofing ─────────────────────────────────

def scrape_platform(browser, platform):
    name     = platform["name"]
    url      = platform["url"]
    loc_type = platform["set_location"]
    in_stock = []

    try:
        # Spoof GPS to Basavanagudi coordinates
        context = browser.new_context(
            geolocation={"latitude": LAT, "longitude": LON},
            permissions=["geolocation"],
            user_agent=(
                "Mozilla/5.0 (Linux; Android 13; Pixel 7) "
                "AppleWebKit/537.36 (KHTML, like Gecko) "
                "Chrome/124.0.6367.82 Mobile Safari/537.36"
            ),
            locale="en-IN",
            timezone_id="Asia/Kolkata",
            viewport={"width": 390, "height": 844},
        )
        page = context.new_page()

        # Inject coordinates into localStorage before page loads
        if loc_type == "blinkit":
            page.add_init_script(f"""
                localStorage.setItem('userLat', '{LAT}');
                localStorage.setItem('userLon', '{LON}');
                localStorage.setItem('userCity', 'Bangalore');
            """)
        elif loc_type == "swiggy":
            page.add_init_script(f"""
                localStorage.setItem('swiggy_location', JSON.stringify({{
                    "lat": {LAT}, "lng": {LON},
                    "address": "Sannidhi Road, Basavanagudi, Bangalore 560004"
                }}));
            """)

        log.info(f"Loading {name}...")
        page.goto(url, wait_until="domcontentloaded", timeout=45000)
        page.wait_for_timeout(6000)  # wait for JS to render products

        # Grab all visible text blocks
        elements = page.query_selector_all("h1, h2, h3, div, span, p, a")
        seen = set()

        for el in elements:
            try:
                text = el.inner_text().strip()
                if not text or len(text) > 150 or text in seen:
                    continue
                seen.add(text)
                tl = text.lower()

                if any(kw in tl for kw in KEYWORDS):
                    # Check parent context for out-of-stock signals
                    parent = el.evaluate("el => el.parentElement ? el.parentElement.innerText : ''")
                    if any(oos in parent.lower() for oos in OOS_KEYWORDS):
                        log.debug(f"  OOS: {text}")
                        continue
                    in_stock.append(text)
            except Exception:
                continue

        context.close()
        log.info(f"{name}: {len(in_stock)} in-stock item(s) found.")

    except Exception as e:
        log.error(f"{name} scrape error: {e}")

    return in_stock

# ── Main check loop ───────────────────────────────────────────────────────────

def run_check():
    log.info(f"=== Check at {datetime.now().strftime('%Y-%m-%d %H:%M:%S')} | Basavanagudi {LAT},{LON} ===")
    state    = load_state()
    new_state = {}

    with sync_playwright() as pw:
        browser = pw.chromium.launch(
            headless=True,
            args=[
                "--no-sandbox",
                "--disable-setuid-sandbox",
                "--disable-dev-shm-usage",
                "--disable-gpu",
            ]
        )

        for platform in PLATFORMS:
            found = scrape_platform(browser, platform)
            new_state[platform["name"]] = found
            prev        = set(state.get(platform["name"], []))
            newly_found = [p for p in found if p not in prev]

            if newly_found:
                log.info(f"🔥 NEW stock on {platform['name']}: {newly_found}")
                send_whatsapp(platform["name"], newly_found)
            elif found:
                log.info(f"{platform['name']}: {len(found)} item(s) still in stock.")
            else:
                log.info(f"{platform['name']}: Nothing in stock.")

        browser.close()

    save_state(new_state)
    status["last_check"] = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    status["results"]    = new_state
    log.info("=== Check complete ===\n")

# ── Entry point ───────────────────────────────────────────────────────────────

if __name__ == "__main__":
    log.info("🚗 Hot Wheels Checker — Basavanagudi, Bangalore 560004")
    log.info(f"GPS: {LAT}, {LON} | Interval: {CHECK_INTERVAL_MINUTES} min")
    log.info(f"Alerts → WhatsApp {YOUR_WHATSAPP_NUMBER}")

    threading.Thread(target=start_web_server, daemon=True).start()
    run_check()
    schedule.every(CHECK_INTERVAL_MINUTES).minutes.do(run_check)
    while True:
        schedule.run_pending()
        time.sleep(30)

