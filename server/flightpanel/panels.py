"""Panels verwalten: python -m flightpanel.panels <Befehl> ...

  list                        alle Panels, wer sie von außen sehen darf, letzter Abruf
  keys                        alle Panel-Einstellungen mit Default und Erklärung
  show <id>                   was bei einem Panel gesetzt ist
  set <id> KEY=WERT ...       setzen, geprüft wie .env (KEY= leer: zurück auf den Default)
  unset <id> KEY ...          zurück auf den Default
  add <id> [--name NAME]      weiteres Panel (das am LAN-Port, LAN_PANEL, legt der Server an)
  remove <id> --yes           Panel samt Album und Statistik löschen
  email <id> <adresse>        darf das Panel im Browser sehen und einstellen (Cloudflare Access)
  token <id> <client-id>      Service-Token eines Panels von außen (Cloudflare Access)
  unlink <adresse|client-id>  wieder weg
  channel <id> stable|canary  Firmware-Kanal
  backup <datei> [--force]    Kopie der Datenbank, auch im laufenden Betrieb; eine vorhandene
                              Datei ersetzt sie nur mit --force, die Datenbank selbst nie

Panel-Einstellungen sind Standort, Sichtfeld, Anzeige, Helligkeit und Ruhezeit und die
Leerlauf-Karten (CARD_<ID>=0); sie liegen je Panel in DATA_DIR/flightpanel.db. Alles
andere (Flughafen, Quellen, Logos, Betrieb) steht in .env.

Für weitere Panels (nicht das am LAN-Port) lehnt set Werte ab, die gegen die Grenzen aus .env
verstoßen: das Fenster höchstens PANEL_MAX_KM vom Flughafen, RADIUS_KM und SCAN_RADIUS_KM
höchstens PANEL_MAX_RADIUS_KM.

Namen gelten in jeder Schreibweise (home_lat wie HOME_LAT). set und unset treffen auch einen
Schlüssel, der von Hand klein geschrieben in die Datenbank kam, und räumen ihn weg.

Im Container:
  docker compose exec flightpanel python3 -m flightpanel.panels set home HOME_LAT=52.52 HOME_LON=13.40
Der laufende Server übernimmt Änderungen spätestens nach einer Minute, ohne Neustart."""

import argparse
import os
import re
import sys
import time
import unicodedata

from .cards import CARDS
from .config import CARD_PREFIX, FW_CHANNELS, Config, default_text, panel_fields, panel_id, too_far
from .db import DatabaseError, Db, db_path

CARD_IDS = sorted(c.id for c in CARDS)
EMAIL = re.compile(r"[^@\s]+@[^@\s]+\.[^@\s]+")
CLIENT_ID = re.compile(r"[0-9a-f]{32}\.access")          # wie Zero Trust sie anzeigt
CHANNELS = FW_CHANNELS                                   # wie der Server sie kennt (config.py)


def _control(c: str) -> bool:
    """Ein Steuerzeichen (Unicode Cc: C0, DEL, C1), darunter ESC, BEL, Tabulator und Zeilenumbruch."""
    return unicodedata.category(c) == "Cc"


def _plain(text: str) -> str:
    """Text fürs Terminal, jedes Steuerzeichen als \\x1b und so weiter. In der Datenbank steht, was ein Freund auf der
    Einstellungsseite gespeichert hat oder was von Hand dazukam; roh ausgegeben könnten Escape-Folgen darin das
    Terminal steuern (Fenstertitel, Zwischenablage, verdeckter Text)."""
    return "".join(f"\\x{ord(c):02x}" if _control(c) else c for c in text)


def _todo(rows: list[dict]) -> None:
    """Was der Server nicht selbst kann, nachdem eine Adresse oder ein Token weg ist."""
    if any(r["kind"] == "email" for r in rows):
        print("Noch zu tun: die Adresse in Zero Trust aus der Access-Gruppe der flightpanel-Anwendung nehmen.")
    if any(r["kind"] == "token" for r in rows):
        print("Noch zu tun: den Service-Token in Zero Trust löschen (sonst ruft, wer das Panel hat, weiter ab, bis er "
              "abläuft) und das Panel auf Werkseinstellungen zurücksetzen (BOOT beim Einschalten halten, Erweitert): "
              "Der Token steht unverschlüsselt in seinem Speicher.")


