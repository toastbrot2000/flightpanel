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

Namen gelten in jeder Schreibweise (home_lat wie HOME_LAT). set und unset treffen auch einen
Schlüssel, der von Hand klein geschrieben in die Datenbank kam, und räumen ihn weg.

Im Container:
  docker compose exec flightpanel python3 -m flightpanel.panels set home HOME_LAT=52.52 HOME_LON=13.40
Der Server liest die Einstellungen beim Start: danach docker compose restart flightpanel."""

import argparse
import os
import sys
import time

from .cards import CARDS
from .config import CARD_PREFIX, Config, default_text, panel_fields, panel_id, too_far
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
        print(f"{p['id']:<12} {p['name']:<20} {len(p['settings']):>2} Einstellungen, seit {when}{_note(cfg, p)}")


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
        print(f"{k}={v}")
    if not p["settings"]:
        print(f"{p['id']}: nichts gesetzt, alles auf Default (keys zeigt die Defaults)")


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
        _check_card(key)
    # Leer gilt als nicht gesetzt: for_panel meldet nur, welche Namen keine Panel-Einstellung sind
    Config.for_panel(cfg, dict.fromkeys(unstored, ""))


def _check(cfg: Config, pid: str, settings: dict[str, str]) -> None:
    """Prüft die Einstellungen eines Panels mit denselben Parsern wie der Server. Für weitere Panels (nicht das am
    LAN-Port) gelten dazu die Grenzen aus .env: Abrufradius höchstens PANEL_MAX_RADIUS_KM, das Fenster höchstens
    PANEL_MAX_KM vom Flughafen. ValueError nennt, was nicht passt."""
    remote = pid != cfg.lan_panel
    panel = Config.for_panel(cfg, settings, remote=remote)
    why = too_far(cfg, panel) if remote else None
    if why:
        raise ValueError(f"{why}. HOME_LAT und HOME_LON zusammen aufs Fenster setzen, oder PANEL_MAX_KM in .env "
                         "erhöhen")


def _save(db: Db, cfg: Config, p: dict, new: dict[str, str]) -> None:
    _check(cfg, p["id"], new)                   # prüft; ValueError nennt die Einstellung
    db.panel_set_settings(p["id"], new)
    print(f"{p['id']}: {len(new)} Einstellungen gesetzt. Gilt nach: docker compose restart flightpanel")


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
            _check_card(key)
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
