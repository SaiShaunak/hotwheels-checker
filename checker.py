import os
import time
import json
import logging
import requests
import threading
from datetime import datetime
from http.server import HTTPServer, BaseHTTPRequestHandler
from twilio.rest import Client
from bs4 import BeautifulSoup

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s"
)
log = logging.getLogger(__name__)

# ── Config ────────────────────────────────────────────────────────────────────
TWILIO_ACCOUNT_SID     = os.environ["TWILIO_ACCOUNT_SID"]
TWILIO_AUTH_TOKEN      = os.environ["TWILIO_AUTH_TOKEN"]
YOUR_WHATSAPP_NUMBER   = os.environ["YOUR_WHATSAPP_NUMBER"]
CHECK_INTERVAL_MINUTES = int(os.environ.get("CHECK_INTERVAL_MINUTES", "15"))
PORT                   = int(os.environ.get("PORT", "8080"))
TWILIO_WHATSAPP_FROM   = "whatsapp:+14155238886"

# ── Your location: Sannidhi Road, Basavanagudi, Bangalore 560004 ──────────────
LAT      = "12.9422"
LON      = "77.5739"
PINCODE  = "560004"
CITY     = "Bangalore"

PLATFORMS = [
    {
        "name": "Blinkit",
        "url": f"https://blinkit.com/s/?q=hot+wheels&lat={LAT}&lon={LON}",
        "headers": {
            "User-Agent": "Mozilla/5.0 (Linux; Android 13; Pixel 7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0.6367.82 Mobile Safari/537.36",
            "Accept-Language": "en-IN,en;q=0.9",
            "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
            "lat": LAT,
            "lon": LON,
        },
        "keywords": ["hot wheels", "hotwheels", "matchbox"],
        "oos_markers": ["out of stock", "notify me", "sold out"],
    },
    {
        "name": "BigBasket",
        "url": f"https://www.bigbasket.com/ps/?q=hot+wheels&tab=prd&pincode={PINCODE}",
        "headers": {
            "User-Agent": "Mozilla/5.0 (Linux; Android 13; Pixel 7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0.6367.82 Mobile Safari/537.36",
            "Accept-Language": "en-IN,en;q=0.9",
            "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
            "Cookie": f"userPincode={PINCODE}; userCity={CITY}",
        },
        "keywords": ["hot wheels", "hotwheels", "matchbox"],
        "oos_markers": ["out of stock", "notify me", "sold out"],
    },
    {
        "name": "Instamart",
        "url": f"https://www.swiggy.com/instamart/search?query=hot+wheels&lat={LAT}&lng={LON}",
        "headers": {
            "User-Agent": "Mozilla/5.0 (Linux; Android 13; Pixel 7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0.6367.82 Mobile Safari/537.36",
            "Accept-Language": "en-IN,en;q=0.9",
            "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
        },
        "keywords": ["hot wheels", "hotwheels", "matchbox"],
        "oos_markers": ["out of stock", "sold out"],
    },
    {
        "name": "Zepto",
        "url": f"https://www.zeptonow.com/search?query=hot+wheels",
        "headers": {
            "User-Agent": "Mozilla/5.0 (Linux; Android 13; Pixel 7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0.6367.82 Mobile Safari/537.36",
            "Accept-Language": "en-IN,en;q=0.9",
            "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
            "x-latitude": LAT,
            "x-longitude": LON,
        },
        "keywords": ["hot wheels", "hotwheels", "matchbox"],
        "oos_markers": ["out of stock", "sold out"],
    },
]

STATE_FILE = "/tmp/hw_state.json"
status = {
    "last_check": "Not yet",
    "next_check": "Soon",
    "results": {},
}

# ── Web server (keeps Railway alive) ─────────────────────────────────────────

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
        <p><b>Status:</b> Running</p>
        <p><b>Location:</b> Sannidhi Road, Basavanagudi, Bangalore 560004</p>
        <p><b>Last check:</b> {status['last_check']}</p>
        <p><b>Next check in:</b> {CHECK_INTERVAL_MINUTES} min</p>
        <p><b>Alerting:</b> {YOUR_WHATSAPP_NUMBER}</p>
        <hr/>{rows}
        </body></html>""".encode()
        self.wfile.write(html)

    def log_message(self, format, *args):
        pass

def start_web_server():
    server = HTTPServer(("0.0.0.0", PORT), Handler)
    log.info(f"Web server running on port {PORT}")
    server.serve_forever()

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

def check_platform(platform):
    name = platform["name"]
    try:
        resp = requests.get(platform["url"], headers=platform["headers"], timeout=20)
        soup = BeautifulSoup(resp.text, "html.parser")
        in_stock = []
        for tag in soup.find_all(["h2", "h3", "span", "div", "a"]):
            t = tag.get_text(strip=True)
            if not t or len(t) > 120:
                continue
            if any(kw in t.lower() for kw in platform["keywords"]):
                parent_text = (tag.parent.get_text(separator=" ") if tag.parent else "").lower()
                if any(oos in parent_text for oos in platform["oos_markers"]):
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
    log.info(f"Location: Sannidhi Road, Basavanagudi {PINCODE} (lat={LAT}, lon={LON})")
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
            log.info(f"{platform['name']}: Same items still in stock.")
        else:
            log.info(f"{platform['name']}: Nothing in stock.")
    save_state(new_state)
    status["last_check"] = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    status["results"] = new_state
    log.info("=== Check complete ===\n")

# ── Main ──────────────────────────────────────────────────────────────────────

if __name__ == "__main__":
    log.info("🚗 Hot Wheels Stock Checker starting up…")
    log.info(f"Location: Sannidhi Road, Basavanagudi, Bangalore {PINCODE}")
    log.info(f"Checking every {CHECK_INTERVAL_MINUTES} minutes.")
    log.info(f"Alerts → WhatsApp {YOUR_WHATSAPP_NUMBER}")

    threading.Thread(target=start_web_server, daemon=True).start()
    run_check()
    import schedule
    schedule.every(CHECK_INTERVAL_MINUTES).minutes.do(run_check)
    while True:
        schedule.run_pending()
        time.sleep(30)
