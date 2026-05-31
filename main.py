import asyncio
from datetime import UTC, datetime

import requests
import yaml
from dotenv import load_dotenv
from loguru import logger
from meshcore import EventType, MeshCore

from adsb import AdsbService
from db import NodeDB
from hf import HfService
from nixle import NixleService
from scheduled_worker import ScheduledWorker
from utils import node_label, split_message
from weather import WeatherService

CONFIG_FILE = "config.yml"
MAX_RETRIES = 10
RETRY_BASE_DELAY = 5
MAX_CHANNELS = 8


class Meshy:
    def __init__(self):
        logger.add("meshy.log", rotation="50 MB")
        load_dotenv()
        self.config = self.load_config()
        self.worker_jobs = []
        self.mc = None
        self.channel_map = {}
        self._send_queue = asyncio.Queue()
        self._channel_cooldowns: dict[tuple[str, str], datetime] = {}
        self.weather = WeatherService(self.config)
        self.adsb = AdsbService(self.config)
        self.nixle = NixleService(self.config)
        self.hf = HfService()

        if self.config["save_node_db"]:
            self.db = NodeDB()
        else:
            self.db = None

    async def start(self):
        self.mc = await self.connect_and_run()
        await self.mc.ensure_contacts()
        await self.build_channel_map()
        await self.bootstrap_nixle()
        self.start_jobs()

        send_task = asyncio.create_task(self._send_queue_worker())
        try:
            await asyncio.sleep(float("inf"))
        except (KeyboardInterrupt, asyncio.CancelledError):
            logger.info("Closing connection, shutting down.")
        finally:
            send_task.cancel()
            for worker_job in self.worker_jobs:
                worker_job.stop()
            await self.mc.disconnect()

    async def _send_queue_worker(self):
        delay = self.config.get("send_delay", 2)
        while True:
            item = await self._send_queue.get()
            kind, *args = item
            try:
                if kind == "dm":
                    contact, chunk = args
                    await self.mc.commands.send_msg(contact, chunk)
                elif kind == "chan":
                    channel_index, chunk = args
                    await self.mc.commands.send_chan_msg(channel_index, chunk)
            except Exception as e:
                logger.error(f"Send queue error: {e}")
            finally:
                self._send_queue.task_done()
            await asyncio.sleep(delay)

    async def connect_and_run(self):
        attempt = 0
        tcp = self.config.get("tcp")
        while True:
            try:
                if tcp:
                    host, port = tcp["host"], tcp["port"]
                    logger.info(
                        f"Connecting to node via TCP {host}:{port} (attempt {attempt + 1})..."
                    )
                    mc = await MeshCore.create_tcp(
                        host, port, auto_reconnect=True, max_reconnect_attempts=10
                    )
                else:
                    logger.info(
                        f"Connecting to node via '{self.config['serial_port']}' (attempt {attempt + 1})..."
                    )
                    mc = await MeshCore.create_serial(
                        self.config["serial_port"], baudrate=115200
                    )
                logger.info("Connected to node.")

                mc.subscribe(EventType.CONTACT_MSG_RECV, self.on_receive)
                mc.subscribe(EventType.CHANNEL_MSG_RECV, self.on_channel_receive)
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
                for chunk in split_message(reply_text):
                    await self._send_queue.put(("dm", contact, chunk))
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
            lat = event.payload.get("adv_lat")
            lon = event.payload.get("adv_lon")
            if not name or lat is None:
                await self.mc.ensure_contacts(follow=True)
                contact = self.mc.get_contact_by_key_prefix(node_id)
                if contact:
                    name = name or contact.get("adv_name", "")
                    lat = lat if lat is not None else contact.get("adv_lat")
                    lon = lon if lon is not None else contact.get("adv_lon")
            self.db.upsert_node(node_id, name, lat, lon)
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

    async def on_channel_receive(self, event):
        if not self.config.get("bot", {}).get("active", True):
            return

        try:
            channel_idx = event.payload.get("channel_idx")
            text = event.payload.get("text", "")

            if not text:
                return

            # Channel messages include a sender prefix: "CallSign: .command"
            cmd_text = text.strip()
            if ": " in cmd_text:
                cmd_text = cmd_text.split(": ", 1)[1]
            cmd = cmd_text.strip().lower()

            channel_key = next(
                (
                    name.lstrip("#")
                    for name, idx in self.channel_map.items()
                    if idx == channel_idx
                ),
                None,
            )
            if channel_key is None:
                logger.info(
                    f"<- channel event: idx={channel_idx} not in channel_map {self.channel_map}, ignoring."
                )
                return

            matched = next(
                (
                    cc
                    for cc in self.config.get("channel_commands", [])
                    if cc.get("channel", "").lower().lstrip("#") == channel_key
                    and cc.get("command", "").lower() == cmd
                ),
                None,
            )
            if matched is None:
                logger.debug(
                    f"<- channel {channel_key}: unrecognized command {cmd!r}, ignoring."
                )
                return

            cooldown_seconds = matched.get("cooldown_seconds", 300)
            if not self._check_and_update_cooldown(channel_key, cmd, cooldown_seconds):
                logger.debug(f"Cooldown active for {cmd} in {channel_key}, ignoring.")
                return

            dispatch = matched.get("dispatch")
            method = getattr(self, dispatch, None)
            if method is None:
                logger.warning(f"Unknown channel command dispatch: {dispatch}")
                return

            logger.info(f"<- channel {channel_key}: {cmd}")
            msg = await asyncio.to_thread(method)

            if msg:
                logger.info(f"-> {channel_key}: {msg}")
                for chunk in split_message(msg):
                    await self._send_queue.put(("chan", channel_idx, chunk))

        except Exception as e:
            logger.error(f"Channel command error: {e}")

    def _check_and_update_cooldown(self, channel_name, command, cooldown_seconds):
        key = (channel_name, command)
        last = self._channel_cooldowns.get(key)
        now = datetime.now(UTC)

        if last is not None and (now - last).total_seconds() < cooldown_seconds:
            return False

        self._channel_cooldowns[key] = now
        return True

    def get_weather_conditions(self):
        return self.weather.get_conditions()

    def get_weather_forecast(self):
        return self.weather.get_forecast()

    def get_aircraft(self):
        return self.adsb.get_aircraft()

    def get_alerts(self):
        alerts = self.nixle.get_alerts()[:3]

        if not alerts:
            return "No recent alerts."
        return "\n".join(NixleService.format_alert(a) for a in alerts)

    def get_hf_conditions(self):
        day_msg, night_msg = self.hf.get_conditions()
        return f"{day_msg}\n{night_msg}"

    def get_seen_nodes(self):
        if self.db is None:
            return "Command inactive."

        seen_nodes = self.db.get_seen_nodes()

        if not seen_nodes:
            return "No nodes seen."

        labels = ", ".join(n["name"] or n["id"] for n in seen_nodes[:5])
        return f"Recently seen: {labels}"

    async def get_advert_worker(self, job):
        flood = job.get("flood", True)
        result = await self.mc.commands.send_advert(flood=flood)
        if result.type != EventType.ERROR:
            logger.info(f"-> Advert sent ({'flood' if flood else 'direct'})")
        elif result.payload.get("reason") == "no_event_received":
            logger.info(
                f"-> Advert sent ({'flood' if flood else 'direct'}) (no ack, expected over TCP)"
            )
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
        for chunk in split_message(msg):
            await self._send_queue.put(("chan", channel_index, chunk))
        logger.info(f"-> '{msg}' on '{job['channel']}' ({channel_index})")

    async def get_beacon_worker(self, job):
        await self._send_channel_message(job, job["text"])

    async def get_weather_conditions_worker(self, job):
        try:
            await self._send_channel_message(
                job, await asyncio.to_thread(self.weather.get_conditions)
            )
        except requests.exceptions.RequestException as e:
            logger.info(f"Weather conditions request failed: {e}")

    async def get_weather_forecast_worker(self, job):
        try:
            await self._send_channel_message(
                job, await asyncio.to_thread(self.weather.get_forecast)
            )
        except requests.exceptions.RequestException as e:
            logger.info(f"Weather forecast request failed: {e}")

    async def bootstrap_nixle(self):
        if not self.nixle.url or self.db is None:
            return
        try:
            alerts = await asyncio.to_thread(self.nixle.get_alerts)
            for alert in alerts:
                self.db.upsert_alert(alert["id"])
            logger.info(f"Nixle bootstrap: marked {len(alerts)} alerts as seen.")
        except Exception as e:
            logger.warning(f"Nixle bootstrap failed: {e}")

    async def get_hf_worker(self, job):
        try:
            day_msg, night_msg = await asyncio.to_thread(self.hf.get_conditions)
            await self._send_channel_message(job, day_msg)
            await self._send_channel_message(job, night_msg)
        except requests.exceptions.RequestException as e:
            logger.info(f"HF conditions request failed: {e}")

    async def get_nixle_worker(self, job):
        try:
            alerts = await asyncio.to_thread(self.nixle.get_alerts)
            for alert in alerts:
                if self.db.upsert_alert(alert["id"]):
                    msg = NixleService.format_alert(alert)
                    await self._send_channel_message(job, msg)
        except requests.exceptions.RequestException as e:
            logger.info(f"Nixle request failed: {e}")

    async def get_aircraft_worker(self, job):
        try:
            await self._send_channel_message(
                job, await asyncio.to_thread(self.adsb.get_aircraft)
            )
        except requests.exceptions.RequestException as e:
            logger.info(f"Aircraft request failed: {e}")

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

            dispatch = job.get("dispatch")
            worker = getattr(self, dispatch, None) if dispatch else None

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
