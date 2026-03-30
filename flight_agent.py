#!/usr/bin/env python3
“””
Agent Alertes Vols — Multi-aéroports
Sources: FlyScraper (RapidAPI) + Ryanair API + SerpAPI fallback
“””

import os, json, time, hashlib, logging, re, random
import requests
from datetime import datetime, timedelta

logging.basicConfig(level=logging.INFO, format=”%(asctime)s %(levelname)s %(message)s”)
log = logging.getLogger(**name**)

# ── CONFIG ─────────────────────────────────────────────────────────────────

TELEGRAM_TOKEN    = os.environ.get(“TELEGRAM_TOKEN”, “”)
TELEGRAM_CHAT_ID  = os.environ.get(“TELEGRAM_CHAT_ID”, “”)
RAPIDAPI_KEY      = os.environ.get(“RAPIDAPI_KEY”, “”)
SERPAPI_KEY       = os.environ.get(“SERPAPI_KEY”, “”)
AGENT_ENABLED     = os.environ.get(“AGENT_ENABLED”, “true”).lower()
CRON_MODE         = os.environ.get(“CRON_MODE”, “4xday”)
IS_MANUAL         = os.environ.get(“IS_MANUAL”, “false”) == “true”

MAX_PRICE   = int(os.environ.get(“MAX_PRICE”,  “80”))
DATE_FROM   = os.environ.get(“DATE_FROM”, “”)
DATE_TO     = os.environ.get(“DATE_TO”,   “”)
MIN_NIGHTS  = int(os.environ.get(“MIN_NIGHTS”, “2”))
MAX_NIGHTS  = int(os.environ.get(“MAX_NIGHTS”, “5”))
MAX_STOPS   = int(os.environ.get(“MAX_STOPS”,  “1”))
DAYS_RAW    = os.environ.get(“DAYS”, “any”)
DEST_RAW    = os.environ.get(“DESTINATIONS”, “”)
ORIGIN_RAW  = os.environ.get(“ORIGIN”, “TLS”)

SEEN_FILE = “seen_deals.json”

# ── NOMS D’AÉROPORTS ───────────────────────────────────────────────────────

