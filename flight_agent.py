#!/usr/bin/env python3
"""
Agent Alertes Vols Toulouse
- Source 1 : Ryanair API non-officielle (sans clé, direct)
- Source 2 : Google Flights scraping (toutes compagnies)
- Source 3 : Kayak scraping (fallback)
- Source 4 : SerpAPI (si clé disponible, prioritaire sur GF+Kayak)
- Notification : Telegram
"""

import os, json, time, hashlib, logging, re, random
import requests
from datetime import datetime, timedelta

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
log = logging.getLogger(__name__)

# ── CONFIG ─────────────────────────────────────────────────────────────────
TELEGRAM_TOKEN   = os.environ.get("TELEGRAM_TOKEN", "")
TELEGRAM_CHAT_ID = os.environ.get("TELEGRAM_CHAT_ID", "")
SERPAPI_KEY      = os.environ.get("SERPAPI_KEY", "")
RAPIDAPI_KEY     = os.environ.get("RAPIDAPI_KEY", "")

MAX_PRICE  = int(os.environ.get("MAX_PRICE",  "80"))
DATE_FROM  = os.environ.get("DATE_FROM", "")
DATE_TO    = os.environ.get("DATE_TO",   "")
MIN_NIGHTS = int(os.environ.get("MIN_NIGHTS", "2"))
MAX_NIGHTS = int(os.environ.get("MAX_NIGHTS", "5"))
MAX_STOPS  = int(os.environ.get("MAX_STOPS",  "1"))
DAYS_RAW   = os.environ.get("DAYS", "friday")
DEST_RAW   = os.environ.get("DESTINATIONS", "")

ORIGIN        = "TLS"
SEEN_FILE     = "seen_deals.json"
AGENT_ENABLED = os.environ.get("AGENT_ENABLED", "true").lower()

HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
        "AppleWebKit/537.36 (KHTML, like Gecko) "
        "Chrome/124.0.0.0 Safari/537.36"
    ),
    "Accept-Language": "fr-FR,fr;q=0.9,en-US;q=0.8",
    "Accept": "text/html,application/xhtml+xml,*/*;q=0.8",
}

# ── JOURS ──────────────────────────────────────────────────────────────────
DAY_MAP = {
    "monday":0, "tuesday":1, "wednesday":2, "thursday":3,
    "friday":4, "saturday":5, "sunday":6,
    "lundi":0, "mardi":1, "mercredi":2, "jeudi":3,
    "vendredi":4, "samedi":5, "dimanche":6,
}

def parse_days(raw):
    if not raw or raw.strip().lower() in ("any", "all", ""):
        return set(range(7))
    result = set()
    for p in raw.replace(";", ",").split(","):
        k = p.strip().lower()
        if k in DAY_MAP:
            result.add(DAY_MAP[k])
    return result or set(range(7))

ALLOWED_DAYS = parse_days(DAYS_RAW)

# ── DESTINATIONS ───────────────────────────────────────────────────────────
RYANAIR_FROM_TLS = {
    "BCN","MAD","DUB","STN","BGY","BVA","CRL","BRE","HAM","SVQ",
    "AGP","ALC","PMI","IBZ","LPA","TFS","FUE","ACE","RAK","CMN",
    "OPO","FAO","VLC","ZAZ","MAN","EDI","BRS","CIA","NAP","PSA",
    "EIN","KRK","WRO","GDN","CAT","TSF",
}

DEFAULT_DESTS = [
    # Ryanair direct TLS
    "BCN","MAD","DUB","STN","BGY","BVA","CRL","SVQ","AGP","ALC",
    "PMI","IBZ","LPA","TFS","FUE","ACE","RAK","CMN","OPO","FAO",
    "VLC","MAN","EDI","CIA","NAP","PSA","EIN","KRK","WRO","GDN",
    # Transavia direct TLS
    "AMS","LIS","TUN","DJE","MIR","NBE","ORN","ALG","TLM",
    # Volotea direct TLS
    "ATH","VCE","OLB","AJA","BIA","CFU","HER","KGS","RHO","SKG","DBV","SPU",
    # EasyJet / Air France / autres
    "LGW","LTN","BRS","NCE","GVA","FCO","MXP","CDG","ORY","LYS","LHR","IST",
]

def parse_destinations(raw):
    if not raw or not raw.strip():
        return DEFAULT_DESTS
    return [d.strip().upper() for d in raw.replace(";", ",").split(",") if d.strip()]

