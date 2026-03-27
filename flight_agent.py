#!/usr/bin/env python3
"""
Agent Alertes Vols Toulouse
- Source 1 : Amadeus API (2000 req/mois gratuit) — prioritaire
- Source 2 : SerpAPI / Google Flights (fallback si Amadeus échoue)
- Notification : Telegram
"""

import os
import json
import time
import hashlib
import logging
import requests
from datetime import datetime, timedelta

# ── LOGGING ────────────────────────────────────────────────────────────────
logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
log = logging.getLogger(__name__)

# ── CONFIG DEPUIS VARIABLES D'ENVIRONNEMENT ────────────────────────────────
SERPAPI_KEY          = os.environ.get("SERPAPI_KEY", "")
TELEGRAM_TOKEN       = os.environ.get("TELEGRAM_TOKEN", "")
TELEGRAM_CHAT_ID     = os.environ.get("TELEGRAM_CHAT_ID", "")
AMADEUS_CLIENT_ID    = os.environ.get("AMADEUS_CLIENT_ID", "")
AMADEUS_CLIENT_SECRET= os.environ.get("AMADEUS_CLIENT_SECRET", "")

MAX_PRICE   = int(os.environ.get("MAX_PRICE",   "80"))
DATE_FROM   = os.environ.get("DATE_FROM",  "")
DATE_TO     = os.environ.get("DATE_TO",    "")
MIN_NIGHTS  = int(os.environ.get("MIN_NIGHTS",  "2"))
MAX_NIGHTS  = int(os.environ.get("MAX_NIGHTS",  "5"))
MAX_STOPS   = int(os.environ.get("MAX_STOPS",   "1"))
DAYS_RAW    = os.environ.get("DAYS", "friday")
DESTINATIONS_RAW = os.environ.get("DESTINATIONS", "")

ORIGIN = "TLS"
SEEN_FILE = "seen_deals.json"

# ── JOURS DE DÉPART ────────────────────────────────────────────────────────
DAY_MAP = {
    "monday": 0, "tuesday": 1, "wednesday": 2, "thursday": 3,
    "friday": 4, "saturday": 5, "sunday": 6,
    "lundi": 0, "mardi": 1, "mercredi": 2, "jeudi": 3,
    "vendredi": 4, "samedi": 5, "dimanche": 6,
}

def parse_days(raw: str) -> set:
    if not raw or raw.strip().lower() in ("any", "all", ""):
        return set(range(7))
    result = set()
    for part in raw.replace(";", ",").split(","):
        key = part.strip().lower()
        if key in DAY_MAP:
            result.add(DAY_MAP[key])
    return result if result else set(range(7))

ALLOWED_DAYS = parse_days(DAYS_RAW)

# ── DESTINATIONS ───────────────────────────────────────────────────────────
DEFAULT_DESTINATIONS = [
    "BCN","MAD","LIS","ORY","CDG","AMS","BRU","DUB","FCO","MXP",
    "VCE","ATH","PRG","BUD","WAW","VIE","CPH","ARN","OSL","HEL",
    "LHR","STN","EDI","MAN","RAK","CMN","TUN","ALG","PMI","IBZ",
    "ALC","AGP","OPO","FAO","FUE","LPA","ACE","TFS",
]

def parse_destinations(raw: str) -> list:
    if not raw or not raw.strip():
        return DEFAULT_DESTINATIONS
    return [d.strip().upper() for d in raw.replace(";", ",").split(",") if d.strip()]

DESTINATIONS = parse_destinations(DESTINATIONS_RAW)

# ── FENÊTRE DE DATES ───────────────────────────────────────────────────────
def build_date_range() -> list:
    today = datetime.utcnow().date()
    start = datetime.strptime(DATE_FROM, "%Y-%m-%d").date() if DATE_FROM else today + timedelta(days=7)
    end   = datetime.strptime(DATE_TO,   "%Y-%m-%d").date() if DATE_TO   else today + timedelta(days=90)
    dates = []
    cur = start
    while cur <= end:
        if cur.weekday() in ALLOWED_DAYS:
            dates.append(cur)
        cur += timedelta(days=1)
    return dates

# ── CACHE DES OFFRES DÉJÀ VUES ────────────────────────────────────────────
def load_seen() -> set:
    try:
        with open(SEEN_FILE) as f:
            return set(json.load(f))
    except Exception:
        return set()

def save_seen(seen: set):
    with open(SEEN_FILE, "w") as f:
        json.dump(list(seen), f)

def deal_id(origin, dest, dep_date, ret_date, price) -> str:
    key = f"{origin}-{dest}-{dep_date}-{ret_date}-{price}"
    return hashlib.md5(key.encode()).hexdigest()

# ══════════════════════════════════════════════════════════════════════════
#  SOURCE 1 — AMADEUS
# ══════════════════════════════════════════════════════════════════════════