def check_card(key: str) -> None:
    """CARD_<ID> nur für Karten, die es gibt: ein Tippfehler bliebe sonst still wirkungslos. Auch für die
    Einstellungsseite (settings.py)."""
    if key.startswith(CARD_PREFIX) and key[len(CARD_PREFIX):].lower() not in CARD_IDS:
        raise ValueError(f"unbekannte Karte: {key}; bekannt: {', '.join(CARD_IDS)}")


def _need(db: Db, pid: str) -> dict:
    p = db.panel_get(pid)
    if p is None:
        have = ", ".join(x["id"] for x in db.panel_list()) or "-"
        raise ValueError(f"Panel {pid} gibt es nicht (vorhanden: {have})")
    return p


def cmd_list(db: Db, cfg: Config, args) -> None:
    ids = db.identity_list()
    for p in db.panel_list():
        when = time.strftime("%Y-%m-%d", time.localtime(p["created"]))
        channel = f", Firmware {p['fw_channel']}" if p["fw_channel"] != "stable" else ""
        print(_plain(f"{p['id']:<12} {p['name']:<20} {len(p['settings']):>2} Einstellungen, seit {when}{channel}"
                     f"{_note(cfg, p)}"))
        access = ", ".join(r["value"] for r in ids if r["panel_id"] == p["id"]) or "kein Zugang von außen"
        print(_plain(f"{'':<12} {_seen(p)}; {access}"))


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


def _note(cfg: Config, p: dict) -> str:
    """Für list: das Panel am LAN-Port, oder ein weiteres, das nicht abruft, weil sein Fenster zu weit vom Flughafen
    liegt (geprüft wie im Server; Einstellungen, die er übergeht, zählen dabei nicht)."""
    if p["id"] == cfg.lan_panel:
        return " (LAN-Port)"
    why = too_far(cfg, Config.for_panel(cfg, p["settings"], [], remote=True))
    return f" (ruft nicht ab: {why})" if why else ""


def cmd_keys(db: Db, cfg: Config, args) -> None:
    for f in panel_fields():
        group, text = f.metadata["panel"]
        print(f"{f.metadata['env']:<18} {default_text(f):<22} {group}: {text}")
    print(f"{'CARD_<ID>':<18} {'1':<22} Karten: 0 schaltet eine Leerlauf-Karte ab ({', '.join(CARD_IDS)})")


def cmd_show(db: Db, cfg: Config, args) -> None:
    p = _need(db, args.id)
    for k, v in sorted(p["settings"].items()):
        print(_plain(f"{k}={v}"))
    if not p["settings"]:
        print(_plain(f"{p['id']}: nichts gesetzt, alles auf Default (keys zeigt die Defaults)"))


def _name(key: str) -> str:
    """Der Name einer Einstellung, wie die Befehle ihn vergleichen: groß geschrieben und ohne Rand. Getippt gilt jede
    Schreibweise (home_lat), und genauso für das, was in der Datenbank steht."""
    return key.strip().upper()


def _spellings(stored: dict[str, str], names: set[str]) -> list[str]:
    """Die gespeicherten Schlüssel zu den Namen, jeder in der Schreibweise, in der er gespeichert ist. Von Hand in die
    Datenbank geschrieben, steht dort mitunter home_lat statt HOME_LAT: Der Server liest das nicht, übergeht es und rät
    zu unset mit genau diesem Schlüssel. Setzen und Zurücksetzen müssen ihn treffen, sonst bliebe er stehen, und das
    strenge Speichern (_save) lehnte jeden Befehl ab, solange er da ist."""
    return [k for k in stored if _name(k) in names]


