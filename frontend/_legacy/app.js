// Minimal BMS dashboard. No build step, no dependencies: plain fetch +
// EventSource against the FastAPI backend.
//
// The API base is editable in the top bar and persisted in localStorage so a
// deployed frontend can point at a remote backend without a rebuild.

const DEFAULT_API = "http://localhost:8000";
const API_KEY = "bms.apiBase";

const state = {
  apiBase: localStorage.getItem(API_KEY) || DEFAULT_API,
  devices: [],
  selected: null,
  stream: null,
  adminKey: "",
};

const $ = (id) => document.getElementById(id);

function api(path) {
  return `${state.apiBase.replace(/\/$/, "")}${path}`;
}

async function getJSON(path, opts = {}) {
  const res = await fetch(api(path), opts);
  const text = await res.text();
  let body = null;
  try {
    body = text ? JSON.parse(text) : null;
  } catch {
    body = text;
  }
  if (!res.ok) {
    const detail = body && body.detail ? body.detail : res.statusText;
    throw new Error(typeof detail === "string" ? detail : JSON.stringify(detail));
  }
  return body;
}

// -- devices ---------------------------------------------------------------

async function loadDevices() {
  try {
    state.devices = await getJSON("/api/v1/devices");
    renderDevices();
  } catch (err) {
    logEvent("error", `devices: ${err.message}`);
  }
}

function renderDevices() {
  const list = $("device-list");
  list.innerHTML = "";
  $("device-empty").hidden = state.devices.length > 0;
  for (const device of state.devices) {
    const li = document.createElement("li");
    const btn = document.createElement("button");
    btn.className = "btn";
    const name = device.name || device.device_id;
    btn.innerHTML = `<span>${name}</span><span class="muted">${device.status}</span>`;
    btn.onclick = () => selectDevice(device.device_id);
    li.appendChild(btn);
    list.appendChild(li);
  }
}

async function selectDevice(deviceId) {
  state.selected = deviceId;
  $("detail").hidden = false;
  $("detail-title").textContent = deviceId;
  await loadDetail();
}

async function loadDetail() {
  if (!state.selected) return;
  const id = encodeURIComponent(state.selected);

  const [telemetry, rul, alerts] = await Promise.allSettled([
    getJSON(`/api/v1/telemetry/latest/${id}`),
    getJSON(`/api/v1/devices/${id}/rul`),
    getJSON(`/api/v1/devices/${id}/alerts?status=active`),
  ]);

  if (telemetry.status === "fulfilled") {
    $("detail-telemetry").textContent = JSON.stringify(telemetry.value, null, 2);
  } else {
    $("detail-telemetry").textContent = `no telemetry (${telemetry.reason.message})`;
  }

  if (rul.status === "fulfilled") {
    const r = rul.value;
    $("c-rul").textContent =
      r.rul_discharges === null || r.rul_discharges === undefined
        ? r.status
        : `${r.rul_discharges} ✕ [${fmt(r.p10_discharges)}–${fmt(r.p90_discharges)}]`;
  } else {
    $("c-rul").textContent = "—";
  }

  if (alerts.status === "fulfilled") {
    const items = alerts.value.items || [];
    $("c-alerts").textContent = String(items.length);
    renderAlerts(items);
  } else {
    $("c-alerts").textContent = "—";
    renderAlerts([]);
  }

  const device = state.devices.find((d) => d.device_id === state.selected);
  $("c-status").textContent = device ? device.status : "—";
  // Latest predicted SoH is per-cycle; fall back to when the device was last
  // heard from so the card is never blank for a device that has reported.
  $("c-soh").textContent =
    device && device.last_seen_at
      ? new Date(device.last_seen_at).toLocaleTimeString()
      : "—";
}

function fmt(v) {
  return v === null || v === undefined ? "?" : Math.round(v);
}

function renderAlerts(items) {
  const ul = $("detail-alerts");
  ul.innerHTML = "";
  if (!items.length) {
    ul.innerHTML = '<li class="muted">none</li>';
    return;
  }
  for (const a of items) {
    const li = document.createElement("li");
    li.innerHTML =
      `<span class="sev sev--${a.severity}">${a.severity}</span> ` +
      `<span class="tag">${a.rule_code}</span> — ${a.message}`;
    ul.appendChild(li);
  }
}

