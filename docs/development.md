# Development

## Tests and lint

The tests run without network access: settings, view field and prediction, landing sequence, routes, card selection, statistics, METAR, sun times (against published times), ISS (against an independent position), every card with demo data, every HTTP endpoint, remote access and the settings page.

In Docker, the same way CI does it (build the image, then lint and test inside it):

```bash
docker build -t flightpanel server
docker run --rm --user root -v "$PWD:/repo" -w /repo flightpanel sh -c \
  "pip install -q --root-user-action=ignore --require-hashes -r server/requirements-dev.lock && ruff check . && python -m pytest -p no:cacheprovider"
```

In Git Bash on Windows, prefix `docker run` with `MSYS_NO_PATHCONV=1`.

Locally, from the repository root (Python 3.12, plus the DejaVu fonts, Debian package `fonts-dejavu-core`, for pixel-identical rendering):

```bash
pip install --require-hashes -r server/requirements-dev.lock
pytest
ruff check .
MOCK=1 DATA_DIR=./data PYTHONPATH=server python3 -m flightpanel.main    # server with fictional flights on :8080
```

GitHub Actions builds the container on every push and runs lint and tests in it; changes under `firmware/` also build the firmware.

## Reference images

The images in `docs/images/` are rendered from fixed demo data by `tools/screenshots.py` and serve as reference frames. After changing anything visual, regenerate them in the container (so the container's fonts are used) and review the diff before committing:

```bash
docker compose run --rm -v "$PWD:/repo" -w /repo flightpanel python tools/screenshots.py
git status docs/images
```

The tool pins the time zone to Europe/Berlin, so the images come out the same on any machine; a test checks that.

## Firmware checks

`sh tools/check_firmware.sh` checks the firmware without a local PlatformIO, in Docker (toolchain and libraries in the volume `flightpanel-pio`, about 1 GB once):

1. `setInsecure()` (TLS without certificate check) may only appear in rescue-mode functions;
2. the test manifest `firmware/test/data/golden_manifest.txt` is validly signed;
3. host tests `pio test -e native`: manifest parser, version comparison and the rescue-mode timer;
4. builds without `include/fw_pubkey.h` and with it (the template as test key).

It builds whatever is in the folder; without `include/secrets.h` that is the image a remote panel gets.

`sh tools/check_firmware_core3.sh` does the same for the experimental Arduino-ESP32 3.x build, with every package verified by SHA-256 ([hardware.md](hardware.md#8-experimental-arduino-esp32-3x-build)).

## Dependency pinning

New versions of Python packages, the base image and build tools only enter the repository once they have been published for at least 7 days, so a compromised release has time to be noticed. Everything is pinned: Python packages with hashes in `*.lock` files (`pip --require-hashes`), the base image by digest, GitHub Actions by commit, firmware libraries by exact version or commit. A weekly workflow (`.github/workflows/escrow.yml`) takes over what is old enough, builds and tests it, and commits only if everything passes; the server only builds from the repository and never fetches the latest version of anything itself.

To add a dependency, add it to `server/requirements.txt` (or `requirements-dev.txt`) and run `sh tools/escrow.sh` (Docker), which rewrites the locks with versions at least 7 days old. Never edit `.lock` files by hand. Note that GitHub disables scheduled workflows in public repositories after 60 days without activity; re-enable it under Actions if that happens in a fork.

## Project layout

```
server/flightpanel/
  main.py        entry point: app (one fetch for all panels), fetch, weather, ISS and save loops
  web.py         HTTP endpoints and preview page; settings.py the settings page; access.py Cloudflare Access checks
  config.py      server settings from .env (Config.from_env), panel settings from the database (Config.for_panel)
  db.py          SQLite database data/flightpanel.db: panels, statistics, album, cache, issues
  panels.py      command line: panel settings, adding panels, database backups
  data.py        bundled data; storage.py atomic file writes
  issues.py      anomaly collection (/issues); cache.py responses with expiry; selfcheck.py trust-list check

  sources        adsblol.py, opensky.py (aircraft), adsbim.py, adsbdb.py (routes),
                 origins.py (origin from the flight track), metar.py (weather), astro.py (sun, ISS),
                 logos.py, logo_image.py, fetch_logos.py (logos)
  model.py       State (one aircraft from a source), Flight (for the panel), DisplayModel
  build.py       builds the DisplayModel on every fetch
  airlines.py    operators, airline or not, route plausibility
  aircraft.py    registrations, aircraft class, text for line 2
  view.py        view field: direction, elevation, when an aircraft enters it
  airport.py     landing direction, landing sequence and time, departures
  airfield.py    what happens at the airport, shared by all panels
  stats.py       daily statistics, collection album, rare aircraft, lulls
  panel.py       one panel (settings, model, renderer, statistics) and the shared fetch radius
  mock.py        fictional flights for MOCK=1

  render.py      flight layouts, sky dots, frame bytes and PNG
  draw.py        line grid and text helpers; fonts.py, colors.py, sprites.py
  idle.py        idle card selection (tiers, weights, urgent cards)
  cards/         the cards: traffic, runway, album, sky, fun; list in __init__.py
server/tests/    pytest
firmware/        ESP32: platformio.ini, src/, include/config.h, *.example templates, test/ (host tests)
tools/           make_logo.py, make_airports.py, make_roots.py, screenshots.py, check_firmware*.sh,
                 fw_release.sh, escrow.py/.sh, update.sh
data/            airlines.json, airports.json, rare.json; at runtime logos/ and flightpanel.db
docs/            documentation and images/
```

## Adding an idle card

1. In the matching file under `server/flightpanel/cards/` add two functions: `foo_cond(ctx)` returns `Hit(data)`, or `None` when the card has nothing to say; `foo_draw(img, ctx, data, t_in, f)` draws it. Optionally `foo_demo(ctx)` with demo data. What `ctx` provides: `idle.Ctx`; text helpers: `draw.py`.
2. Add an entry to `CARDS` in `cards/__init__.py`, with tier and weight.
3. Look at it: `/frame.png?card=foo`. The test `test_every_card_renders_with_demo_data` renders it automatically.
4. Add a line to [screens.md](screens.md). Cards are on by default; `panels set home CARD_FOO=0` turns it off.