AIRPORT_NAMES = {
“TLS”:“Toulouse”,“BCN”:“Barcelone”,“MAD”:“Madrid”,“LIS”:“Lisbonne”,
“OPO”:“Porto”,“FCO”:“Rome Fiumicino”,“MXP”:“Milan Malpensa”,“VCE”:“Venise”,
“NAP”:“Naples”,“ATH”:“Athènes”,“PRG”:“Prague”,“BUD”:“Budapest”,
“WAW”:“Varsovie”,“VIE”:“Vienne”,“AMS”:“Amsterdam”,“BRU”:“Bruxelles”,
“DUB”:“Dublin”,“EDI”:“Édimbourg”,“MAN”:“Manchester”,“STN”:“Londres Stansted”,
“LTN”:“Londres Luton”,“LGW”:“Londres Gatwick”,“LHR”:“Londres Heathrow”,
“BRS”:“Bristol”,“CDG”:“Paris CDG”,“ORY”:“Paris Orly”,“LYS”:“Lyon”,
“NCE”:“Nice”,“GVA”:“Genève”,“PMI”:“Majorque”,“IBZ”:“Ibiza”,“MAH”:“Minorque”,
“LPA”:“Gran Canaria”,“TFS”:“Tenerife Sud”,“FUE”:“Fuerteventura”,
“ACE”:“Lanzarote”,“AGP”:“Malaga”,“ALC”:“Alicante”,“SVQ”:“Séville”,
“VLC”:“Valence”,“FAO”:“Faro”,“RAK”:“Marrakech”,“CMN”:“Casablanca”,
“TUN”:“Tunis”,“DJE”:“Djerba”,“MIR”:“Monastir”,“NBE”:“Enfidha”,
“ORN”:“Oran”,“ALG”:“Alger”,“TLM”:“Tlemcen”,“CPH”:“Copenhague”,
“ARN”:“Stockholm”,“OSL”:“Oslo”,“HEL”:“Helsinki”,“IST”:“Istanbul”,
“DBV”:“Dubrovnik”,“SPU”:“Split”,“CFU”:“Corfou”,“HER”:“Héraklion”,
“RHO”:“Rhodes”,“SKG”:“Thessalonique”,“OLB”:“Olbia”,“AJA”:“Ajaccio”,
“BIA”:“Bastia”,“BGY”:“Milan Bergame”,“BVA”:“Paris Beauvais”,
“CRL”:“Bruxelles Charleroi”,“CIA”:“Rome Ciampino”,“PSA”:“Pise”,
“EIN”:“Eindhoven”,“KRK”:“Cracovie”,“WRO”:“Wroclaw”,“GDN”:“Gdansk”,
“ZTH”:“Zakynthos”,“JMK”:“Mykonos”,“KGS”:“Kos”,“PVK”:“Preveza”,
“BOD”:“Bordeaux”,“MRS”:“Marseille”,“NTE”:“Nantes”,“MLH”:“Bâle-Mulhouse”,
“BER”:“Berlin”,“VIE”:“Vienne”,“BUH”:“Bucarest”,“OTP”:“Bucarest”,
“SOF”:“Sofia”,“WAW”:“Varsovie”,“TIV”:“Tivat”,“SKP”:“Skopje”,
“AAE”:“Annaba”,“BJA”:“Béjaïa”,“CZL”:“Constantine”,“ORN”:“Oran”,
“ESS”:“Essaouira”,“NDR”:“Nador”,“OUD”:“Oujda”,“RBA”:“Rabat”,“TNG”:“Tanger”,
“AGP”:“Malaga”,“FES”:“Fès”,“AGA”:“Agadir”,
}

def airport_label(iata):
name = AIRPORT_NAMES.get(iata, “”)
return f”{name} ({iata})” if name else iata

# ── DESTINATIONS PAR AÉROPORT DE DÉPART ───────────────────────────────────

# Routes directes confirmées par compagnie low-cost

RYANAIR_ROUTES = {
“TLS”: [“BCN”,“MAD”,“DUB”,“STN”,“BGY”,“BVA”,“CRL”,“SVQ”,“AGP”,“ALC”,
“PMI”,“IBZ”,“LPA”,“TFS”,“FUE”,“ACE”,“RAK”,“CMN”,“OPO”,“FAO”,
“VLC”,“MAN”,“EDI”,“CIA”,“NAP”,“PSA”,“EIN”,“KRK”,“WRO”,“GDN”],
“BOD”: [“BCN”,“MAD”,“LIS”,“OPO”,“PMI”,“IBZ”,“ALC”,“AGP”,“LPA”,“TFS”,
“FUE”,“ACE”,“RAK”,“CMN”,“SVQ”,“VLC”,“STN”,“LGW”,“LTN”,“MAN”,
“EDI”,“BRS”,“CIA”,“VCE”,“FCO”,“BUD”,“KRK”,“BER”,“PRG”],
“MRS”: [“BCN”,“MAD”,“LIS”,“OPO”,“PMI”,“ALC”,“AGP”,“LPA”,“RAK”,“CMN”,
“TUN”,“DJE”,“MIR”,“ALG”,“ORN”,“STN”,“LGW”,“LTN”,“MAN”,“EDI”,
“CIA”,“NAP”,“BGY”,“EIN”,“KRK”,“WRO”,“ATH”,“AGA”,“FES”,
“ESS”,“NDR”,“OUD”,“RBA”,“TNG”],
“CDG”: [“BCN”,“MAD”,“LIS”,“OPO”,“FCO”,“MXP”,“VCE”,“NAP”,“ATH”,“PRG”,
“BUD”,“WAW”,“VIE”,“AMS”,“DUB”,“EDI”,“MAN”,“STN”,“LGW”,“LTN”,
“PMI”,“IBZ”,“LPA”,“TFS”,“FUE”,“ACE”,“AGP”,“ALC”,“SVQ”,“VLC”,
“FAO”,“RAK”,“CMN”,“TUN”,“DJE”,“MIR”,“ALG”,“ORN”,“CPH”,“ARN”,
“OSL”,“HEL”,“IST”,“DBV”,“SPU”,“CFU”,“HER”,“RHO”,“SKG”,“GVA”,
“ZTH”,“JMK”,“KGS”,“BER”,“AAE”,“BJA”,“CZL”],
“ORY”: [“BCN”,“MAD”,“LIS”,“OPO”,“PMI”,“ALC”,“AGP”,“LPA”,“TFS”,“FUE”,
“ACE”,“RAK”,“CMN”,“TUN”,“DJE”,“MIR”,“ALG”,“ORN”,“MAN”,“STN”,
“LGW”,“LTN”,“EDI”,“CIA”,“NAP”,“BGY”,“VCE”,“ATH”,“PRG”,“BUD”],
}

