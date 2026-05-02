import xml.etree.ElementTree as ET

import requests

URL = "https://www.hamqsl.com/solarxml.php"


def _format(bands, emoji):
    parts = " ".join(f"{name}:{cond}" for name, cond in bands)
    return f"HF {emoji} {parts}"


class HfService:
    def get_conditions(self):
        response = requests.get(URL, timeout=10)
        response.raise_for_status()
        root = ET.fromstring(response.text)

        bands = root.findall(".//calculatedconditions/band")
        day = [(b.get("name"), b.text) for b in bands if b.get("time") == "day"]
        night = [(b.get("name"), b.text) for b in bands if b.get("time") == "night"]

        return _format(day, "☀️"), _format(night, "🌙")
