# Flightpanel for friends: one multi-tenant server, panels over Cloudflare

## Context

Today one server serves exactly one panel at one location (`Config` from `.env`), with no auth and only on the LAN. The firmware speaks plain HTTP, has Wi‑Fi and server URL compiled in, and updates only via ArduinoOTA on the same LAN.

Goal: friends in the same area (same home airport, BER) get a panel that runs in **their** Wi‑Fi without a VPN, through the existing Cloudflare Tunnel into the homelab. Each friend sets their own position, view field and display settings. Nothing answers without a verified identity, and one friend can be removed without affecting the others. Someone who clones the repo for themselves keeps a simple LAN setup with none of the remote machinery running.

Operating reality this plan is built for: a hobby project, implemented mostly by AI agents, run by an owner who does not look at the server daily and wants it as maintenance-free as possible. Two things must never happen: the owner's home network getting compromised, and a friend's network getting compromised.

Decisions made in the conversation:
- **One instance, many panels.** One adsb.lol fetch for everyone. Shared API cache, logos, airline data, METAR and ISS orbit data.
- **SQLite replaces the JSON state files**: stats and album, panel settings, panels and identities, cache, issues. Reference data (`airlines.json`, `airports.json`, `rare.json`) stays JSON in the repo; logos stay files.
- **Cloudflare Access (already set up) is the gate.** Friends' browsers log in with an email code; panels use one Access service token each.
- **Self-service:** friends edit their settings and calibrate the view field on a page.
- **Admin by command on the homelab** (`python -m flightpanel.panels …`); no admin web UI.
- **Signed pull updates:** panels install only images signed with a key that never leaves the laptop.
- **Everything remote is opt-in** via `.env`, and the code isn't loaded when off. Panel settings like location are set by CLI or the page. **One source per setting, DRY.**
- **Token check:** a stdlib verifier in the app, plus cloudflared's check and network isolation. This is the first adversarial review's verdict.
- **Deploy stays as today:** the homelab pulls `main`; the owner approves every push to `main`. No deploy branch, no commit-signature checks (the second review proposed them; the owner declined).
- **Good enough is the bar.** Residual risks are listed, not engineered away; friends' panels sit in their normal Wi‑Fi.

Defaults I chose (say if you want otherwise):
- **Album and stats are per panel.** Each friend collects from their own start, with their own "NEU!" grace period.
- **Your own panel stays on the LAN over plain HTTP.** It keeps working without internet.

## Principles

**One definition per setting.**
- Each `Config` field (config.py) already carries its env name, default and parser. It gets two more attributes:
  - `scope`: `server` or `panel`;
  - `label` and `group` for display.
- `.env` holds **server** settings only. **Panel** settings live in the DB, per panel.
- The CLI (`panels set`), the settings page and validation all use the same field parsers.
- `.env.example` lists only server settings. `panels keys` prints the panel settings with defaults and labels from the same metadata. No second list of keys anywhere.

**Scope split:**

| Scope | Fields |
|---|---|
| Panel | `HOME_*`, `RADIUS_KM`, `MAX_ALT_M`, `SCAN_RADIUS_KM`, `VIEW_*`, `VIEW_ONLY`, `SHOW_OTHER`, `LAYOUT`, `MAX_FLIGHTS`, `CALM`, `PAGE_SECONDS`, `FLIGHT_SECONDS`, `INFO_PAGES`, `BLINK`, `IDLE_SECONDS`, `IDLE_CLOCK_SHARE`, `CARD_<ID>`, `BRIGHTNESS_*`, `DAY_HOURS`, `QUIET_HOURS` |
| Server | airport/runway/curfew, METAR, sources, `POLL_INTERVAL`, OpenSky, routes, `WIDTH`/`HEIGHT`, ISS, logos, `DATA_DIR`, ports, `STALE_AFTER`/`EMPTY_AFTER`, `LOG_LEVEL`, the new switches |

**Self-hoster flow:**
1. `cp .env.example .env` (airport, `MOCK=0`).
2. `docker compose up -d --build`. The panel `home` is created on first start.
3. `docker compose exec flightpanel python -m flightpanel.panels set home HOME_LAT=… HOME_LON=… VIEW_FROM=…`, or `SETTINGS_PAGE=1` and the browser.

**Migration:** on first start, panel keys still present in an existing `.env` are imported once into `home`. Afterwards they're ignored, with a warning that names the CLI command.

**Opt-in switches; nothing loads when off:**

