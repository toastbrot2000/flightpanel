# Configuration and operation

Settings come in two layers:

- **Server settings** in `.env`: home airport, data sources, logos, ports, operation. Read at start-up; change them and run `docker compose up -d`.
- **Per-panel settings** in the SQLite database `data/flightpanel.db`: location, view field, display, brightness, quiet hours, idle cards. Set them with the `panels` command or the optional settings page; the running server picks them up without a restart.

Invalid values stop the server at start-up (`.env`) or are rejected by the command (`panels set`), naming the setting. A stored panel setting that is no longer valid after an update is skipped with its default and reported in the log and in `/issues`. `/health` shows the values actually in effect.

## Contents

- [Server settings (.env)](#server-settings-env)
- [Per-panel settings](#per-panel-settings)
- [Settings page](#settings-page)
- [Calibrating the view field](#calibrating-the-view-field)
- [Allowed host names (LAN_HOSTS)](#allowed-host-names-lan_hosts)
- [Airline logos](#airline-logos)
- [Endpoints and monitoring](#endpoints-and-monitoring)
- [Data and backups](#data-and-backups)
- [Updating](#updating)

---

## Server settings (.env)

Copy `.env.example` to `.env`; every setting is explained there. Missing or empty values use the defaults from `server/flightpanel/config.py` (Berlin, BER).

**Home airport.** `LOCAL_AIRPORT_IATA` is usually enough: position, elevation and METAR station come from `data/airports.json` (generated from OurAirports by `tools/make_airports.py`). For an airport not in that list, set `AIRPORT_LAT`, `AIRPORT_LON`, `AIRPORT_ALT_M` and `METAR_STATION`. Also set `RUNWAY_COURSE`, the final approach course of one landing direction (BER 25: 249°, MUC 26: 263°; the opposite direction follows), and `CURFEW_HOURS` for a night flight ban.

| Setting | Default | Meaning |
|---|---|---|
| `LOCAL_AIRPORT_IATA` | `BER` | home airport |
| `AIRPORT_LAT`, `AIRPORT_LON`, `AIRPORT_ALT_M`, `METAR_STATION` | from `airports.json` | only for airports missing there |
| `RUNWAY_COURSE` | `249` | final approach course of one runway direction, degrees |
| `CURFEW_HOURS` | `00:00-05:00` | night flight ban, for the `curfew` card |
| `METAR` | `1` | weather from aviationweather.gov (fallback NOAA), every 20 min |
| `MOCK` | `0` (`.env.example`: `1`) | `1` shows fictional flights, no network needed |
| `SOURCE` | `adsblol` | `adsblol`: adsb.lol, OpenSky as fallback; `opensky`: OpenSky only |
| `POLL_INTERVAL` | `20` | seconds between fetches (minimum 5) |
| `ADSBIM`, `TRACK_ORIGIN` | `1`, `1` | routes from adsb.im before adsbdb; origin airport from the OpenSky track when no route is known |
| `OPENSKY_CLIENT_ID`, `OPENSKY_CLIENT_SECRET` | empty | OpenSky API client; anonymous gets 400 credits a day, with an account 4000 |
| `ISS` | `1` | ISS orbit data from CelesTrak once a day, for the `iss` card |
| `WIDTH`, `HEIGHT` | `128`, `32` | display size; must match the firmware (`firmware/include/config.h`), and the layouts assume 32 px height |
| `LOGO_FETCH`, `LOGO_SIZE`, `LOGO_MAX_ASPECT` | `1`, `32`, `1.5` | see [Airline logos](#airline-logos) |
| `LOGO_URLS` | built-in list | own logo sources, comma-separated templates with `{iata}` or `{icao}`, optional `\|aspect` |
| `PUID`, `PGID` | `1000` | user and group the container runs as (Linux, see below) |
| `TZ` | `Europe/Berlin` | time zone for quiet hours, daily statistics and clock |
| `HTTP_PORT` | `8080` | LAN port for preview and panel inside the container; home network only. Leave it: `compose.yml` and the image's health check expect 8080. For another port on the host, change the left side of `ports:` in `compose.yml` (e.g. `"9090:8080"`) |
| `LOG_LEVEL` | `INFO` | |
| `LAN_PANEL` | `home` | the panel served on the LAN port; created at first start |
| `PANEL_MAX_KM`, `PANEL_MAX_RADIUS_KM` | `60`, `80` | limits for additional panels, see below |
| `STALE_AFTER`, `EMPTY_AFTER` | `120`, `600` | seconds without fresh data until the image is dimmed / blank |
| `LAN_HOSTS` | empty | extra host names for the LAN port, see [below](#allowed-host-names-lan_hosts) |
| `SETTINGS_PAGE` | `0` | `1` enables the [settings page](#settings-page) |
| `PUBLIC_PORT`, `ACCESS_TEAM`, `ACCESS_AUD`, `PUBLIC_HOST` | empty | remote panels via Cloudflare Access: all four or none, see [remote-panels.md](remote-panels.md) |

When a source fails, only its part is missing: no routes means no origin, no METAR means no weather cards. Without fresh flight data the image is dimmed after 2 minutes and blank after 10. Errors show up in `/issues` and `/health`.

**Linux file permissions.** The container runs unprivileged as `PUID:PGID` (default `1000:1000`) and must be able to write `./data`. Before the first start, set `PUID` and `PGID` in `.env` to the owner of `data/` (`id -u`, `id -g`). That avoids a `chown`, and `git pull` can still write to `data/`. Without compose (`docker run`) add `--user UID:GID`. If the permissions are wrong, the start aborts and the log names the folder, the user and what to do. Docker Desktop on Windows and macOS needs nothing.

**Port 8080 belongs on your home network.** It has no login, and `/display.json` roughly reveals where your window is (direction and distance of the aircraft). Don't forward it on your router and don't put it behind a reverse proxy or tunnel without authentication. For panels outside your home, see [remote-panels.md](remote-panels.md).

---

## Per-panel settings

All commands run inside the container:

```bash
docker compose exec flightpanel python3 -m flightpanel.panels <command>
```

| Command | Effect |
|---|---|
| `list` | all panels: settings count, access, last fetch, firmware, Wi-Fi signal |
| `keys` | all panel settings with default and explanation |
| `show <id>` | what is set for a panel |
| `set <id> KEY=VALUE …` | set, validated like `.env`; `KEY=` (empty) resets to the default |
| `unset <id> KEY …` | reset to the default |
| `add <id> [--name NAME]` | add a panel |
| `remove <id> --yes` | delete a panel with its album, statistics and access entries |
| `email <id> <address>` | this e-mail address may view and configure the panel in the browser (remote panels) |
| `token <id> <client-id>` | the panel fetches frames with this Cloudflare service token (remote panels) |
| `unlink <address\|client-id>` | remove an address or token again |
| `channel <id> stable\|canary` | firmware update channel ([hardware.md](hardware.md#72-release-canary-stable)) |
| `backup <file> [--force]` | copy of the database, safe while the server runs |

Changes from the command take effect within a minute, without a restart. Setting names are case-insensitive.

Example: location and view field of the panel `home`:

```bash
docker compose exec flightpanel python3 -m flightpanel.panels set home HOME_LAT=52.52 HOME_LON=13.40 HOME_ALT_M=40
docker compose exec flightpanel python3 -m flightpanel.panels set home VIEW_FROM=90 VIEW_TO=180 VIEW_MIN_ELEV=1 VIEW_MAX_KM=50 VIEW_ONLY=1
```

| Setting | Default | Meaning |
|---|---|---|
| `HOME_LAT`, `HOME_LON` | `52.52`, `13.40` | window position, decimal degrees |
| `HOME_ALT_M` | `40` | eye height at the window above sea level, metres |
| `RADIUS_KM` | `40` | show aircraft at most this far away |
| `MAX_ALT_M` | `6000` | … and at most this high, metres |
| `SCAN_RADIUS_KM` | = `RADIUS_KM` | radius for statistics, rare aircraft and emergencies |
| `VIEW_FROM`, `VIEW_TO` | `0`, `360` | view field, clockwise from/to in degrees (0 north, 90 east); 0 to 360 = all around |
| `VIEW_MIN_ELEV` | `0` | minimum elevation above the horizon, degrees (buildings, trees) |
| `VIEW_MAX_ELEV` | `90` | maximum elevation, degrees (a balcony above) |
| `VIEW_MAX_KM` | `100` | visibility range, km |
| `VIEW_ONLY` | `0` | `1`: only show what is in the view field |
| `SHOW_OTHER` | `1` | private aircraft, helicopters, police, military in the rotation, with a symbol instead of a logo |
| `AIRPORT` | = server | the panel's own airport (IATA code from `data/airports.json`), `-` = none; owner only, see below |
| `RUNWAY` | – | final approach course of one landing direction, degrees (e.g. `240` for runway 24); required with `AIRPORT` |
| `CURFEW` | – | night curfew `from-to`, e.g. `00:00-05:00`; without it, no curfew card |
| `LAYOUT` | `C` | `C` logo and three lines, `A` no logo, `B` two flights |
| `MAX_FLIGHTS` | `3` | how many flights rotate (1 to 10) |
| `CALM` | `0` | `1`: calm mode, see below |
| `PAGE_SECONDS` | `4` | seconds per info page |
| `FLIGHT_SECONDS` | `0` | minimum seconds per flight; 0 = each info page once |
| `INFO_PAGES` | `eta,alt,dist,callsign` | info pages in order |
| `BLINK` | `1` | `0`: nothing blinks except an emergency |
| `IDLE_SECONDS` | `12` | seconds per idle card |
| `IDLE_CLOCK_SHARE` | `0.35` | share of the clock among idle cards, 0 to 1 |
| `BRIGHTNESS_DAY`, `BRIGHTNESS_EVENING` | `90`, `35` | brightness 0 to 255 |
| `DAY_HOURS` | `08:00-19:00` | daytime brightness window |
| `QUIET_HOURS` | `00:00-06:30` | panel dark, no fetching |
| `CARD_<ID>` | `1` | `0` turns an idle card off, e.g. `CARD_PIGEON=0` (IDs in [screens.md](screens.md)) |

**Calm mode** (`CALM=1`) changes defaults only: info pages 10 s, each flight at least 20 s, idle cards 30 s, no blinking except emergencies, only arrival time and altitude/speed in the info line. Values you set explicitly still win.

**Additional panels** (`panels add`, e.g. for friends) share one fetch per region, so they must be near their airport: their window at most `PANEL_MAX_KM` from it, `RADIUS_KM` and `SCAN_RADIUS_KM` at most `PANEL_MAX_RADIUS_KM`. `panels set` rejects values beyond that; the LAN panel is exempt.

**Regions (another airport).** A panel far away, e.g. at your parents' place, gets its own airport: `panels set eltern AIRPORT=FMM RUNWAY=240` (the runway course of one landing direction; the server detects the other one from traffic), optionally `CURFEW=00:00-05:00`. Position, elevation and METAR station come from `data/airports.json`; the panel then has its own landing sequence, departures, landing direction and weather. Panels with the same airport, or close enough together, share one fetch; every further region is one more fetch per poll from adsb.lol (two regions: two requests every 20 s). `AIRPORT=-` means no airport at all: only overflights and the sky cards, nothing lands or departs. With cruising traffic overhead, raise `MAX_ALT_M` (e.g. `13000`). These three keys are set by the owner with `panels set` only; the settings page shows them but doesn't change them, so nobody can move their panel across the country or add fetches. `/health` → `source.regions` lists the regions currently fetching.

---

## Settings page

With `SETTINGS_PAGE=1` there is a mobile-friendly page at `/settings`: on the LAN port for the LAN panel (no login, so anyone on your home network can change it), and for remote panels behind Cloudflare Access ([remote-panels.md](remote-panels.md)). It sets the same panel settings as `panels set` with the same validation, never `.env` settings, and saved values apply immediately. The page is in German.

---

## Calibrating the view field

Real sightings beat a sketch. Stand at the window with your phone:

1. **Location:** copy the coordinates of the window from a map app (long press) and paste them; the settings page understands "latitude, longitude". Add `HOME_ALT_M`, your eye height above sea level.
2. **Wait for planes.** The settings page lists all aircraft nearby, refreshed every 5 s: callsign, type, direction, elevation above the horizon, distance and whether it is in the view field. Without the settings page, read `nearby` in `/display.json`: `az` is the direction, `elev` the elevation.
3. **Set the edges:** the view field runs clockwise from start to end (0 = north, 90 = east). When a plane disappears behind a building, use its direction as start or end of the view field (the page has buttons for that); use its elevation as minimum elevation for roofs and trees. The visibility range is how far you can still see planes: approaching aircraft are often visible at night from 50 to 60 km by their landing lights, high overflights are faint and clouds hide them.
4. **Only the view field:** set `VIEW_ONLY=1` so the panel only shows what you can actually see.
5. **Save**, watch a few more planes and adjust.

---

## Allowed host names (LAN_HOSTS)

To protect against DNS rebinding from a browser, the LAN port only answers under IP addresses, `localhost`, names without a dot (`nas`) and names ending in `.local`, `.home.arpa` or `.internal`. Everything else gets HTTP 421.

Names your router hands out, such as `nas.fritz.box`, must be listed explicitly (`.box` is a public top-level domain):

```bash
LAN_HOSTS=nas.fritz.box
```

This also applies to a panel whose server address in the setup portal uses such a name. `/ok` turns red when a panel's frame request was refused with 421 in the last 10 minutes and the LAN panel has not fetched a frame during that time (other refused requests, e.g. from a browser or monitor, only show in the log); `/health` shows the number of refusals and the age of the last one. Fix: add the name to `LAN_HOSTS` and restart the server, or use the IP address.

---

## Airline logos

Only square logo marks are used: wordmarks are unreadable at 32 × 32 pixels, so the panel shows a grey placeholder instead. The server fetches a logo the first time an airline appears. It tries the sources in order (Kiwi.com, FlightAware, Google), takes the first image with an aspect ratio up to `LOGO_MAX_ASPECT` (Google's set: up to 2:1), removes a white background, crops it and stores `data/logos/<IATA>.png` (without an IATA code `<ICAO>.png`). Failures appear in `/health` → `logos_missing` and are retried after 7 days.

Logos are trademarks of their owners; they stay on your server.

- **All at once:** `docker compose exec flightpanel python3 -m flightpanel.fetch_logos`; specific ones: `… fetch_logos LH U2 FR`; `--force` refetches downloaded ones.
- **Your own logos**, if none is found or you don't like the fetched one: `python3 tools/make_logo.py image.png U2` turns a PNG, JPG, GIF, WebP or SVG (with cairosvg) into the right format in `data/logos/own/`, with an enlarged preview in `own/preview/`. Without Python on your machine (put the image inside the project folder): `docker compose run --rm -v "$PWD:/repo" -w /repo flightpanel python tools/make_logo.py image.png U2`. Options: `--boost` brightens dark marks, `--mark "#ff6600"` takes only the white mark from a coloured tile and paints it in that colour, `--circle`, `--crop`, `--bg`, `--keep-white`. Own logos take precedence and are never touched by fetching; delete one to go back to the fetched logo. When moving to another machine, take `data/logos/own/` with you.
- **`data/airlines.json`** maps ICAO operator codes to short names and a logo code. The logo code is the brand, not necessarily the operating subsidiary (Malta Air → `FR`, KLM Cityhopper → `KL`), so subsidiaries get the brand's logo. `"album": false` keeps small operators out of the collection album.

**Adding an airline:** in `data/airlines.json` add `"ICAO": {"name": "Short name", "iata": "XY"}`. For rescue, police or military use `"airline": false` and a `"label"`; with `"number": true` a callsign like CHX31 becomes "Christoph 31". Then fetch or make the logo. Candidates collect in `/issues` under `unknown_operator`. A name entry is optional: if adsbdb doesn't know the callsign, the server looks up the operator name itself and shortens it at word boundaries. An entry is worth it if that name is outdated or misleading, for a brand logo, and for rescue, police and military. The name gets 94 px next to the logo; pick a short form if it's too long.

---

## Endpoints and monitoring

| Path | Purpose |
|---|---|
| `/` | preview page, with links to layouts and every idle card |
| `/panel/frame` (LAN port also `/frame`) | binary frame for the ESP32: `[1, brightness, 128, 32]` + 12 288 bytes RGB |
| `/panel/firmware.txt`, `/panel/firmware.bin` | signed firmware updates ([hardware.md](hardware.md#7-signed-firmware-updates)) |
| `/frame.png` | preview image; `layout=A\|B\|C`, `card=<id>` (with demo data if the card has nothing to say), `scale`, `gap=0` |
| `/display.json` | display model, plus `nearby`: all aircraft in range with direction, elevation, visibility, landing sequence |
| `/health` | sources, effective settings, cache, logos, last ESP32 fetch (`esp32.alive=false`: panel stuck), one line per panel under `panels`, nightly maintenance (`dream`), database state, remote access state |
| `/ok` | `ok` with 200, otherwise 503 and the reasons; for the Docker health check and an uptime monitor |
| `/issues` | collected anomalies (below); `POST /issues/clear` empties the list |
| `/stats` | daily statistics, collection album, rare sightings, landing direction |
| `/idle` | idle cards: tier, whether they currently have something to say, which one is showing |
| `/settings` | settings page, only with `SETTINGS_PAGE=1` |

**`/ok`** is red when there was no fresh flight data for 15 minutes, the LAN panel hasn't fetched for 10 minutes (once it had), the code raised errors in the last hour, the database was put aside, a panel's frame requests were refused with 421 (see [LAN_HOSTS](#allowed-host-names-lan_hosts)), or, with remote access, the firmware trust list is outdated. Point an uptime monitor (Uptime Kuma or similar) at `http://<server>:8080/ok` and you only hear from it when something is really wrong; `docker ps` then shows the container as `unhealthy`. Use `/ok` for alerts, not as a liveness probe that restarts the container: most of what it reports isn't fixed by a restart. Remote panels don't count towards `/ok`; `panels list` and `/health` show when each panel last fetched.

**`/issues`** collects anomalies with a counter, first and last occurrence, callsigns and an example. Clear after reviewing: `curl -X POST http://<server>:8080/issues/clear`.

| Kind | Meaning | What to do |
|---|---|---|
| `unknown_operator` | operator code not in `airlines.json` | only needed if the name fetched from adsbdb is outdated or misleading, for a brand logo, or for rescue/police/military |
| `name_mismatch` | our name differs from adsbdb | usually stale data at adsbdb: after checking, add `"adsbdb": "their name"` to the entry |
| `route_implausible` | route doesn't fit the position, discarded | nothing, for information |
| `route_unknown` | airline flight shown without a route | nothing, no source knows it |
| `source_error`, `rate_limit`, `fallback` | error, HTTP 429, OpenSky took over | if frequent, increase `POLL_INTERVAL` |
| `stale` | image dimmed or blank for lack of data | check sources |
| `esp32_gap` | the ESP32 didn't fetch for a while | Wi-Fi, power |
| `exception` | error in the code, with file and line | bug; traceback in the container log |
| `setting` | a stored panel setting is not valid (any more); the default is used | run the command in the example (`panels unset …`), then set it again |
| `backup` | nightly database copy failed | check space and permissions in `data/` |
| `database` | the database was broken and put aside as `data/flightpanel.db.broken-<date>`; the server started empty | restore as described in the example and [below](#data-and-backups), then clear `/issues` |
| `tls_roots` | remote access only: the certificate of your hostname no longer matches the firmware's trust list (`roots.pem`, checked daily), or `roots.pem` is missing | publish firmware with an updated `roots.pem` ([remote-panels.md](remote-panels.md#firmware-updates)) |

**Long-term operation.** The server is built to run for months. Once a night, at the start of `QUIET_HOURS` (4:00 without quiet hours), it cleans up: `/issues` forgets one-off entries after 7 days and everything else after 30 days without recurrence (code errors and a put-aside database stay at least 30 days; at 1000 entries the oldest makes room). It also writes a consistent database copy, `data/flightpanel.db.bak`, replacing the previous night's. `/health` → `dream` shows what the last night did. The Docker log is capped at 15 MB (`compose.yml`).

---

## Data and backups

Everything that changes lives in `data/`: the database `flightpanel.db` (panels and their settings, statistics, album, cache, issues) and the logos in `logos/`. These survive updates.

- **Don't put `data/` in a synced folder** (OneDrive, Dropbox, …): the database is written continuously (with `-wal` and `-shm` files next to it). If the project lives in one, exclude `data/` from syncing.
- **Backups:** back up `data/`, but use the nightly copy `data/flightpanel.db.bak` rather than the live database (data still in `flightpanel.db-wal` would be missing). For a copy at a specific moment:
  ```bash
  docker compose exec flightpanel python3 -m flightpanel.panels backup /data/backup-$(date +%F).db
  ```
  It ends up in `data/`. The command only overwrites an existing file with `--force`, hence the date in the name. In Git Bash on Windows prefix it with `MSYS_NO_PATHCONV=1`, otherwise the shell turns `/data/…` into a Windows path; in PowerShell use `$(Get-Date -Format yyyy-MM-dd)` instead of `$(date +%F)`.
- **Restore:** `docker compose stop`, copy `data/flightpanel.db.bak` to `data/flightpanel.db`, delete `flightpanel.db-wal` and `flightpanel.db-shm` next to it, `docker compose start`. This is also the way back when the server has put a broken database aside (`data/flightpanel.db.broken-<date>`, `/ok` red, `/issues` kind `database`): it then starts empty and the log lists these steps.
- **Database contents are private:** the panel settings contain window positions, often accurate to a few metres. Keep backups to yourself.

**Moving to another machine:**

```bash
git clone https://github.com/toastbrot2000/flightpanel && cd flightpanel
cp /path/to/old/.env .env                       # Linux: check PUID, PGID
cp /path/to/backup.db data/flightpanel.db       # a copy made with panels backup, see above
chmod 640 data/flightpanel.db
cp -r /path/to/old/data/logos/own data/logos/   # your own logos, if any
docker compose up -d --build
docker compose exec flightpanel python3 -m flightpanel.fetch_logos
```

Then check `/health`: `source.active` should be `adsblol`. Give the server a fixed address (a DHCP reservation in your router), otherwise you have to re-enter it in every panel's setup portal after it changes.

---

## Updating

`sh tools/update.sh` pulls the latest version, rebuilds and restarts, but only if there is a new commit (`--force`: always). Weekly via cron, during the panel's quiet hours, as a user in the `docker` group:

```
0 6 * * 1  cd /path/to/flightpanel && sh tools/update.sh >> data/update.log 2>&1
```

It also removes old images. The build only uses the pinned dependencies from the repository ([development.md](development.md#dependency-pinning)).
