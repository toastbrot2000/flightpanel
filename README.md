# flightpanel

**An LED panel for your window that shows which plane you are looking at:** airline logo, route, aircraft type, altitude and minutes to landing. When nothing is in view, it cycles through departures and arrivals at the local airport, the next plane to come into view, a radar, weather, ISS passes and a few extras.

> [!WARNING]
> **A fully AI-written weekend project.** Every line of code and documentation was written by an AI (Claude). No support, no warranty. Rebuilding, borrowing from it and building on it is welcome, at your own risk.

![Six example screens: a landing with logo and sky dots, departures at BER, the next plane, radar, a departure without logo showing the placeholder, an ISS pass](docs/images/overview.png)

The texts on the panel and the configuration pages are in German.

## What it shows

- **The plane in your view field**, ranked: aircraft landing or departing at your airport before overflights, airlines before private aircraft, the next to land first. Up to three flights rotate, each with the airline logo (or a grey placeholder symbol), route, type and rotating info: minutes to landing, altitude and speed, distance, callsign.
- **Idle cards** when nothing is in view, in fixed tiers: airport traffic (departures, arrivals, next plane) before information (radar, landing direction, weather, statistics, ISS, sunrise/sunset) before animations that only appear when nothing at all is going on. An emergency squawk overrides everything.

All screens with images and the rules for when each appears: [docs/screens.md](docs/screens.md).

| | |
|---|---|
| ![Arrivals at BER with origin and minutes](docs/images/arrivals.png) | ![Next plane: visible in about 3 minutes, from the left](docs/images/next.png) |
| ![Air rescue helicopter Christoph 31](docs/images/private.png) | ![Clouds at 600 m hiding the planes](docs/images/weather.png) |

## How it works

```
adsb.lol (fallback: OpenSky) ──┐
adsb.im, adsbdb (routes) ──────┤
OpenSky track (origin) ────────┼─> server (Python, Docker) ──HTTP /panel/frame──> ESP32 ──HUB75──> 2 panels
METAR, CelesTrak (ISS) ────────┤   fetches every 20 s,          12 KB every 2 s         DMA refresh
logo CDNs ─────────────────────┘   renders a frame per request
```

The server renders the complete 128 × 32 image. The ESP32 only fetches it as a 12 KB frame every 2 seconds and writes it to the panels; it knows nothing about flights, fonts or logos. Every change to how things look happens in Python, with a live preview in the browser and without reflashing.

## Hardware

Two HUB75 P2.5 panels (64 × 32 each) side by side give 128 × 32 pixels on 32 × 8 cm, driven by a classic ESP32 (WROOM-32) and powered by a 5 V / 4 A desktop power supply. No soldering, no mains wiring; about 100 to 120 € in parts. The server runs anywhere Docker runs, e.g. on a home server. Parts list, wiring, firmware and setup: [docs/hardware.md](docs/hardware.md).

## Quick start (no hardware, fictional flights)

You need Git and Docker with Compose v2 (Docker Desktop, or Docker Engine on Linux).

```bash
git clone https://github.com/toastbrot2000/flightpanel && cd flightpanel
cp .env.example .env            # MOCK=1 is preset: fictional flights
docker compose up -d --build
```

Then open `http://localhost:8080`: a preview that refreshes every 2 seconds, with links to the layouts and every idle card (`/frame.png?card=radar&scale=8` works directly too). From another device use the server's IP address; a router name such as `nas.fritz.box` first needs `LAN_HOSTS` ([configuration.md](docs/configuration.md#allowed-host-names-lan_hosts)), otherwise the server answers 421.