// -- registration ----------------------------------------------------------

function readForm(form) {
  const data = new FormData(form);
  const payload = {
    device_id: data.get("device_id"),
    name: data.get("name") || null,
    cell_count: Number(data.get("cell_count")),
    nominal_capacity_ah: Number(data.get("nominal_capacity_ah")),
    chemistry: data.get("chemistry"),
  };
  const credential = data.get("credential");
  if (credential) payload.credential = credential;
  return { payload, adminKey: data.get("admin_key") };
}

async function registerDevice(event) {
  event.preventDefault();
  const form = event.target;
  const { payload, adminKey } = readForm(form);
  const headers = { "Content-Type": "application/json" };
  if (adminKey) headers["X-Admin-Key"] = adminKey;

  const msg = $("register-msg");
  msg.textContent = "";
  msg.className = "msg";
  try {
    const device = await getJSON("/api/v1/devices", {
      method: "POST",
      headers,
      body: JSON.stringify(payload),
    });
    state.adminKey = adminKey;
    msg.textContent = `registered ${device.device_id}`;
    msg.classList.add("msg--ok");
    form.reset();
    await loadDevices();
  } catch (err) {
    msg.textContent = `error: ${err.message}`;
    msg.classList.add("msg--err");
  }
}

// -- live stream -----------------------------------------------------------

function connectStream() {
  if (state.stream) state.stream.close();
  setLive("wait");

  const url = api("/api/v1/stream");
  let source;
  try {
    source = new EventSource(url);
  } catch (err) {
    setLive("off");
    logEvent("error", `stream: ${err.message}`);
    return;
  }
  state.stream = source;

  source.onopen = () => {
    setLive("on");
    logEvent("stream", "connected");
  };
  source.onerror = () => {
    setLive("off");
  };
  source.onmessage = (event) => {
    let frame;
    try {
      frame = JSON.parse(event.data);
    } catch {
      return;
    }
    logEvent(frame.kind, summarize(frame));
    onLiveEvent(frame);
  };
}

function summarize(frame) {
  const d = frame.data || {};
  if (frame.kind === "telemetry") return `${d.device_id} @ ${d.measured_at || ""}`;
  if (frame.kind === "cycle") return `${d.device_id}: ${d.cycles} cycle(s), ${d.predictions} prediction(s)`;
  if (frame.kind === "alert") return `${d.device_id}: +${d.opened} -${d.resolved} (firing: ${(d.firing || []).join(", ") || "none"})`;
  if (frame.kind === "device") return `${d.device_id} (${d.status})`;
  if (frame.kind === "monitor") return `${d.processed} device(s)`;
  return JSON.stringify(d);
}

function onLiveEvent(frame) {
  if (frame.kind === "device") {
    loadDevices();
    return;
  }
  const deviceId = frame.data && frame.data.device_id;
  if (!deviceId) return;
  // Refresh the list badges for any device change; refresh the detail panel
  // only when the event is about the device the user is looking at.
  if (deviceId === state.selected) {
    loadDetail();
  }
}

function setLive(stateName) {
  const dot = $("live-dot");
  const label = $("live-label");
  dot.className = `dot dot--${stateName}`;
  label.textContent =
    stateName === "on" ? "live: connected" : stateName === "wait" ? "live: connecting…" : "live: disconnected";
}

function logEvent(kind, text) {
  const ul = $("event-log");
  const li = document.createElement("li");
  const time = new Date().toLocaleTimeString();
  li.innerHTML = `<span class="muted">${time}</span> <span class="tag">${kind}</span> ${text}`;
  ul.prepend(li);
  while (ul.children.length > 200) ul.removeChild(ul.lastChild);
}

// -- wiring ----------------------------------------------------------------

function init() {
  const base = $("api-base");
  base.value = state.apiBase;
  base.onchange = () => {
    state.apiBase = base.value.trim() || DEFAULT_API;
    localStorage.setItem(API_KEY, state.apiBase);
    logEvent("config", `api base → ${state.apiBase}`);
    loadDevices();
    connectStream();
  };

  $("refresh-devices").onclick = loadDevices;
  $("refresh-detail").onclick = loadDetail;
  $("clear-log").onclick = () => ($("event-log").innerHTML = "");
  $("register-form").onsubmit = registerDevice;

  loadDevices();
  connectStream();
}

init();