| Switch | Default | Turns on |
|---|---|---|
| `PUBLIC_PORT` + `ACCESS_TEAM` + `ACCESS_AUD` + `PUBLIC_HOST` | off | The second listener for remote panels and friends. `access.py` is imported only then; a partial config stops startup with a clear error. |
| `SETTINGS_PAGE=1` | off | The settings and calibration page (LAN panel on :8080; friends' panels on the public listener). |
| files in `DATA_DIR/firmware/<channel>/` | none | Serving signed firmware updates; without files the routes answer 404. |
| `panels add …` | only `home` exists | Extra panels, reachable only through the public listener. |

**Reuse over duplication:**
- One set of route handlers. They differ only in how the panel is resolved: LAN → `LAN_PANEL`; public → verified identity.
- One firmware build; its features switch on by configuration.

**Deploy:** unchanged. `tools/update.sh` fast-forwards `main`; the escrow bot's lock updates land there too. (If the repo ever becomes public: GitHub disables scheduled workflows after 60 days without repository activity, so the escrow cron would need a keepalive.)

## Architecture

```
friend panel ─HTTPS + service token─┐
friend browser ─email code──────────┴─> Cloudflare Access ─tunnel─> cloudflared (checks JWT) ─own network─> flightpanel :8081 (public, app checks JWT again)
panel in rescue mode ─HTTPS, no token─> Cloudflare (Access bypass on /panel/firmware*) ─tunnel─> cloudflared ─> flightpanel :8081 (signed files only)
your panel, LAN browser ─HTTP──────────────────────────────────────────────────────────────────────────────> flightpanel :8080 (LAN, LAN panel only)

flightpanel (one process)
  shared:    fetch (adsb.lol/OpenSky) covering all panels, routes/tracks/adsbdb cache, METAR, ISS orbit, logos, airlines,
             airport state (Recent, landing course, last airport movement)
  per panel: Config (server env + panel settings), model, renderer, idle cards, stats/album, ISS passes, frame bookkeeping
  SQLite DATA_DIR/flightpanel.db
```

The two listeners:
- **:8080 LAN** (published on the host, as today) serves only the LAN panel (`LAN_PANEL`, default `home`) without login, plus the server pages `/health`, `/ok` and `/issues`. It accepts only `Host` headers that are an IP literal, `localhost`, `*.local` or a name in `LAN_HOSTS` (against DNS rebinding from a malicious website in the LAN browser).
- **:8081 public** (only with `PUBLIC_PORT`) is not published, only on a dedicated network shared with cloudflared. Every request needs a verified Access identity, except the two signed firmware files:
  - email → browser pages of that person's panel;
  - service token → only `/panel/*`;
  - no identity → only `/panel/firmware.txt` and `/panel/firmware.bin` (signed artifacts without secrets; this is the rescue channel, see Firmware §3).

  Panel IDs never appear in URLs.

## Server (`server/flightpanel/`)

### 1. SQLite store — new `db.py`
- `DATA_DIR/flightpanel.db`, stdlib `sqlite3` (no new dependency). WAL mode, `busy_timeout=5000`, foreign keys on. Schema versions in `PRAGMA user_version` with ordered migrations. `db.py` is the only module that imports `sqlite3`; all statements are parameterized (ruff `S608` plus a grep test enforce both).
- Tables:
  - `panels(id, name, settings JSON of raw strings, fw_channel, created, updated, stats_since, last_obs, last_in_view, seen_at, fw_version INT, uptime_s INT, rssi INT, min_heap INT)`
  - `identities(kind 'email'|'token', value, panel_id)`, primary key `(kind, value)`. Several emails per panel are allowed; one email belongs to one panel.
  - `album(panel_id, brand, code, name, known, callsign, first, last, fanfare)`
  - `days(panel_id, date, aircraft, airlines, shown, max_alt_m, max_alt_callsign, quiet_record_s, ids JSON)`. `ids` holds today's ID sets only; the rows are the 60-day history.
  - `rare(panel_id, icao24, …)` with today's fields.
  - `cache(key, expires, value)`
  - `issues(id, kind, key, panel_id NULL, seen, days, first, last, callsigns, example)`
  - `logo_missing(code, failed)`
  - `state(key, value)`: landing course and its pending flip, TLE plus explicit fetched-at, issues `since`, the last good Access key set.
- The stores keep their in-memory working sets and public APIs (`Cache`, `Issues`, `Stats`, `LogoStore`, `astro.Iss`). They write changed rows in **one transaction after every poll** (and at least once a minute while anything is dirty, e.g. in quiet hours) and on stop, instead of rewriting JSON every 10 min. That gives at most 20 s of loss and fixes Windows skipping the final save.
- **One-time import on first start:**
  - `cache.json`, `issues.json`, `tle.txt` (its mtime becomes fetched-at) and `logos/.missing.json` are imported.
  - `stats.json` goes into `home`; its landing course goes into `state`.
  - The panel keys from `.env` go into `home`'s settings.
  - The imported files are renamed `*.imported` and kept as backup.
- `storage.py` stays for the reference JSON.

### 2. Shared layer and per-panel layer — `main.py`, `config.py`, new `panel.py`
- **`Config`:** the field metadata gains `scope`, `label` and `group`.
  - `Config.from_env` reads server fields from the environment.
  - `Config.for_panel(server_cfg, settings)` parses panel fields from the DB with the same parsers, CALM preset and error messages ("HOME_LAT='x': …").
  - Panel fields not set fall back to code defaults (Berlin-Mitte, as today), never to another panel's values.
- **Extra validation:**
  - Added panels (not the LAN panel) must have their home within `PANEL_MAX_KM` (default 60) of the airport. That keeps the shared fetch bounded without restricting a self-hoster who lives far from their airport.
  - Panel radii are capped by server settings (`RADIUS_KM`, `SCAN_RADIUS_KM` ≤ `PANEL_MAX_RADIUS_KM`, default 80), so one friend cannot blow up the shared fetch.
- **`Panel`:** `id`, `cfg`, `model`, `renderer`, `idle`, `stats`, ISS observer, last fetch, frame count, ETag, diagnostics. `App` holds what's shared plus `panels`.
- **Poll loop**, once per interval:
  1. Reload panels from the DB if anything changed (CLI changes apply within one poll); rebuild only the changed panels.
  2. If every panel is in its quiet hours, don't fetch.
  3. Fetch once: a circle covering every panel's own fetch circle. The center is the centroid of the homes; the radius is the largest of `dist(center, home) + fetch_radius_km`, capped at 250 nm. The OpenSky bbox is the union of the panels' boxes. With one panel both equal today's.
  4. Each active panel, one after another: `build_model(panel.cfg, all_states, …, prev_course=shared, recent=shared, noted=poll_set)`.
     - `build_model` filters by home and radius itself (build.py `candidates`).
     - Two small changes in `build.py`: `recent.update` moves out of `build_model`, and `_Builder` takes a shared `noted` set so each issue is counted once per poll.
     - Panels in quiet hours get the blank model.
  5. Once per poll: `recent.update`, landing-course tracking (moved out of `Stats._course` into shared airport state, still readable by the runway cards), `last_airport_move`.
  6. Each active panel: `stats.observe(states within the panel's fetch radius, model, now)`. New airline codes go to the shared logo fetch and adsbdb name lookup.
  7. Flush the DB.
- **Things that now follow "any panel active":** METAR refresh, the OpenSky budget (union of active windows) and the nightly cleanup.
- **`/ok`:** server problems plus only the LAN panel's ESP32, plus the daily TLS self-check (below). A friend unplugging must not mark the container unhealthy.
- **Daily TLS self-check (only with `PUBLIC_PORT`):** once a day the server fetches `https://<PUBLIC_HOST>/panel/firmware.txt` with a Python SSL context that trusts **only** `firmware/certs/roots.pem`, the same file the firmware embeds. If that fails, `/ok` reports "root list stale" and `/issues` records it. This is the early warning that the panels' trust list no longer matches Cloudflare's certificate chain, long before friends notice dark panels.
- **ISS:** the TLE is fetched and stored once; passes are computed per panel in the existing hourly thread. `astro._SUN_CACHE` gets room for more homes.
- **Mock:** `mock_model` per panel.
- **Why the rest stays untouched:** `airport.py`, `build.py`, `render.py`, the cards and `astro.py` read airport and panel fields from one `cfg`. A per-panel cfg with the same airport fields keeps every signature, so rendering, idle cards and astro need no structural change.

### 3. Identity on the public listener — new `access.py`, `web.py`
Decided by the first adversarial review:
- **Check in the app with the stdlib** (no new dependency). cloudflared's `access.required` stays as an outer layer, plus network isolation.
- **cloudflared alone fails open:** any container on a shared network could send unsigned claims.
- **PyJWT + cryptography would add four packages** with many 2026 advisories, and the PyJWT key fetch blocks the event loop.

**What it is:**
- A second aiohttp site on `PUBLIC_PORT`, `client_max_size=16 KB`. `access.py` is only imported when that is set.
- **Deny by default, declared per route.** Every public route is registered with an explicit identity class: `email`, `token` or `public` (the two firmware files only). The middleware rejects any request whose verified identity class doesn't match the matched route's declaration, and it rejects routes without a declaration outright. A test enumerates `app.router.routes()` and asserts: every route has a class; every non-`public` route answers 401 without a token and 403 with the wrong class; exactly two routes are `public`. This makes "forgot the decorator" impossible rather than merely caught.
- Every exception in verification becomes 401.
- **Per-identity rate limit:** a token bucket (2 requests/s, burst 10) keyed by identity, so one friend's hammering browser can't stall the shared process. `scale` for `/frame.png` is capped at 12 on the public listener.

**Verifier checklist:**
- Exactly one token header, at most 1 KB, three segments matching `[A-Za-z0-9_-]+` that re-encode identically.
- Header: `alg == "RS256"`; reject `crit`; never use `jku`/`x5u`/`jwk`.
- Signature length exactly k and smaller than n. Key n at least 2048 bits, `e == 65537`.
- Verify by RFC 8017 §8.2.2 re-encoding with a constant-time compare of the whole block; never parse DigestInfo. Parse the payload only after the signature checks out.
- `exp` required; `nbf` only if present; ±60 s leeway.
- Exact `iss`; `type == "app"`; `aud` must be a list containing the tag.

**Identity:**
- Either `email` (lower-cased) or `common_name` with `sub == ""` (service token = client ID), never both.
- Explicit allowlist in `identities`; panels are never created automatically. Unknown → 403 ("nicht freigeschaltet").

**Signing keys:**
- `https://<team>.cloudflareaccess.com/cdn-cgi/access/certs`, fetched with aiohttp: no redirects, a timeout, a size cap; each key validated separately.
- The last good set is persisted in `state`. Re-fetched on an unknown `kid` at most once a minute (Cloudflare rotates every 6 weeks with a 7-day overlap).
- `/health` (LAN) shows key-set age, auth failures and clock skew from `iat`.

**Guards:**
- Tokens and headers are never logged.
- With `PUBLIC_PORT` set, the LAN listener refuses requests carrying Cloudflare headers (`Cf-Ray`, `Cf-Connecting-Ip`, `Cf-Access-Jwt-Assertion`). If the unauthenticated port is ever wired into the tunnel by mistake, it fails closed.
- Response headers on both listeners: `Content-Security-Policy: default-src 'self'; frame-ancestors 'none'`, `X-Content-Type-Options: nosniff`, `Referrer-Policy: no-referrer`. Page scripts are static files, not inline, so the CSP holds.
- Writes (settings JSON) require `Origin` equal to the configured origin: `https://<PUBLIC_HOST>` on the public listener; on the LAN, an origin whose host passes the LAN `Host` allowlist.

### 4. Routes — `web.py`
One set of handlers; a resolver supplies the panel.
- **LAN (:8080):** today's routes for the LAN panel. `/frame` stays as an alias of `/panel/frame`. Also: `/panel/firmware.*`, and `/settings` with `SETTINGS_PAGE=1`.
- **Public (:8081):**
  - email: `/` (preview, linking only the person's own pages), `/frame.png`, `/display.json`, `/stats`, `/idle`, `/settings` (with the switch);
  - token: `/panel/frame`;
  - public (no identity): `/panel/firmware.txt`, `/panel/firmware.bin`;
  - never public: `/health`, `/ok`, `/issues`.
- **`/panel/frame`:**
  - The ETag is a short hash of the frame bytes, so `If-None-Match` gets a 304 with no body. That covers identical frames between page flips and quiet hours.
  - It records the last fetch and the `X-Panel` diagnostics header. The header is parsed at the edge into four integers (firmware version, uptime, RSSI, minimum free heap) with bounds; only those integers are stored and shown. Nothing from a panel is ever stored or displayed as a string.
- **Firmware files:** `DATA_DIR/firmware/<channel>/firmware.txt|.bin`. The channel is set per panel (`stable` default, or `canary`) and is resolved for identified panels; the rescue channel (no identity) always gets `stable`. `firmware.txt` is served `no-store`; `firmware.bin` is fetched as `/panel/firmware.bin?v=<version>` and served `Cache-Control: public, max-age=86400`, so Cloudflare's edge caches the 1.8 MB download instead of the homelab serving it per panel.

### 5. Settings page — new `settings.py`
- The form is generated from the `Config` field metadata (scope `panel`, grouped and labeled). It's server-rendered and every value is `html.escape`d. A paste helper accepts "52.52, 13.40".
- **Saving** goes through `fetch()` with a JSON body (`application/json` only) and the `Origin` check above, on both listeners. Then:
  - only panel keys are accepted (a test posts `DATA_DIR`, `OPENSKY_CLIENT_SECRET`, `PUBLIC_PORT` and expects 400 with the DB unchanged);
  - values are validated with the same parsers as the CLI;
  - the settings are saved to the DB and that one panel is rebuilt live.
- **Calibration block:** a live table of nearby planes from the friend's own `/display.json` (callsign, type, direction, elevation, distance, in view), refreshed every 5 s. Buttons "als Anfang / Ende des Sichtfelds" and "als Mindesthöhe" fill the form. The table is built with `textContent` only: callsigns and types come from ADS-B and adsbdb, i.e. from strangers.

### 6. CLI — new `panels.py` (`python -m flightpanel.panels`)
- Commands:
  - `list`: panels, identities, last fetch, firmware, signal;
  - `keys`: panel settings with defaults and labels, from the metadata;
  - `show <id>`, `set <id> KEY=VALUE…`, `unset <id> KEY…`;
  - `add <id> [--name]`, `remove <id> --yes`;
  - `email <id> <addr>`, `token <id> <client-id>`, `unlink <addr|client-id>`;
  - `channel <id> stable|canary`;
  - `backup <file>` (SQLite backup API).
- `remove` and `unlink` print what the server cannot do itself: delete the service token in Zero Trust, remove the email from the Access group, factory-reset the panel (its NVS holds the secret in plain flash).
- It writes the DB while the server runs (WAL); the server applies changes at the next poll.

## Firmware (`firmware/`) — one build, features by configuration

### 1. Configuration and setup portal
- **NVS (Preferences)** holds Wi‑Fi SSID and password, server base URL, and the Access client ID and secret. Flash is not encrypted: anyone with the device in hand can read these, which is why a removed friend's token also gets deleted at Cloudflare (CLI checklist) and why a token only ever grants that panel's own frame.
- **`secrets.h` becomes optional:** if present (bench and dev), it supplies defaults and the ArduinoOTA password. ArduinoOTA is compiled in only when `OTA_PASSWORD` is set, so release builds for friends have none. In station mode a release build opens **no listening socket** at all (no OTA, no mDNS, no web server); the portal runs only in AP mode.
- **Setup portal**, built from core libraries only (WebServer, DNSServer; no new dependency). Two variants, decided at open time:
  - **Outage portal** (opens after 5 min without Wi‑Fi, e.g. a router swap): Wi‑Fi fields only. It cannot change the server URL or the token, so nobody within radio range can redirect the panel or extract its secret while a friend's router reboots. It keeps retrying the stored Wi‑Fi and closes on connect, or after 10 min.
  - **Full portal** (BOOT held 3 s at power-on, or nothing configured yet): Wi‑Fi plus an "Erweitert" section with server, client ID and secret, and a factory reset. The secret can only be overwritten, never shown. Changing the server URL wipes the stored token.
  - The access point is `flightpanel-XXXX` with an 8-digit password from `esp_random()` taken after the radio is up, shown on the LED panel.
  - The page lists SSIDs escaped.
- **Self-hosters** enter Wi‑Fi and `http://<server>:8080` on their phone. For friends, you enter server and token before handing over; they only enter Wi‑Fi.

### 2. Fetching (core: Arduino-ESP32 2.0.17 in the pinned platform 7.1.3)
- `/panel/frame` with `If-None-Match` and `X-Panel`. A 304 keeps the current image.
- **Reading the body:** `HTTPClient::writeToStream` into a fixed 12,292-byte sink whose `write()` returns 0 at capacity; anything other than exactly 12,292 bytes is a failure. Chunked responses work too (Cloudflare can drop `Content-Length`). The server sends `Cache-Control: no-store, no-transform`.
- **`https://` base URL:**
  - **Keep-alive** needs one global `WiFiClientSecure` and one global `HTTPClient`; local objects close the socket. Headers are re-added after every `begin()`.
  - **The Access headers are sent only over `https://`**, never over plain HTTP, so a changed or downgraded URL can't leak the secret.
  - **Trust:** `setCACert` with the concatenated roots in `firmware/certs/roots.pem`: ISRG Root X1/X2, GTS Root R1/R4, the SSL.com ECC/RSA roots, the Sectigo backup roots, plus GlobalSign R46/E46 and DigiCert Global Root G2 as reserve. The 2.0.17 bundle API can't follow Google's cross-signed chain, so no bundle. The same file feeds the server's daily self-check.
  - **TLS 1.2 only** (all this core offers). No NTP needed: the core doesn't check certificate dates.
  - **Memory:** one TLS session takes about 45–55 KB at peak, next to about 75 KB for the HUB75 double buffer, out of about 200 KB free. That leaves roughly 50–70 KB spare. Only one TLS connection at a time, updates download over the same one, and the portal never runs while TLS is up. Diagnostics report minimum free heap and the largest free block.
  - **Portability:** use APIs that also exist in Arduino 3.x (`mbedtls_md`; `WiFiClientSecure` stays as a typedef), because the next platform release moves there.
- **`http://` base URL:** plain, as today. The TLS code is never executed.
- **Known weakness, accepted:** 2.0.17 ships mbedTLS 2.28 on ESP-IDF 4.4, both past end of life. The panel's TLS and HTTP parsing is the only untrusted-input parsing on devices inside friends' networks, and a compromised homelab would be its peer. The fix is the move to Arduino 3.x (IDF 5, mbedTLS 3), listed as the follow-up after the hardware bring-up.

### 3. Signed pull updates (compiled in only if `include/fw_pubkey.h` exists — untracked, example committed)
- `FW_VERSION` is the build time as YYMMDDHHMM (UTC). Versions are compared as fixed 10-character strings, never as `int`: 2610131200 doesn't fit in 32 bits.
- **Update check**, at boot (after the first frame) and every 6 h:
  1. Fetch `/panel/firmware.txt`: a fixed four-line grammar, LF line endings, at most 512 bytes: `version` (exactly 10 digits), `size` (0 < size ≤ slot size), `sha256` (exactly 64 hex), `sig` (base64 DER ECDSA P-256 over the first three lines, at most 96 chars). Each field is read with an exact-width scan into a fixed buffer; anything else is rejected before any field is interpreted. `fw_release.sh` writes LF and the test asserts it: a CRLF manifest would silently block every update.
  2. Verify with the embedded public key (mbedtls, part of the core). Only `version > FW_VERSION` is accepted (no downgrades).
  3. Stream `/panel/firmware.bin?v=<version>` into `Update` while hashing, abort on any mismatch before `end()`, then reboot. `Update` holds back the first bytes until `end()`, so an aborted image never boots.
- **Automatic rollback:** this core's bootloader supports it.
  - `verifyRollbackLater()` returns true, so a new image starts unconfirmed.
  - The image calls `esp_ota_mark_app_valid_cancel_rollback()` only after its first successful frame fetch.
  - A crash, or 10 min without a frame (the existing restart rule), reboots into the previous image.
  - The bench test asserts `esp_ota_get_state_partition() == ESP_OTA_IMG_PENDING_VERIFY` right after an update; without that check the safety net is imaginary.
- **Rescue mode (stale root list):** if TLS fails with a certificate-verify error (`MBEDTLS_ERR_X509_CERT_VERIFY_FAILED`, not a connection error) for more than 24 h, the panel fetches **only** the manifest and the image with certificate verification off and **no token**, trusting the ECDSA signature, the SHA-256 and the monotonic version. A new release carrying the updated root list then restores normal operation without a USB visit. `setInsecure()` may appear only in this function (CI grep).
- **`tools/fw_release.sh`:**
  - builds in the pinned Docker toolchain (like `check_firmware.sh`), without `secrets.h`;
  - writes the manifest and signs it with `openssl dgst -sha256 -sign`; the key is passphrase-protected, lives outside the repo and outside any synced folder, has an offline backup, and the passphrase is typed by a human: AI agents never run this script;
  - copies the result to `data/firmware/canary/`.

  Your panel is on `canary`; promotion means copying canary to stable. Anyone building from GitHub uses their own key or none, never yours.
- **Partition table:** `min_spiffs.csv`, with two 1.875 MB app slots instead of 1.25 MB. That leaves room for TLS and the portal now, and for Arduino 3.x later (about +9%). It needs one USB flash, before panels go out.
- **Safety net:** rollback plus the canary step on your own panel; rescue mode for trust-list changes; USB is the last resort.
- **Host-side tests (`firmware/test`, PlatformIO `native` env, run by `check_firmware.sh`):** the manifest parser and version compare are in a header compiled on the host, tested with a committed golden manifest + signature, and with truncated, oversized, CRLF, wrong-width, garbage and old-version cases.

## Infrastructure (manual; new German guide `docs/freunde.md`)
- **Cloudflare account:** hardware 2FA; no stored API tokens with Access scope; cloudflared only on the networks it must reach. A compromised account can add identities, but the app's allowlist still rejects them; it could re-point the tunnel at other homelab services, which is a homelab-wide concern, not a flightpanel one.
- **Cloudflare zone:**
  - use a first-level hostname, `flightpanel.<domain>`; Universal SSL doesn't cover deeper levels;
  - Bot Fight Mode off: it can't be skipped per rule and would challenge the panels;
  - minimum TLS version ≤ 1.2, because the ESP32 core speaks only 1.2.
- **Cloudflare Access:** one self-hosted app for `flightpanel.<domain>` with two policies:
  - Allow: an email group "flightpanel";
  - Service Auth: the panel tokens, **listed individually** (no "Any Access Service Token").

  A second, path-scoped app `flightpanel.<domain>/panel/firmware` with a Bypass policy for everyone: those two files are signed, contain no secrets, and are the rescue channel. This is the **only** Bypass anywhere. Other settings:
  - strict service-token mode;
  - cookie SameSite set to Lax explicitly (the docs are unclear about the default), with the binding cookie on;
  - at most 50 service tokens per account;
  - tokens valid as long as Cloudflare allows (the API takes an arbitrary duration string; try 10 years, fall back to 2), with the "Expiring Access Service Token" alert, and **Refresh** before expiry (same ID and secret, so panels need no change).
- **Network: cloudflared talks to flightpanel directly, without the Traefik hop.**
  - A dedicated Docker network `flightpanel-public` connects only cloudflared and flightpanel.
  - Ingress rules, in order: `flightpanel.<domain>` path `^/panel/firmware` → `http://flightpanel:8081` without Access; then `flightpanel.<domain>` → `http://flightpanel:8081` with `originRequest.access: {required: true, teamName, audTag}` ("Protect with Access" in the dashboard).
  - The container gets the label `traefik.enable=false`. The Dockerfile's `EXPOSE 8080` would otherwise let Traefik's Docker provider expose the unauthenticated LAN port.
  - 8081 is never published on the host.
  - All of this goes in an untracked `compose.override.yml`; the repo gets `compose.public.example.yml` with placeholders.
- **Container hardening (in `compose.yml`, for everyone):** runs as uid 1000, `read_only: true` with `tmpfs: /tmp`, `cap_drop: [ALL]`, `security_opt: [no-new-privileges]`; `data/` owned by uid 1000. The README's quickstart stays `docker compose up`.
- **`.env.example`:** only server settings. The remote switches are commented out, with a pointer to `docs/freunde.md`. The README's setup section shows the self-hoster flow (`panels set …`) and links the optional guide.
- **Onboarding checklist for a friend:**
  1. Create the service token.
  2. `panels add`, then `token` and `email`.
  3. Add their email to the Access group.
  4. Enter server and token in the panel's full portal (BOOT held).
  5. Hand it over; the friend enters Wi‑Fi, logs in, and sets position and view.
- **`.gitignore` additions:** `data/flightpanel.db*`, `data/*.imported`, `data/firmware/`, `compose.override.yml`, `firmware/include/fw_pubkey.h`.
- **No names, emails, domain or coordinates in the repo.** Docs use placeholders.

## What the owner should read personally
Small, high-value pieces where an AI mistake would matter most, together under ~400 lines:
1. `access.py` (verifier + middleware) and the route identity table.
2. The firmware manifest parser, rescue mode and the portal split.
3. `tools/fw_release.sh`.

Plus one `/security-review` (or `/code-review ultra`) after step 3 and after step 5, before any friend gets hardware.

## Order
Each step gets its own German commits, tests and golden-frame check, is pushed to `main`, and is followed by a local container rebuild. Homelab deploy only when you say so.

1. SQLite store, scoped `Config` and the `panels` CLI, with the import. Still one panel. Container hardening.
2. Shared/per-panel split and multi-panel polling.
3. Public listener, identity, rate limit, settings and calibration page, the `/panel/*` routes (ETag, diagnostics, firmware files), daily TLS self-check. `/security-review`.
4. Firmware: first bring up the **current** firmware on real hardware (it has never run on panels). Then portal, NVS, HTTPS/Access, ETag and diagnostics.
5. Firmware: signed updates, rescue mode, release tool, partition table, host-side tests. `/security-review`.
6. `docs/freunde.md`, README and `.env.example`, the compose example. You set up Cloudflare following the guide.
7. Follow-up: move the firmware to Arduino 3.x (IDF 5, maintained TLS stack).

## Maintenance calendar (what still needs a human)
| When | What | If missed |
|---|---|---|
| Before token expiry (alert + calendar entry; yearly if 10-year tokens aren't accepted) | Refresh the service tokens in Zero Trust | friends' panels go dark; safe |
| When `/ok` reports "root list stale" | `fw_release.sh` with updated `roots.pem`; panels pick it up via rescue mode | panels dark until done; safe |
| When a friend leaves | CLI checklist (token, group, factory reset) | the ex-friend can still reach the pre-auth surface, not data |
| Automatic | Access key rotation, dependency locks via escrow, Docker image digest, DB pruning, firmware rollback | — |

An uptime monitor on the LAN `/ok` (e.g. Uptime Kuma in the homelab) turns every "safe failure" above into one notification instead of a dark panel discovered weeks later.

## Verification
- **pytest and ruff in Docker** (`flightpanel-ci-dev`), with ruff's `S` rules enabled for `S608`. New tests:
  - schema and migrations;
  - the import from fixtures in today's format (the album must survive, `.env` panel keys arrive in `home`);
  - store round-trips; only `db.py` imports `sqlite3`;
  - `Config` scopes: every field has one, server fields come only from env, panel fields only from the DB, same error texts;
  - CLI `set`/`unset`/`keys`;
  - one poll with two panels: identical airport fields, separate stats, each issue counted once;
  - the `PANEL_MAX_KM` rule, radius caps and live apply;
  - the verifier against **external test vectors**: RFC 7515 A.2 and the Wycheproof RSA PKCS#1 2048/SHA-256 vectors, vendored into `tests/data`, plus `alg=none`, PS256, `e=3`, trailing base64 garbage;
  - the middleware: route enumeration (every route declared; 401/403 as specified; exactly two public routes), valid email, valid token, wrong `aud`/`iss`/`alg`/`kid`/`type`, `aud` as a string, `crit`, oversized or non-canonical base64, expired, email and `common_name` both present, unknown identity, token on a browser route, email on `/panel/*`, missing header, cross-panel access, rate limit;
  - `Origin`, JSON-only, the server-key rejection test for settings, CSP headers present;
  - the LAN `Host` allowlist and the LAN Cloudflare-header guard;
  - `X-Panel` parsing (garbage, overflow, escape sequences → ignored);
  - ETag/304, firmware cache headers;
  - the TLS self-check against a local test server with a cert chain outside `roots.pem` → failure reported.

  Tests that read JSON files directly get adapted: `test_dream`, `test_logos`, and the corruption test in `test_stats`.
- **Defaults off:** with an empty `.env`:
  - `access.py` isn't imported (asserted via `sys.modules`);
  - no second site starts;
  - `/settings` returns 404;
  - `/panel/firmware.*` returns 404 without files.
- **Golden frames:** the LAN panel's scenario matrix stays byte-identical across steps 1–2 (`git archive HEAD server` vs. the working tree).
- **Local container:** `MOCK=1` plus a second panel via the CLI; both render, and LAN routes work. The container runs as uid 1000 with a read-only root.
- **After deploy (your call):**
  - `curl` the hostname without a token → Cloudflare 403; `/panel/firmware.txt` without a token → 200;
  - with the token → 200, then 304;
  - a panel token on `/settings` → 403;
  - a friend's login sees only their own panel;
  - echo the incoming headers once to check whether `Cf-Access-Client-Secret` reaches the origin;
  - trigger an Access key rotation via the API and confirm the panels keep working.
- **Firmware:**
  - `tools/check_firmware.sh` builds with and without `secrets.h` / `fw_pubkey.h`, and runs the native tests.
  - The `Flash:` line stays well under the 1.875 MB slot.
  - On your panel: portal from a phone (both variants; the outage portal must not offer server/token fields), LAN HTTP, HTTPS through Cloudflare with a token, heap/DMA metrics over several hours, and an `nmap` of the panel in station mode showing no open ports.
  - Updates with a valid, a tampered, a CRLF and an older manifest; only the valid newer one installs; `PENDING_VERIFY` state observed before confirmation.
  - Rollback: a canary that can never fetch a frame must return to the previous version after the restart rule fires.
  - Rescue mode: with a deliberately wrong `roots.pem` on the bench, the panel must fetch and install a signed release after the 24 h threshold (shortened for the test) and come back online.

## Not in this plan
- An admin web UI.
- Friends at other airports.
- A map widget.
- An album shared across panels.
- Flash encryption / secure boot on the ESP32 (physical access to a panel reveals its own token only).