_amadeus_token = None
_amadeus_token_expiry = 0

def amadeus_get_token() -> str | None:
    global _amadeus_token, _amadeus_token_expiry
    if not AMADEUS_CLIENT_ID or not AMADEUS_CLIENT_SECRET:
        return None
    if _amadeus_token and time.time() < _amadeus_token_expiry - 30:
        return _amadeus_token
    try:
        r = requests.post(
            "https://test.api.amadeus.com/v1/security/oauth2/token",
            data={
                "grant_type": "client_credentials",
                "client_id": AMADEUS_CLIENT_ID,
                "client_secret": AMADEUS_CLIENT_SECRET,
            },
            timeout=15,
        )
        r.raise_for_status()
        data = r.json()
        _amadeus_token = data["access_token"]
        _amadeus_token_expiry = time.time() + data.get("expires_in", 1799)
        log.info("Amadeus token OK")
        return _amadeus_token
    except Exception as e:
        log.warning(f"Amadeus auth échouée : {e}")
        return None


def amadeus_search(origin: str, destination: str, dep_date: str, ret_date: str) -> list:
    """
    Retourne une liste de dicts {price, stops, duration, airline}
    """
    token = amadeus_get_token()
    if not token:
        return []

    params = {
        "originLocationCode":      origin,
        "destinationLocationCode": destination,
        "departureDate":           dep_date,
        "returnDate":              ret_date,
        "adults":                  1,
        "nonStop":                 "false",
        "max":                     5,
        "currencyCode":            "EUR",
    }
    if MAX_STOPS == 0:
        params["nonStop"] = "true"

    try:
        r = requests.get(
            "https://test.api.amadeus.com/v2/shopping/flight-offers",
            headers={"Authorization": f"Bearer {token}"},
            params=params,
            timeout=20,
        )
        if r.status_code == 429:
            log.warning("Amadeus rate limit atteint")
            return []
        if not r.ok:
            log.warning(f"Amadeus {r.status_code} pour {origin}→{destination} {dep_date}")
            return []

        offers = r.json().get("data", [])
        results = []
        for offer in offers:
            try:
                price = float(offer["price"]["grandTotal"])
                itins = offer["itineraries"]
                # stops = nombre de segments - 1 sur le trajet aller
                stops = len(itins[0]["segments"]) - 1
                if stops > MAX_STOPS:
                    continue
                duration = itins[0].get("duration", "")
                airline = offer["validatingAirlineCodes"][0] if offer.get("validatingAirlineCodes") else "?"
                results.append({
                    "price": price,
                    "stops": stops,
                    "duration": duration,
                    "airline": airline,
                    "source": "Amadeus",
                })
            except (KeyError, IndexError, ValueError):
                continue
        return results

    except Exception as e:
        log.warning(f"Amadeus search exception : {e}")
        return []


# ══════════════════════════════════════════════════════════════════════════
#  SOURCE 2 — SERPAPI (Google Flights)
# ══════════════════════════════════════════════════════════════════════════

def serpapi_search(origin: str, destination: str, dep_date: str, ret_date: str) -> list:
    if not SERPAPI_KEY:
        return []
    params = {
        "engine":           "google_flights",
        "departure_id":     origin,
        "arrival_id":       destination,
        "outbound_date":    dep_date,
        "return_date":      ret_date,
        "currency":         "EUR",
        "hl":               "fr",
        "type":             "1",  # 1 = aller-retour
        "api_key":          SERPAPI_KEY,
    }
    if MAX_STOPS == 0:
        params["stops"] = "1"  # 1 = direct only in SerpAPI

    try:
        r = requests.get("https://serpapi.com/search", params=params, timeout=25)
        if not r.ok:
            log.warning(f"SerpAPI {r.status_code} pour {origin}→{destination}")
            return []
        data = r.json()
        results = []
        for flight in data.get("best_flights", []) + data.get("other_flights", []):
            try:
                price = float(flight.get("price", 999999))
                legs  = flight.get("flights", [])
                stops = len(legs) - 1
                if stops > MAX_STOPS:
                    continue
                airline  = legs[0].get("airline", "?") if legs else "?"
                duration = flight.get("total_duration", 0)
                dur_str  = f"PT{duration//60}H{duration%60}M" if duration else ""
                results.append({
                    "price":    price,
                    "stops":    stops,
                    "duration": dur_str,
                    "airline":  airline,
                    "source":   "SerpAPI",
                })
            except (KeyError, ValueError):
                continue
        return results

    except Exception as e:
        log.warning(f"SerpAPI exception : {e}")
        return []


# ══════════════════════════════════════════════════════════════════════════
#  RECHERCHE COMBINÉE
# ══════════════════════════════════════════════════════════════════════════

