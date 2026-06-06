import requests


class Ny511Service:
    def __init__(self, config):
        ny511_cfg = config.get("ny511", {})
        self.api_key = ny511_cfg.get("api_key", "")
        self.area = ny511_cfg.get("area", "").lower()
        self.prefix = ny511_cfg.get("prefix", "")

    def get_alerts(self):
        if not self.api_key:
            return []

        url = f"https://511ny.org/api/getalerts?key={self.api_key}&format=json"
        response = requests.get(url, timeout=30)
        response.raise_for_status()

        alerts = []
        for item in response.json():
            if self.area:
                area_names = [a.lower() for a in item.get("AreaNames", [])]
                if self.area not in area_names:
                    continue

            alert_id = item.get("Id", "")
            message = item.get("Message", "").strip()

            if alert_id and message:
                alerts.append({"id": alert_id, "message": message})

        return alerts

    def format_alert(self, alert):
        if self.prefix:
            return f"{self.prefix} {alert['message']}"
        return alert["message"]