def _check_reset(cfg: Config, stored: dict[str, str], names: list[str]) -> None:
    """Zurücksetzen (KEY= in set, unset) nimmt, was gespeichert ist, in jeder Schreibweise, auch Veraltetes: So lässt es
    sich löschen. Alles andere muss ein Name sein, den set annähme; sonst bliebe ein Tippfehler, ein Server-Schlüssel
    oder KEY=WERT in unset still wirkungslos. names: Namen im Sinne von _name."""
    have = {_name(k) for k in stored}
    unstored = [k for k in names if k not in have]
    for key in unstored:
        check_card(key)
    # Leer gilt als nicht gesetzt: for_panel meldet nur, welche Namen keine Panel-Einstellung sind
    Config.for_panel(cfg, dict.fromkeys(unstored, ""))


def check(cfg: Config, pid: str, settings: dict[str, str]) -> None:
    """Prüft die Einstellungen eines Panels mit denselben Parsern wie der Server. Für weitere Panels (nicht das am
    LAN-Port) gelten dazu die Grenzen aus .env: Abrufradius höchstens PANEL_MAX_RADIUS_KM, das Fenster höchstens
    PANEL_MAX_KM vom Flughafen. Kein Wert darf ein Steuerzeichen enthalten: Schalter und Karten nehmen sonst jeden
    Text, und Escape-Folgen kämen roh in die Datenbank (siehe _plain). ValueError nennt, was nicht passt. Auch für die
    Einstellungsseite (settings.py): Sie prüft genau wie set."""
    for key, value in settings.items():
        if any(_control(c) for c in str(value)):
            raise ValueError(f"{key}={value!r}: Steuerzeichen (etwa Tabulator oder Escape) sind nicht erlaubt")
    remote = pid != cfg.lan_panel
    panel = Config.for_panel(cfg, settings, remote=remote)
    why = too_far(cfg, panel) if remote else None
    if why:
        raise ValueError(f"{why}. HOME_LAT und HOME_LON zusammen aufs Fenster setzen, oder PANEL_MAX_KM in .env "
                         "erhöhen")


def _save(db: Db, cfg: Config, p: dict, new: dict[str, str]) -> None:
    check(cfg, p["id"], new)                    # prüft; ValueError nennt die Einstellung
    db.panel_set_settings(p["id"], new)
    print(f"{p['id']}: {len(new)} Einstellungen gesetzt; der Server übernimmt sie spätestens nach einer Minute")


def cmd_set(db: Db, cfg: Config, args) -> None:
    p = _need(db, args.id)
    new = dict(p["settings"])
    reset = []
    for item in args.pairs:
        key, sep, value = item.partition("=")
        key = _name(key)
        if not sep or not key:
            raise ValueError(f"erwartet KEY=WERT, nicht {item!r}")
        for spelling in _spellings(new, {key}):     # auch home_lat, wenn HOME_LAT gemeint ist: Die letzte Angabe gilt
            del new[spelling]
        if value.strip():
            check_card(key)
            new[key] = value.strip()
        else:
            reset.append(key)
    _check_reset(cfg, p["settings"], reset)
    _save(db, cfg, p, new)


def cmd_unset(db: Db, cfg: Config, args) -> None:
    p = _need(db, args.id)
    names = {_name(k) for k in args.keys}
    _check_reset(cfg, p["settings"], sorted(names))
    gone = _spellings(p["settings"], names)
    _save(db, cfg, p, {k: v for k, v in p["settings"].items() if k not in gone})


def cmd_add(db: Db, cfg: Config, args) -> None:
    try:
        panel_id(args.id)
    except ValueError as e:
        raise ValueError(f"id {args.id!r}: {e}") from None
    db.panel_add(args.id, args.name)
    print(f"{args.id} angelegt; der Server bedient es spätestens nach einer Minute. "
          f"Standort: set {args.id} HOME_LAT=... HOME_LON=... VIEW_FROM=...")