# Toutes destinations directes (toutes compagnies) par aéroport

ALL_DIRECT_ROUTES = {
“TLS”: list(set(RYANAIR_ROUTES[“TLS”] + [
“AMS”,“LIS”,“TUN”,“DJE”,“MIR”,“NBE”,“ORN”,“ALG”,“TLM”,
“ATH”,“VCE”,“OLB”,“AJA”,“BIA”,“CFU”,“HER”,“KGS”,“RHO”,“SKG”,“DBV”,“SPU”,“ZTH”,
“LGW”,“LTN”,“BRS”,“NCE”,“GVA”,“FCO”,“MXP”,“CDG”,“ORY”,“LYS”,“LHR”,“IST”,
])),
“BOD”: list(set(RYANAIR_ROUTES[“BOD”] + [
“AMS”,“NCE”,“GVA”,“MLH”,“ATH”,“VCE”,“OLB”,“DBV”,“SPU”,“HER”,“RHO”,“CFU”,“SKG”,
“LHR”,“BRS”,“BER”,“IST”,“TUN”,“DJE”,“MIR”,“ALG”,“ORN”,“RAK”,“CMN”,
“CPH”,“OSL”,“LYS”,“NTE”,
])),
“MRS”: list(set(RYANAIR_ROUTES[“MRS”] + [
“AMS”,“NCE”,“GVA”,“ATH”,“VCE”,“DBV”,“SPU”,“HER”,“RHO”,“CFU”,“SKG”,“ZTH”,
“LHR”,“LGW”,“BER”,“IST”,“CPH”,“LYS”,“NTE”,“CDG”,“ORY”,
“AAE”,“BJA”,“CZL”,“OTP”,
])),
“CDG”: RYANAIR_ROUTES[“CDG”],
“ORY”: list(set(RYANAIR_ROUTES[“ORY”] + [
“AMS”,“NCE”,“GVA”,“ATH”,“VCE”,“DBV”,“HER”,“RHO”,“IST”,“CPH”,“BER”,
“AAE”,“BJA”,“CZL”,“ORN”,“TUN”,“DJE”,“MIR”,
])),
}

# ── JOURS ──────────────────────────────────────────────────────────────────

DAY_MAP = {
“monday”:0,“tuesday”:1,“wednesday”:2,“thursday”:3,
“friday”:4,“saturday”:5,“sunday”:6,
“lundi”:0,“mardi”:1,“mercredi”:2,“jeudi”:3,
“vendredi”:4,“samedi”:5,“dimanche”:6,
}

def parse_days(raw):
if not raw or raw.strip().lower() in (“any”,“all”,””):
return set(range(7))
result = set()
for p in raw.replace(”;”,”,”).split(”,”):
k = p.strip().lower()
if k in DAY_MAP:
result.add(DAY_MAP[k])
return result or set(range(7))

