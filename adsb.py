import math

import requests
from geopy.distance import geodesic

CATEGORY_LABELS = {
    "A5": "Heavy",
    "A6": "High Perf",
    "A7": "🚁",
    "B1": "🛩️",
    "B2": "🎈",
    "B4": "🛩️",
    "B6": "🤖",
    "C1": "🚛",
}

_CARDINALS = ["N", "NE", "E", "SE", "S", "SW", "W", "NW"]


def _cardinal(degrees):
    return _CARDINALS[round(degrees / 45) % 8]


def _climb_indicator(baro_rate):
    if baro_rate is None:
        return ""
    if baro_rate > 100:
        return f"⬆️{abs(baro_rate)}fpm"
    if baro_rate < -100:
        return f"⬇️{abs(baro_rate)}fpm"
    return ""


def _distance_bearing(home_lat, home_lon, lat, lon):
    home, point = (home_lat, home_lon), (lat, lon)
    distance = round(geodesic(home, point).miles, 1)
    lat1, lon1 = math.radians(home_lat), math.radians(home_lon)
    lat2, dlon = math.radians(lat), math.radians(lon - home_lon)
    y = math.sin(dlon) * math.cos(lat2)
    x = math.cos(lat1) * math.sin(lat2) - math.sin(lat1) * math.cos(lat2) * math.cos(
        dlon
    )
    bearing = (math.degrees(math.atan2(y, x)) + 360) % 360
    return distance, _cardinal(bearing)


def _format_aircraft(a, home_lat=None, home_lon=None):
    callsign = f"[{a.get('flight', '').strip() or a.get('hex', '???').upper()}]"
    parts = [callsign]

    category = CATEGORY_LABELS.get(a.get("category", ""), "")
    if category:
        parts.append(category)

    alt = a.get("alt_baro")
    if alt == "ground":
        parts.append("🛬")
    elif alt is not None:
        alt = int(alt)
        parts.append(f"FL{alt // 100}" if alt >= 18000 else f"{alt}ft")

    lat, lon = a.get("lat"), a.get("lon")
    if (
        lat is not None
        and lon is not None
        and home_lat is not None
        and home_lon is not None
    ):
        distance, bearing = _distance_bearing(home_lat, home_lon, lat, lon)
        parts.append(f"📍{distance}mi {bearing}")

    track = a.get("track")
    if track is not None:
        parts.append(f"→{_cardinal(track)}")

    climb = _climb_indicator(a.get("baro_rate"))
    if climb:
        parts.append(climb)

    return " ".join(parts)


class AdsbService:
    def __init__(self, config):
        self.config = config

    def get_aircraft(self):
        adsb_config = self.config.get("adsb", {})
        url = adsb_config.get("url", "http://localhost:8080")
        max_age = adsb_config.get("max_age_seconds", 60)
        max_count = adsb_config.get("max_count", 5)
        home_lat = adsb_config.get("home_lat")
        home_lon = adsb_config.get("home_lon")

        response = requests.get(f"{url}/data/aircraft.json", timeout=5)
        response.raise_for_status()
        aircraft = response.json().get("aircraft", [])

        recent = sorted(
            [a for a in aircraft if a.get("seen", 999) <= max_age],
            key=lambda a: a.get("seen", 999),
        )[:max_count]

        if not recent:
            return "No aircraft currently seen."

        lines = [_format_aircraft(a, home_lat, home_lon) for a in recent]

        return f"✈️\r\n{chr(10).join(f'{line},' for line in lines)}"
