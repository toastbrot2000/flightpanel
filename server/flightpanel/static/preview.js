// Vorschauseite (web.py, GET /): das Bild alle 2 s neu, die Flugzeuge in der Nähe alle 5 s aus display.json, und
// der Zustand oben (Live, Ruhezeit, alte Daten) aus /health am LAN-Port, sonst aus display.json. Eine Datei statt
// eines Skripts in der Seite: Die Content-Security-Policy erlaubt nur Skripte vom Server selbst. Was aus ADS-B kommt
// (Callsigns, Airlines, Typen), kommt nur als textContent auf die Seite, nie als HTML.
"use strict";

(() => {
  const FRAME_MS = 2000;
  const MODEL_MS = 5000;
  const HEALTH_MS = 10000;
  const MAX_FLIGHTS = 8;

  // ------------------------------------------------------------------------------------------------ Bild --
  // Erst laden, dann tauschen: Das Bild flackert nicht und bleibt stehen, wenn ein Abruf scheitert.
  const img = document.getElementById("f");
  const base = img.getAttribute("src");          // /frame.png?layout=…&card=…, wie die Seite es vorgibt
  let loading = false;
  setInterval(() => {
    if (document.hidden || loading) return;
    loading = true;
    const next = new Image();
    next.onload = () => {
      img.src = next.src;
      loading = false;
    };
    next.onerror = () => {
      loading = false;
    };
    next.src = base + "&t=" + Date.now();
  }, FRAME_MS);

  // --------------------------------------------------------------------------------------------- Zustand --
  const state = document.getElementById("s");
  const stateText = document.getElementById("s-text");
  const live = state.dataset.mode === "live";    // Probeansicht und Beispielkarte sagen selbst, was sie sind
  const health = state.dataset.health;           // nur am LAN-Port

  function setState(mode, text) {
    if (!live) return;
    if (state.dataset.mode !== mode) state.dataset.mode = mode;
    if (stateText.textContent !== text) stateText.textContent = text;
  }

  if (health) {
    const check = () => fetch(health, { cache: "no-store" }).then((r) => r.json()).then((h) => {
      if (h.brightness_now === 0) setState("quiet", "Ruhezeit: Das Panel ist dunkel");
      else if (h.model_stale) setState("warn", "Keine frischen Flugdaten");
      else setState("live", "Live");
    }).catch(() => setState("warn", "Server nicht erreichbar"));
    check();
    setInterval(check, HEALTH_MS);
  }

  // ----------------------------------------------------------------------------------------- In der Nähe --
  const list = document.getElementById("flights");
  const empty = document.getElementById("flights-empty");
  const meta = document.getElementById("sky-meta");
  const nextLine = document.getElementById("next");
  const number = new Intl.NumberFormat("de-DE", { maximumFractionDigits: 0 });
  const decimal = new Intl.NumberFormat("de-DE", { maximumFractionDigits: 1 });
  const WINDS = ["Norden", "Nordosten", "Osten", "Südosten", "Süden", "Südwesten", "Westen", "Nordwesten"];
  const finite = (v) => typeof v === "number" && Number.isFinite(v);

  function node(tag, className, text) {
    const element = document.createElement(tag);
    if (className) element.className = className;
    if (text !== undefined && text !== null) element.textContent = String(text);
    return element;
  }

  function minutes(s) {
    const m = Math.max(1, Math.round(s / 60));
    return m;
  }

  function route(f) {
    const line = node("div", "route");
    const iata = (code) => node("span", "iata", code);
    if (f.status === "ARR") {
      line.append("aus ", iata(f.origin || "?"));
    } else if (f.status === "DEP") {
      line.append("nach ", iata(f.dest || "?"));
    } else if (f.origin || f.dest) {
      line.append(iata(f.origin || "?"), " → ", iata(f.dest || "?"));
    } else {
      line.append("Überflug");
    }
    if (f.actype) {
      line.append(node("span", "sep", "·"), node("span", "type", f.actype));
    }
    if (f.visible) line.append(node("span", "badge", "im Blick"));
    return line;
  }

  function figure(f) {
    const box = node("div", "figure");
    const num = node("span", "num");
    let cap;
    if (f.status === "ARR" && finite(f.land_eta_s)) {
      num.append(String(minutes(f.land_eta_s)), node("small", "", "min"));
      cap = "bis zur Landung";
    } else if (finite(f.dist_km)) {
      num.append(decimal.format(f.dist_km), node("small", "", "km"));
      cap = finite(f.alt_ft) && f.alt_ft > 0 ? number.format(Math.round(f.alt_ft * 0.3048 / 50) * 50) + " m hoch"
        : "entfernt";
    } else {
      return box;
    }
    box.append(num, node("span", "cap", cap));
    return box;
  }

  function flight(f) {
    const item = node("li", "flight " + String(f.status || "").toLowerCase());
    const who = node("div", "who");
    who.append(node("span", "airline", f.airline || f.label || "Unbekannt"));
    if (f.callsign) who.append(node("span", "callsign", f.callsign));
    const text = node("div", "text");
    text.append(who, route(f));
    item.append(text, figure(f));
    return item;
  }

  function upcoming(m) {
    const f = m.upcoming;
    if (!f || !finite(m.upcoming_eta_s)) return "";
    const age = Math.max(0, Date.now() / 1000 - (m.generated_at || 0));
    const eta = Math.max(0, m.upcoming_eta_s - age);
    nextLine.replaceChildren(
      "Gleich im Blick: ",
      node("strong", "", f.airline || f.label || f.callsign || "ein Flugzeug"),
      eta < 45 ? " in wenigen Sekunden" : " in etwa " + minutes(eta) + " min",
      finite(m.upcoming_az) ? ", aus " + WINDS[Math.round(m.upcoming_az / 45) % 8] : "",
      ".",
    );
    return "shown";
  }

  function show(m) {
    const flights = (Array.isArray(m.flights) ? m.flights : []).filter((f) => f && typeof f === "object");
    list.replaceChildren(...flights.slice(0, MAX_FLIGHTS).map(flight));
    const seen = flights.filter((f) => f.visible).length;
    meta.textContent = flights.length
      ? flights.length + (flights.length === 1 ? " Flug" : " Flüge") + (seen ? ", " + seen + " im Blick" : "")
      : "";
    if (flights.length) empty.textContent = "";
    else if (m.stale) empty.textContent = "Gerade keine frischen Flugdaten. Das Panel versucht es weiter.";
    else empty.textContent = "Gerade ist kein Flugzeug nah genug fürs Panel. Es zeigt solange Karten wie Uhr und "
      + "Wetter.";
    if (!upcoming(m)) nextLine.textContent = "";
    if (!health) setState(m.stale ? "warn" : "live", m.stale ? "Keine frischen Flugdaten" : "Live");
  }

  async function refresh() {
    if (document.hidden) return;
    try {
      const response = await fetch("display.json", { cache: "no-store" });
      if (!response.ok) throw new Error("HTTP " + response.status);
      show(await response.json());
    } catch (e) {
      if (!list.children.length) empty.textContent = "Flugdaten gerade nicht abrufbar, neuer Versuch in 5 s.";
    }
  }

  document.addEventListener("visibilitychange", refresh);
  refresh();
  setInterval(refresh, MODEL_MS);
})();