ALLOWED_DAYS = parse_days(DAYS_RAW)

# ── ORIGINES ───────────────────────────────────────────────────────────────

def parse_origins(raw):
origins = [o.strip().upper() for o in raw.replace(”;”,”,”).split(”,”) if o.strip()]
return [o for o in origins if o in ALL_DIRECT_ROUTES] or [“TLS”]

ORIGINS = parse_origins(ORIGIN_RAW)

# ── DESTINATIONS ───────────────────────────────────────────────────────────

def get_destinations(origin, custom_raw=””):
if custom_raw and custom_raw.strip():
return [d.strip().upper() for d in custom_raw.replace(”;”,”,”).split(”,”) if d.strip()]
return ALL_DIRECT_ROUTES.get(origin, ALL_DIRECT_ROUTES[“TLS”])

# ── DATES ──────────────────────────────────────────────────────────────────

def build_date_range(nb_origins=1):
today = datetime.utcnow().date()
start = datetime.strptime(DATE_FROM,”%Y-%m-%d”).date() if DATE_FROM else today + timedelta(days=7)
end   = datetime.strptime(DATE_TO,  “%Y-%m-%d”).date() if DATE_TO   else today + timedelta(days=90)

```
all_dates = []
cur = start
while cur <= end:
    if cur.weekday() in ALLOWED_DAYS:
        all_dates.append(cur)
    cur += timedelta(days=1)

# Réduire la fenêtre si plusieurs origines
if nb_origins > 1:
    max_dates = max(3, len(all_dates) // nb_origins)
    all_dates = all_dates[:max_dates]

return all_dates
```

# ── CACHE ──────────────────────────────────────────────────────────────────

def load_seen():
try:
with open(SEEN_FILE) as f:
return set(json.load(f))
except Exception:
return set()

def save_seen(seen):
with open(SEEN_FILE,“w”) as f:
json.dump(list(seen), f)

def deal_id(orig, dest, dep, ret, price):
return hashlib.md5(f”{orig}-{dest}-{dep}-{ret}-{price:.0f}”.encode()).hexdigest()

# ══════════════════════════════════════════════════════════════════════════

# SOURCE 1 — FLYSCRAPER

# ══════════════════════════════════════════════════════════════════════════

_flyscraper_errors = 0
_flyscraper_ok     = 0

def flyscraper_search(origin, dest, dep_str, ret_str):
global _flyscraper_errors, _flyscraper_ok
if not RAPIDAPI_KEY:
return [], “no_key”

```
url = "https://flyScraper.p.rapidapi.com/flights/search"
headers = {
    "x-rapidapi-host": "flyScraper.p.rapidapi.com",
    "x-rapidapi-key":  RAPIDAPI_KEY,
}
params = {
    "origin": origin, "destination": dest,
    "departureDate": dep_str, "returnDate": ret_str,
    "adults": "1", "currency": "EUR", "cabinClass": "economy",
}

for attempt in range(2):  # 1 retry
    try:
        r = requests.get(url, headers=headers, params=params, timeout=20)
        if r.status_code == 429:
            log.warning("FlyScraper: quota dépassé")
            _flyscraper_errors += 1
            return [], "quota"
        if not r.ok:
            log.debug(f"FlyScraper {r.status_code} pour {origin}→{dest}")
            if attempt == 0:
                time.sleep(1)
                continue
            _flyscraper_errors += 1
            return [], "error"

        data = r.json()
        itineraries = (data.get("data",{}).get("itineraries") or
                       data.get("itineraries") or [])

        results = []
        for itin in itineraries[:10]:
            try:
                price_obj = itin.get("price",{})
                raw = price_obj.get("raw") or price_obj.get("amount","0")
                unit = price_obj.get("unit","")
                price = float(str(raw).replace(",","."))
                if "MILLI" in unit:
                    price /= 1000

                legs = itin.get("legs",[])
                if not legs: continue
                stops = legs[0].get("stopCount", len(legs[0].get("segments",[])) - 1)
                if stops > MAX_STOPS: continue

                airline = "Diverses"
                marketing = legs[0].get("carriers",{}).get("marketing",[])
                if marketing:
                    airline = marketing[0].get("name","Diverses")

                results.append({
                    "price": round(price,2), "stops": stops,
                    "airline": airline, "source": "FlyScraper",
                })
            except (KeyError,ValueError,TypeError):
                continue

        _flyscraper_ok += 1
        return results, "ok"

    except requests.exceptions.Timeout:
        log.debug(f"FlyScraper timeout {origin}→{dest} (attempt {attempt+1})")
        if attempt == 0: time.sleep(2)
    except Exception as e:
        log.debug(f"FlyScraper exception {origin}→{dest}: {e}")
        _flyscraper_errors += 1
        return [], "error"

_flyscraper_errors += 1
return [], "error"
```

