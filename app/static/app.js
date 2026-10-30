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
let spots = [];
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
  marker.on("click", () => {
    if (!routeMode) return;
    addSpotToRoute(spot);
  });
  spotMarkers.set(spot.spot_id, marker);
}

// Shared by marker clicks and the name-search "add" UI. Silently ignores a
// spot already in the route (matches the marker-click dedup behavior).
function addSpotToRoute(spot) {
  if (routeSelection.some((s) => s.spot_id === spot.spot_id)) return;
  routeSelection.push({
    spot_id: spot.spot_id,
    name: spot.name,
    lat: spot.lat,
    lng: spot.lng,
    dwellMinutes: null,
    // Shown as the placeholder: what happens when the box is left empty.
    typicalDwellMinutes: spot.dwell_minutes ?? 60,
    requiredArrivalTime: "",
    required: true,
    settingsOpen: false,
  });
  renderRouteSelection();
}

async function loadSpots() {
  const response = await fetch("/spots");
  if (!response.ok) return;
  spots = await response.json();
  for (const spot of spots) {
    addSpotMarker(spot);
  }
  populateSuggestControls();
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
  } else if (routeMode) {
    routeModeToggle.click();
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

document.getElementById("plan-date").valueAsDate = new Date();

// Route-creation flow: toggle a mode, click existing spot markers in
// order (1st = base, rest = targets in visit order), then run
// /plan/route-plan and open the results in a new tab.
let routeMode = false;
let routeSelection = [];
const routeModeToggle = document.getElementById("route-mode-toggle");
const routeSelectionListEl = document.getElementById("route-selection-list");
const routeSearchButton = document.getElementById("route-search-submit");
const routeRoundTripCheckbox = document.getElementById("route-round-trip");
const routeOptimizeCheckbox = document.getElementById("route-optimize");

// Name-based add: the primary way to build a route — type a spot name,
// click a match to append it (badges in the list below show its role).
const routeSpotSearchInput = document.getElementById("route-spot-search");
const routeSpotSearchResultsEl = document.getElementById("route-spot-search-results");

routeSpotSearchInput.addEventListener("input", () => {
  const query = routeSpotSearchInput.value.trim().toLowerCase();
  routeSpotSearchResultsEl.innerHTML = "";
  if (!query) return;

  const matches = spots.filter((s) => s.name.toLowerCase().includes(query)).slice(0, 30);
  for (const spot of matches) {
    const item = document.createElement("div");
    item.className = "search-result";
    item.textContent = spot.name;
    item.addEventListener("click", () => {
      addSpotToRoute(spot);
      routeSpotSearchInput.value = "";
      routeSpotSearchResultsEl.innerHTML = "";
      routeSpotSearchInput.focus();
    });
    routeSpotSearchResultsEl.appendChild(item);
  }
});

function renderRouteSelection() {
  routeSelectionListEl.innerHTML = "";
  routeSelection.forEach((spot, index) => {
    const isBase = index === 0;
    const isLastTarget = !isBase && index === routeSelection.length - 1;

    const item = document.createElement("div");
    item.className = "route-selection-item";

    const label = document.createElement("span");
    label.className = "label";
    let badge;
    if (isBase) {
      badge = "起点";
    } else if (isLastTarget && !routeRoundTripCheckbox.checked) {
      badge = "終点";
    } else if (routeOptimizeCheckbox.checked) {
      // The order is about to be decided for them, so a number here lies.
      badge = "経由";
    } else {
      badge = `${index}`;
    }
    label.innerHTML = `<span class="badge">${badge}</span>${escapeHtml(spot.name)}`;

    const buttons = document.createElement("span");
    buttons.className = "row-buttons";

    if (!isBase) {
      const settingsButton = document.createElement("button");
      settingsButton.textContent = "⚙";
      settingsButton.title = "滞在時間・集合時刻・マスト設定";
      settingsButton.addEventListener("click", () => {
        spot.settingsOpen = !spot.settingsOpen;
        renderRouteSelection();
      });
      buttons.appendChild(settingsButton);
    }

    const removeButton = document.createElement("button");
    removeButton.textContent = "×";
    removeButton.addEventListener("click", () => {
      routeSelection.splice(index, 1);
      renderRouteSelection();
    });
    buttons.appendChild(removeButton);

    item.appendChild(label);
    item.appendChild(buttons);
    routeSelectionListEl.appendChild(item);

    if (!isBase && spot.settingsOpen) {
      routeSelectionListEl.appendChild(buildSpotSettingsForm(spot));
    }
  });
  routeSearchButton.disabled = routeSelection.length < 2;
}

function buildSpotSettingsForm(spot) {
  const form = document.createElement("div");
  form.className = "spot-settings";

  const dwellLabel = document.createElement("label");
  dwellLabel.textContent = "滞在時間（分）";
  const dwellInput = document.createElement("input");
  dwellInput.type = "number";
  dwellInput.min = "1";
  dwellInput.placeholder = String(spot.typicalDwellMinutes ?? 60);
  dwellInput.value = spot.dwellMinutes ?? "";
  dwellInput.addEventListener("input", () => {
    const value = parseInt(dwellInput.value, 10);
    spot.dwellMinutes = Number.isFinite(value) && value > 0 ? value : null;
  });
  dwellLabel.appendChild(dwellInput);

  const reservationLabel = document.createElement("label");
  reservationLabel.textContent = "集合時刻";
  const reservationInput = document.createElement("input");
  reservationInput.type = "time";
  reservationInput.value = spot.requiredArrivalTime || "";
  reservationInput.addEventListener("input", () => {
    spot.requiredArrivalTime = reservationInput.value;
  });
  reservationLabel.appendChild(reservationInput);

  const requiredLabel = document.createElement("label");
  requiredLabel.textContent = "マスト（必須訪問）";
  const requiredInput = document.createElement("input");
  requiredInput.type = "checkbox";
  requiredInput.checked = spot.required;
  requiredInput.addEventListener("change", () => {
    spot.required = requiredInput.checked;
  });
  requiredLabel.appendChild(requiredInput);

  form.appendChild(dwellLabel);
  form.appendChild(reservationLabel);
  form.appendChild(requiredLabel);
  return form;
}

for (const checkbox of [routeRoundTripCheckbox, routeOptimizeCheckbox]) {
  checkbox.addEventListener("change", () => {
    renderRouteSelection();
  });
}

routeModeToggle.addEventListener("click", () => {
  routeMode = !routeMode;
  routeModeToggle.textContent = `地図クリックで経由地を追加: ${routeMode ? "ON" : "OFF"}`;
  routeModeToggle.classList.toggle("active", routeMode);
  if (routeMode && addSpotMode) {
    addSpotToggle.click();
  }
});

routeSearchButton.addEventListener("click", async () => {
  if (routeSelection.length < 2) return;
  const [base, ...targets] = routeSelection;
  const departureDate = document.getElementById("plan-date").value;
  const departureTime = document.getElementById("plan-time").value;
  if (!departureDate || !departureTime) {
    setStatus("日付と時刻を指定してください");
    return;
  }

  const transferBufferMinutes = parseInt(document.getElementById("transfer-buffer").value, 10) || 0;

  const targetSpotIds = targets.map((spot) => ({
    spot_id: spot.spot_id,
    dwell_minutes: spot.dwellMinutes || undefined,
    required_arrival_time: spot.requiredArrivalTime || undefined,
    required: spot.required,
  }));
  if (routeRoundTripCheckbox.checked) {
    // The trip ends at the last target by default; listing base_location_id
    // again as the final target is how a round trip is expressed.
    targetSpotIds.push({ spot_id: base.spot_id });
  }

  setStatus("ルートを検索中...");
  const resultsTab = openPendingTab();
  try {
    const response = await fetch("/plan/route-plan", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({
        base_location_id: base.spot_id,
        target_spot_ids: targetSpotIds,
        departure_time: departureTime,
        departure_date: departureDate,
        transfer_buffer_minutes: transferBufferMinutes,
        optimize_visit_order: routeOptimizeCheckbox.checked,
      }),
    });
    if (!response.ok) {
      const body = await response.json().catch(() => ({}));
      resultsTab.cancel();
      setStatus(`ルート検索に失敗しました: ${body.detail || response.status}`);
      return;
    }
    const result = await response.json();
    localStorage.setItem("latest_route_plan", JSON.stringify(result));
    resultsTab.show("results.html");
    setStatus(result.status === "success" ? "ルートが見つかりました" : `不成立: ${result.ai_hint}`);
  } catch (err) {
    resultsTab.cancel();
    setStatus(`ルート検索に失敗しました: ${err}`);
  }
});


