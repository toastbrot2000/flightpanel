"""Panels verwalten: python -m flightpanel.panels <Befehl> ...

  list                        alle Panels
  keys                        alle Panel-Einstellungen mit Default und Erklärung
  show <id>                   was bei einem Panel gesetzt ist
  set <id> KEY=WERT ...       setzen, geprüft wie .env (KEY= leer: zurück auf den Default)
  unset <id> KEY ...          zurück auf den Default
  add <id> [--name NAME]      weiteres Panel (das am LAN-Port, LAN_PANEL, legt der Server an)
  remove <id> --yes           Panel samt Album und Statistik löschen
  backup <datei> [--force]    Kopie der Datenbank, auch im laufenden Betrieb; eine vorhandene
                              Datei ersetzt sie nur mit --force, die Datenbank selbst nie

Panel-Einstellungen sind Standort, Sichtfeld, Anzeige, Helligkeit und Ruhezeit und die
Leerlauf-Karten (CARD_<ID>=0); sie liegen je Panel in DATA_DIR/flightpanel.db. Alles
andere (Flughafen, Quellen, Logos, Betrieb) steht in .env.

Im Container:
  docker compose exec flightpanel python3 -m flightpanel.panels set home HOME_LAT=52.52 HOME_LON=13.40
Der Server liest die Einstellungen beim Start: danach docker compose restart flightpanel."""

import argparse
import os
import sys
import time

from .cards import CARDS
from .config import CARD_PREFIX, Config, default_text, panel_fields, panel_id
from .db import DatabaseError, Db, db_path

CARD_IDS = sorted(c.id for c in CARDS)


def _check_card(key: str) -> None:
    """CARD_<ID> nur für Karten, die es gibt: ein Tippfehler bliebe sonst still wirkungslos."""
    if key.startswith(CARD_PREFIX) and key[len(CARD_PREFIX):].lower() not in CARD_IDS:
        raise ValueError(f"unbekannte Karte: {key}; bekannt: {', '.join(CARD_IDS)}")


def _need(db: Db, pid: str) -> dict:
    p = db.panel_get(pid)
    if p is None:
        have = ", ".join(x["id"] for x in db.panel_list()) or "-"
        raise ValueError(f"Panel {pid} gibt es nicht (vorhanden: {have})")
    return p


def cmd_list(db: Db, cfg: Config, args) -> None:
    for p in db.panel_list():
        when = time.strftime("%Y-%m-%d", time.localtime(p["created"]))
        lan = " (LAN-Port)" if p["id"] == cfg.lan_panel else ""
        print(f"{p['id']:<12} {p['name']:<20} {len(p['settings']):>2} Einstellungen, seit {when}{lan}")


def cmd_keys(db: Db, cfg: Config, args) -> None:
    for f in panel_fields():
        group, text = f.metadata["panel"]
        print(f"{f.metadata['env']:<18} {default_text(f):<22} {group}: {text}")
    print(f"{'CARD_<ID>':<18} {'1':<22} Karten: 0 schaltet eine Leerlauf-Karte ab ({', '.join(CARD_IDS)})")


def cmd_show(db: Db, cfg: Config, args) -> None:
    p = _need(db, args.id)
    for k, v in sorted(p["settings"].items()):
        print(f"{k}={v}")
    if not p["settings"]:
        print(f"{p['id']}: nichts gesetzt, alles auf Default (keys zeigt die Defaults)")


def _check_reset(cfg: Config, stored: dict[str, str], keys: list[str]) -> None:
    """Zurücksetzen (KEY= in set, unset) nimmt, was gespeichert ist, auch Veraltetes: So lässt es sich löschen. Alles
    andere muss ein Name sein, den set annähme; sonst bliebe ein Tippfehler, ein Server-Schlüssel oder KEY=WERT in
    unset still wirkungslos."""
    unstored = [k for k in keys if k not in stored]
    for key in unstored:
        _check_card(key)
    # Leer gilt als nicht gesetzt: for_panel meldet nur, welche Namen keine Panel-Einstellung sind
    Config.for_panel(cfg, dict.fromkeys(unstored, ""))


def _save(db: Db, cfg: Config, p: dict, new: dict[str, str]) -> None:
    Config.for_panel(cfg, new)                  # prüft; ValueError nennt die Einstellung
    db.panel_set_settings(p["id"], new)
    print(f"{p['id']}: {len(new)} Einstellungen gesetzt. Gilt nach: docker compose restart flightpanel")


def cmd_set(db: Db, cfg: Config, args) -> None:
    p = _need(db, args.id)
    new = dict(p["settings"])
    reset = []
    for item in args.pairs:
        key, sep, value = item.partition("=")
        key = key.strip().upper()
        if not sep or not key:
            raise ValueError(f"erwartet KEY=WERT, nicht {item!r}")
        if value.strip():
            _check_card(key)
            new[key] = value.strip()
        else:
            reset.append(key)
            new.pop(key, None)
    _check_reset(cfg, p["settings"], reset)
    _save(db, cfg, p, new)


def cmd_unset(db: Db, cfg: Config, args) -> None:
    p = _need(db, args.id)
    gone = {k.strip().upper() for k in args.keys}
    _check_reset(cfg, p["settings"], sorted(gone))
    _save(db, cfg, p, {k: v for k, v in p["settings"].items() if k not in gone})


def cmd_add(db: Db, cfg: Config, args) -> None:
    try:
        panel_id(args.id)
    except ValueError as e:
        raise ValueError(f"id {args.id!r}: {e}") from None
    db.panel_add(args.id, args.name)
    print(f"{args.id} angelegt. Einstellungen: set {args.id} HOME_LAT=... HOME_LON=... VIEW_FROM=...")


def cmd_remove(db: Db, cfg: Config, args) -> None:
    p = _need(db, args.id)
    if p["id"] == cfg.lan_panel:
        raise ValueError(f"{p['id']} ist das Panel am LAN-Port (LAN_PANEL) und bleibt")
    if not args.yes:
        raise ValueError(f"löscht {p['id']} samt Album und Statistik; zum Bestätigen --yes")
    db.panel_remove(p["id"])
    print(f"{p['id']} gelöscht")


def cmd_backup(db: Db, cfg: Config, args) -> None:
    """Nie still überschreiben: Eine vorhandene Datei könnte die Sicherung von gestern sein oder etwas ganz anderes.
    Die Datenbank selbst lehnt Db.backup ab, auch mit --force."""
    if os.path.exists(args.file) and not args.force:
        raise ValueError(f"{args.file} gibt es schon und bleibt, wie sie ist. Einen neuen Namen nehmen, etwa mit "
                         "Datum, oder mit --force ersetzen")
    db.backup(args.file)
    print(f"Kopie: {args.file}")


def main(argv: list[str] | None = None) -> int:
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
        print(f"Fehler: {e}", file=sys.stderr)
        return 2
    finally:
        db.close()


if __name__ == "__main__":
    sys.exit(main())
