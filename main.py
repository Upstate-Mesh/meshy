import asyncio
import math
import os

import metpy.calc as mpcalc
import numpy
import requests
import yaml
from dotenv import load_dotenv
from loguru import logger
from meshcore import EventType, MeshCore
from metpy.units import units

from db import NodeDB
from scheduled_worker import ScheduledWorker

CONFIG_FILE = "config.yml"
MAX_RETRIES = 10
RETRY_BASE_DELAY = 5


class Meshy:
    def __init__(self):
        logger.add("meshy.log", rotation="50 MB")
        load_dotenv()
        self.config = self.load_config()
        self.worker_jobs = []
        self.mc = None
        self.channel_map = {}

        if self.config["save_node_db"]:
            self.db = NodeDB()
        else:
            self.db = None

    async def start(self):
        self.mc = await self.connect_and_run()
        await self.build_channel_map()
        self.start_jobs()
        await self._send_connect_adverts()

        try:
            await asyncio.sleep(float("inf"))
        except (KeyboardInterrupt, asyncio.CancelledError):
            logger.info("Closing connection, shutting down.")
        finally:
            for worker_job in self.worker_jobs:
                worker_job.stop()
            await self.mc.disconnect()

    async def connect_and_run(self):
        attempt = 0
        while True:
            try:
                logger.info(
                    f"Connecting to node via '{self.config['serial_port']}' (attempt {attempt + 1})..."
                )
                mc = await MeshCore.create_serial(
                    self.config["serial_port"], baudrate=115200
                )
                logger.info("Connected to node.")

                mc.subscribe(EventType.CONTACT_MSG_RECV, self.on_receive)

                if self.db is not None:
                    mc.subscribe(EventType.NEW_CONTACT, self.observe_node)

                await mc.start_auto_message_fetching()

                return mc

            except Exception as e:
                delay = min(RETRY_BASE_DELAY * (2**attempt), 300)
                logger.exception(
                    f"Connection failed ({e}). Retrying in {delay} seconds..."
                )
                await asyncio.sleep(delay)
                attempt = min(attempt + 1, MAX_RETRIES)

    def load_config(self):
        with open(CONFIG_FILE, "r", encoding="utf-8") as f:
            return yaml.safe_load(f)

    async def on_receive(self, event):
        if self.config["bot"]["active"] is False:
            return

        try:
            pubkey_prefix = event.payload.get("pubkey_prefix", "")
            text = event.payload.get("text", "")

            if not text:
                return

            cmd = text.strip().lower()
            reply_text = await self.handle_command(cmd)

            if reply_text is None:
                logger.debug(
                    f"<- Unrecognized command from {pubkey_prefix} ({cmd}), ignoring."
                )
                return

            logger.info(f"<- from {pubkey_prefix}: {cmd}")
            logger.info(f"-> to {pubkey_prefix}: {reply_text}")

            contact = self.mc.get_contact_by_key_prefix(pubkey_prefix)
            if contact:
                await self.mc.commands.send_msg(contact, reply_text)
            else:
                logger.warning(
                    f"Could not find contact for {pubkey_prefix}, cannot reply."
                )
        except Exception as e:
            logger.error(f"Command error: {e}")

    async def observe_node(self, event):
        try:
            contact = event.payload
            public_key = contact.get("public_key", "")
            name = contact.get("name", "")
            node_id = public_key[:12] if public_key else ""

            if node_id and name:
                self.db.upsert_node(node_id, name)
        except Exception as e:
            logger.error(f"Error observing node: {e}")

    async def handle_command(self, cmd):
        commands = self.config.get("bot", {}).get("commands", {})
        action = commands.get(cmd)

        if action is None:
            return None

        if isinstance(action, str) and hasattr(self, action):
            return await asyncio.to_thread(getattr(self, action))

        return action

    async def _send_connect_adverts(self):
        for job in self.config.get("workers", []):
            if job.get("type") == "advert" and job.get("active", True):
                await self.get_advert_worker(job)

    async def get_advert_worker(self, job):
        result = await self.mc.commands.send_advert(flood=True)
        if result.type != EventType.ERROR:
            logger.info("-> Advert sent (flood)")
        else:
            logger.warning(f"Advert failed: {result.payload}")

    def _resolve_channel(self, job):
        channel_name = job.get("channel", "").lower()
        channel_index = self.channel_map.get(channel_name)
        if channel_index is None:
            logger.warning(f"Channel '{channel_name}' not found on device, skipping.")
        return channel_index

    async def get_beacon_worker(self, job):
        channel_index = self._resolve_channel(job)
        if channel_index is None:
            return
        await self.mc.commands.send_chan_msg(channel_index, job["text"])
        logger.info(
            f"-> Beacon: '{job['text']}' on channel '{job['channel']}' ({channel_index})"
        )

    async def get_weather_conditions_worker(self, job):
        channel_index = self._resolve_channel(job)
        if channel_index is None:
            return
        try:
            msg = await asyncio.to_thread(self.get_weather_conditions)
            await self.mc.commands.send_chan_msg(channel_index, msg)
            logger.info(
                f"-> Weather conditions: '{msg}' on channel '{job['channel']}' ({channel_index})"
            )
        except requests.exceptions.RequestException as e:
            logger.info(f"Weather conditions job request failed: {e}")

    async def get_weather_forecast_worker(self, job):
        channel_index = self._resolve_channel(job)
        if channel_index is None:
            return
        try:
            msg = await asyncio.to_thread(self.get_weather_forecast)
            await self.mc.commands.send_chan_msg(channel_index, msg)
            logger.info(
                f"-> Weather forecast: '{msg}' on channel '{job['channel']}' ({channel_index})"
            )
        except requests.exceptions.RequestException as e:
            logger.info(f"Weather forecast job request failed: {e}")

    def get_seen_nodes(self):
        if self.db is None:
            return "Command inactive."

        seen_nodes = self.db.get_seen_nodes()

        if len(seen_nodes) == 0:
            return "No nodes seen."

        # TODO make this useful
        n = seen_nodes[0]
        return f"Most recently seen node:\n{n['name']} ({n['id']})"

    def get_weather_forecast(self):
        weather_config = self.config.get("weather").get("forecast")
        url = weather_config.get("url")
        user_agent = weather_config.get("user_agent")

        headers = {
            "Content-Type": "application/json",
            "User-Agent": user_agent,
        }

        response = requests.get(url, headers=headers, timeout=10)
        response.raise_for_status()
        data = response.json()

        periods = data.get("properties", {}).get("periods", [])
        if len(periods) == 0:
            return "Forecast unavailable."

        period = periods[0]
        name = period.get("name").lower()
        detailed_forecast = period.get("detailedForecast")
        return f"NWS forecast for {name}: {detailed_forecast}"

    def get_weather_conditions(self):
        conditions_config = self.config.get("weather").get("conditions")
        temp_entity_id = conditions_config.get("temp_entity_id")
        humidity_entity_id = conditions_config.get("humidity_entity_id")
        location_description = conditions_config.get("location_description")
        ha_url = conditions_config.get("url")

        temp_data = self.get_ha_sensor_state(ha_url, temp_entity_id)
        temp = round(float(temp_data["state"]))
        humidity_data = self.get_ha_sensor_state(ha_url, humidity_entity_id)
        humidity = float(humidity_data["state"])
        heat_index = mpcalc.heat_index(temp * units.degF, humidity * units.percent)

        feels_like = temp
        magnitude = heat_index.m

        if not numpy.ma.is_masked(magnitude) and not math.isnan(float(magnitude)):
            feels_like = round(float(magnitude))

        return (
            f"Currently in {location_description}, {temp}{temp_data['unit']}. "
            f"Feels like {feels_like}{temp_data['unit']}. "
            f"Humidity {round(humidity)}{humidity_data['unit']}."
        )

    def get_ha_sensor_state(self, ha_base, entity_id):
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

    async def build_channel_map(self):
        self.channel_map = {}
        for i in range(8):
            result = await self.mc.commands.get_channel(i)
            if result.type == EventType.CHANNEL_INFO:
                name = result.payload.get("channel_name", "").strip().lower()
                if name:
                    self.channel_map[name] = i
                    stripped = name.lstrip("#")
                    if stripped != name:
                        self.channel_map[stripped] = i
        logger.info(f"Channel map: {self.channel_map}")

    def start_jobs(self):
        for job in self.config.get("workers", []):
            job_type = job.get("type")

            if not job.get("active", True):
                logger.info(f"Job inactive: {job_type}, skipping")
                continue

            worker = getattr(self, job.get("dispatch"), None)

            if worker:
                cron = job.get("cron")
                scheduled_worker = ScheduledWorker(cron, worker, job)
                scheduled_worker.start()
                self.worker_jobs.append(scheduled_worker)
                logger.info(f"{job_type} job started with schedule: {cron}")
            else:
                logger.warning(f"Unknown job dispatch: {job.get('dispatch')}")


if __name__ == "__main__":
    meshy = Meshy()
    asyncio.run(meshy.start())