// Suggestion flow: let the planner choose the spots. Start/end are fixed,
// tags narrow what's eligible, and the candidates open in their own tab.
const suggestBaseSelect = document.getElementById("suggest-base");
const suggestDestinationSelect = document.getElementById("suggest-destination");
const suggestTagsEl = document.getElementById("suggest-tags");

function populateSuggestControls() {
  const byName = [...spots].sort((a, b) => a.name.localeCompare(b.name, "ja"));
  for (const select of [suggestBaseSelect, suggestDestinationSelect]) {
    select.innerHTML = "";
    for (const spot of byName) {
      const option = document.createElement("option");
      option.value = spot.spot_id;
      option.textContent = spot.name;
      select.appendChild(option);
    }
  }
  if (spots.some((s) => s.spot_id === "station_nasushiobara")) {
    suggestBaseSelect.value = "station_nasushiobara";
  }
  if (spots.some((s) => s.spot_id === "spot_hotel_epinard")) {
    suggestDestinationSelect.value = "spot_hotel_epinard";
  }

  const tags = [...new Set(spots.flatMap((spot) => spot.tags || []))].sort((a, b) =>
    a.localeCompare(b, "ja"),
  );
  suggestTagsEl.innerHTML = "";
  for (const tag of tags) {
    const label = document.createElement("label");
    label.className = "tag-choice";
    const input = document.createElement("input");
    input.type = "checkbox";
    input.value = tag;
    label.appendChild(input);
    label.appendChild(document.createTextNode(tag));
    suggestTagsEl.appendChild(label);
  }
}

