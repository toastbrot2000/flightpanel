# Öffentlicher Zugang über Cloudflare Access, Einstellungsseite — Implementation Plan (Schritt 3)

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Friends' panels and browsers reach the server through Cloudflare Access on a second, opt-in listener that verifies every request's Access token itself; the LAN listener gets the hardening the spec asks for (Host allowlist, Cloudflare-header guard, security headers, Origin checks); panels fetch `/panel/frame` with ETag and send diagnostics; signed firmware files are served per channel; a mobile-first settings and calibration page edits a panel's settings live. With an empty `.env` none of the remote machinery is imported or listening. The spec's daily TLS self-check moves to step 5, next to the trust list the firmware embeds (owner's call on 2026-10-14 to keep scope lean: it is useless before panels speak HTTPS).

**Architecture:** `config.py` gains the opt-in switches (`PUBLIC_PORT`, `ACCESS_TEAM`, `ACCESS_AUD`, `PUBLIC_HOST`, `SETTINGS_PAGE`, `LAN_HOSTS`). `db.py` gets identity rows and per-panel diagnostics; `panels.py` gets `email`, `token`, `unlink`, `channel`. New `access.py` (imported only when the public listener is configured) holds a stdlib RS256/JWT verifier, the Access key set and the request middleware with per-route identity classes and a per-identity rate limit. `web.py` builds two aiohttp apps from one set of handlers that differ only in how the panel is resolved (LAN: `app.lan`; public: the verified identity's panel). New `settings.py` renders the settings page from the `Config` field metadata and saves through the same parsers as the CLI; its script and styles are static files (CSP without `unsafe-inline`).

**Tech Stack:** Python 3.12 stdlib (`hashlib`, `hmac`, `base64`, `json`, `ssl`, `ipaddress`), aiohttp (already a dependency), pytest + ruff in the Docker image `flightpanel-ci-dev`. No new dependencies.

**Spec:** `docs/superpowers/specs/2026-10-13-freunde-multi-tenant-design.md` — "Opt-in switches", "Architecture → the two listeners", "Server §3 Identity on the public listener", "§4 Routes", "§5 Settings page", "§6 CLI" (identity commands), "Order → step 3" (without the daily TLS self-check, moved to step 5) and its follow-up (`POST /issues/clear` gets the Origin check), "Verification" (the access, middleware, settings, LAN and defaults-off tests). Step 2 (`docs/superpowers/plans/2026-10-14-mehrere-panels.md`) is done: `App.panels`, `App.lan`, `Panel` (panel.py), `App._sync_panels()`, `Config.for_panel(..., remote=)`, `config.too_far()`.

## Decisions taken while planning (rulings; the spec stays the authority)

1. **Token size cap 8 KB, not 1 KB.** The spec says "at most 1 KB", but an Access user token with `email`, `sub`, `identity_nonce`, `country` and a 2048-bit signature is already about 1 KB, and the optional `custom` claim adds up to 1 KB more. 8 KB is still a hard bound (aiohttp's own header limit is 8190 bytes).
2. **Client IDs** must look like Cloudflare's: 32 lowercase hex characters plus `.access`. Emails are lower-cased and checked loosely (`x@y.z`, at most 254 characters).
3. **Firmware channel on the public routes.** Cloudflare's path-scoped Bypass app means `/panel/firmware.*` arrives without an Access token, so those routes serve `stable`; a valid token, if one arrives anyway, selects its panel's channel. On the LAN, the LAN panel's channel applies (the owner's panel is the canary).
4. **LAN Host allowlist** accepts IP literals, `localhost`, single-label names (`nas`), `*.local`, `*.home.arpa` and every name in `LAN_HOSTS`. Single-label names cannot be registered in public DNS, so a rebinding page cannot use them; refusing them would lock the owner out of `http://nas:8080`. Anything else gets 421 with a German hint naming `LAN_HOSTS`.
5. **Origin checks.** Settings writes require an `Origin` header that passes (public: exactly `https://<PUBLIC_HOST>`; LAN: a host the LAN allowlist accepts) and `Content-Type: application/json`. `POST /issues/clear` rejects a foreign `Origin` but accepts a request without one, so the README's `curl -X POST …/issues/clear` keeps working (browsers always send `Origin` on POST).
6. **The daily TLS self-check moves to step 5** (with the trust list `roots.pem` the firmware embeds). Note for then: the Docker build context is `server/`, so the trust list belongs in the server package (`server/flightpanel/certs/roots.pem`) and the firmware embeds it by relative path.
7. **Rate limits.** Per identity 2 requests/s, burst 10 (spec). The two public firmware routes share one bucket of 5/s, burst 20, keyed by nothing: they are signed static files, cached by Cloudflare's edge.
8. **Static assets** (`preview.js`, `preview.css`, `settings.js`, `settings.css`) are served from a fixed name list read at import time, never from a directory path, on both listeners; on the public listener they are `email`-class routes.

## Global Constraints

- No new Python dependencies. Only `server/flightpanel/db.py` imports `sqlite3`. Every SQL statement parameterized (ruff `S608`).
- **Opt-in, nothing loads when off:** with no public switch set, `flightpanel.access` is never imported (a test asserts `sys.modules`), no second site starts, `/settings` answers 404 unless `SETTINGS_PAGE=1`, `/panel/firmware.*` answer 404 without files. The self-hoster flow and the LAN panel's frames stay as after step 2 (golden frames byte-identical).
- **Deny by default on the public listener:** every route has an explicit identity class (`email`, `token`, `public`); a route without one is refused; any exception during verification is 401; panel IDs never appear in public URLs; nothing from a panel or a token is ever logged or stored as a string (diagnostics are four bounded integers).
- Tokens, client secrets and full request headers are never logged. Error texts go through `issues.error_text` (no URLs).
- Code comments, docstrings, commit messages and docs are **German**; identifiers English; line length 120; `filterwarnings = error::DeprecationWarning:flightpanel.*`.
- No names, emails, domains, team names, AUD tags or coordinates of real people in the repo; tests use `example.com`, made-up team names (`beispiel`) and made-up AUD tags.
- Work happens in the worktree `.claude/worktrees/freunde-sqlite` (branch `worktree-freunde-sqlite`); never `cd`/`git -C` to the main checkout; never bare `git stash`; commit per task with a German message and the trailer `Co-Authored-By: Claude <Model> <noreply@anthropic.com>` naming the model that wrote it; do not push. Never start/stop Docker Desktop; never touch the `flightpanel` container or port 8080.
- **Running tests** (Git Bash, worktree root): `MSYS_NO_PATHCONV=1 docker run --rm -v "$(pwd -W):/repo" -w /repo flightpanel-ci-dev sh -c "ruff check . && python -m pytest -p no:cacheprovider server/tests/test_access.py"`; the whole suite without a path. No extra `-q` (pyproject adds one).

## Review Focus

1. **A forged, replayed or mis-addressed Access token** (`alg` none or PS256, wrong `aud`/`iss`/`type`, expired, `aud` as a string, `crit` present, non-canonical base64, trailing garbage, `e=3` key, email and `common_name` both present): every one must answer 401/403 and never reach a handler — pinned in Task 3 and Task 6.
2. **A request that reaches the wrong panel's data** (friend A's browser asking for friend B's frame, a panel token on a browser page, an email on `/panel/frame`, an identity not in the allowlist): 403, never another panel's content — pinned in Task 6 (`test_cross_panel_access_is_impossible`).
3. **The LAN port wired into the tunnel by mistake, or a malicious web page in the LAN** (DNS rebinding with a foreign Host, a cross-site POST to `/issues/clear` or `/settings`): 403/421, nothing changed — pinned in Task 5 and Task 7.
4. **A friend's sloppy settings input on the phone** (comma decimals, a server key smuggled into the JSON, a home 200 km away, HTML in a value): rejected with a German message next to the field, database unchanged, every value escaped when shown — pinned in Task 7.
5. **Cloudflare rotating its signing key, or its key endpoint misbehaving** (redirect, huge or malformed answer, one bad key among good ones): an unknown `kid` refreshes the key set at most once a minute, the last good set keeps working, a bad key is skipped alone — pinned in Task 4.

## File Structure

| File | Responsibility |
|---|---|
| `server/flightpanel/config.py` | public switches, `Config.public`, LAN host list |
| `server/flightpanel/db.py` | identity rows, panel diagnostics, firmware channel |
| `server/flightpanel/panels.py` | `email`, `token`, `unlink`, `channel`; `list` with identities and diagnostics; checklists after `remove`/`unlink` |
| `server/flightpanel/access.py` (new, public only) | strict base64url, JWK keys, RS256 (PKCS#1 v1.5, constant-time), JWT claims, `KeySet` (fetch, persist, refresh), middleware with identity classes and rate limit |
| `server/flightpanel/web.py` | one set of handlers, LAN app and public app, security headers, Host allowlist, Cloudflare-header guard, Origin checks, `/panel/frame` (ETag, X-Panel), firmware files, static assets |
| `server/flightpanel/panel.py` | per-panel diagnostics (`Panel.diag`), `esp32_gap` key per panel |
| `server/flightpanel/settings.py` (new) | settings page from `Config` metadata, JSON save with the same parsers |
| `server/flightpanel/static/` (new) | `preview.js`, `preview.css`, `settings.js`, `settings.css` |
| `server/flightpanel/main.py` | public site and its key-refresh loop only when configured; diagnostics saved; `/health` access block |
| tests: `test_access.py`, `test_public.py`, `test_settings.py` (new), `tests/data/` vectors and test key; `test_config.py`, `test_db.py`, `test_panels.py`, `test_web.py`, `test_app.py` (extended) | |

---

### Task 1: the opt-in switches in `config.py`

**Files:**
- Modify: `server/flightpanel/config.py`, `.env.example`
- Test: `server/tests/test_config.py`

**Interfaces:**
- Produces: server fields `public_port: int` (`PUBLIC_PORT`, 0 = off), `access_team: str` (`ACCESS_TEAM`), `access_aud: str` (`ACCESS_AUD`), `public_host: str` (`PUBLIC_HOST`), `settings_page: bool` (`SETTINGS_PAGE`), `lan_hosts: tuple[str, ...]` (`LAN_HOSTS`); property `Config.public -> bool`; `config.PUBLIC_KEYS` (the four field names). `Config.from_env` raises `ValueError` for a partial public setup or `PUBLIC_PORT == HTTP_PORT`.

- [ ] **Step 1: Write the failing tests** — append to `server/tests/test_config.py`:

```python
PUBLIC = {"PUBLIC_PORT": "8081", "ACCESS_TEAM": "Beispiel", "ACCESS_AUD": "a" * 64,
          "PUBLIC_HOST": "flightpanel.example.com"}


def test_the_public_listener_is_off_by_default():
    cfg = Config.from_env({"DATA_DIR": DATA})
    assert not cfg.public and not cfg.settings_page and cfg.lan_hosts == ()


def test_the_public_listener_needs_all_four_settings():
    cfg = Config.from_env({**PUBLIC, "DATA_DIR": DATA})
    assert cfg.public and cfg.public_port == 8081
    assert (cfg.access_team, cfg.public_host) == ("beispiel", "flightpanel.example.com")   # klein geschrieben
    for missing in PUBLIC:
        partial = {k: v for k, v in PUBLIC.items() if k != missing}
        with pytest.raises(ValueError, match=f"es fehlt {missing}"):
            Config.from_env({**partial, "DATA_DIR": DATA})
    with pytest.raises(ValueError, match="anderer Port"):
        Config.from_env({**PUBLIC, "PUBLIC_PORT": "8080", "DATA_DIR": DATA})


@pytest.mark.parametrize(("key", "raw"), [("ACCESS_AUD", "abc"), ("ACCESS_AUD", "g" * 64),
                                          ("ACCESS_TEAM", "zwei worte"), ("ACCESS_TEAM", "-x"),
                                          ("PUBLIC_HOST", "localhost"), ("PUBLIC_HOST", "https://x.example.com")])
def test_public_settings_are_checked(key, raw):
    with pytest.raises(ValueError, match=f"{key}="):
        Config.from_env({**PUBLIC, key: raw, "DATA_DIR": DATA})


def test_lan_hosts_are_a_list_of_names():
    cfg = Config.from_env({"LAN_HOSTS": "nas.fritz.box, Laptop", "DATA_DIR": DATA})
    assert cfg.lan_hosts == ("nas.fritz.box", "laptop")
    with pytest.raises(ValueError, match="LAN_HOSTS="):
        Config.from_env({"LAN_HOSTS": "nas/x", "DATA_DIR": DATA})
```

- [ ] **Step 2: Run them to see them fail** (`AttributeError: 'Config' object has no attribute 'public'`).

- [ ] **Step 3: Implement**

In `config.py`, after `_elev` add the text parsers:

```python
def _matching(pattern: str, hint: str):
    """Text in Kleinbuchstaben, der ganz auf pattern passt, sonst ValueError mit hint (wie die anderen Parser)."""
    rx = re.compile(pattern)

    def parse(s: str) -> str:
        v = s.strip().lower()
        if not rx.fullmatch(v):
            raise ValueError(hint)
        return v
    return parse


_LABEL = r"[a-z0-9](?:[a-z0-9-]{0,61}[a-z0-9])?"
_team = _matching(_LABEL, "der Team-Name aus Zero Trust, etwa beispiel (aus beispiel.cloudflareaccess.com)")
_aud = _matching(r"[0-9a-f]{64}", "das Application Audience (AUD) Tag der Access-Anwendung, 64 Hexziffern")
_host = _matching(rf"(?:{_LABEL}\.)+[a-z]{{2,63}}", "ein Hostname mit Punkt, etwa flightpanel.example.com, ohne https://")
_name = _matching(rf"{_LABEL}(?:\.{_LABEL})*", "ein Hostname wie nas oder nas.fritz.box")


def _names(s: str) -> tuple[str, ...]:
    """"nas.fritz.box, Laptop" -> ("nas.fritz.box", "laptop")."""
    return tuple(_name(x) for x in s.split(",") if x.strip())
```

New fields, in a new section after "Betrieb" (before the classmethods):

```python
    # --- Zugang von außen (optional, docs/freunde.md): ein zweiter Port nur für Cloudflare Access. Alle vier oder
    # keiner; ohne sie lädt der Server nichts davon (access.py).
    public_port: int = _env("PUBLIC_PORT", 0, _int(0, 65535))
    access_team: str = _env("ACCESS_TEAM", "", _team)
    access_aud: str = _env("ACCESS_AUD", "", _aud)
    public_host: str = _env("PUBLIC_HOST", "", _host)
    settings_page: bool = _env("SETTINGS_PAGE", False)          # Einstellungsseite /settings
    lan_hosts: tuple[str, ...] = _env("LAN_HOSTS", (), _names)  # weitere Namen, unter denen :8080 erreichbar ist
```

`from_env`, after `_airport_from_list(...)`: `_check_public(values)`. Module level (after `_env_name`):

```python
PUBLIC_KEYS = ("public_port", "access_team", "access_aud", "public_host")


def _check_public(values: dict) -> None:
    """Der Zugang von außen braucht alle vier Angaben. Ein Teil davon ist ein Tippfehler oder eine halbe
    Einrichtung: lieber nicht starten als still ohne oder halb offen laufen."""
    have = [k for k in PUBLIC_KEYS if values.get(k)]
    if not have:
        return
    missing = [_env_name(k) for k in PUBLIC_KEYS if k not in have]
    if missing:
        raise ValueError(f"{', '.join(_env_name(k) for k in have)} gesetzt, es fehlt {', '.join(missing)}: für den "
                         "Zugang von außen alle vier setzen (docs/freunde.md), sonst keinen")
    if values["public_port"] == values.get("http_port", Config.http_port):
        raise ValueError("PUBLIC_PORT muss ein anderer Port sein als HTTP_PORT")
```

Property (after `fetch_radius_km`):

```python
    @property
    def public(self) -> bool:
        """Ist der Zugang von außen eingerichtet (PUBLIC_PORT und die drei anderen, _check_public)?"""
        return bool(self.public_port)
```

`.env.example`, at the end of "Betrieb":

```
# Unter welchen Namen der LAN-Port erreichbar ist, außer IP-Adressen, localhost, Namen ohne Punkt,
# *.local und *.home.arpa (gegen DNS-Rebinding aus dem Browser): etwa nas.fritz.box
# LAN_HOSTS=
# Einstellungsseite /settings für das Panel am LAN-Port (Standort, Sichtfeld, Eichen am Fenster)
# SETTINGS_PAGE=1

# --- Zugang von außen für Panels von Freunden (Cloudflare Access), siehe docs/freunde.md ---
# Alle vier oder keiner. Ohne sie läuft nichts davon.
# PUBLIC_PORT=8081
# ACCESS_TEAM=
# ACCESS_AUD=
# PUBLIC_HOST=
```

- [ ] **Step 4: Run the whole suite**, all green.

- [ ] **Step 5: Commit** — `Zugang von außen: Schalter in .env, alle vier oder keiner` (body: what the switches do, that nothing loads without them).

---

### Task 2: identities, diagnostics and channels in the database and the CLI

**Files:**
- Modify: `server/flightpanel/db.py`, `server/flightpanel/panels.py`
- Test: `server/tests/test_db.py`, `server/tests/test_panels.py`

**Interfaces:**
- Produces (`Db`): `identity_add(kind, value, pid)` (ValueError if taken or the panel is missing), `identity_panel(kind, value) -> str | None`, `identity_list() -> list[dict]` (`kind, value, panel_id`, sorted by panel, kind, value), `identity_remove(value) -> list[dict]` (removed rows), `panel_set_channel(pid, channel)` (bumps `updated`), `panel_seen(pid, seen_at, diag: dict)` (does **not** bump `updated`). `PANEL_SELECT` also returns `seen_at, fw_version, uptime_s, rssi, min_heap`. CLI: `email <id> <address>`, `token <id> <client-id>`, `unlink <address|client-id>`, `channel <id> stable|canary`; `list` shows identities, channel and diagnostics; `remove` and `unlink` print what is still to do in Zero Trust and on the panel. `panels.EMAIL`, `panels.CLIENT_ID` (regexes), `panels.CHANNELS = ("stable", "canary")`.

- [ ] **Step 1: Write the failing tests**

Append to `server/tests/test_db.py`:

```python
def test_identities_belong_to_one_panel_and_go_with_it(tmp_path):
    db = Db(str(tmp_path / "f.db"))
    db.panel_add("home")
    db.panel_add("anna")
    db.identity_add("email", "anna@example.com", "anna")
    db.identity_add("token", "0" * 32 + ".access", "anna")
    assert db.identity_panel("email", "anna@example.com") == "anna"
    assert db.identity_panel("token", "anna@example.com") is None                 # Art gehört dazu
    with pytest.raises(ValueError, match="gehört schon zu Panel anna"):
        db.identity_add("email", "anna@example.com", "home")
    with pytest.raises(ValueError, match="gibt es nicht"):
        db.identity_add("email", "b@example.com", "nirgends")
    assert [r["value"] for r in db.identity_list()] == ["anna@example.com", "0" * 32 + ".access"]
    assert db.identity_remove("anna@example.com") == [{"kind": "email", "value": "anna@example.com",
                                                       "panel_id": "anna"}]
    db.panel_remove("anna")                                                        # samt Token
    assert db.identity_list() == []


def test_diagnostics_do_not_count_as_a_settings_change(tmp_path):
    db = Db(str(tmp_path / "f.db"))
    db.panel_add("home")
    before = db.panel_get("home")["updated"]
    db.panel_seen("home", 1791654544.0, {"fw": 2610131200, "up": 60, "rssi": -61, "heap": 45000})
    p = db.panel_get("home")
    assert (p["seen_at"], p["fw_version"], p["uptime_s"], p["rssi"], p["min_heap"]) == (
        1791654544.0, 2610131200, 60, -61, 45000)
    assert p["updated"] == before
    db.panel_set_channel("home", "canary")
    assert db.panel_get("home")["fw_channel"] == "canary" and db.panel_get("home")["updated"] >= before
```

Append to `server/tests/test_panels.py`:

```python
TOKEN = "0123456789abcdef0123456789abcdef.access"


def test_email_token_unlink(run):
    run("add", "anna")
    code, out, _ = run("email", "anna", " Anna@Example.com ")
    assert code == 0 and "anna@example.com" in out and "Access-Gruppe" in out
    assert run("token", "anna", TOKEN.upper().replace(".ACCESS", ".access"))[0] == 0      # klein gespeichert
    assert run("email", "home", "anna@example.com")[0] == 2                            # gehört schon zu anna
    assert run("email", "anna", "keine-adresse")[0] == 2
    assert run("token", "anna", "abc.access")[0] == 2
    _, out, _ = run("list")
    assert "anna@example.com" in out and TOKEN in out
    code, out, _ = run("unlink", TOKEN)
    assert code == 0 and "Service-Token in Zero Trust löschen" in out and "Werkseinstellungen" in out
    assert run("unlink", TOKEN)[0] == 2                                               # schon weg


def test_remove_prints_what_is_left_to_do(run):
    run("add", "anna")
    run("email", "anna", "anna@example.com")
    run("token", "anna", TOKEN)
    code, out, _ = run("remove", "anna", "--yes")
    assert code == 0 and "Access-Gruppe" in out and "Service-Token in Zero Trust löschen" in out


def test_channel(run):
    assert run("channel", "home", "canary")[0] == 0
    assert "canary" in run("list")[1]
    assert run("channel", "home", "beta")[0] == 2
```

- [ ] **Step 2: Run them to see them fail.**

- [ ] **Step 3: Implement in `db.py`**

`PANEL_SELECT` gets the diagnostic columns:

```python
PANEL_SELECT = ("SELECT id, name, settings, fw_channel, created, updated, stats_since, last_obs, last_in_view, "
                "seen_at, fw_version, uptime_s, rssi, min_heap FROM panels")
```

After `panel_remove`:

```python
    def panel_set_channel(self, pid: str, channel: str) -> None:
        """Firmware-Kanal (stable, canary). Gilt als Änderung der Einstellungen (updated): main.App zieht ihn nach."""
        if self.execute("UPDATE panels SET fw_channel = ?, updated = ? WHERE id = ?",
                        (channel, time.time(), pid)).rowcount == 0:
            raise ValueError(f"Panel {pid} gibt es nicht")

    def panel_seen(self, pid: str, seen_at: float, diag: dict) -> None:
        """Letzter Abruf des ESP32 und seine Diagnose (vier Zahlen, web.parse_diag), für panels list. Keine Änderung
        der Einstellungen: updated bleibt, sonst baute main.App das Panel bei jedem Speichern neu."""
        self.execute("UPDATE panels SET seen_at = ?, fw_version = ?, uptime_s = ?, rssi = ?, min_heap = ? WHERE id = ?",
                     (seen_at, diag.get("fw"), diag.get("up"), diag.get("rssi"), diag.get("heap"), pid))

    # ------------------------------------------------------- Zugang --
    # Wer welches Panel sehen darf (access.py): E-Mail-Adressen (Browser) und Client-IDs (Service-Tokens der Panels).
    # Eine Adresse gehört zu höchstens einem Panel; ein Panel darf mehrere haben.
    def identity_add(self, kind: str, value: str, pid: str) -> None:
        try:
            self.execute("INSERT INTO identities (kind, value, panel_id) VALUES (?, ?, ?)", (kind, value, pid))
        except sqlite3.IntegrityError:
            other = self.identity_panel(kind, value)
            raise ValueError(f"{value} gehört schon zu Panel {other}" if other
                             else f"Panel {pid} gibt es nicht") from None

    def identity_panel(self, kind: str, value: str) -> str | None:
        row = self.one("SELECT panel_id FROM identities WHERE kind = ? AND value = ?", (kind, value))
        return row["panel_id"] if row else None

    def identity_list(self) -> list[dict]:
        return [dict(r) for r in self.query("SELECT kind, value, panel_id FROM identities "
                                            "ORDER BY panel_id, kind, value")]

    def identity_remove(self, value: str) -> list[dict]:
        """Entfernt eine E-Mail-Adresse oder Client-ID, gleich welcher Art; gibt die entfernten Zeilen zurück."""
        gone = [dict(r) for r in self.query("SELECT kind, value, panel_id FROM identities WHERE value = ?", (value,))]
        self.execute("DELETE FROM identities WHERE value = ?", (value,))
        return gone
```

(`identity_list` sorts `email` before `token` alphabetically, which the test relies on.)

- [ ] **Step 4: Implement in `panels.py`**

Module docstring: add the four commands to the list:

```
  email <id> <adresse>        darf das Panel im Browser sehen und einstellen (Cloudflare Access)
  token <id> <client-id>      Service-Token eines Panels von außen (Cloudflare Access)
  unlink <adresse|client-id>  wieder weg
  channel <id> stable|canary  Firmware-Kanal
```

Constants and helpers (after `CARD_IDS`):

```python
EMAIL = re.compile(r"[^@\s]+@[^@\s]+\.[^@\s]+")
CLIENT_ID = re.compile(r"[0-9a-f]{32}\.access")          # wie Zero Trust sie anzeigt
CHANNELS = ("stable", "canary")


def _todo(rows: list[dict]) -> None:
    """Was der Server nicht selbst kann, nachdem eine Adresse oder ein Token weg ist."""
    if any(r["kind"] == "email" for r in rows):
        print("Noch zu tun: die Adresse in Zero Trust aus der Access-Gruppe der flightpanel-Anwendung nehmen.")
    if any(r["kind"] == "token" for r in rows):
        print("Noch zu tun: den Service-Token in Zero Trust löschen (sonst ruft, wer das Panel hat, weiter ab, bis er "
              "abläuft) und das Panel auf Werkseinstellungen zurücksetzen (BOOT beim Einschalten halten, Erweitert): "
              "Der Token steht unverschlüsselt in seinem Speicher.")
```

Commands:

```python
def cmd_email(db: Db, cfg: Config, args) -> None:
    p = _need(db, args.id)
    address = args.address.strip().lower()
    if len(address) > 254 or not EMAIL.fullmatch(address):
        raise ValueError(f"{args.address!r} ist keine E-Mail-Adresse")
    db.identity_add("email", address, p["id"])
    print(f"{address} darf Panel {p['id']} im Browser sehen und einstellen. In Zero Trust die Adresse in die "
          "Access-Gruppe der flightpanel-Anwendung aufnehmen (docs/freunde.md).")


def cmd_token(db: Db, cfg: Config, args) -> None:
    p = _need(db, args.id)
    client_id = args.client_id.strip().lower()
    if not CLIENT_ID.fullmatch(client_id):
        raise ValueError(f"{args.client_id!r} ist keine Client-ID eines Service-Tokens (32 Hexziffern und .access)")
    db.identity_add("token", client_id, p["id"])
    print(f"Panel {p['id']} ruft mit diesem Service-Token ab. Client-ID und Secret ins Setup-Portal des Panels "
          "(BOOT beim Einschalten halten, Erweitert).")


def cmd_unlink(db: Db, cfg: Config, args) -> None:
    value = args.value.strip().lower()
    gone = db.identity_remove(value)
    if not gone:
        raise ValueError(f"{value} gehört zu keinem Panel")
    for r in gone:
        print(f"{value} gehört nicht mehr zu Panel {r['panel_id']}")
    _todo(gone)


def cmd_channel(db: Db, cfg: Config, args) -> None:
    if args.channel not in CHANNELS:
        raise ValueError(f"Kanal {args.channel!r}: möglich sind {', '.join(CHANNELS)}")
    db.panel_set_channel(_need(db, args.id)["id"], args.channel)
    print(f"{args.id}: Firmware aus {args.channel}")
```

`cmd_remove`: before `db.panel_remove(...)`: `rows = [r for r in db.identity_list() if r["panel_id"] == p["id"]]`; after the deletion print: `_todo(rows)`.

`cmd_list` becomes two lines per panel:

```python
def cmd_list(db: Db, cfg: Config, args) -> None:
    ids = db.identity_list()
    for p in db.panel_list():
        when = time.strftime("%Y-%m-%d", time.localtime(p["created"]))
        channel = f", Firmware {p['fw_channel']}" if p["fw_channel"] != "stable" else ""
        print(f"{p['id']:<12} {p['name']:<20} {len(p['settings']):>2} Einstellungen, seit {when}{channel}"
              f"{_note(cfg, p)}")
        print(f"{'':<12} {_seen(p)}; {', '.join(r['value'] for r in ids if r['panel_id'] == p['id']) or 'kein Zugang von außen'}")


def _seen(p: dict) -> str:
    """Wann sein ESP32 zuletzt abgerufen hat und was es dabei meldete (main.App speichert es jede Minute)."""
    if not p["seen_at"]:
        return "noch kein Abruf"
    out = f"zuletzt {time.strftime('%Y-%m-%d %H:%M', time.localtime(p['seen_at']))}"
    if p["fw_version"] is not None:
        out += f", Firmware {p['fw_version']}"
    if p["rssi"] is not None:
        out += f", WLAN {p['rssi']} dBm"
    if p["min_heap"] is not None:
        out += f", Speicher min. {p['min_heap']} B"
    return out
```

Parser registrations:

```python
    add("email", cmd_email, ("id", {}), ("address", {}))
    add("token", cmd_token, ("id", {}), ("client_id", {"metavar": "client-id"}))
    add("unlink", cmd_unlink, ("value", {"metavar": "adresse|client-id"}))
    add("channel", cmd_channel, ("id", {}), ("channel", {}))
```

Existing tests that parse `list` output (e.g. `test_add_list_remove`, `test_list_names_a_panel_that_does_not_fetch`) must still pass: they match the first line of each panel.

- [ ] **Step 5: Run the whole suite**, all green.

- [ ] **Step 6: Commit** — `panels: E-Mail-Adressen und Service-Tokens je Panel, Firmware-Kanal, Diagnose in list`.

---

### Task 3: `access.py` — the token check

**Files:**
- Create: `server/flightpanel/access.py`, `server/tests/test_access.py`, `server/tests/data/access_test_key.json`, `server/tests/data/rfc7515_a2.json`, `server/tests/data/wycheproof_rsa_signature_2048_sha256.json`
- No other module imports `access.py` in this task.

**Interfaces:**
- Produces: `class Invalid(Exception)`, `class UnknownKey(Invalid)` (carries the kid), `class Identity(NamedTuple)`: `kind` (`"email"`|`"token"`), `value`, `iat: float | None`; `class RsaKey(NamedTuple)`: `n`, `e`; `b64url(segment: str) -> bytes`; `rsa_key(jwk: dict) -> tuple[str, RsaKey]`; `rs256_valid(key, signing_input: bytes, signature: bytes) -> bool`; `verify(token: str, keys: dict[str, RsaKey], team: str, aud: str, now: float) -> Identity`. Constants `MAX_TOKEN = 8192`, `LEEWAY_S = 60`, `MIN_BITS = 2048`, `MAX_BITS = 8192`.

- [ ] **Step 1: Vendor the test data**

1. `server/tests/data/access_test_key.json`: a 2048-bit RSA test key made once for these tests, `{"kid": "testkey", "n": <base64url>, "e": "AQAB", "d": <base64url>}`. Make it with a throwaway pure-Python script in the Docker image (`secrets.randbits`, Miller–Rabin with 40 rounds, two 1024-bit primes with the top two bits set, `e = 65537`, `d = pow(e, -1, lcm(p-1, q-1))`, check `pow(pow(m, e, n), d, n) == m`), not committed. The file gets a `"_comment": "nur für Tests, nie für etwas Echtes"`.
2. `server/tests/data/rfc7515_a2.json`: from RFC 7515 Appendix A.2 (https://www.rfc-editor.org/rfc/rfc7515.txt): the RSA key's `n` and `e` (A.2.1) and the complete JWS compact serialization (A.2.1, end), as `{"n": …, "e": "AQAB", "jws": "<header>.<payload>.<signature>", "source": "RFC 7515, Appendix A.2"}`. Copy the values from the RFC text byte-for-byte (download the .txt, extract with a script, never retype).
3. `server/tests/data/wycheproof_rsa_signature_2048_sha256.json`: the C2SP/wycheproof file `testvectors_v1/rsa_signature_2048_sha256_test.json` (RSASSA-PKCS1-v1_5, 2048-bit, SHA-256) from `https://raw.githubusercontent.com/C2SP/wycheproof/main/testvectors_v1/rsa_signature_2048_sha256_test.json`, unchanged. If that path does not exist any more, find the file with the same content in the repository (`testvectors/` instead of `testvectors_v1/`) and note the URL and commit in the commit message.

All three are data, never executed. Check their sizes (the Wycheproof file is a few hundred KB at most) and that no `.env`-like secrets are in them.

- [ ] **Step 2: Write the failing tests**

Create `server/tests/test_access.py`:

```python
"""Das Token von Cloudflare Access: RS256 nur mit der Standardbibliothek, gegen fremde Testvektoren (RFC 7515 A.2,
Wycheproof) und gegen alles, was ein Angreifer probieren kann."""

import base64
import hashlib
import json
import os

import pytest

from conftest import FIXTURES
from flightpanel import access
from flightpanel.access import Identity, Invalid, RsaKey, UnknownKey, b64url, rs256_valid, rsa_key, verify

TEAM, AUD, NOW = "beispiel", "a" * 64, 1791654544.0


def load(name: str):
    with open(os.path.join(FIXTURES, name), encoding="utf-8") as f:
        return json.load(f)


KEY = load("access_test_key.json")
N, E, D = (int.from_bytes(b64url(KEY[k]), "big") for k in ("n", "e", "d"))
KEYS = {"testkey": RsaKey(N, E)}


def enc(raw: bytes) -> str:
    return base64.urlsafe_b64encode(raw).rstrip(b"=").decode()


def sign(claims: dict, header: dict | None = None, d: int = D, n: int = N) -> str:
    """Ein Token wie von Access, mit dem Testschlüssel signiert (RSASSA-PKCS1-v1_5, SHA-256)."""
    head = enc(json.dumps(header or {"alg": "RS256", "kid": "testkey", "typ": "JWT"}).encode())
    body = enc(json.dumps(claims).encode())
    k = (n.bit_length() + 7) // 8
    t = access.SHA256_DIGESTINFO + hashlib.sha256(f"{head}.{body}".encode()).digest()
    em = b"\x00\x01" + b"\xff" * (k - len(t) - 3) + b"\x00" + t
    sig = pow(int.from_bytes(em, "big"), d, n).to_bytes(k, "big")
    return f"{head}.{body}.{enc(sig)}"


def user(**kw) -> dict:
    return {"aud": [AUD], "email": "Anna@Example.com", "exp": NOW + 600, "iat": NOW - 5, "nbf": NOW - 5,
            "iss": f"https://{TEAM}.cloudflareaccess.com", "type": "app", "sub": "7335d417-61da", **kw}


def service(**kw) -> dict:
    return {"aud": [AUD], "common_name": "0123456789abcdef0123456789abcdef.access", "exp": NOW + 600,
            "iat": NOW - 5, "iss": f"https://{TEAM}.cloudflareaccess.com", "type": "app", "sub": "", **kw}


def test_a_browser_and_a_panel_token_are_recognized():
    assert verify(sign(user()), KEYS, TEAM, AUD, NOW) == Identity("email", "anna@example.com", NOW - 5)
    assert verify(sign(service()), KEYS, TEAM, AUD, NOW) == Identity(
        "token", "0123456789abcdef0123456789abcdef.access", NOW - 5)


def test_rfc_7515_a2_signature_checks_out():
    v = load("rfc7515_a2.json")
    head, body, sig = v["jws"].split(".")
    key = RsaKey(int.from_bytes(b64url(v["n"]), "big"), int.from_bytes(b64url(v["e"]), "big"))
    assert rs256_valid(key, f"{head}.{body}".encode(), b64url(sig))
    bad = bytearray(b64url(sig))
    bad[-1] ^= 1
    assert not rs256_valid(key, f"{head}.{body}".encode(), bytes(bad))


def test_wycheproof_pkcs1_vectors():
    """Jeder Vektor: gültig genau dann, wenn Wycheproof "valid" sagt. "acceptable" (veraltete Kodierungen) lehnen
    wir ab: Verglichen wird mit der einen kanonischen Kodierung."""
    data = load("wycheproof_rsa_signature_2048_sha256.json")
    count = 0
    for group in data["testGroups"]:
        key = group.get("publicKey") or {}
        n = int(key.get("modulus") or group["n"], 16)
        e = int(key.get("publicExponent") or group["e"], 16)
        for t in group["tests"]:
            ok = rs256_valid(RsaKey(n, e), bytes.fromhex(t["msg"]), bytes.fromhex(t["sig"]))
            assert ok == (t["result"] == "valid"), t["tcId"]
            count += 1
    assert count > 100


@pytest.mark.parametrize("change", [
    {"aud": AUD}, {"aud": ["b" * 64]}, {"iss": "https://andere.cloudflareaccess.com"}, {"type": "org"},
    {"exp": NOW - 61}, {"nbf": NOW + 61}, {"exp": None}, {"exp": True}, {"exp": "morgen"},
    {"common_name": "x.access"}, {"email": ""}, {"email": 5}], ids=str)
def test_wrong_claims_are_refused(change):
    claims = {k: v for k, v in user(**change).items() if v is not None}
    with pytest.raises(Invalid):
        verify(sign(claims), KEYS, TEAM, AUD, NOW)


def test_a_service_token_needs_an_empty_sub_and_no_email():
    with pytest.raises(Invalid):
        verify(sign(service(sub="x")), KEYS, TEAM, AUD, NOW)
    with pytest.raises(Invalid):
        verify(sign(service(email="a@example.com")), KEYS, TEAM, AUD, NOW)


def test_the_clock_has_sixty_seconds_of_leeway():
    assert verify(sign(user(exp=NOW - 59)), KEYS, TEAM, AUD, NOW).kind == "email"
    assert verify(sign(user(nbf=NOW + 59)), KEYS, TEAM, AUD, NOW).kind == "email"


@pytest.mark.parametrize("header", [{"alg": "none", "kid": "testkey"}, {"alg": "PS256", "kid": "testkey"},
                                    {"alg": "HS256", "kid": "testkey"},
                                    {"alg": "RS256", "kid": "testkey", "crit": ["exp"]}, {"alg": "RS256"}], ids=str)
def test_wrong_headers_are_refused(header):
    with pytest.raises(Invalid):
        verify(sign(user(), header=header), KEYS, TEAM, AUD, NOW)


def test_an_unknown_kid_says_so():
    with pytest.raises(UnknownKey):
        verify(sign(user(), header={"alg": "RS256", "kid": "neu"}), KEYS, TEAM, AUD, NOW)


def test_tampering_and_odd_encodings_are_refused():
    token = sign(user())
    head, body, sig = token.split(".")
    other = enc(json.dumps(user(email="mallory@example.com")).encode())
    for bad in (f"{head}.{other}.{sig}",                              # anderer Inhalt, alte Signatur
                f"{head}.{body}.{sig}AA",                             # Müll hinten
                f"{head}.{body}.{sig[:-1]}",                          # gekürzt
                f"{head}.{body}.{sig}=",                              # Füllzeichen
                f"{head}.{body}",                                     # zwei Teile
                f"{head}.{body}.{sig}.{sig}",                         # vier Teile
                f"{head} .{body}.{sig}",                              # Leerraum
                "x" * (access.MAX_TOKEN + 1)):
        with pytest.raises(Invalid):
            verify(bad, KEYS, TEAM, AUD, NOW)
    last = sig[-1]
    twin = "ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmnopqrstuvwxyz0123456789-_"
    sibling = twin[twin.index(last) ^ 1]                               # gleiche Bytes, andere letzte Bits
    with pytest.raises(Invalid):
        verify(f"{head}.{body}.{sig[:-1]}{sibling}", KEYS, TEAM, AUD, NOW)


def test_keys_are_checked_one_by_one():
    good = {"kty": "RSA", "kid": "k", "alg": "RS256", "use": "sig", "n": KEY["n"], "e": "AQAB"}
    assert rsa_key(good) == ("k", RsaKey(N, E))
    for bad in ({**good, "e": enc(b"\x03")},                              # e = 3
                {**good, "kty": "EC"}, {**good, "alg": "PS256"}, {**good, "use": "enc"},
                {**good, "n": enc((N >> 1100).to_bytes(120, "big"))},   # zu kurz
                {**good, "kid": ""}, {**good, "n": KEY["n"] + "="}, "kein Objekt"):
        with pytest.raises(Invalid):
            rsa_key(bad)


def test_a_signature_as_long_as_n_but_not_smaller_is_refused():
    k = (N.bit_length() + 7) // 8
    assert not rs256_valid(RsaKey(N, E), b"x", N.to_bytes(k, "big"))
    assert not rs256_valid(RsaKey(N, E), b"x", b"\x00" + N.to_bytes(k, "big"))     # ein Byte zu lang
```

- [ ] **Step 3: Run them to see them fail** (`ModuleNotFoundError: flightpanel.access`).

- [ ] **Step 4: Write `access.py`**

```python
"""Wer fragt? Prüft das Token, das Cloudflare Access jeder Anfrage an den Zugang von außen mitgibt (Header
Cf-Access-Jwt-Assertion), selbst und nur mit der Standardbibliothek: RS256 nach RFC 7515/7518, die Signatur nach
RFC 8017 (RSASSA-PKCS1-v1_5) durch Nachbauen und Vergleichen des ganzen Blocks in konstanter Zeit, nie durch
Auslesen der DigestInfo. Den Inhalt liest es erst, wenn die Signatur stimmt.

Geladen wird dieses Modul nur mit eingerichtetem Zugang von außen (Config.public). cloudflared prüft das Token
vorher auch (access.required), und der Port hängt nur in einem eigenen Docker-Netz mit cloudflared: drei
Schichten, und die App verlässt sich auf keine der anderen.

Was ein Token erfüllen muss:
  - genau ein Header, höchstens MAX_TOKEN Zeichen, drei Teile aus [A-Za-z0-9_-], die sich genau so zurück
    kodieren (kein Füllzeichen, keine überzähligen Bits, kein Leerraum)
  - Kopf: alg RS256, kein crit; jku, x5u und jwk werden nie benutzt; kid ein bekannter Schlüssel (sonst
    UnknownKey: KeySet holt die Liste neu)
  - Schlüssel: RSA, n mit MIN_BITS bis MAX_BITS Bit, e genau 65537; Signatur genau so lang wie n und kleiner
  - exp Pflicht, nbf nur wenn da, je LEEWAY_S Spielraum; iss genau https://<team>.cloudflareaccess.com;
    type app; aud eine Liste, die das AUD-Tag der Anwendung enthält
  - Identität: entweder email (klein geschrieben) oder common_name mit sub "" (Service-Token: die Client-ID),
    nie beides. Ob sie etwas darf, entscheidet die Datenbank (identities), nie das Token."""

import base64
import hashlib
import hmac
import json
import math
import re
from typing import NamedTuple

MAX_TOKEN = 8192        # Zeichen: Ein Token mit custom-Claims hat gut 2 KB; aiohttp lässt Header bis 8190 Byte zu
LEEWAY_S = 60
MIN_BITS = 2048
MAX_BITS = 8192
_SEGMENT = re.compile(r"[A-Za-z0-9_-]+")
# DigestInfo von SHA-256 (RFC 8017, 9.2, Anmerkung 1); dahinter folgen die 32 Bytes des Hashwerts
SHA256_DIGESTINFO = bytes.fromhex("3031300d060960864801650304020105000420")


class Invalid(Exception):
    """Token oder Schlüssel gelten nicht. Die Nachricht ist fürs Log des Servers, nie für den, der fragt."""


class UnknownKey(Invalid):
    """Das Token nennt einen Schlüssel (kid), den die Liste nicht kennt: vielleicht hat Cloudflare gewechselt."""


class Identity(NamedTuple):
    kind: str               # "email" (Browser) oder "token" (Service-Token eines Panels)
    value: str              # E-Mail-Adresse, klein geschrieben, oder die Client-ID
    iat: float | None       # wann Access das Token ausgestellt hat, für die Uhrzeit-Abweichung in /health


class RsaKey(NamedTuple):
    n: int
    e: int


def b64url(segment: str) -> bytes:
    """Base64url ohne Füllzeichen, streng: nur das Alphabet, und das Ergebnis kodiert sich genau so zurück. So hat
    jedes Token genau eine Schreibweise."""
    if not isinstance(segment, str) or not _SEGMENT.fullmatch(segment) or len(segment) % 4 == 1:
        raise Invalid("kein Base64url")
    raw = base64.urlsafe_b64decode(segment + "=" * (-len(segment) % 4))
    if base64.urlsafe_b64encode(raw).rstrip(b"=").decode("ascii") != segment:
        raise Invalid("Base64url nicht kanonisch")
    return raw


def rsa_key(jwk) -> tuple[str, RsaKey]:
    """(kid, Schlüssel) aus einem Eintrag der Schlüsselliste von Access. Nur RSA für RS256-Signaturen, n mit
    MIN_BITS bis MAX_BITS Bit, e genau 65537; alles andere ist Invalid."""
    if not isinstance(jwk, dict) or jwk.get("kty") != "RSA":
        raise Invalid("kein RSA-Schlüssel")
    if jwk.get("alg", "RS256") != "RS256" or jwk.get("use", "sig") != "sig":
        raise Invalid("Schlüssel nicht für RS256-Signaturen")
    kid = jwk.get("kid")
    if not isinstance(kid, str) or not kid:
        raise Invalid("Schlüssel ohne kid")
    n, e = (int.from_bytes(b64url(jwk.get(k)), "big") for k in ("n", "e"))
    if not MIN_BITS <= n.bit_length() <= MAX_BITS or e != 65537:
        raise Invalid("Schlüssel zu kurz, zu lang oder e nicht 65537")
    return kid, RsaKey(n, e)


def rs256_valid(key: RsaKey, signing_input: bytes, signature: bytes) -> bool:
    """RSASSA-PKCS1-v1_5 mit SHA-256 (RFC 8017, 8.2.2): den erwarteten Block bauen und mit dem aus der Signatur
    vergleichen, ganz und in konstanter Zeit."""
    k = (key.n.bit_length() + 7) // 8
    if len(signature) != k:
        return False
    s = int.from_bytes(signature, "big")
    if s >= key.n:
        return False
    em = pow(s, key.e, key.n).to_bytes(k, "big")
    t = SHA256_DIGESTINFO + hashlib.sha256(signing_input).digest()
    return hmac.compare_digest(em, b"\x00\x01" + b"\xff" * (k - len(t) - 3) + b"\x00" + t)


def verify(token: str, keys: dict[str, RsaKey], team: str, aud: str, now: float) -> Identity:
    """Das Token prüfen (siehe oben) und die Identität darin zurückgeben; sonst Invalid, bei unbekanntem
    Schlüssel UnknownKey."""
    if not isinstance(token, str) or len(token) > MAX_TOKEN:
        raise Invalid("Token fehlt oder ist zu lang")
    parts = token.split(".")
    if len(parts) != 3:
        raise Invalid("Token hat nicht drei Teile")
    head = _object(b64url(parts[0]))
    if head.get("alg") != "RS256" or "crit" in head:
        raise Invalid("alg nicht RS256 oder crit gesetzt")
    kid = head.get("kid")
    key = keys.get(kid) if isinstance(kid, str) else None
    if key is None:
        raise UnknownKey(f"Schlüssel {kid!r} unbekannt")
    if not rs256_valid(key, f"{parts[0]}.{parts[1]}".encode("ascii"), b64url(parts[2])):
        raise Invalid("Signatur falsch")
    claims = _object(b64url(parts[1]))                    # erst jetzt: Die Signatur stimmt
    exp, nbf = claims.get("exp"), claims.get("nbf", now)
    if not (_number(exp) and _number(nbf)):
        raise Invalid("exp fehlt, oder exp oder nbf sind keine Zahl")
    if now > exp + LEEWAY_S or now < nbf - LEEWAY_S:
        raise Invalid("abgelaufen oder noch nicht gültig")
    if claims.get("iss") != f"https://{team}.cloudflareaccess.com" or claims.get("type") != "app":
        raise Invalid("iss oder type falsch")
    audience = claims.get("aud")
    if not (isinstance(audience, list) and aud in audience):
        raise Invalid("aud passt nicht")
    email, name = claims.get("email"), claims.get("common_name")
    iat = claims["iat"] if _number(claims.get("iat")) else None
    if isinstance(email, str) and email and name is None:
        return Identity("email", email.lower(), iat)
    if isinstance(name, str) and name and claims.get("sub") == "" and email is None:
        return Identity("token", name.lower(), iat)
    raise Invalid("weder E-Mail noch Service-Token, oder beides")


def _object(raw: bytes) -> dict:
    try:
        v = json.loads(raw.decode("utf-8"))
    except ValueError as e:                               # UnicodeDecodeError und JSONDecodeError sind ValueError
        raise Invalid("kein JSON") from e
    if not isinstance(v, dict):
        raise Invalid("kein JSON-Objekt")
    return v


def _number(v) -> bool:
    return isinstance(v, int | float) and not isinstance(v, bool) and math.isfinite(v)
```

- [ ] **Step 5: Run `test_access.py`**, then the whole suite; all green. If a Wycheproof vector disagrees, find out why before changing anything; never relax the comparison.

- [ ] **Step 6: Commit** — `Zugang von außen: Token von Cloudflare Access selbst prüfen (access.py)` (body: stdlib only, RFC 8017 re-encoding, the vendored vectors with their sources).

---

### Task 4: the Access key set

**Files:**
- Modify: `server/flightpanel/access.py`
- Test: `server/tests/test_access.py`

**Interfaces:**
- Consumes: `rsa_key`, `RsaKey`, `Invalid` (Task 3); `issues.error_text`; `Db.state_get/state_set`.
- Produces: `certs_url(team) -> str`; `class KeySet(db, session, url)` with `keys: dict[str, RsaKey]`, `fetched: float`, `last_try: float`, `last_error: str | None`, `async refresh(now, force=False) -> bool` (at most once per `REFETCH_S = 60` unless forced; redirects refused; `TIMEOUT_S = 10`; at most `MAX_BYTES = 64 * 1024`; keys checked one by one; persisted as state `"access_keys"` = `{"keys": [<jwk>…], "fetched": t}`), `age_h(now) -> float | None`.

- [ ] **Step 1: Write the failing tests** — append to `server/tests/test_access.py` (imports: `asyncio`, `from aiohttp import web`, `from aiohttp.test_utils import TestServer`, `import aiohttp`, `from conftest import make_db`, `from flightpanel.access import KeySet, certs_url`):

```python
JWK = {"kty": "RSA", "kid": "testkey", "alg": "RS256", "use": "sig", "n": KEY["n"], "e": "AQAB"}


def serve(handler):
    """Ein kleiner HTTP-Server statt Cloudflare; gibt (Server, URL) zurück."""
    app = web.Application()
    app.router.add_get("/cdn-cgi/access/certs", handler)
    app.router.add_get("/umweg", lambda req: web.HTTPFound("/cdn-cgi/access/certs"))
    server = TestServer(app)
    return server


def test_the_key_set_is_fetched_checked_one_by_one_and_kept(tmp_path):
    db = make_db(tmp_path)

    async def certs(req):
        return web.json_response({"keys": [JWK, {**JWK, "kid": "schwach", "e": "Aw"}],
                                  "public_cert": {}, "public_certs": []})

    async def run():
        server = serve(certs)
        await server.start_server()
        async with aiohttp.ClientSession() as session:
            ks = KeySet(db, session, str(server.make_url("/cdn-cgi/access/certs")))
            assert await ks.refresh(NOW) and set(ks.keys) == {"testkey"}           # e = 3 fällt weg, der Rest gilt
            assert not await ks.refresh(NOW + 30)                                  # höchstens einmal pro Minute
            again = KeySet(db, session, "http://127.0.0.1:9/nie")                  # Neustart ohne Netz
            assert set(again.keys) == {"testkey"} and again.fetched == NOW
            assert not await again.refresh(NOW + 120, force=True)                  # Netz weg: alte Liste bleibt
            assert set(again.keys) == {"testkey"} and again.last_error
        await server.close()

    asyncio.run(run())


@pytest.mark.parametrize("answer", ["redirect", "huge", "html", "nokeys", "status"])
def test_a_misbehaving_key_endpoint_changes_nothing(tmp_path, answer):
    db = make_db(tmp_path)
    db.state_set("access_keys", {"keys": [JWK], "fetched": NOW - 3600})

    async def certs(req):
        if answer == "redirect":
            raise web.HTTPFound("/umweg")
        if answer == "huge":
            return web.Response(body=b"{" + b" " * (70 * 1024) + b"}", content_type="application/json")
        if answer == "html":
            return web.Response(text="<html>Anmelden</html>", content_type="text/html")
        if answer == "nokeys":
            return web.json_response({"keys": [{"kty": "EC"}]})
        return web.Response(status=503)

    async def run():
        server = serve(certs)
        await server.start_server()
        async with aiohttp.ClientSession() as session:
            ks = KeySet(db, session, str(server.make_url("/cdn-cgi/access/certs")))
            assert not await ks.refresh(NOW, force=True)
            assert set(ks.keys) == {"testkey"} and ks.fetched == NOW - 3600 and ks.last_error
        await server.close()

    asyncio.run(run())


def test_certs_url():
    assert certs_url("beispiel") == "https://beispiel.cloudflareaccess.com/cdn-cgi/access/certs"
```

- [ ] **Step 2: Run them to see them fail.**

- [ ] **Step 3: Implement** — add to `access.py` (imports `logging`, `aiohttp`, `from .issues import error_text`):

```python
log = logging.getLogger("access")


def certs_url(team: str) -> str:
    return f"https://{team}.cloudflareaccess.com/cdn-cgi/access/certs"


class KeySet:
    """Die Signaturschlüssel von Access. Cloudflare wechselt sie alle sechs Wochen, die alten gelten noch sieben
    Tage: Ein unbekannter kid holt die Liste neu, höchstens einmal pro Minute; dazu einmal am Tag (main.App). Die
    letzte gute Liste liegt in der Datenbank (state "access_keys") und gilt nach einem Neustart sofort, auch ohne
    Netz. Jeder Schlüssel wird einzeln geprüft (rsa_key): Ein unbrauchbarer fällt weg, die anderen gelten. Keine
    Umleitungen, eine Zeitgrenze und eine Größengrenze: Die Antwort kommt aus dem Netz."""

    REFETCH_S = 60
    TIMEOUT_S = 10
    MAX_BYTES = 64 * 1024

    def __init__(self, db, session: aiohttp.ClientSession, url: str):
        self.db, self.session, self.url = db, session, url
        self.keys: dict[str, RsaKey] = {}
        self.fetched = 0.0
        self.last_try = 0.0
        self.last_error: str | None = None
        saved = db.state_get("access_keys")
        if isinstance(saved, dict):
            self.keys = _usable(saved.get("keys"))
            self.fetched = saved["fetched"] if _number(saved.get("fetched")) else 0.0

    async def refresh(self, now: float, force: bool = False) -> bool:
        """Die Liste neu holen; True, wenn eine neue gültige da ist. Ohne force höchstens einmal pro REFETCH_S."""
        if not force and now - self.last_try < self.REFETCH_S:
            return False
        self.last_try = now
        try:
            async with self.session.get(self.url, allow_redirects=False,
                                        timeout=aiohttp.ClientTimeout(total=self.TIMEOUT_S)) as r:
                if r.status != 200:
                    raise Invalid(f"HTTP {r.status}")
                raw = await r.content.read(self.MAX_BYTES + 1)
            if len(raw) > self.MAX_BYTES:
                raise Invalid("Antwort zu groß")
            doc = _object(raw)
            jwks = [k for k in doc.get("keys") or [] if _usable([k])] if isinstance(doc.get("keys"), list) else []
            if not jwks:
                raise Invalid("kein brauchbarer Schlüssel")
        except Exception as e:                            # Netz, Antwort, Inhalt: die alte Liste bleibt
            self.last_error = error_text(e)
            log.warning("Access-Schlüssel nicht erneuert: %s", self.last_error)
            return False
        self.keys, self.fetched, self.last_error = _usable(jwks), now, None
        self.db.state_set("access_keys", {"keys": jwks, "fetched": now})
        log.info("Access-Schlüssel erneuert: %s", ", ".join(sorted(self.keys)))
        return True

    def age_h(self, now: float) -> float | None:
        return round((now - self.fetched) / 3600, 1) if self.fetched else None


def _usable(jwks) -> dict[str, RsaKey]:
    """Die brauchbaren Schlüssel einer Liste (rsa_key), nach kid; die anderen stehen im Log."""
    out = {}
    for jwk in jwks if isinstance(jwks, list) else []:
        try:
            kid, key = rsa_key(jwk)
        except Invalid as e:
            log.warning("Access-Schlüssel übergangen: %s", e)
            continue
        out[kid] = key
    return out
```

- [ ] **Step 4: Run `test_access.py` and the whole suite**, all green.

- [ ] **Step 5: Commit** — `Zugang von außen: Schlüssel von Access holen, einzeln prüfen, die letzte gute Liste behalten`.

---

<!-- Tasks 5 onwards (LAN hardening and /panel/* routes, the public listener, the settings page, docs) follow once step 2 is complete, so they build on its final web.py and main.py. -->
