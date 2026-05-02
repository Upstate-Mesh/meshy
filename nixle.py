import requests
from bs4 import BeautifulSoup


class NixleService:
    def __init__(self, config):
        self.url = config.get("nixle", {}).get("url", "")

    def get_alerts(self):
        response = requests.get(self.url, timeout=10)
        response.raise_for_status()

        soup = BeautifulSoup(response.text, "html.parser")
        wire = soup.find("ol", id="wire")
        items = wire.find_all("li") if wire else []

        alerts = []
        for item in items:
            more_link = item.find("a", href=lambda h: h and "nixle.us/" in h)
            if not more_link:
                continue

            alert_id = more_link["href"].rstrip("/").split("/")[-1]

            body_p = more_link.find_parent("p")
            text = (
                "".join(body_p.find_all(string=True, recursive=False)).strip()
                if body_p
                else ""
            )

            if alert_id and text:
                alerts.append({"id": alert_id, "text": text})

        return alerts

    @staticmethod
    def format_alert(alert):
        return f"{alert['text']} https://nixle.us/{alert['id']}"