DESTINATIONS = parse_destinations(DEST_RAW)

# ── DATES ──────────────────────────────────────────────────────────────────
def build_date_range():
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

# ── CACHE ──────────────────────────────────────────────────────────────────
def load_seen():
    try:
        with open(SEEN_FILE) as f:
            return set(json.load(f))
    except Exception:
        return set()

def save_seen(seen):
    with open(SEEN_FILE, "w") as f:
        json.dump(list(seen), f)

def deal_id(orig, dest, dep, ret, price):
    return hashlib.md5(f"{orig}-{dest}-{dep}-{ret}-{price:.0f}".encode()).hexdigest()

# ══════════════════════════════════════════════════════════════════════════
#  SOURCE 1 — RYANAIR (API non-officielle, sans clé)
# ══════════════════════════════════════════════════════════════════════════

def ryanair_search(dest, dep_str, ret_str):
    if dest not in RYANAIR_FROM_TLS:
        return []
    url = "https://www.ryanair.com/api/farfnd/v4/roundTripFares"
    params = {
        "departureAirportIataCode":  ORIGIN,
        "arrivalAirportIataCode":    dest,
        "outboundDepartureDateFrom": dep_str,
        "outboundDepartureDateTo":   dep_str,
        "inboundDepartureDateFrom":  ret_str,
        "inboundDepartureDateTo":    ret_str,
        "currency":     "EUR",
        "priceValueTo": MAX_PRICE,
    }
    try:
        r = requests.get(url, params=params,
                         headers={**HEADERS, "Accept": "application/json"},
                         timeout=15)
        if not r.ok:
            return []
        results = []
        for fare in r.json().get("fares", []):
            try:
                total = fare["outbound"]["price"]["value"] + fare["inbound"]["price"]["value"]
                results.append({"price": round(total, 2), "stops": 0,
                                 "airline": "Ryanair", "source": "Ryanair"})
            except (KeyError, TypeError):
                continue
        return results
    except Exception as e:
        log.debug(f"Ryanair {dest}: {e}")
        return []

# ══════════════════════════════════════════════════════════════════════════
#  SOURCE 2 — GOOGLE FLIGHTS (scraping)
# ══════════════════════════════════════════════════════════════════════════

def gf_scrape(dest, dep_str, ret_str):
    """
    Appelle l'endpoint de recherche Google Flights et extrait les prix
    depuis les données JSON embarquées dans le HTML.
    """
    url = "https://www.google.com/travel/flights"
    params = {
        "hl":   "fr",
        "curr": "EUR",
        "q":    f"Vols {ORIGIN} {dest} {dep_str} {ret_str}",
    }
    try:
        r = requests.get(url, params=params, headers=HEADERS, timeout=20)
        if not r.ok:
            return []
        html = r.text

        # Google embarque les données dans des blocs JSON dans le HTML
        # On cherche des patterns de prix EUR réalistes
        candidates = []

        # Pattern 1 : prix suivi de EUR ou €
        for m in re.finditer(r'(\d{2,4})[,\.]?(\d{0,2})\s*(?:EUR|€)', html):
            try:
                price = float(m.group(1))
                if 15 <= price <= MAX_PRICE:
                    candidates.append(price)
            except ValueError:
                pass

        # Pattern 2 : dans des structures JSON type "price":{"amount":"123"}
        for m in re.finditer(r'"(?:amount|price|totalPrice)"\s*:\s*"?(\d{2,4})"?', html):
            try:
                price = float(m.group(1))
                if 15 <= price <= MAX_PRICE:
                    candidates.append(price)
            except ValueError:
                pass

        if candidates:
            price = min(candidates)
            return [{"price": price, "stops": MAX_STOPS,
                     "airline": "Diverses", "source": "Google Flights"}]
        return []
    except Exception as e:
        log.debug(f"GF scrape {dest}: {e}")
        return []

# ══════════════════════════════════════════════════════════════════════════
#  SOURCE 3 — KAYAK (scraping)
# ══════════════════════════════════════════════════════════════════════════

