import os

import requests


class WeatherService:
    def __init__(self, config):
        self.config = config

    def get_conditions(self):
        conditions_config = self.config.get("weather").get("conditions")
        temp_entity_id = conditions_config.get("temp_entity_id")
        humidity_entity_id = conditions_config.get("humidity_entity_id")
        location_description = conditions_config.get("location_description")
        ha_url = conditions_config.get("url")

        temp_data = self._get_ha_sensor_state(ha_url, temp_entity_id)
        temp = round(float(temp_data["state"]))
        humidity_data = self._get_ha_sensor_state(ha_url, humidity_entity_id)
        humidity = round(float(humidity_data["state"]))

        return (
            f"Currently in {location_description}, {temp}{temp_data['unit']}. "
            f"Humidity {humidity}{humidity_data['unit']}."
        )

    def get_forecast(self):
        forecast_config = self.config.get("weather").get("forecast")
        url = forecast_config.get("url")
        user_agent = forecast_config.get("user_agent")

        headers = {
            "Content-Type": "application/json",
            "User-Agent": user_agent,
        }

        response = requests.get(url, headers=headers, timeout=10)
        response.raise_for_status()
        data = response.json()

        periods = data.get("properties", {}).get("periods", [])
        if not periods:
            return "Forecast unavailable."

        period = periods[0]
        name = period.get("name").lower()
        detailed_forecast = period.get("detailedForecast")
        return f"NWS forecast for {name}: {detailed_forecast}"

    def _get_ha_sensor_state(self, ha_base, entity_id):
        ha_token = os.getenv("HA_TOKEN")

        headers = {
            "Authorization": f"Bearer {ha_token}",
            "Content-Type": "application/json",
        }
        url = f"{ha_base}/api/states/{entity_id}"

        response = requests.get(url, headers=headers, timeout=10)
        response.raise_for_status()
        data = response.json()
        return {
            "state": data.get("state"),
            "unit": data["attributes"].get("unit_of_measurement"),
        }