def cmd_remove(db: Db, cfg: Config, args) -> None:
    p = _need(db, args.id)
    if p["id"] == cfg.lan_panel:
        raise ValueError(f"{p['id']} ist das Panel am LAN-Port (LAN_PANEL) und bleibt")
    if not args.yes:
        raise ValueError(f"löscht {p['id']} samt Album und Statistik; zum Bestätigen --yes")
    rows = [r for r in db.identity_list() if r["panel_id"] == p["id"]]
    db.panel_remove(p["id"])
    print(f"{p['id']} gelöscht; der Server lässt es spätestens nach einer Minute fallen")
    _todo(rows)


def cmd_email(db: Db, cfg: Config, args) -> None:
    p = _need(db, args.id)
    address = args.address.strip().lower()
    if len(address) > 254 or not EMAIL.fullmatch(address):
        raise ValueError(f"{args.address!r} ist keine E-Mail-Adresse")
    db.identity_add("email", address, p["id"])
    print(f"{address} darf Panel {p['id']} im Browser sehen und einstellen. In Zero Trust die Adresse in die "
          "Access-Gruppe der flightpanel-Anwendung aufnehmen (docs/remote-panels.md).")


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


def cmd_backup(db: Db, cfg: Config, args) -> None:
    """Nie still überschreiben: Eine vorhandene Datei könnte die Sicherung von gestern sein oder etwas ganz anderes.
    Die Datenbank selbst lehnt Db.backup ab, auch mit --force."""
    if os.path.exists(args.file) and not args.force:
        raise ValueError(f"{args.file} gibt es schon und bleibt, wie sie ist. Einen neuen Namen nehmen, etwa mit "
                         "Datum, oder mit --force ersetzen")
    db.backup(args.file)
    print(f"Kopie: {args.file}")


def main(argv: list[str] | None = None) -> int:
    os.umask(0o027)         # wie der Server (main._main): eine Kopie mit Standort und Album ist nicht für alle lesbar
    parser = argparse.ArgumentParser(prog="python -m flightpanel.panels", description=__doc__,
                                     formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = parser.add_subparsers(dest="cmd", required=True)

    def add(name, func, *arguments):
        p = sub.add_parser(name)
        for flag, kw in arguments:
            p.add_argument(flag, **kw)
        p.set_defaults(func=func)

    add("list", cmd_list)
    add("keys", cmd_keys)
    add("show", cmd_show, ("id", {}))
    add("set", cmd_set, ("id", {}), ("pairs", {"nargs": "+", "metavar": "KEY=WERT"}))
    add("unset", cmd_unset, ("id", {}), ("keys", {"nargs": "+", "metavar": "KEY"}))
    add("add", cmd_add, ("id", {}), ("--name", {"default": ""}))
    add("remove", cmd_remove, ("id", {}), ("--yes", {"action": "store_true"}))
    add("email", cmd_email, ("id", {}), ("address", {}))
    add("token", cmd_token, ("id", {}), ("client_id", {"metavar": "client-id"}))
    add("unlink", cmd_unlink, ("value", {"metavar": "adresse|client-id"}))
    add("channel", cmd_channel, ("id", {}), ("channel", {}))
    add("backup", cmd_backup, ("file", {}), ("--force", {"action": "store_true"}))
    args = parser.parse_args(argv)
    try:
        cfg = Config.from_env()
    except ValueError as e:
        print(f"Fehler: {e}", file=sys.stderr)
        return 2
    path = db_path(cfg.data_dir)
    try:
        db = Db(path, put_aside=False)          # läuft neben dem Server: eine kaputte Datei bleibt liegen
    except DatabaseError as e:
        print(f"Fehler: {path}: {e}", file=sys.stderr)
        return 2
    try:
        args.func(db, cfg, args)
        return 0
    except (ValueError, DatabaseError, OSError) as e:   # auch: Datenbank gesperrt, Sicherungsziel nicht schreibbar
        print(f"Fehler: {_plain(str(e))}", file=sys.stderr)    # nennt mitunter, was in der Datenbank steht
        return 2
    finally:
        db.close()


if __name__ == "__main__":
    sys.exit(main())