def kayak_scrape(dest, dep_str, ret_str):
    url = f"https://www.kayak.fr/flights/{ORIGIN}-{dest}/{dep_str}/{ret_str}?sort=price_a&fs=stops=~{MAX_STOPS}"
    try:
        r = requests.get(url, headers=HEADERS, timeout=20)
        if not r.ok:
            return []
        html = r.text

        candidates = []
        # Kayak affiche les prix dans des spans avec classe "price-text" ou similaire
        for m in re.finditer(r'(\d{2,4})\s*€', html[:100000]):
            try:
                price = float(m.group(1))
                if 15 <= price <= MAX_PRICE:
                    candidates.append(price)
            except ValueError:
                pass

        if candidates:
            price = min(candidates)
            return [{"price": price, "stops": MAX_STOPS,
                     "airline": "Diverses", "source": "Kayak"}]
        return []
    except Exception as e:
        log.debug(f"Kayak {dest}: {e}")
        return []

# ══════════════════════════════════════════════════════════════════════════
#  SOURCE 4 — SERPAPI (si clé dispo)
# ══════════════════════════════════════════════════════════════════════════

def serpapi_search(dest, dep_str, ret_str):
    if not SERPAPI_KEY:
        return []
    params = {
        "engine": "google_flights",
        "departure_id": ORIGIN, "arrival_id": dest,
        "outbound_date": dep_str, "return_date": ret_str,
        "currency": "EUR", "hl": "fr", "type": "1",
        "api_key": SERPAPI_KEY,
    }
    if MAX_STOPS == 0:
        params["stops"] = "1"
    try:
        r = requests.get("https://serpapi.com/search", params=params, timeout=25)
        if not r.ok:
            return []
        data = r.json()
        results = []
        for flight in data.get("best_flights", []) + data.get("other_flights", []):
            try:
                price = float(flight.get("price", 9999))
                legs  = flight.get("flights", [])
                stops = len(legs) - 1
                if stops > MAX_STOPS:
                    continue
                results.append({
                    "price":   price,
                    "stops":   stops,
                    "airline": legs[0].get("airline", "?") if legs else "?",
                    "source":  "SerpAPI",
                })
            except (KeyError, ValueError):
                continue
        return results
    except Exception as e:
        log.debug(f"SerpAPI {dest}: {e}")
        return []


# ══════════════════════════════════════════════════════════════════════════
#  SOURCE — FLYSCRAPER via RapidAPI (toutes compagnies)
# ══════════════════════════════════════════════════════════════════════════

def flyscraper_search(dest, dep_str, ret_str):
    if not RAPIDAPI_KEY:
        return []
    url = "https://flyScraper.p.rapidapi.com/flights/search"
    headers = {
        "x-rapidapi-host": "flyScraper.p.rapidapi.com",
        "x-rapidapi-key":  RAPIDAPI_KEY,
    }
    params = {
        "origin":        ORIGIN,
        "destination":   dest,
        "departureDate": dep_str,
        "returnDate":    ret_str,
        "adults":        "1",
        "currency":      "EUR",
        "cabinClass":    "economy",
    }
    try:
        r = requests.get(url, headers=headers, params=params, timeout=20)
        if not r.ok:
            log.debug(f"FlyScraper {r.status_code} pour {dest}")
            return []
        data = r.json()
        results = []
        flights = data.get("data", data.get("flights", data.get("results", [])))
        if isinstance(flights, dict):
            flights = flights.get("itineraries", flights.get("offers", []))
        for flight in flights[:10]:
            try:
                # Essaie différentes structures JSON selon la version de l'API
                price = (
                    flight.get("price", {}).get("total")
                    or flight.get("price", {}).get("amount")
                    or flight.get("totalPrice")
                    or flight.get("fare", {}).get("total")
                )
                if price is None:
                    continue
                price = float(str(price).replace(",","."))
                legs  = flight.get("legs", flight.get("segments", flight.get("slices", [])))
                stops = len(legs) - 1 if legs else 0
                if stops > MAX_STOPS:
                    continue
                airline = "Diverses"
                if legs:
                    seg = legs[0]
                    airline = (
                        seg.get("airline", {}).get("name")
                        or seg.get("operatingCarrier", {}).get("name")
                        or seg.get("carrierCode")
                        or "Diverses"
                    )
                results.append({
                    "price":   round(price, 2),
                    "stops":   stops,
                    "airline": airline,
                    "source":  "FlyScraper",
                })
            except (KeyError, ValueError, TypeError):
                continue
        return results
    except Exception as e:
        log.debug(f"FlyScraper exception {dest}: {e}")
        return []

# ══════════════════════════════════════════════════════════════════════════
#  MOTEUR COMBINÉ
# ══════════════════════════════════════════════════════════════════════════

