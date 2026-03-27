"""
Agent d'alertes vols depuis Toulouse (TLS)
Utilise SerpAPI (Google Flights) + Telegram pour les notifications
"""

import os
import json
import requests
from datetime import datetime, timedelta
from itertools import product

# ─── CONFIG ─────────────────────────────────────────────────────────────────

SERPAPI_KEY = os.environ.get("SERPAPI_KEY")
TELEGRAM_TOKEN = os.environ.get("TELEGRAM_TOKEN")
TELEGRAM_CHAT_ID = os.environ.get("TELEGRAM_CHAT_ID")

ORIGIN = "TLS"  # Aéroport de Toulouse-Blagnac

# ─── FILTRES — modifie ces valeurs selon tes envies ─────────────────────────

MAX_PRICE_EUR = 80          # Prix max aller-retour en €
MIN_DAYS_AHEAD = 7          # Ne pas chercher dans moins de X jours
MAX_DAYS_AHEAD = 90         # Horizon de recherche max (3 mois)
WEEKEND_ONLY = True         # True = départ vendredi ou samedi uniquement
MIN_TRIP_DAYS = 2           # Durée min du séjour (nuits)
MAX_TRIP_DAYS = 5           # Durée max du séjour (nuits)
MAX_STOPS = 1               # 0 = direct uniquement, 1 = max 1 escale
MIN_FLIGHT_DURATION_H = 1   # Ignorer les vols trop courts (erreurs de données)

# Destinations à cibler (laisser vide [] pour "n'importe où")
# Exemples : ["BCN", "LIS", "AMS", "CDG", "FCO", "MAD", "VIE", "PRG"]
TARGET_DESTINATIONS = []

# Destinations à exclure
EXCLUDED_DESTINATIONS = ["TLS"]

# ─── UTILITAIRES ─────────────────────────────────────────────────────────────

def get_weekends(min_days_ahead: int, max_days_ahead: int) -> list[tuple[str, str]]:
    """Retourne les paires (vendredi, lundi) dans la fenêtre de recherche."""
    today = datetime.today()
    pairs = []
    current = today + timedelta(days=min_days_ahead)
    end = today + timedelta(days=max_days_ahead)

    while current <= end:
        # Chercher le prochain vendredi (weekday 4)
        days_until_friday = (4 - current.weekday()) % 7
        friday = current + timedelta(days=days_until_friday)
        if friday > end:
            break

        for trip_len in range(MIN_TRIP_DAYS, MAX_TRIP_DAYS + 1):
            return_date = friday + timedelta(days=trip_len)
            if return_date <= end:
                pairs.append((
                    friday.strftime("%Y-%m-%d"),
                    return_date.strftime("%Y-%m-%d")
                ))

        current = friday + timedelta(days=7)

    return pairs

def get_date_pairs(min_days_ahead: int, max_days_ahead: int) -> list[tuple[str, str]]:
    """Mode flexible : retourne des paires de dates espacées de MIN à MAX jours."""
    today = datetime.today()
    pairs = []

    for outbound_offset in range(min_days_ahead, max_days_ahead, 3):
        outbound = today + timedelta(days=outbound_offset)
        for trip_len in range(MIN_TRIP_DAYS, MAX_TRIP_DAYS + 1):
            inbound = outbound + timedelta(days=trip_len)
            if (inbound - today).days <= max_days_ahead:
                pairs.append((
                    outbound.strftime("%Y-%m-%d"),
                    inbound.strftime("%Y-%m-%d")
                ))

    return pairs

# ─── RECHERCHE DE VOLS ───────────────────────────────────────────────────────

