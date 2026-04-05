<img src="./logo.png" alt="Meshy Logo" width="400" height="auto">

Meshy is a MeshCore/LoRa-based bot for scheduled interval workers with general messages, beaconing, weather condition reporting via Home Assistant connected sensors, NWS forecasts, and DM commands. Fully configurable to have as many or as few channel outputs of any type you'd like on a schedule.

Also has support for commands which respond to DMs with a string or a function call. Optionally also keeps a SQLite DB of "seen" nodes for you to use in bot commands or interval workers.

## Requirements

- A MeshCore companion device connected to the machine you intend to run the script on, with any required USB serial drivers pre-installed.
- Something unix-ish to run the script on (Linux, Mac OS etc). It might work in windows but ymmv.
- Python 3 and Pip 3.
- SQLite3 headers (generally `libsqlite3-dev`).
- Probably something to keep the script running in the background e.g. `tmux`, `screen`, `pm2`, or write your own service daemon.

## Usage

- Connect your device via USB.
- One time: `cp config.sample.yml config.yml`
- One time: `pip3 install -r requirements.tsx`
- One time if you want to integrate with Home Assistant sensors: Create and Edit `.env` and add `HA_TOKEN=YOUR_API_TOKEN_HERE`
- As needed: Edit `config.yml` to have the worker jobs and commands you desire.
- Run `python3 main.py`
- Enjoy!

### Commands

Meshy can be configured to respond to commands via MeshCore DM. Here are the
out of box commands:

- `.about`: Basic info about the bot
- `.conditions`: Current temperature and humidity via Home Assistant
- `.forecast`: National Weather Service forecast for the latest period
- `.help`: Lists available commands
- `.ping`: Replies `pong!`
- `.seen`: Most recently "seen" node (WIP)

## Security and privacy

While MeshCore uses encryption on private channels, public channels are unencrypted. A good best practice is to never send anything truly sensitive on any channel, but that's up to your personal level of privacy.

## License

Apache 2.0 license, see `LICENSE.md` for more information.
