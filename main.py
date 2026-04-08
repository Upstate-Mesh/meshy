import asyncio
import math
import os
import textwrap

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
MAX_MSG_LEN = 133
MAX_CHANNELS = 8


def node_label(contact):
    name = contact.get("adv_name", "") if contact else "unknown"
    key = (contact.get("public_key", "") if contact else "unknown")[:12]
    return f"{name} ({key})" if name else f"({key})"


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
        await self.mc.ensure_contacts()
        await self.build_channel_map()
        self.start_jobs()

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
                mc.subscribe(EventType.NEW_CONTACT, self.on_advert_received)

                if self.db is not None:
                    mc.subscribe(EventType.NEW_CONTACT, self.observe_node)
                    mc.subscribe(EventType.ADVERTISEMENT, self.observe_node)

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
            contact = self.mc.get_contact_by_key_prefix(pubkey_prefix)
            label = node_label(contact) if contact else f"({pubkey_prefix})"

            logger.debug(f"<- DM event from {label}")
            text = event.payload.get("text", "")

            if not text:
                return

            cmd = text.strip().lower()
            reply_text = await self.handle_command(cmd)

            if reply_text is None:
                logger.debug(f"<- Unrecognized command from {label} ({cmd}), ignoring.")
                return

            logger.info(f"<- from {label}: {cmd}")
            logger.info(f"-> to {label}: {reply_text}")

            if contact:
                for chunk in textwrap.wrap(reply_text, width=MAX_MSG_LEN):
                    await self.mc.commands.send_msg(contact, chunk)
            else:
                logger.warning(f"Could not find contact for {label}, cannot reply.")
        except Exception as e:
            logger.error(f"Command error: {e}")

    async def on_advert_received(self, event):
        if not self.config.get("bot", {}).get("auto_accept_contacts", False):
            return

        try:
            public_key = event.payload.get("public_key", "")
            if not public_key:
                return

            # we already know them
            if self.mc.get_contact_by_key_prefix(public_key[:12]):
                return

            # payload has full contact info
            contact = dict(event.payload)
            out_path = contact.get("out_path", "")
            contact["out_path_len"] = len(out_path)

            result = await self.mc.commands.add_contact(contact)
            if result.type == EventType.ERROR:
                logger.warning(
                    f"Failed to add contact {node_label(contact)}: {result.payload}"
                )
                return

            self.mc._contacts[public_key] = contact
            logger.info(f"<- Accepted new contact: {node_label(contact)}")

            # exchange our contact info in return to get a mutual handshake
            await self.mc.commands.send_advert(flood=False)
        except Exception as e:
            logger.error(f"Error accepting contact: {e}")

    async def observe_node(self, event):
        try:
            public_key = event.payload.get("public_key", "")
            if not public_key:
                return

            node_id = public_key[:12]
            name = event.payload.get("adv_name", "")
            if not name:
                contact = self.mc.get_contact_by_key_prefix(node_id)
                name = contact.get("adv_name", "") if contact else ""
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

    async def get_advert_worker(self, job):
        flood = job.get("flood", True)
        result = await self.mc.commands.send_advert(flood=flood)
        if result.type != EventType.ERROR:
            logger.info(f"-> Advert sent ({'flood' if flood else 'direct'})")
        else:
            logger.warning(f"Advert failed: {result.payload}")

    def _resolve_channel(self, job):
        channel_name = job.get("channel", "").lower()
        channel_index = self.channel_map.get(channel_name)
        if channel_index is None:
            logger.warning(f"Channel '{channel_name}' not found on device, skipping.")
        return channel_index

    async def _send_channel_message(self, job, msg):
        channel_index = self._resolve_channel(job)
        if channel_index is None:
            return
        for chunk in textwrap.wrap(msg, width=MAX_MSG_LEN):
            await self.mc.commands.send_chan_msg(channel_index, chunk)
        logger.info(f"-> '{msg}' on '{job['channel']}' ({channel_index})")

    async def get_beacon_worker(self, job):
        await self._send_channel_message(job, job["text"])

    async def get_weather_conditions_worker(self, job):
        try:
            await self._send_channel_message(
                job, await asyncio.to_thread(self.get_weather_conditions)
            )
        except requests.exceptions.RequestException as e:
            logger.info(f"Weather conditions request failed: {e}")

    async def get_weather_forecast_worker(self, job):
        try:
            await self._send_channel_message(
                job, await asyncio.to_thread(self.get_weather_forecast)
            )
        except requests.exceptions.RequestException as e:
            logger.info(f"Weather forecast request failed: {e}")

    def get_seen_nodes(self):
        if self.db is None:
            return "Command inactive."

        seen_nodes = self.db.get_seen_nodes()

        if not seen_nodes:
            return "No nodes seen."

        labels = ", ".join(n["name"] or n["id"] for n in seen_nodes[:5])
        return f"Recently seen: {labels}"

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
        for i in range(MAX_CHANNELS):
            result = await self.mc.commands.get_channel(i)
            if result.type != EventType.CHANNEL_INFO:
                continue
            name = result.payload.get("channel_name", "").strip().lower()
            if not name:
                continue
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