def search_flights(outbound_date: str, inbound_date: str, destination: str = "") -> list[dict]:
    """
    Interroge SerpAPI (Google Flights) pour un aller-retour TLS → destination.
    Si destination est vide, recherche vers les hubs principaux d'Europe.
    """
    # SerpAPI endpoint Google Flights
    url = "https://serpapi.com/search"

    dest = destination if destination else "anyplace"

    params = {
        "engine": "google_flights",
        "departure_id": ORIGIN,
        "arrival_id": dest,
        "outbound_date": outbound_date,
        "return_date": inbound_date,
        "currency": "EUR",
        "hl": "fr",
        "type": "1",          # 1 = aller-retour
        "api_key": SERPAPI_KEY,
    }

    if MAX_STOPS == 0:
        params["stops"] = "1"  # Filtre "direct" dans SerpAPI = "1"

    try:
        response = requests.get(url, params=params, timeout=30)
        response.raise_for_status()
        data = response.json()
    except requests.RequestException as e:
        print(f"  Erreur API : {e}")
        return []

    results = []

    # Parcourir les résultats (best_flights + other_flights)
    for category in ["best_flights", "other_flights"]:
        for flight in data.get(category, []):
            price = flight.get("price")
            if not price or price > MAX_PRICE_EUR:
                continue

            flights_legs = flight.get("flights", [])
            if not flights_legs:
                continue

            # Extraire infos du premier leg (aller)
            first_leg = flights_legs[0]
            airline = first_leg.get("airline", "?")
            dep_airport = first_leg.get("departure_airport", {})
            arr_airport = flights_legs[-1].get("arrival_airport", {})

            dest_code = arr_airport.get("id", "?")
            dest_name = arr_airport.get("name", "?")

            if dest_code in EXCLUDED_DESTINATIONS:
                continue
            if TARGET_DESTINATIONS and dest_code not in TARGET_DESTINATIONS:
                continue

            total_duration = flight.get("total_duration", 0)
            if total_duration < MIN_FLIGHT_DURATION_H * 60:
                continue

            stops = len(flights_legs) - 1
            if stops > MAX_STOPS:
                continue

            results.append({
                "price": price,
                "airline": airline,
                "destination_code": dest_code,
                "destination_name": dest_name,
                "outbound_date": outbound_date,
                "inbound_date": inbound_date,
                "duration_min": total_duration,
                "stops": stops,
                "booking_token": flight.get("booking_token", ""),
            })

    return results

# ─── NOTIFICATIONS TELEGRAM ──────────────────────────────────────────────────

def format_message(deals: list[dict]) -> str:
    """Formate le message Telegram avec les meilleures offres."""
    if not deals:
        return ""

    lines = [
        "✈️ *Alertes vols depuis Toulouse !*",
        f"_{len(deals)} offre(s) trouvée(s) sous {MAX_PRICE_EUR}€_\n",
    ]

    for i, deal in enumerate(deals[:10], 1):  # Max 10 offres par message
        stops_label = "Direct" if deal["stops"] == 0 else f"{deal['stops']} escale(s)"
        duration_h = deal["duration_min"] // 60
        duration_m = deal["duration_min"] % 60
        duration_str = f"{duration_h}h{duration_m:02d}"

        outbound = datetime.strptime(deal["outbound_date"], "%Y-%m-%d")
        inbound = datetime.strptime(deal["inbound_date"], "%Y-%m-%d")
        nights = (inbound - outbound).days

        lines.append(
            f"*{i}. {deal['destination_name']} ({deal['destination_code']})*\n"
            f"   💶 *{deal['price']}€* A/R · {deal['airline']}\n"
            f"   📅 {outbound.strftime('%d/%m')} → {inbound.strftime('%d/%m')} "
            f"({nights} nuits)\n"
            f"   🕐 {duration_str} · {stops_label}\n"
        )

    lines.append(
        "🔍 [Voir sur Google Flights]"
        f"(https://www.google.com/flights?hl=fr#flt=TLS..{datetime.today().strftime('%Y-%m-%d')})"
    )

    return "\n".join(lines)