On Linux, set `PUID` and `PGID` in `.env` to the owner of `data/` first ([details](docs/configuration.md#server-settings-env)); Docker Desktop on Windows and macOS needs nothing.

## Going live

1. In `.env`: your airport (`LOCAL_AIRPORT_IATA`, `RUNWAY_COURSE`, `CURFEW_HOURS`), `MOCK=0`, your time zone `TZ`. Then `docker compose up -d`.
2. Your window's position and view field, per panel (example values; calibrate at the window, see [configuration.md](docs/configuration.md#calibrating-the-view-field)):
   ```bash
   docker compose exec flightpanel python3 -m flightpanel.panels set home HOME_LAT=52.52 HOME_LON=13.40 HOME_ALT_M=40
   docker compose exec flightpanel python3 -m flightpanel.panels set home VIEW_FROM=90 VIEW_TO=180 VIEW_MIN_ELEV=1 VIEW_MAX_KM=50 VIEW_ONLY=1
   ```
3. Optional: fetch the logos of all known airlines at once (otherwise each is fetched when the airline first appears): `docker compose exec flightpanel python3 -m flightpanel.fetch_logos`.
4. Build the panel, flash it and enter your Wi-Fi and server address from your phone ([docs/hardware.md](docs/hardware.md#5-step-by-step-assembly)).

Keep port 8080 on your home network: it has no login, and `/display.json` roughly reveals where your window is. Everything else, including calibrating the view field at the window, monitoring, backups and updates: [docs/configuration.md](docs/configuration.md).

## Documentation

| Document | Contents |
|---|---|
| [docs/hardware.md](docs/hardware.md) | parts, wiring, power, assembly, firmware, setup portal, OTA, signed updates |
| [docs/configuration.md](docs/configuration.md) | server and panel settings, view-field calibration, logos, endpoints, monitoring, backups, updating |
| [docs/screens.md](docs/screens.md) | every screen with images, tiers and conditions |
| [docs/remote-panels.md](docs/remote-panels.md) | optional: panels for friends elsewhere, via Cloudflare Tunnel and Access |
| [docs/development.md](docs/development.md) | tests, reference images, firmware checks, dependency pinning, code layout |

## Status and limitations

- **Works:** the server with live data in Docker, checked at a real window against the sky and against Flightradar24. Landing order, minutes to landing and the view field match well. The firmware runs on a real panel: setup from a phone, frames over the LAN.
- **Not yet tested on hardware:** remote panels through Cloudflare end to end, signed updates with rollback and rescue mode, the experimental Arduino-ESP32 3.x build, and continuous operation over weeks.
- **Built for one place:** developed in Berlin for BER. Everything location-specific is configurable, but other airports have seen little testing.
- Data comes from free community and public services; when one fails, only its part is missing (no routes means no origin, no METAR means no weather cards).

## Acknowledgements

This project is heavily inspired by these projects and could not have been done without them:

- [smartbutnot/flightportal](https://github.com/smartbutnot/flightportal): shows overhead flights on a 64 × 32 RGB matrix driven by an Adafruit MatrixPortal M4, in CircuitPython.
- [AxisNimble/TheFlightWall_OSS](https://github.com/AxisNimble/TheFlightWall_OSS): an LED wall of 16 × 16 panels driven by an ESP32 that shows flights passing your window, using OpenSky and FlightAware AeroAPI.
- [theqkash/esp32flight](https://github.com/theqkash/esp32flight): a desk flight radar for ESP32 touchscreen boards (and Android), fed by community ADS-B feeds or your own receiver.

Related open-source projects:

- [ColinWaddell/its-a-plane-python](https://github.com/ColinWaddell/its-a-plane-python): a Raspberry Pi driving a 64 × 32 RGB matrix with nearby aircraft, weather, time and satellite passes.
- [WeegeeNumbuh1/FlightGazer](https://github.com/WeegeeNumbuh1/FlightGazer): tracks and logs aircraft from a local ADS-B receiver and shows them on an RGB LED matrix.

**Data sources** used at runtime; please respect their terms of use and rate limits:

- [adsb.lol](https://adsb.lol): aircraft positions, types and categories (data under ODbL)
- [OpenSky Network](https://opensky-network.org): fallback positions and flight tracks for origin airports
- [adsb.im](https://adsb.im) and [adsbdb](https://www.adsbdb.com): routes and operator names
- [Aviation Weather Center](https://aviationweather.gov) (NOAA), with NOAA's METAR files as fallback: weather
- [CelesTrak](https://celestrak.org), with [tle.ivanstanojevic.me](https://tle.ivanstanojevic.me) as fallback: ISS orbital elements
- [OurAirports](https://ourairports.com): airport data in `data/airports.json` (public domain)
- Kiwi.com, FlightAware and Google: airline logos, fetched at runtime for personal use and not part of this repository. Airline names and logos are trademarks of their owners; "Demo Air" in the example images is made up.

**Libraries and tools:**

- Firmware: [ESP32-HUB75-MatrixPanel-DMA](https://github.com/mrcodetastic/ESP32-HUB75-MatrixPanel-DMA), [Adafruit GFX](https://github.com/adafruit/Adafruit-GFX-Library) and [Adafruit BusIO](https://github.com/adafruit/Adafruit_BusIO), [Arduino-ESP32](https://github.com/espressif/arduino-esp32) and ESP-IDF with mbedTLS, [QR Code generator](https://github.com/nayuki/QR-Code-generator) by Project Nayuki (shipped with ESP-IDF) for the Wi-Fi QR code, [PlatformIO](https://platformio.org) and, for the experimental 3.x build, [pioarduino](https://github.com/pioarduino/platform-espressif32), [Unity](https://github.com/ThrowTheSwitch/Unity) for host tests
- Server: [aiohttp](https://github.com/aio-libs/aiohttp), [Pillow](https://python-pillow.org), [sgp4](https://pypi.org/project/sgp4/); pytest, ruff and uv for development; [cloudflared](https://github.com/cloudflare/cloudflared) for remote panels
- Font: [DejaVu Sans](https://dejavu-fonts.github.io) (regular and bold, under the DejaVu fonts license), installed in the container from Debian's `fonts-dejavu-core` and not bundled in this repository; the arrows are hand-drawn pixel patterns.

## License

Code and documentation are under the [MIT License](LICENSE). Third-party data and logos remain under their own terms (see above).