# ══════════════════════════════════════════════════════════════════════════

# SOURCE 2 — RYANAIR

# ══════════════════════════════════════════════════════════════════════════

def ryanair_search(origin, dest, dep_str, ret_str):
ryanair_dests = RYANAIR_ROUTES.get(origin, [])
if dest not in ryanair_dests:
return []

```
url = "https://www.ryanair.com/api/farfnd/v4/roundTripFares"
params = {
    "departureAirportIataCode": origin,
    "arrivalAirportIataCode":   dest,
    "outboundDepartureDateFrom": dep_str,
    "outboundDepartureDateTo":   dep_str,
    "inboundDepartureDateFrom":  ret_str,
    "inboundDepartureDateTo":    ret_str,
    "currency": "EUR",
    "priceValueTo": MAX_PRICE,
}
try:
    r = requests.get(url, params=params,
        headers={"User-Agent":"Mozilla/5.0","Accept":"application/json"},
        timeout=15)
    if not r.ok: return []
    results = []
    for fare in r.json().get("fares",[]):
        try:
            total = fare["outbound"]["price"]["value"] + fare["inbound"]["price"]["value"]
            results.append({"price":round(total,2),"stops":0,"airline":"Ryanair","source":"Ryanair"})
        except (KeyError,TypeError):
            continue
    return results
except Exception as e:
    log.debug(f"Ryanair {origin}→{dest}: {e}")
    return []
```

# ══════════════════════════════════════════════════════════════════════════

# SOURCE 3 — SERPAPI

# ══════════════════════════════════════════════════════════════════════════

