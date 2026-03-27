"""
Agent d'alertes vols depuis Toulouse (TLS)
Paramètres configurables via variables d'environnement (GitHub Actions / interface web)
"""

import os
import json
import requests
from datetime import datetime, timedelta

# ─── SECRETS ────────────────────────────────────────────────────────────────
SERPAPI_KEY      = os.environ.get("SERPAPI_KEY")
TELEGRAM_TOKEN   = os.environ.get("TELEGRAM_TOKEN")
TELEGRAM_CHAT_ID = os.environ.get("TELEGRAM_CHAT_ID")

# ─── PARAMÈTRES (depuis l'interface web ou valeurs par défaut) ───────────────
ORIGIN        = "TLS"
MAX_PRICE     = int(os.environ.get("MAX_PRICE", "80"))
MIN_NIGHTS    = int(os.environ.get("MIN_NIGHTS", "2"))
MAX_NIGHTS    = int(os.environ.get("MAX_NIGHTS", "5"))
MAX_STOPS     = int(os.environ.get("MAX_STOPS", "1"))
DAYS_RAW      = os.environ.get("DAYS", "friday")           # friday,saturday,any...
DESTS_RAW     = os.environ.get("DESTINATIONS", "")         # BCN,LIS,AMS ou vide
DATE_FROM_RAW = os.environ.get("DATE_FROM", "")            # YYYY-MM-DD ou vide
DATE_TO_RAW   = os.environ.get("DATE_TO", "")

# Parser les jours autorisés
ALLOWED_DAYS_MAP = {"friday": 4, "saturday": 5, "sunday": 6}
if "any" in DAYS_RAW or DAYS_RAW.strip() == "":
    ALLOWED_WEEKDAYS = None  # tous les jours
else:
    ALLOWED_WEEKDAYS = [ALLOWED_DAYS_MAP[d] for d in DAYS_RAW.split(",") if d in ALLOWED_DAYS_MAP]

# Parser les destinations
TARGET_DESTINATIONS = [d.strip().upper() for d in DESTS_RAW.split(",") if d.strip()] if DESTS_RAW else []

# Fenêtre de dates
today = datetime.today()
if DATE_FROM_RAW:
    DATE_FROM = datetime.strptime(DATE_FROM_RAW, "%Y-%m-%d")
else:
    DATE_FROM = today + timedelta(days=7)

if DATE_TO_RAW:
    DATE_TO = datetime.strptime(DATE_TO_RAW, "%Y-%m-%d")
else:
    DATE_TO = today + timedelta(days=90)

EXCLUDED_DESTINATIONS = ["TLS"]

# ─── GÉNÉRATION DES PAIRES DE DATES ─────────────────────────────────────────

def get_date_pairs() -> list[tuple[str, str]]:
    pairs = []
    current = DATE_FROM
    while current <= DATE_TO:
        if ALLOWED_WEEKDAYS is None or current.weekday() in ALLOWED_WEEKDAYS:
            for nights in range(MIN_NIGHTS, MAX_NIGHTS + 1):
                ret = current + timedelta(days=nights)
                if ret <= DATE_TO:
                    pairs.append((
                        current.strftime("%Y-%m-%d"),
                        ret.strftime("%Y-%m-%d")
                    ))
        current += timedelta(days=1)
    return pairs

# ─── RECHERCHE VOLS ──────────────────────────────────────────────────────────

def search_flights(outbound: str, inbound: str, destination: str) -> list[dict]:
    url = "https://serpapi.com/search"
    params = {
        "engine": "google_flights",
        "departure_id": ORIGIN,
        "arrival_id": destination,
        "outbound_date": outbound,
        "return_date": inbound,
        "currency": "EUR",
        "hl": "fr",
        "type": "1",
        "api_key": SERPAPI_KEY,
    }
    if MAX_STOPS == 0:
        params["stops"] = "1"

    try:
        r = requests.get(url, params=params, timeout=30)
        r.raise_for_status()
        data = r.json()
    except Exception as e:
        print(f"  Erreur API : {e}")
        return []

    results = []
    for cat in ["best_flights", "other_flights"]:
        for flight in data.get(cat, []):
            price = flight.get("price")
            if not price or price > MAX_PRICE:
                continue
            legs = flight.get("flights", [])
            if not legs:
                continue
            stops = len(legs) - 1
            if stops > MAX_STOPS:
                continue
            arr = legs[-1].get("arrival_airport", {})
            dest_code = arr.get("id", "?")
            if dest_code in EXCLUDED_DESTINATIONS:
                continue
            results.append({
                "price": price,
                "airline": legs[0].get("airline", "?"),
                "destination_code": dest_code,
                "destination_name": arr.get("name", "?"),
                "outbound_date": outbound,
                "inbound_date": inbound,
                "duration_min": flight.get("total_duration", 0),
                "stops": stops,
            })
    return results