def send_telegram(message: str) -> bool:
    """Envoie un message via le bot Telegram."""
    url = f"https://api.telegram.org/bot{TELEGRAM_TOKEN}/sendMessage"
    payload = {
        "chat_id": TELEGRAM_CHAT_ID,
        "text": message,
        "parse_mode": "Markdown",
        "disable_web_page_preview": True,
    }
    try:
        response = requests.post(url, json=payload, timeout=15)
        response.raise_for_status()
        print("  Notification Telegram envoyée !")
        return True
    except requests.RequestException as e:
        print(f"  Erreur Telegram : {e}")
        return False

# ─── LOGIQUE PRINCIPALE ──────────────────────────────────────────────────────

def load_seen_deals(path="seen_deals.json") -> set:
    """Charge les offres déjà notifiées pour éviter les doublons."""
    try:
        with open(path) as f:
            return set(json.load(f))
    except (FileNotFoundError, json.JSONDecodeError):
        return set()

def save_seen_deals(deals: set, path="seen_deals.json"):
    with open(path, "w") as f:
        json.dump(list(deals), f)

def deal_id(deal: dict) -> str:
    """Identifiant unique d'une offre (pour déduplication)."""
    return f"{deal['destination_code']}_{deal['outbound_date']}_{deal['inbound_date']}_{deal['price']}"

def main():
    print(f"\n{'='*50}")
    print(f"Agent vols Toulouse — {datetime.now().strftime('%d/%m/%Y %H:%M')}")
    print(f"Seuil : {MAX_PRICE_EUR}€ | Fenêtre : J+{MIN_DAYS_AHEAD} à J+{MAX_DAYS_AHEAD}")
    print(f"{'='*50}\n")

    if not SERPAPI_KEY:
        raise ValueError("Variable SERPAPI_KEY manquante !")
    if not TELEGRAM_TOKEN or not TELEGRAM_CHAT_ID:
        raise ValueError("Variables TELEGRAM_TOKEN ou TELEGRAM_CHAT_ID manquantes !")

    # Générer les paires de dates
    if WEEKEND_ONLY:
        date_pairs = get_weekends(MIN_DAYS_AHEAD, MAX_DAYS_AHEAD)
        print(f"Mode week-end : {len(date_pairs)} combinaisons à tester")
    else:
        date_pairs = get_date_pairs(MIN_DAYS_AHEAD, MAX_DAYS_AHEAD)
        print(f"Mode flexible : {len(date_pairs)} combinaisons à tester")

    # Charger les offres déjà vues
    seen = load_seen_deals()
    all_deals = []

    # Destinations à scanner (si vide → grands hubs européens)
    destinations = TARGET_DESTINATIONS if TARGET_DESTINATIONS else [
        "BCN", "MAD", "LIS", "OPO", "AMS", "BRU", "CDG", "FCO", "CIA",
        "VCE", "NAP", "ATH", "VIE", "PRG", "BUD", "WAW", "DUB", "EDI",
        "LGW", "STN", "CPH", "ARN", "OSL", "HEL", "MXP", "LIN", "BLQ",
        "PMI", "IBZ", "AGP", "SVQ", "SCQ", "BIO", "VLC", "RAK", "CMN",
    ]

    for dest in destinations:
        for outbound, inbound in date_pairs[:8]:  # Limiter les requêtes API
            print(f"  Recherche TLS → {dest} | {outbound} → {inbound}...")
            deals = search_flights(outbound, inbound, dest)

            for deal in deals:
                did = deal_id(deal)
                if did not in seen:
                    all_deals.append(deal)
                    seen.add(did)

    # Trier par prix
    all_deals.sort(key=lambda x: x["price"])

    print(f"\n→ {len(all_deals)} nouvelles offres trouvées")

    if all_deals:
        message = format_message(all_deals)
        send_telegram(message)
        save_seen_deals(seen)
    else:
        print("  Aucune nouvelle offre sous le seuil.")
        # Optionnel : envoyer un message de statut
        # send_telegram("✅ Agent actif — aucune nouvelle offre pour le moment.")

    print("\nTerminé.\n")

if __name__ == "__main__":
    main()
