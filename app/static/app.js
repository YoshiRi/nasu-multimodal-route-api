const map = L.map("map", { preferCanvas: true }).setView([37.05, 140.0], 11);
L.tileLayer("https://{s}.tile.openstreetmap.org/{z}/{x}/{y}.png", {
  maxZoom: 19,
  attribution: "&copy; OpenStreetMap contributors",
}).addTo(map);

const statusEl = document.getElementById("status");
function setStatus(message) {
  statusEl.textContent = message;
}

let stops = [];
const stopMarkers = new Map();
const spotMarkers = new Map();

function colorForRouteId(routeId) {
  const text = routeId || "";
  let hash = 0;
  for (let i = 0; i < text.length; i++) {
    hash = (hash * 31 + text.charCodeAt(i)) >>> 0;
  }
  return `hsl(${hash % 360}, 70%, 45%)`;
}

function escapeHtml(text) {
  const div = document.createElement("div");
  div.textContent = text;
  return div.innerHTML;
}

async function loadNetwork() {
  setStatus("路線データを読み込み中...");
  const response = await fetch("/transit/network");
  if (!response.ok) {
    setStatus("路線データの取得に失敗しました");
    return;
  }
  const data = await response.json();
  stops = data.stops;

  for (const shape of data.shapes) {
    if (shape.points.length < 2) continue;
    L.polyline(shape.points, {
      color: colorForRouteId(shape.route_id),
      weight: 2,
      opacity: 0.6,
    })
      .bindPopup(shape.line_name || shape.shape_id)
      .addTo(map);
  }

  for (const stop of stops) {
    const marker = L.circleMarker([stop.stop_lat, stop.stop_lon], {
      radius: 3,
      color: "#2266cc",
      fillColor: "#2266cc",
      fillOpacity: 0.8,
      weight: 1,
    })
      .bindPopup(escapeHtml(stop.stop_name))
      .addTo(map);
    stopMarkers.set(stop.stop_id, marker);
  }

  setStatus(`停留所 ${stops.length}件 / 路線形状 ${data.shapes.length}件を表示中`);
}

function addSpotMarker(spot) {
  const marker = L.circleMarker([spot.lat, spot.lng], {
    radius: 8,
    color: "#d98c2b",
    fillColor: "#ffb347",
    fillOpacity: 0.9,
    weight: 2,
  })
    .bindPopup(`<strong>${escapeHtml(spot.name)}</strong><br>${escapeHtml(spot.spot_id)}`)
    .addTo(map);
  spotMarkers.set(spot.spot_id, marker);
}

async function loadSpots() {
  const response = await fetch("/spots");
  if (!response.ok) return;
  const spots = await response.json();
  for (const spot of spots) {
    addSpotMarker(spot);
  }
}

// Stop name search (client-side filter over the already-fetched stop list).
const searchInput = document.getElementById("stop-search");
const searchResultsEl = document.getElementById("stop-search-results");

searchInput.addEventListener("input", () => {
  const query = searchInput.value.trim().toLowerCase();
  searchResultsEl.innerHTML = "";
  if (!query) return;

  const matches = stops.filter((s) => s.stop_name.toLowerCase().includes(query)).slice(0, 30);
  for (const stop of matches) {
    const item = document.createElement("div");
    item.className = "search-result";
    item.textContent = stop.stop_name;
    item.addEventListener("click", () => {
      map.setView([stop.stop_lat, stop.stop_lon], 16);
      const marker = stopMarkers.get(stop.stop_id);
      if (marker) marker.openPopup();
    });
    searchResultsEl.appendChild(item);
  }
});

// Add-spot flow: toggle a mode, click the map to place, fill in a name, submit.
let addSpotMode = false;
let pendingLatLng = null;
const addSpotToggle = document.getElementById("add-spot-toggle");
const addSpotForm = document.getElementById("add-spot-form");
const addSpotNameInput = document.getElementById("add-spot-name");
const addSpotCoordsEl = document.getElementById("add-spot-coords");

addSpotToggle.addEventListener("click", () => {
  addSpotMode = !addSpotMode;
  addSpotToggle.textContent = `地図クリックで追加: ${addSpotMode ? "ON" : "OFF"}`;
  addSpotToggle.classList.toggle("active", addSpotMode);
  if (!addSpotMode) {
    addSpotForm.hidden = true;
    pendingLatLng = null;
  }
});

map.on("click", (event) => {
  if (!addSpotMode) return;
  pendingLatLng = event.latlng;
  addSpotForm.hidden = false;
  addSpotNameInput.value = "";
  addSpotCoordsEl.textContent = `緯度 ${event.latlng.lat.toFixed(5)}, 経度 ${event.latlng.lng.toFixed(5)}`;
  addSpotNameInput.focus();
});

document.getElementById("add-spot-cancel").addEventListener("click", () => {
  addSpotForm.hidden = true;
  pendingLatLng = null;
});

document.getElementById("add-spot-submit").addEventListener("click", async () => {
  const name = addSpotNameInput.value.trim();
  if (!name || !pendingLatLng) return;

  setStatus("スポットを追加中...");
  try {
    const response = await fetch("/spots", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ name, lat: pendingLatLng.lat, lng: pendingLatLng.lng }),
    });
    if (!response.ok) {
      const body = await response.json().catch(() => ({}));
      setStatus(`追加に失敗しました: ${body.detail || response.status}`);
      return;
    }
    const spot = await response.json();
    addSpotMarker(spot);
    setStatus(`スポット「${spot.name}」を追加しました`);
    addSpotForm.hidden = true;
    pendingLatLng = null;
  } catch (err) {
    setStatus(`追加に失敗しました: ${err}`);
  }
});

// Date/time inputs just hold state for the next-step routing UI.
document.getElementById("plan-date").valueAsDate = new Date();

loadNetwork();
loadSpots();
