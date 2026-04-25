import requests


class AdsbService:
    def __init__(self, config):
        self.config = config

    def get_aircraft(self):
        adsb_config = self.config.get("adsb", {})
        url = adsb_config.get("url", "http://localhost:8080")
        max_age = adsb_config.get("max_age_seconds", 60)
        max_count = adsb_config.get("max_count", 5)

        response = requests.get(f"{url}/data/aircraft.json", timeout=5)
        response.raise_for_status()
        aircraft = response.json().get("aircraft", [])

        recent = sorted(
            [a for a in aircraft if a.get("seen", 999) <= max_age],
            key=lambda a: a.get("seen", 999),
        )[:max_count]

        if not recent:
            return "No aircraft currently seen."

        labels = []
        for a in recent:
            callsign = f"[{a.get('flight', '').strip() or a.get('hex', '???').upper()}]"
            alt = a.get("alt_baro")

            parts = [callsign]
            if alt is not None and alt != "ground":
                alt = int(alt)
                parts.append(f"@ FL{alt // 100}" if alt >= 18000 else f"@ {alt}ft")

            labels.append(" ".join(parts))

        return f"✈️ spotted:\n{',\n'.join(labels)}"