document.getElementById("suggest-submit").addEventListener("click", async () => {
  const departureDate = document.getElementById("plan-date").value;
  const departureTime = document.getElementById("plan-time").value;
  const arriveBy = document.getElementById("suggest-arrive-by").value;
  if (!departureDate || !departureTime || !arriveBy) {
    setStatus("日付・出発時刻・到着したい時刻を指定してください");
    return;
  }
  if (suggestBaseSelect.value === suggestDestinationSelect.value) {
    setStatus("起点と終点には違うスポットを選んでください");
    return;
  }

  const tags = [...suggestTagsEl.querySelectorAll("input:checked")].map((i) => i.value);
  const baseSpot = spots.find((s) => s.spot_id === suggestBaseSelect.value);
  const destinationSpot = spots.find((s) => s.spot_id === suggestDestinationSelect.value);

  setStatus("候補を探しています...");
  const suggestTab = openPendingTab();
  try {
    const response = await fetch("/plan/suggest", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({
        base_location_id: suggestBaseSelect.value,
        destination_spot_id: suggestDestinationSelect.value,
        departure_time: departureTime,
        departure_date: departureDate,
        arrive_by: arriveBy,
        spot_count: parseInt(document.getElementById("suggest-spot-count").value, 10) || 3,
        max_taxi_legs: parseInt(document.getElementById("suggest-taxi").value, 10) || 0,
        tags,
      }),
    });
    if (!response.ok) {
      const body = await response.json().catch(() => ({}));
      suggestTab.cancel();
      setStatus(`候補の取得に失敗しました: ${body.detail || response.status}`);
      return;
    }
    const result = await response.json();
    localStorage.setItem(
      "latest_suggestions",
      JSON.stringify({
        ...result,
        request: {
          base_location_id: baseSpot.spot_id,
          base_name: baseSpot.name,
          base_lat: baseSpot.lat,
          base_lng: baseSpot.lng,
          destination_spot_id: destinationSpot.spot_id,
          destination_name: destinationSpot.name,
          destination_lat: destinationSpot.lat,
          destination_lng: destinationSpot.lng,
          departure_time: departureTime,
          departure_date: departureDate,
          arrive_by: arriveBy,
        },
      }),
    );
    suggestTab.show("suggest.html");
    setStatus(
      result.status === "success"
        ? `${result.candidates.length}件の候補が見つかりました`
        : `候補なし: ${result.ai_hint}`,
    );
  } catch (err) {
    suggestTab.cancel();
    setStatus(`候補の取得に失敗しました: ${err}`);
  }
});

// Without real timetables every plan comes back empty, which looks like a
// bug rather than a setup step. Say what's wrong before anyone tries.
async function loadDataWarnings() {
  const response = await fetch("/status");
  if (!response.ok) return;
  const { warnings } = await response.json();
  if (!warnings.length) return;
  const box = document.getElementById("data-warnings");
  box.innerHTML = warnings.map((w) => `<p>⚠ ${escapeHtml(w)}</p>`).join("");
  box.hidden = false;
}

loadDataWarnings();
loadNetwork();
loadSpots();
