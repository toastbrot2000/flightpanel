"""flightpanel: zeigt auf einem 128 x 32 LED-Panel die Flugzeuge vor dem Fenster.

Ablauf pro Abruf: Quellen (adsblol.py, opensky.py; Routen adsbim.py, adsbdb.py,
origins.py) -> State -> build.py -> DisplayModel -> render.py (Fluglayouts) oder
idle.py mit cards/ (Leerlauf-Karten) -> 128 x 32 Bild -> web.py (/frame für den
ESP32, /frame.png für den Browser). main.py verbindet alles, config.py liest .env.
Überblick über alle Module: docs/development.md#project-layout."""

USER_AGENT = "flightpanel/1.0"
