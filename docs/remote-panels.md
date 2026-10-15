# Remote panels (Cloudflare Tunnel + Access)

Run panels for other people, e.g. friends living near the same airport, or family near another one ([regions](configuration.md#per-panel-settings)). Each panel sits on its owner's Wi-Fi and fetches its frames from your server over the internet, without a VPN, through Cloudflare Access and a Cloudflare Tunnel. Server and panel are covered in [configuration.md](configuration.md) and [hardware.md](hardware.md); this page only describes what remote panels add.

> **Status:** written against the Cloudflare documentation of October 2026 and not yet run through end to end. Dashboard labels change from time to time; what matters is what a setting does. Feedback welcome.

This is entirely optional. Without the remote-access settings in `.env` the server doesn't even load the code for it and opens no second port. Remote panels share one fetch per region, so their windows must be within `PANEL_MAX_KM` (default 60 km) of their airport: the server's, or one you give the panel with `panels set <id> AIRPORT=… RUNWAY=…` (owner only).

**Placeholders:** `flightpanel.example.com` is your hostname, `<team>` your Zero Trust team name, `<AUD-tag>` the audience tag of the Access application, `alice@example.com` a friend's address. Keep your real values in `.env`, `compose.override.yml`, `cloudflared.env` and the database, never in a public repository.

## Contents

- [Overview](#overview)
- [1. Cloudflare account and zone](#1-cloudflare-account-and-zone)
- [2. Cloudflare Access](#2-cloudflare-access)
- [3. Tunnel and Docker network](#3-tunnel-and-docker-network)
- [4. Enable it on the server](#4-enable-it-on-the-server)
- [5. Check](#5-check)
- [6. Onboarding a panel](#6-onboarding-a-panel)
- [7. Removing a panel](#7-removing-a-panel)
- [Firmware updates](#firmware-updates)
- [Maintenance calendar](#maintenance-calendar)

---

## Overview

```
remote panel ──HTTPS + service token──┐
friend's browser ──e-mail code────────┴─> Cloudflare Access ─tunnel─> cloudflared ─private network─> server :8081
panel in rescue mode ─HTTPS, no token─> Cloudflare (only /panel/firmware*) ─> cloudflared ─────────> server :8081
your own panel, browser at home ─HTTP─────────────────────────────────────────────────────────────> server :8080
```

The server has two ports. **:8080** is the LAN port: no login, only the LAN panel (`LAN_PANEL`, default `home`) and the server pages `/health`, `/ok` and `/issues`. **:8081** (`PUBLIC_PORT`) only exists with the settings from step 4. It is not published on the host, only cloudflared reaches it, and every request needs a verified identity, except the two firmware files.

Order of work: steps 1 to 3 once in Cloudflare and Docker, step 4 on the server, step 5 as a check. Then step 6 for each panel and step 7 when someone stops.

**What a friend gets:** a panel with its own location, view field, brightness and quiet hours; album and statistics count per panel from the day it is set up. A browser page made for phones: preview of their own panel, settings, and view-field calibration at the window. Login is a code sent by e-mail; no account, no password. They see nothing of other panels and nothing of the server itself.

**Three independent checks:**

1. **Cloudflare Access** only lets through a browser whose e-mail address is in the group and that logged in with the e-mail code, or a panel presenting its service token.
2. **cloudflared** verifies the Access token again (`access.required`) before forwarding anything.
3. **The server** verifies it a third time: signature against Cloudflare's keys, issuer, application, expiry. It only knows addresses and tokens you have assigned to a panel; everything else gets 403, even with a valid token.

In addition, `PUBLIC_PORT` lives in its own Docker network containing only the server and cloudflared, and with `PUBLIC_PORT` set the LAN port refuses requests carrying Cloudflare headers, so a tunnel accidentally pointed at port 8080 stays closed.

| Who | Paths |
|---|---|
| a friend's browser (e-mail code) | `/`, `/frame.png`, `/display.json`, `/stats`, `/idle`, with `SETTINGS_PAGE=1` also `/settings`; always only their own panel |
| a panel (service token) | `/panel/frame`, nothing else |
| anyone, no login | `/panel/firmware.txt` and `/panel/firmware.bin`: signed files without secrets, the rescue path for panels ([Firmware updates](#firmware-updates)) |
| nobody from outside | `/health`, `/ok`, `/issues` |

Which panel is meant is determined only by the login; panel IDs never appear in URLs. Each login is limited to about 2 requests per second on average. A panel reports four numbers about itself (firmware version, uptime, Wi-Fi signal, free memory); text from a panel is neither stored nor shown.

**Remaining risks:**

- Whoever physically has a panel can read the Wi-Fi password and token from the ESP32's flash, where they are stored unencrypted. The token only allows fetching that panel's frames, and you can revoke it in Cloudflare (step 7).
- The default firmware's TLS library (mbedTLS 2.28) no longer receives fixes. The panel listens on nothing on the friend's Wi-Fi, it only makes outgoing requests. An [Arduino-ESP32 3.x build](hardware.md#8-experimental-arduino-esp32-3x-build) with mbedTLS 3.6 exists but has not been run on hardware yet.
- Whoever takes over your Cloudflare account can add addresses (though the server only knows the ones you assigned) and redirect the tunnel to other services on your network. Hence the hardware key in step 1.

**What is stored:** on the server, in `data/flightpanel.db`, per panel: its settings (the window position, often accurate to a few metres), assigned addresses and client IDs, statistics, album and the four diagnostic numbers. Cloudflare logs logins. The panel holds Wi-Fi credentials and token. Keep the database and its backups private, and tell your friends what is stored.

---

## 1. Cloudflare account and zone

Once, in the Cloudflare dashboard:

- **Account:** two-factor login with a hardware key (passkey or security key, under Profile → Authentication), not an app or SMS. Don't create and keep API tokens with Access permissions "for later": whoever has one can change rules and identities.
- **Hostname:** one level below your domain, e.g. `flightpanel.example.com`. The free Universal certificate covers the domain and one level below, not deeper names like `panel.friends.example.com`.
- **Bot Fight Mode off** (Security → Settings, filter "Bot traffic"). It can't be excepted per host or path and would challenge the panels as bots. It applies to the whole zone; if something else there depends on it, use a separate domain for flightpanel. No other challenge may apply to the host either (Under Attack mode, "Managed Challenge" in WAF rules): a panel can't solve a CAPTCHA.
- **Minimum TLS version at most 1.2** (SSL/TLS → Edge Certificates → Minimum TLS Version). The ESP32 firmware only speaks TLS 1.2; with 1.3 as minimum all panels would be locked out.

---

## 2. Cloudflare Access

Zero Trust is set up in the account (team name chosen). Menu paths are under Zero Trust → Access controls unless stated otherwise.

**Group** (Policies → Rule groups → Add a group): name `flightpanel`, Include → Emails, initially only your own address. Friends' addresses are added here later.

**Application** (Applications → Create new application → Self-hosted and private):

1. Public hostname `flightpanel.example.com`, path empty (applies to everything).
2. Two policies:
   - Action **Allow**, Include → Rule groups → `flightpanel`. This is the browser access.
   - Action **Service Auth**, Include → **Service Token**, selecting each panel token **individually** (step 6). Never "Any Access Service Token": that would let in every token of the account, including those for other services.
3. Login method: only the one-time PIN by e-mail.
4. Under Advanced settings → Cookie settings: set **SameSite to Lax** explicitly (the default is None) and **enable the binding cookie**. It binds the login to the browser, so a stolen cookie alone is useless.
5. Save. The application overview shows the **Application Audience (AUD) Tag**, 64 hex digits: this is `<AUD-tag>` and goes into `.env` as `ACCESS_AUD` and into the tunnel (steps 3 and 4). The team name, the part before `.cloudflareaccess.com`, is `<team>`.

**The open firmware path** (a second self-hosted application): hostname `flightpanel.example.com`, path `/panel/firmware*`. One policy: action **Bypass**, Include → Everyone. This is the **only** Bypass policy and should stay that way: Bypass turns Access off for these paths and logs nothing. The two files are signed and contain no secrets; they are the rescue path for panels whose trust list is outdated. The asterisk is needed so that both `firmware.txt` and `firmware.bin` match. The more specific application applies to these paths; step 5 checks it.

**Strict mode** (Access settings → Manage service tokens → enable **Strict service token authentication**): a request with a wrong token then gets 401 or 403 instead of a redirect to the login page, and requests with a token are decided only by Service Auth policies. This applies to the whole account; if you use it for other services, check first that their tokens are in Service Auth policies, not in Allow policies.

**Token lifetime:** when creating a token (Service credentials → Service Tokens → Create Service Token, field "Service Token Duration") choose the longest duration Cloudflare accepts, e.g. `87600h` (ten years) or, if refused, `17520h` (two years). Add a notification: Notifications → Add → "Expiring Access Service Token", which arrives about a week before expiry. Before expiry, **Refresh** each token: that extends it by a year, client ID and secret stay the same and the panels notice nothing. An account can hold at most 50 service tokens, including those of other services.

---

## 3. Tunnel and Docker network

cloudflared picks up requests from Cloudflare (one outgoing connection, nothing opened on your router) and passes them to the server, directly in a dedicated Docker network with exactly two members: server and cloudflared. Not through your reverse proxy.

**Create a tunnel** (Networking → Tunnels → Create a tunnel → Cloudflared), name `flightpanel`. Use a separate tunnel for this rather than an existing one: the tunnel token is the key to everything that runs through it. The dashboard shows it in the Docker command line (`--token …`); don't run that command, Compose does it. Put the token into a file `cloudflared.env` next to `compose.yml`, with one line `TUNNEL_TOKEN=…`, not into `.env` (the server container reads `.env` and doesn't need the token):

```bash
touch cloudflared.env && chmod 600 cloudflared.env     # then add TUNNEL_TOKEN=… with an editor
```

**Two routes** (Tunnel → Routes → Add route → Published application; the DNS record is created automatically), in this order:

| # | Hostname | Path | Service | "Protect with Access" |
|---|---|---|---|---|
| 1 | `flightpanel.example.com` | `^/panel/firmware` | `http://flightpanel:8081` | off |
| 2 | `flightpanel.example.com` | empty | `http://flightpanel:8081` | on, with `<team>` and `<AUD-tag>` |

"Protect with Access" is under "Additional application settings" and makes cloudflared verify the Access token (`access.required`). In a configuration file (locally managed tunnel) the same looks like this:

```yaml
ingress:
  - hostname: flightpanel.example.com
    path: ^/panel/firmware               # without Access: the two firmware files
    service: http://flightpanel:8081
  - hostname: flightpanel.example.com
    service: http://flightpanel:8081
    originRequest:
      access:
        required: true
        teamName: <team>
        audTag:
          - <AUD-tag>
  - service: http_status:404
```

Order matters: cloudflared uses the first matching rule, and the path is a regular expression. With the second rule on top, panels in rescue mode would get 403. Always use port **8081** (`PUBLIC_PORT`) as the service, never 8080: the LAN port has no login.

**Docker network and cloudflared container** are defined in [compose.public.example.yml](../compose.public.example.yml). Copy it to `compose.override.yml`, which Compose reads automatically next to `compose.yml`:

```bash
cp compose.public.example.yml compose.override.yml
```

Replace `<version>` and `<digest>` of the cloudflared image. Pin it by digest, never `:latest` or a bare tag, and pick a release that is at least 7 days old; the file's comments show how to get version and digest.

What the file ensures:

- There is no `ports:` entry for `PUBLIC_PORT`, and none may be added. Only 8080 is published.
- The network `flightpanel-public` has only two members. No Traefik or other service is attached.
- The server container keeps the label `traefik.enable=false` (already in `compose.yml`, don't remove it): the image exposes 8080, and a Traefik with the Docker provider would otherwise publish the port without login.
- If you already run cloudflared and want to reuse it: delete the `cloudflared` block from the file and attach your existing container to the network `flightpanel-public` (declared as `external: true` in its compose file). Its token still doesn't belong in the server's `.env`.

---

## 4. Enable it on the server

In the server's `.env`. The four remote-access settings go together: if one is missing, the server refuses to start and says which.

```bash
PUBLIC_PORT=8081
ACCESS_TEAM=<team>
ACCESS_AUD=<AUD-tag>
PUBLIC_HOST=flightpanel.example.com
SETTINGS_PAGE=1
```

| Setting | Meaning |
|---|---|
| `PUBLIC_PORT` | port for Cloudflare inside the container, here 8081. Must differ from `HTTP_PORT`. Never published. |
| `ACCESS_TEAM` | the Zero Trust team name (the part before `.cloudflareaccess.com`) |
| `ACCESS_AUD` | the AUD tag of the application from step 2 |
| `PUBLIC_HOST` | the hostname without `https://`. Saving on the settings page only works from this address. |
| `SETTINGS_PAGE` | `1` enables `/settings` for friends (and on the LAN port for the LAN panel); also works without the four settings above |

Firmware updates need no setting: if there are files in `data/firmware/<channel>/` they are served, otherwise the server answers 404.

```bash
docker compose up -d
docker compose logs --tail 20 flightpanel cloudflared
```

`up -d` now also starts cloudflared; its log shows the tunnel is up (`Registered tunnel connection`).

---

## 5. Check

Still without a panel, from a laptop. Without login, Cloudflare must refuse (302 to the login page or 403), the app must never answer. Only the firmware files come through: 200 if there are files, otherwise the app's 404. A 403 there would be wrong.

```bash
curl -s -o /dev/null -w '%{http_code}\n' https://flightpanel.example.com/
curl -s -o /dev/null -w '%{http_code}\n' https://flightpanel.example.com/panel/frame
curl -s -o /dev/null -w '%{http_code}\n' https://flightpanel.example.com/panel/firmware.txt
```

Port 8081 must not be reachable from your home network, the LAN port must:

```bash
curl -m 3 http://<server>:8081/          # must fail (connection refused or timeout)
curl http://<server>:8080/ok             # ok
docker compose ps                        # only 8080 is published
```

In the browser, `https://flightpanel.example.com/` asks for an e-mail address and sends a code. With an address from the group you'll then see "Diese Anmeldung ist nicht freigeschaltet" (this login is not enabled) until a panel is assigned to it (step 6).

| Symptom | Cause | What to do |
|---|---|---|
| server refuses to start, "… gesetzt, es fehlt …" (… set, … missing) | only some of the four settings | set all four or none |
| `/panel/firmware.txt` returns 403 | order of the tunnel routes or path of the Bypass application | step 3 and 2: route 1 on top, application path ends in `*` |
| browser: no code arrives, or Cloudflare refuses | address not in the group `flightpanel` | step 6, item 3 |
| browser: "nicht freigeschaltet" (not enabled) | address not assigned to a panel | `panels email …` (step 6, item 2) |
| panel fetches nothing, `panels list` shows no fetch | token not in the Service Auth policy, expired, or not assigned with `panels token` | Access logs in Zero Trust; step 6, items 1 and 2 |
| `/settings` returns 404 | `SETTINGS_PAGE=1` missing | `.env`, then `docker compose up -d` |
| LAN port returns 421 | a name with a dot not in `LAN_HOSTS` | [configuration.md](configuration.md#allowed-host-names-lan_hosts) |

---

## 6. Onboarding a panel

One panel, one service token, one or more addresses. The commands run on the server in the project folder.

1. **Create a service token** (Service credentials → Service Tokens → Create Service Token), name e.g. `flightpanel-alice`, longest duration. Copy client ID and secret immediately: Cloudflare shows the secret only once. Then open the **Service Auth** policy of the `flightpanel` application and add this token as a separate entry.
2. **Create and assign the panel:**
   ```bash
   docker compose exec flightpanel python3 -m flightpanel.panels add alice --name "Alice"
   docker compose exec flightpanel python3 -m flightpanel.panels token alice <client-id>
   docker compose exec flightpanel python3 -m flightpanel.panels email alice alice@example.com
   ```
   The client ID looks like `0123456789abcdef0123456789abcdef.access`. A panel can have several addresses (run `email` again), but an address belongs to one panel only. Your friend sets location and view field themselves (item 5); you can also preset them with `panels set alice HOME_LAT=<lat> HOME_LON=<lon>`. For a panel near another airport, set that airport yourself now: `panels set alice AIRPORT=FMM RUNWAY=240` (not possible from the settings page).
3. **Add the address to the group** `flightpanel` (Policies → Rule groups → `flightpanel` → Emails). Both are needed: the group gets your friend past Cloudflare, `panels email` tells the server which panel is theirs.
4. **Prepare the panel** before it leaves: flash it once over USB with firmware built without `secrets.h` and with your `fw_pubkey.h` ([hardware.md](hardware.md#7-signed-firmware-updates)), so it can receive signed updates later. Then open the setup portal: it opens by itself if nothing is set up, otherwise hold BOOT for 3 seconds at power-on (keep the button reachable when building the case). Join `flightpanel-XXXX` by scanning the QR code on the panel or with the password it shows, choose your own Wi-Fi for the test, and under **Erweitert** (Advanced) enter server `https://flightpanel.example.com`, client ID and secret. Your friend never sees client ID or secret.

   Test at home: the panel fetches its first frame through Cloudflare, and `panels list` shows "zuletzt …" (last seen) with firmware version and Wi-Fi signal. The same test without a panel:
   ```bash
   read -rs SECRET        # type the secret; it doesn't appear on screen or in the history
   curl -s -o /dev/null -w '%{http_code}\n' -H 'CF-Access-Client-Id: <client-id>' -H "CF-Access-Client-Secret: $SECRET" https://flightpanel.example.com/panel/frame
   curl -s -o /dev/null -w '%{http_code}\n' -H 'CF-Access-Client-Id: <client-id>' -H "CF-Access-Client-Secret: $SECRET" https://flightpanel.example.com/settings
   ```
   The first must return 200 (and 304 with the response's ETag as `If-None-Match`), the second 403: a panel token may only fetch `/panel/*`.
5. **Hand it over.** Your friend powers it on. It can't find your Wi-Fi, so after 5 minutes it opens the setup portal with the Wi-Fi fields only: server and token can't be changed there, so nobody within radio range can redirect the panel. (A panel handed over with no Wi-Fi stored at all opens the full portal immediately at every power-on instead, including server and token fields; the tested way above is safer.) They scan the QR code or join `flightpanel-XXXX` with the password shown, pick their Wi-Fi (2.4 GHz) and enter its password. Then they open `https://flightpanel.example.com/settings`, enter their address, type in the code from the e-mail (sent by Cloudflare; check the spam folder) and set location and view field ([calibration](configuration.md#calibrating-the-view-field)).

---

## 7. Removing a panel

First on the server, then in Cloudflare, then on the panel:

```bash
docker compose exec flightpanel python3 -m flightpanel.panels unlink alice@example.com    # just the address
docker compose exec flightpanel python3 -m flightpanel.panels unlink <client-id>          # just the token
docker compose exec flightpanel python3 -m flightpanel.panels remove alice --yes          # panel with album, statistics, addresses and token
```

`unlink` and `remove` finish by listing what the server can't do itself:

1. **Delete the service token** in Zero Trust (Service credentials → Service Tokens) and remove it from the Service Auth policy. Otherwise whoever has the panel keeps fetching until it expires.
2. **Remove the address from the group** `flightpanel`.
3. **Factory-reset the panel:** hold BOOT at power-on, then Zurücksetzen (Reset) in the portal. The token is stored unencrypted on the panel.

Until 1 and 2 are done, the former user still reaches the login page and the two firmware files, but no data: the server no longer knows them.

---

## Firmware updates

Remote panels can't be reached by OTA; they update themselves with images signed by your key. Key, release script, canary/stable channels and rollback are described in [hardware.md](hardware.md#7-signed-firmware-updates). For remote panels:

- **Remote panels always get `stable`.** Their firmware requests arrive through the open Bypass path, so the server can't tell which panel is asking. Use your own LAN panel on `canary` as the test bench.
- **Flash once over USB** before a panel leaves, with a build without `secrets.h`. That also installs the partition table with the two update slots, which no update can change.
- **When Cloudflare's certificates change:** the firmware trusts only a fixed list of root certificates (`roots.pem`). If none matches any more, the panels fail certificate verification and after 24 hours switch to [rescue mode](hardware.md#7-signed-firmware-updates), which is why the two firmware files are reachable without login. As an early warning, the server checks once a day with the same list whether the certificate of `PUBLIC_HOST` still matches, like a panel would (TLS 1.2 only, hostname verified). If not, `/ok` reports it and `/issues` records `tls_roots`, long before a panel goes dark. A mere network outage shows up as `source_error` instead and leaves `/ok` green.

**Updating the trust list.** `tools/make_roots.py` reads the CA bundle of the pinned base image and checks pinned fingerprints, so run it in that image (Git Bash or Linux, in the repository root):

```bash
IMAGE=$(grep -o 'python:3.12-slim@sha256:[0-9a-f]*' tools/check_firmware.sh)
MSYS_NO_PATHCONV=1 docker run --rm -v "$(pwd -W 2>/dev/null || pwd):/repo" -w /repo "$IMAGE" python3 -I tools/make_roots.py
```

If a root is missing or a fingerprint differs, it aborts and writes nothing; if Cloudflare moved to a new certificate authority, the root list in the script needs updating first. Commit the new `server/flightpanel/certs/roots.pem`, then publish a firmware release ([hardware.md](hardware.md#72-release-canary-stable)); the panels pick it up via rescue mode.

---

## Maintenance calendar

| When | What | If it's missed |
|---|---|---|
| before service tokens expire (Cloudflare e-mail plus a calendar entry; yearly if only one year is possible) | **Refresh** every token in Zero Trust | the remote panels go dark; harmless |
| when `/ok` reports an outdated trust list | [update the trust list](#firmware-updates) and release firmware; the panels pick it up via rescue mode | the panels stay dark until done; harmless |
| when a friend stops | the list in step 7: token, group, factory reset | the former user still reaches the login page and the firmware files, but no data |
| every few months | cloudflared: pick a version at least 7 days old and update the digest in `compose.override.yml` | keeps running, but Cloudflare eventually drops support for old versions |
| automatic | rotation of the Access keys, dependency updates, server base image, database cleanup, firmware rollback | – |

An uptime monitor on `http://<server>:8080/ok` turns each "harmless" outage above into a message instead of a dark panel you find weeks later. `/ok` only covers the LAN panel: if a friend unplugs theirs, it stays green. When each panel last fetched, with firmware version and Wi-Fi signal, is shown by `panels list` and under `panels` in `/health`.