def serpapi_search(origin, dest, dep_str, ret_str):
if not SERPAPI_KEY: return []
params = {
“engine”:“google_flights”,
“departure_id”:origin,“arrival_id”:dest,
“outbound_date”:dep_str,“return_date”:ret_str,
“currency”:“EUR”,“hl”:“fr”,“type”:“1”,“api_key”:SERPAPI_KEY,
}
if MAX_STOPS == 0: params[“stops”] = “1”
try:
r = requests.get(“https://serpapi.com/search”, params=params, timeout=25)
if not r.ok: return []
data = r.json()
results = []
for flight in data.get(“best_flights”,[]) + data.get(“other_flights”,[]):
try:
price = float(flight.get(“price”,9999))
legs  = flight.get(“flights”,[])
stops = len(legs) - 1
if stops > MAX_STOPS: continue
results.append({
“price”:price,“stops”:stops,
“airline”:legs[0].get(“airline”,”?”) if legs else “?”,
“source”:“SerpAPI”,
})
except (KeyError,ValueError): continue
return results
except Exception as e:
log.debug(f”SerpAPI {origin}→{dest}: {e}”)
return []

# ══════════════════════════════════════════════════════════════════════════

# MOTEUR COMBINÉ

# ══════════════════════════════════════════════════════════════════════════

def search_all(origin, dest, dep_str, ret_str):
results = []
log_parts = []
flyscraper_status = “skipped”

```
# 1. FlyScraper (prioritaire — toutes compagnies)
if RAPIDAPI_KEY:
    fs_results, flyscraper_status = flyscraper_search(origin, dest, dep_str, ret_str)
    if fs_results:
        results += fs_results
        log_parts.append(f"FS:{len(fs_results)}")

# 2. Ryanair (complément ou fallback)
ry = ryanair_search(origin, dest, dep_str, ret_str)
if ry:
    # Dédoublonnage : éviter les doublons Ryanair déjà dans FlyScraper
    existing = {round(x["price"]) for x in results if x["airline"] == "Ryanair"}
    new_ry = [o for o in ry if round(o["price"]) not in existing]
    if new_ry:
        results += new_ry
        log_parts.append(f"RY:{len(new_ry)}")

# 3. SerpAPI si pas de FlyScraper
if SERPAPI_KEY and not RAPIDAPI_KEY:
    sp = serpapi_search(origin, dest, dep_str, ret_str)
    if sp:
        results += sp
        log_parts.append(f"SP:{len(sp)}")

if results:
    log.info(f"  {origin}→{dest} {dep_str}: {len(results)} offre(s) [{', '.join(log_parts)}]")

return results, flyscraper_status
```

# ══════════════════════════════════════════════════════════════════════════

# TELEGRAM

# ══════════════════════════════════════════════════════════════════════════

def send_telegram(message):
if not TELEGRAM_TOKEN or not TELEGRAM_CHAT_ID:
log.warning(“Telegram non configuré”)
return
try:
r = requests.post(
f”https://api.telegram.org/bot{TELEGRAM_TOKEN}/sendMessage”,
json={“chat_id”:TELEGRAM_CHAT_ID,“text”:message,“parse_mode”:“HTML”},
timeout=15,
)
r.raise_for_status()
log.info(“Telegram ✓”)
except Exception as e:
log.error(f”Telegram: {e}”)

def skyscanner_link(origin, dest, dep_str, ret_str):
dep = dep_str.replace(”-”,””)
ret = ret_str.replace(”-”,””)
return f”https://www.skyscanner.fr/transport/vols/{origin.lower()}/{dest.lower()}/{dep}/{ret}/”

def fmt_deal(origin, dest, dep, ret, o):
stops = “Direct ✅” if o[“stops”] == 0 else f”{o[‘stops’]} escale(s)”
orig_label = airport_label(origin)
dest_label = airport_label(dest)
link = skyscanner_link(origin, dest, dep, ret)
return (
f”✈️ <b>{orig_label} → {dest_label}</b>\n”
f”📅 {dep} → {ret}\n”
f”💶 <b>{o[‘price’]:.0f}€</b> A/R · {stops}\n”
f”🏢 {o[‘airline’]} · via {o[‘source’]}\n”
f”🔗 <a href='{link}'>Voir sur Skyscanner</a>”
)

# ══════════════════════════════════════════════════════════════════════════

# MAIN

# ══════════════════════════════════════════════════════════════════════════

def main():
print(”=”*56)
print(f”Agent vols — {datetime.utcnow().strftime(’%d/%m/%Y %H:%M’)} UTC”)
print(f”Origines: {’, ’.join(ORIGINS)} | Seuil: {MAX_PRICE}€”)
print(f”Séjour: {MIN_NIGHTS}–{MAX_NIGHTS}j | Escales max: {MAX_STOPS}”)
print(”=”*56)

```
# ON/OFF
if AGENT_ENABLED == "false":
    print("Agent désactivé. Arrêt.")
    return

# Vérification fréquence (runs auto seulement)
if not IS_MANUAL:
    current_hour = datetime.utcnow().hour
    fr_hour = (current_hour + 2) % 24
    allowed_hours = {
        "hourly":  list(range(24)),
        "every2h": [0,2,4,6,8,10,12,14,16,18,20,22],
        "every3h": [0,3,6,9,12,15,18,21],
        "4xday":   [8,14,20,0],
        "3xday":   [8,14,21],
    }
    hours = allowed_hours.get(CRON_MODE, allowed_hours["4xday"])
    if fr_hour not in hours:
        print(f"Heure FR ({fr_hour}h) hors planning {CRON_MODE}. Arrêt.")
        return
    print(f"Fréquence: {CRON_MODE} — {fr_hour}h FR ✓")

if not TELEGRAM_TOKEN or not TELEGRAM_CHAT_ID:
    raise ValueError("TELEGRAM_TOKEN et TELEGRAM_CHAT_ID requis")

seen     = load_seen()
new_seen = set()
all_deals = []  # (origin, dest, dep, ret, offer)
flyscraper_had_errors = False
rapidapi_calls = 0

dates = build_date_range(nb_origins=len(ORIGINS))
log.info(f"{len(dates)} dates · {len(ORIGINS)} origine(s)")

for origin in ORIGINS:
    destinations = get_destinations(origin, DEST_RAW)
    log.info(f"  {origin}: {len(destinations)} destinations")

    for dep_date in dates:
        for dest in destinations:
            for nights in range(MIN_NIGHTS, MAX_NIGHTS + 1):
                ret_date = dep_date + timedelta(days=nights)
                dep_str  = dep_date.strftime("%Y-%m-%d")
                ret_str  = ret_date.strftime("%Y-%m-%d")

                offers, fs_status = search_all(origin, dest, dep_str, ret_str)
                if RAPIDAPI_KEY: rapidapi_calls += 1
                if fs_status in ("error","quota"): flyscraper_had_errors = True

                # Garder uniquement la meilleure offre par destination
                best = min((o for o in offers if o["price"] <= MAX_PRICE),
                           key=lambda x: x["price"], default=None)
                if best:
                    did = deal_id(origin, dest, dep_str, ret_str, best["price"])
                    new_seen.add(did)
                    if did not in seen:
                        all_deals.append((origin, dest, dep_str, ret_str, best))

                time.sleep(random.uniform(0.3, 0.7))

# Trier par prix croissant
all_deals.sort(key=lambda x: x[4]["price"])

log.info(f"{len(all_deals)} nouvelle(s) offre(s) sous {MAX_PRICE}€")
if RAPIDAPI_KEY:
    log.info(f"Quota RapidAPI utilisé ce run: ~{rapidapi_calls} requêtes")

if all_deals:
    origins_str = " · ".join(airport_label(o) for o in ORIGINS)
    header = (
        f"🔔 <b>{len(all_deals)} bon(s) plan(s) !</b>\n"
        f"🛫 Depuis: {origins_str}\n"
        f"💶 Seuil: {MAX_PRICE}€"
    )
    if flyscraper_had_errors:
        header += "\n⚠️ <i>FlyScraper partiellement indisponible — résultats Ryanair uniquement pour certaines routes</i>"

    blocs = [header]
    for origin, dest, dep, ret, offer in all_deals[:15]:
        blocs.append(fmt_deal(origin, dest, dep, ret, offer))

    send_telegram("\n\n".join(blocs))

elif not IS_MANUAL:
    # Notif quotidienne même si rien trouvé (une fois par jour à 8h)
    fr_hour = (datetime.utcnow().hour + 2) % 24
    if fr_hour == 8:
        origins_str = " · ".join(airport_label(o) for o in ORIGINS)
        send_telegram(
            f"📊 <b>Résumé quotidien</b>\n"
            f"🛫 Depuis: {origins_str}\n"
            f"💶 Seuil: {MAX_PRICE}€\n"
            f"📭 Aucune offre trouvée aujourd'hui sous ce seuil."
        )
    else:
        log.info("Aucune offre — pas de notif Telegram")

save_seen(seen | new_seen)
print("="*56)
print("Terminé.")
```

if **name** == “**main**”:
main()