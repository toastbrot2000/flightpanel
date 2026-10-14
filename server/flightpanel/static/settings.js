// Einstellungsseite (settings.py, GET /settings): speichert als JSON, zeigt Fehler am Feld, übernimmt eingefügte
// Koordinaten und eicht das Sichtfeld mit den Flugzeugen aus display.json. Eine Datei statt eines Skripts in der
// Seite: Die Content-Security-Policy erlaubt nur Skripte vom Server selbst. Was von außen kommt (Callsigns und Typen
// aus ADS-B, Fehlertexte mit dem Getippten), kommt nur als textContent auf die Seite, nie als HTML.
"use strict";

(() => {
  const form = document.getElementById("settings");
  const status = document.getElementById("status");
  const saveButton = form.querySelector(".save");
  const smooth = !window.matchMedia("(prefers-reduced-motion: reduce)").matches;
  let dirty = false;
  let saving = false;

  // ------------------------------------------------------------------------------------------ Speichern --
  function say(text, tone) {
    status.textContent = text;
    status.className = tone ? "status " + tone : "status";
  }

  function changed() {
    dirty = true;
    say("Geändert, noch nicht gespeichert.", "dirty");
  }

  function clearError(row) {
    row.classList.remove("invalid");
    const error = row.querySelector(".error");
    if (error) {
      error.hidden = true;
      error.textContent = "";
    }
    const control = row.querySelector("[data-key]");
    if (control) control.removeAttribute("aria-invalid");
  }

  function showError(key, message) {
    const row = typeof key === "string" ? document.getElementById("row-" + key) : null;
    const error = row && row.querySelector(".error");
    if (!error) {
      say(message, "bad");
      return;
    }
    const group = row.closest("details");
    if (group) group.open = true;
    error.textContent = message;
    error.hidden = false;
    row.classList.add("invalid");
    const control = row.querySelector("[data-key]");
    control.setAttribute("aria-invalid", "true");
    control.focus({ preventScroll: true });
    row.scrollIntoView({ block: "center", behavior: smooth ? "smooth" : "auto" });
    say("Nicht gespeichert: Das rot markierte Feld passt nicht.", "bad");
  }

  function setField(key, value) {
    const control = document.getElementById("f-" + key);
    if (!control) return;
    control.value = value;
    const row = document.getElementById("row-" + key);
    if (row) clearError(row);
    changed();
  }

  // Alle Felder: leer heißt Standard (null); eine Karte an ist der Standard, aus speichert "0".
  function collect() {
    const settings = {};
    for (const control of form.querySelectorAll("[data-key]")) {
      const key = control.dataset.key;
      if (control.type === "checkbox") {
        settings[key] = control.checked ? null : "0";
      } else {
        const value = control.value.trim();
        settings[key] = value === "" ? null : value;
      }
    }
    return settings;
  }

  async function save(event) {
    event.preventDefault();
    if (saving) return;
    saving = true;
    for (const row of form.querySelectorAll(".invalid")) clearError(row);
    saveButton.disabled = true;
    saveButton.textContent = "Speichere …";
    say("Speichere …");
    try {
      const response = await fetch(form.action, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ settings: collect() }),
      });
      let answer = {};
      try {
        answer = await response.json();
      } catch (e) {
        // keine JSON-Antwort, etwa ein Fehler unterwegs: unten der Status
      }
      if (response.ok && answer.ok === true) {
        dirty = false;
        say("Gespeichert. Das Panel zeigt es ab dem nächsten Bild.", "good");
      } else {
        showError(answer.key, typeof answer.error === "string" ? answer.error
          : "Nicht gespeichert (HTTP " + response.status + ").");
      }
    } catch (e) {
      say("Keine Verbindung zum Server, nichts gespeichert.", "bad");
    } finally {
      saving = false;
      saveButton.disabled = false;
      saveButton.textContent = "Speichern";
    }
  }

  form.addEventListener("submit", save);
  for (const type of ["input", "change"]) {
    form.addEventListener(type, (event) => {
      const control = event.target;
      if (!control.dataset || !control.dataset.key) return;      // Einfügen und Eichen setzen selbst
      const row = control.closest(".invalid");
      if (row) clearError(row);
      changed();
    });
  }
  window.addEventListener("beforeunload", (event) => {
    if (!dirty) return;
    event.preventDefault();
    event.returnValue = "";
  });

  // --------------------------------------------------------------------------- Koordinaten einfügen --
  // "52.52, 13.40" oder "52.52 13.40", wie Karten-Apps sie kopieren: Breite und Länge mit Punkt.
  const COORDINATES = /^\s*(-?\d{1,3}(?:\.\d+)?)\s*(?:,\s*|\s+)(-?\d{1,3}(?:\.\d+)?)\s*$/;
  const paste = document.getElementById("paste");
  const pasteNote = document.getElementById("paste-note");
  const pasteHint = pasteNote.textContent;

  paste.addEventListener("input", () => {
    const text = paste.value;
    const match = COORDINATES.exec(text);
    pasteNote.classList.remove("good", "bad");
    if (!text.trim()) {
      pasteNote.textContent = pasteHint;
      return;
    }
    if (!match || Math.abs(Number(match[1])) > 90 || Math.abs(Number(match[2])) > 180) {
      pasteNote.textContent = "Erwartet Breite und Länge mit Punkt, etwa 52.52, 13.40.";
      pasteNote.classList.add("bad");
      return;
    }
    setField("HOME_LAT", match[1]);
    setField("HOME_LON", match[2]);
    pasteNote.textContent = "Übernommen: Breite " + match[1] + ", Länge " + match[2] + ".";
    pasteNote.classList.add("good");
  });

  // -------------------------------------------------------------------------------------------- Eichen --
  const calib = document.getElementById("calib");
  const planes = document.getElementById("planes");
  const planesNote = document.getElementById("planes-note");
  const calibNote = document.getElementById("calib-note");
  const MAX_PLANES = 8;
  const REFRESH_MS = 5000;
  const HOLD_MS = 4000;                        // nach dem Antippen: die Liste steht still, nichts verrutscht
  const decimal = new Intl.NumberFormat("de-DE", { maximumFractionDigits: 1 });
  const WINDS = ["N", "NNO", "NO", "ONO", "O", "OSO", "SO", "SSO", "S", "SSW", "SW", "WSW", "W", "WNW", "NW", "NNW"];
  const VIEW = { now: ["im Sichtfeld", "view now"], soon: ["gleich im Sichtfeld", "view soon"] };
  const ACTIONS = [                            // Feld, Wert, Knopf, was es ist
    ["VIEW_FROM", "az", "als Anfang des Sichtfelds", "Anfang des Sichtfelds"],
    ["VIEW_TO", "az", "als Ende", "Ende des Sichtfelds"],
    ["VIEW_MIN_ELEV", "elev", "als Mindesthöhe", "Mindesthöhe"],
  ];
  let holdUntil = 0;
  let inView = true;

  const finite = (v) => typeof v === "number" && Number.isFinite(v);

  function node(tag, className, text) {
    const element = document.createElement(tag);
    if (className) element.className = className;
    if (text !== undefined) element.textContent = text;
    return element;
  }

  function note(text) {
    if (planesNote.textContent !== text) planesNote.textContent = text;
  }

  function plane(p) {
    const az = Math.round(p.az) % 360;
    const elev = Math.round(p.elev * 10) / 10;
    const head = node("div", "plane-head");
    head.append(node("span", "cs", String(p.callsign || p.icao24 || "?")));
    if (p.type) head.append(node("span", "type", String(p.type)));
    const view = VIEW[p.in_view];
    head.append(node("span", view ? view[1] : "view", view ? view[0] : "außerhalb"));
    const data = node("div", "plane-data");     // umbrochen wird nur zwischen den Angaben, nie vor "km"
    const facts = ["Richtung " + az + "° " + WINDS[Math.round(az / 22.5) % 16],
      "Höhenwinkel " + decimal.format(elev) + "°", decimal.format(p.dist_km) + " km"];
    facts.forEach((text, i) => {
      if (i) data.append(" · ");
      data.append(node("span", "fact", text));
    });
    const actions = node("div", "plane-actions");
    for (const [key, what, label, name] of ACTIONS) {
      const button = node("button", "", label);
      button.type = "button";
      button.dataset.set = key;              // kein data-key: das wäre ein Feld des Formulars
      button.dataset.value = String(what === "az" ? az : elev);
      button.dataset.name = name;
      actions.append(button);
    }
    const item = node("li", "plane");
    item.append(head, data, actions);
    return item;
  }

  function show(nearby) {
    const usable = nearby.filter((p) => p && finite(p.az) && finite(p.elev) && finite(p.dist_km))
      .sort((a, b) => a.dist_km - b.dist_km);
    const shown = usable.slice(0, MAX_PLANES);
    planes.replaceChildren(...shown.map(plane));
    if (!usable.length) note("Gerade kein Flugzeug in der Nähe. Die Liste füllt sich von selbst.");
    else if (usable.length > shown.length) note("Die " + shown.length + " nächsten von " + usable.length
      + " Flugzeugen, alle 5 s neu.");
    else note("Alle 5 s neu.");
  }

  async function refresh() {
    if (document.hidden || !inView || Date.now() < holdUntil) return;
    try {
      const response = await fetch("display.json", { cache: "no-store" });
      if (!response.ok) throw new Error("HTTP " + response.status);
      const model = await response.json();
      if (Date.now() < holdUntil) return;    // inzwischen angetippt
      show(Array.isArray(model.nearby) ? model.nearby : []);
    } catch (e) {
      note("Flugzeuge gerade nicht abrufbar, neuer Versuch in 5 s.");
    }
  }

  planes.addEventListener("pointerdown", () => {
    holdUntil = Date.now() + HOLD_MS;
  });
  planes.addEventListener("click", (event) => {
    const button = event.target.closest("button[data-set]");
    if (!button) return;
    holdUntil = Date.now() + HOLD_MS;
    setField(button.dataset.set, button.dataset.value);
    calibNote.textContent = button.dataset.name + ": " + button.dataset.value + "° (" + button.dataset.set
      + "). Speichern nicht vergessen.";
  });

  // Nur abrufen, solange das Eichen zu sehen ist: Sonst verschöbe die wachsende Liste, was darunter steht.
  if ("IntersectionObserver" in window) {
    new IntersectionObserver((entries) => {
      inView = entries.some((entry) => entry.isIntersecting);
      if (inView) refresh();
    }).observe(calib);
  }
  document.addEventListener("visibilitychange", refresh);
  refresh();
  setInterval(refresh, REFRESH_MS);
})();