def search_all(dest, dep_str, ret_str):
    results = []
    log_parts = []

    # 1. FlyScraper (prioritaire — toutes compagnies, rapide)
    if RAPIDAPI_KEY:
        f = flyscraper_search(dest, dep_str, ret_str)
        if f:
            results += f
            log_parts.append(f"FlyScraper:{len(f)}")

    # 2. Ryanair — API directe en complément
    r = ryanair_search(dest, dep_str, ret_str)
    if r:
        # Éviter les doublons Ryanair si déjà dans FlyScraper
        existing_prices = {round(x["price"]) for x in results}
        for offer in r:
            if round(offer["price"]) not in existing_prices:
                results.append(offer)
                log_parts.append("Ryanair:1")

    # 3. SerpAPI si clé dispo et pas de FlyScraper
    if SERPAPI_KEY and not RAPIDAPI_KEY:
        s = serpapi_search(dest, dep_str, ret_str)
        if s:
            results += s
            log_parts.append(f"SerpAPI:{len(s)}")

    if results:
        log.info(f"  {ORIGIN}→{dest} {dep_str}: {len(results)} offre(s) [{', '.join(log_parts)}]")
    return results

# ══════════════════════════════════════════════════════════════════════════
#  TELEGRAM
# ══════════════════════════════════════════════════════════════════════════

def send_telegram(message):
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
        log.error(f"Telegram: {e}")

def fmt_deal(dest, dep, ret, o):
    stops = "Direct ✅" if o["stops"] == 0 else f"{o['stops']} escale(s)"
    return (
        f"✈️ <b>TLS → {dest}</b>\n"
        f"📅 {dep} → {ret}\n"
        f"💶 <b>{o['price']:.0f}€</b> A/R · {stops}\n"
        f"🏢 {o['airline']} · via {o['source']}"
    )

# ══════════════════════════════════════════════════════════════════════════
#  MAIN
# ══════════════════════════════════════════════════════════════════════════

def main():
    print("=" * 54)
    print(f"Agent vols TLS — {datetime.utcnow().strftime('%d/%m/%Y %H:%M')} UTC")
    print(f"Seuil: {MAX_PRICE}€ | Séjour: {MIN_NIGHTS}–{MAX_NIGHTS}j | Escales max: {MAX_STOPS}")
    print(f"Sources: {'FlyScraper' if RAPIDAPI_KEY else ''} + Ryanair + {'SerpAPI' if SERPAPI_KEY else 'scraping'}")
    print("=" * 54)

    # Vérification ON/OFF
    if AGENT_ENABLED == "false":
        print("Agent désactivé (AGENT_ENABLED=false). Arrêt.")
        return

    if not TELEGRAM_TOKEN or not TELEGRAM_CHAT_ID:
        raise ValueError("TELEGRAM_TOKEN et TELEGRAM_CHAT_ID sont requis")

    seen     = load_seen()
    new_seen = set()
    deals    = []
    dates    = build_date_range()

    log.info(f"{len(dates)} dates · {len(DESTINATIONS)} destinations · {len(dates)*len(DESTINATIONS)*(MAX_NIGHTS-MIN_NIGHTS+1)} combos max")

    for dep_date in dates:
        for dest in DESTINATIONS:
            for nights in range(MIN_NIGHTS, MAX_NIGHTS + 1):
                ret_date = dep_date + timedelta(days=nights)
                dep_str  = dep_date.strftime("%Y-%m-%d")
                ret_str  = ret_date.strftime("%Y-%m-%d")

                offers = search_all(dest, dep_str, ret_str)

                for offer in offers:
                    if offer["price"] > MAX_PRICE:
                        continue
                    did = deal_id(ORIGIN, dest, dep_str, ret_str, offer["price"])
                    new_seen.add(did)
                    if did not in seen:
                        deals.append((dest, dep_str, ret_str, offer))

                time.sleep(random.uniform(0.2, 0.5))

    log.info(f"{len(deals)} nouvelle(s) offre(s) sous {MAX_PRICE}€")

    if deals:
        blocs = [f"🔔 <b>{len(deals)} bon(s) plan(s) depuis Toulouse !</b>"]
        blocs += [fmt_deal(d, dp, rt, o) for d, dp, rt, o in deals[:15]]
        send_telegram("\n\n".join(blocs))
    else:
        log.info("Aucune offre — pas de notif Telegram")

    save_seen(seen | new_seen)
    print("=" * 54)
    print("Terminé.")

if __name__ == "__main__":
    main()