def search_flights(origin: str, destination: str, dep_date: str, ret_date: str) -> list:
    """
    Essaie Amadeus en premier. Si aucun résultat, bascule sur SerpAPI.
    """
    results = amadeus_search(origin, destination, dep_date, ret_date)
    source_used = "Amadeus"

    if not results and SERPAPI_KEY:
        log.info(f"  Amadeus vide → fallback SerpAPI pour {origin}→{destination}")
        results = serpapi_search(origin, destination, dep_date, ret_date)
        source_used = "SerpAPI"

    if results:
        log.info(f"  {origin}→{destination} {dep_date} : {len(results)} offre(s) via {source_used}")
    return results


# ══════════════════════════════════════════════════════════════════════════
#  TELEGRAM
# ══════════════════════════════════════════════════════════════════════════

def send_telegram(message: str):
    if not TELEGRAM_TOKEN or not TELEGRAM_CHAT_ID:
        log.warning("Telegram non configuré")
        return
    try:
        r = requests.post(
            f"https://api.telegram.org/bot{TELEGRAM_TOKEN}/sendMessage",
            json={"chat_id": TELEGRAM_CHAT_ID, "text": message, "parse_mode": "HTML"},
            timeout=15,
        )
        r.raise_for_status()
        log.info("Telegram ✓")
    except Exception as e:
        log.error(f"Telegram erreur : {e}")


def format_duration(iso: str) -> str:
    """PT2H35M → 2h35"""
    if not iso:
        return ""
    iso = iso.replace("PT", "")
    h = m = 0
    if "H" in iso:
        h, iso = iso.split("H")
        h = int(h)
    if "M" in iso:
        m = int(iso.replace("M", ""))
    return f"{h}h{m:02d}" if h else f"{m}min"


def format_deal(dest, dep_date, ret_date, offer: dict) -> str:
    stops_label = "Direct" if offer["stops"] == 0 else f"{offer['stops']} escale(s)"
    dur = format_duration(offer.get("duration", ""))
    dur_str = f" · {dur}" if dur else ""
    src = offer.get("source", "")
    return (
        f"✈️ <b>TLS → {dest}</b>\n"
        f"📅 {dep_date} → {ret_date}\n"
        f"💶 <b>{offer['price']:.0f}€</b> A/R · {stops_label}{dur_str}\n"
        f"🏢 {offer['airline']} · via {src}"
    )


# ══════════════════════════════════════════════════════════════════════════
#  MAIN
# ══════════════════════════════════════════════════════════════════════════

def main():
    print("=" * 52)
    print(f"Agent vols Toulouse — {datetime.utcnow().strftime('%d/%m/%Y %H:%M')} UTC")
    print(f"Seuil : {MAX_PRICE}€ | Séjour : {MIN_NIGHTS}–{MAX_NIGHTS} nuits")
    print(f"Escales max : {MAX_STOPS} | Sources : Amadeus + SerpAPI fallback")
    print("=" * 52)

    # Validation minimale
    if not TELEGRAM_TOKEN or not TELEGRAM_CHAT_ID:
        raise ValueError("TELEGRAM_TOKEN et TELEGRAM_CHAT_ID sont requis")
    if not AMADEUS_CLIENT_ID and not SERPAPI_KEY:
        raise ValueError("Au moins AMADEUS_CLIENT_ID+SECRET ou SERPAPI_KEY sont requis")

    seen      = load_seen()
    deals     = []
    dates     = build_date_range()
    new_seen  = set()

    log.info(f"{len(dates)} dates candidates · {len(DESTINATIONS)} destinations")

    for dep_date in dates:
        for dest in DESTINATIONS:
            for nights in range(MIN_NIGHTS, MAX_NIGHTS + 1):
                ret_date = dep_date + timedelta(days=nights)
                dep_str  = dep_date.strftime("%Y-%m-%d")
                ret_str  = ret_date.strftime("%Y-%m-%d")

                offers = search_flights(ORIGIN, dest, dep_str, ret_str)

                for offer in offers:
                    if offer["price"] > MAX_PRICE:
                        continue
                    did = deal_id(ORIGIN, dest, dep_str, ret_str, offer["price"])
                    new_seen.add(did)
                    if did not in seen:
                        deals.append((dest, dep_str, ret_str, offer))

                # Petite pause pour ne pas surcharger les APIs
                time.sleep(0.3)

    log.info(f"{len(deals)} nouvelle(s) offre(s) sous {MAX_PRICE}€")

    if deals:
        header = f"🔔 <b>{len(deals)} bon(s) plan(s) depuis Toulouse !</b>\n"
        chunks = [header]
        for dest, dep, ret, offer in deals[:15]:  # max 15 par notif
            chunks.append(format_deal(dest, dep, ret, offer))
        send_telegram("\n\n".join(chunks))
    else:
        log.info("Aucune offre sous le seuil — pas de notif Telegram")

    # Fusionner anciens + nouveaux IDs vus
    save_seen(seen | new_seen)
    print("=" * 52)
    print("Terminé.")


if __name__ == "__main__":
    main()