# ─── TELEGRAM ────────────────────────────────────────────────────────────────

def format_message(deals: list[dict]) -> str:
    lines = [
        "✈️ *Alertes vols depuis Toulouse !*",
        f"_{len(deals)} offre(s) sous {MAX_PRICE}€_\n",
    ]
    for i, d in enumerate(deals[:10], 1):
        h, m = divmod(d["duration_min"], 60)
        dep = datetime.strptime(d["outbound_date"], "%Y-%m-%d")
        ret = datetime.strptime(d["inbound_date"], "%Y-%m-%d")
        nights = (ret - dep).days
        stops_lbl = "Direct" if d["stops"] == 0 else f"{d['stops']} escale(s)"
        lines.append(
            f"*{i}. {d['destination_name']} ({d['destination_code']})*\n"
            f"   💶 *{d['price']}€* A/R · {d['airline']}\n"
            f"   📅 {dep.strftime('%d/%m')} → {ret.strftime('%d/%m')} ({nights} nuits)\n"
            f"   🕐 {h}h{m:02d} · {stops_lbl}\n"
        )
    return "\n".join(lines)

def send_telegram(msg: str):
    url = f"https://api.telegram.org/bot{TELEGRAM_TOKEN}/sendMessage"
    try:
        r = requests.post(url, json={
            "chat_id": TELEGRAM_CHAT_ID,
            "text": msg,
            "parse_mode": "Markdown",
            "disable_web_page_preview": True,
        }, timeout=15)
        r.raise_for_status()
        print("  Telegram : message envoyé !")
    except Exception as e:
        print(f"  Erreur Telegram : {e}")

# ─── DÉDUPLICATION ───────────────────────────────────────────────────────────

SEEN_FILE = "seen_deals.json"

def load_seen() -> set:
    try:
        with open(SEEN_FILE) as f:
            return set(json.load(f))
    except Exception:
        return set()

def save_seen(seen: set):
    with open(SEEN_FILE, "w") as f:
        json.dump(list(seen), f)

def deal_id(d: dict) -> str:
    return f"{d['destination_code']}_{d['outbound_date']}_{d['inbound_date']}_{d['price']}"

# ─── MAIN ────────────────────────────────────────────────────────────────────

def main():
    print(f"\n{'='*52}")
    print(f"Agent vols TLS — {datetime.now().strftime('%d/%m/%Y %H:%M')}")
    print(f"Prix max : {MAX_PRICE}€ | Nuits : {MIN_NIGHTS}-{MAX_NIGHTS}")
    print(f"Du {DATE_FROM.strftime('%d/%m/%Y')} au {DATE_TO.strftime('%d/%m/%Y')}")
    print(f"Destinations : {TARGET_DESTINATIONS or 'toute Europe'}")
    print(f"{'='*52}\n")

    if not SERPAPI_KEY:
        raise ValueError("SERPAPI_KEY manquant !")
    if not TELEGRAM_TOKEN or not TELEGRAM_CHAT_ID:
        raise ValueError("TELEGRAM_TOKEN ou TELEGRAM_CHAT_ID manquant !")

    date_pairs = get_date_pairs()
    print(f"{len(date_pairs)} paires de dates générées")

    destinations = TARGET_DESTINATIONS or [
        "BCN","MAD","LIS","OPO","AMS","BRU","CDG","FCO","CIA",
        "VCE","NAP","ATH","VIE","PRG","BUD","WAW","DUB","EDI",
        "LGW","STN","CPH","ARN","OSL","HEL","MXP","LIN","BLQ",
        "PMI","IBZ","AGP","SVQ","RAK","CMN",
    ]

    seen = load_seen()
    new_deals = []

    for dest in destinations:
        for outbound, inbound in date_pairs[:6]:
            print(f"  TLS → {dest} | {outbound} → {inbound}")
            for deal in search_flights(outbound, inbound, dest):
                did = deal_id(deal)
                if did not in seen:
                    new_deals.append(deal)
                    seen.add(did)

    new_deals.sort(key=lambda x: x["price"])
    print(f"\n→ {len(new_deals)} nouvelle(s) offre(s)")

    if new_deals:
        send_telegram(format_message(new_deals))
        save_seen(seen)
    else:
        print("  Aucune nouvelle offre sous le seuil.")

    print("Terminé.\n")

if __name__ == "__main__":
    main()
