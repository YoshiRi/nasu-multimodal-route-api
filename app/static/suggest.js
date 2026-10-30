// Candidate itineraries from /plan/suggest, laid out side by side so a
// person can compare them at a glance and pick one. Choosing a candidate
// runs it through /plan/route-plan and hands off to the existing results
// view, which is where the real routes and exact times live.

function escapeHtml(text) {
  const div = document.createElement("div");
  div.textContent = text;
  return div.innerHTML;
}

function loadSuggestions() {
  const raw = localStorage.getItem("latest_suggestions");
  if (!raw) return null;
  try {
    return JSON.parse(raw);
  } catch {
    return null;
  }
}

function renderMiniMap(container, request, candidate) {
  const map = L.map(container, {
    zoomControl: false,
    attributionControl: false,
    dragging: false,
    scrollWheelZoom: false,
    doubleClickZoom: false,
    boxZoom: false,
    keyboard: false,
  });
  L.tileLayer("https://{s}.tile.openstreetmap.org/{z}/{x}/{y}.png", {
    maxZoom: 19,
  }).addTo(map);

  // Start and destination are not in candidate.stops, but the sketch only
  // makes sense with them, so pull their coordinates from the request.
  const points = [
    { name: request.base_name, lat: request.base_lat, lng: request.base_lng, label: "発" },
    ...candidate.stops.map((stop, index) => ({
      name: stop.name,
      lat: stop.lat,
      lng: stop.lng,
      label: `${index + 1}`,
    })),
    {
      name: request.destination_name,
      lat: request.destination_lat,
      lng: request.destination_lng,
      label: "着",
    },
  ].filter((point) => typeof point.lat === "number" && typeof point.lng === "number");

  if (points.length < 2) {
    map.setView([37.05, 140.0], 10);
    return;
  }

  const latLngs = points.map((point) => [point.lat, point.lng]);
  L.polyline(latLngs, {
    color: "#1d5cb8",
    weight: 2,
    opacity: 0.7,
    dashArray: "5,5",
  }).addTo(map);

  points.forEach((point) => {
    L.marker([point.lat, point.lng], {
      icon: L.divIcon({
        className: "mini-marker",
        html: `<div class="mini-pin">${escapeHtml(point.label)}</div>`,
        iconSize: [20, 20],
      }),
    })
      .bindTooltip(point.name || "")
      .addTo(map);
  });

  map.fitBounds(L.latLngBounds(latLngs), { padding: [22, 22] });
}

function arrivalNote(stop) {
  const parts = [stop.tags.join("・")];
  if (stop.wait_minutes > 0) parts.push(`開館まで${stop.wait_minutes}分待ち`);
  parts.push(stop.opening_hours ? `営業 ${stop.opening_hours}` : "営業時間未確認");
  return parts.filter(Boolean).join(" / ");
}

function renderSchedule(candidate, request) {
  const table = document.createElement("table");
  table.className = "schedule";

  const rows = [
    [request.departure_time, request.base_name, "出発"],
    ...candidate.stops.flatMap((stop) => [
      [stop.arrival_time, stop.name, arrivalNote(stop)],
      [stop.departure_time, stop.name, `出発（滞在${stop.stay_minutes}分）`],
    ]),
    [candidate.destination_arrival_time, request.destination_name, "到着"],
  ];

  for (const [time, place, note] of rows) {
    const tr = document.createElement("tr");
    tr.innerHTML =
      `<td class="t">${escapeHtml(time || "")}</td>` +
      `<td class="p">${escapeHtml(place || "")}</td>` +
      `<td class="n">${escapeHtml(note || "")}</td>`;
    table.appendChild(tr);
  }
  return table;
}

async function buildFullPlan(candidate, request, button) {
  button.disabled = true;
  const original = button.textContent;
  button.textContent = "行程を作成中...";
  try {
    const response = await fetch("/plan/route-plan", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({
        base_location_id: request.base_location_id,
        // Carry each stop's stay over, so the full plan stays as long
        // as the candidate it was chosen from.
        target_spot_ids: [
          ...candidate.stops.map((stop) => ({
            spot_id: stop.spot_id,
            dwell_minutes: stop.stay_minutes,
          })),
          { spot_id: request.destination_spot_id },
        ],
        departure_time: request.departure_time,
        departure_date: request.departure_date,
      }),
    });
    if (!response.ok) {
      const body = await response.json().catch(() => ({}));
      button.textContent = `失敗: ${body.detail || response.status}`;
      button.disabled = false;
      return;
    }
    const result = await response.json();
    localStorage.setItem("latest_route_plan", JSON.stringify(result));
    window.open("results.html", "_blank");
    button.textContent = result.status === "success" ? "詳細を開きました" : "行程が組めません";
    button.disabled = false;
  } catch (err) {
    button.textContent = `失敗: ${err}`;
    button.disabled = false;
  }
}

function renderCandidate(candidate, request, index) {
  const card = document.createElement("div");
  card.className = "candidate";

  const head = document.createElement("div");
  head.className = "candidate-head";
  head.innerHTML =
    `<span class="rank">候補 ${index + 1}</span>` +
    `<span class="arrive">${escapeHtml(candidate.destination_arrival_time)} 着</span>` +
    `<span class="duration">所要 ${Math.floor(candidate.total_duration_min / 60)}時間` +
    `${candidate.total_duration_min % 60}分</span>`;
  card.appendChild(head);

  const names = document.createElement("div");
  names.className = "candidate-spots";
  names.textContent = candidate.stops.map((stop) => stop.name).join(" → ");
  card.appendChild(names);

  const tags = document.createElement("div");
  tags.className = "candidate-tags";
  for (const tag of candidate.tags) {
    const chip = document.createElement("span");
    chip.className = "chip";
    chip.textContent = tag;
    tags.appendChild(chip);
  }
  card.appendChild(tags);

  const body = document.createElement("div");
  body.className = "candidate-body";

  const mapEl = document.createElement("div");
  mapEl.className = "mini-map";
  body.appendChild(mapEl);
  body.appendChild(renderSchedule(candidate, request));
  card.appendChild(body);

  const note = document.createElement("div");
  note.className = "sketch-note";
  note.textContent = "地図は位置関係のスケッチです（実際の経路ではありません）。時刻も概算です。";
  card.appendChild(note);

  const button = document.createElement("button");
  button.className = "build";
  button.textContent = "この案で詳細を作る";
  button.addEventListener("click", () => buildFullPlan(candidate, request, button));
  card.appendChild(button);

  document.getElementById("candidates").appendChild(card);
  // Leaflet needs the element in the document and sized before it inits.
  renderMiniMap(mapEl, request, candidate);
}

function main() {
  const data = loadSuggestions();
  const summaryEl = document.getElementById("suggest-summary");

  if (!data) {
    summaryEl.textContent = "候補がありません。メイン画面から「候補を出す」を実行してください。";
    return;
  }

  const request = data.request || {};
  document.getElementById("suggest-title").textContent =
    `${request.base_name || "起点"} → ${request.destination_name || "終点"} の候補`;
  summaryEl.textContent = `${request.departure_date} ${request.departure_time} 発 / ${request.arrive_by} までに到着`;

  if (data.status !== "success" || !data.candidates || !data.candidates.length) {
    const empty = document.createElement("div");
    empty.className = "empty";
    empty.textContent = data.ai_hint || "条件に合う候補が見つかりませんでした。";
    document.getElementById("candidates").appendChild(empty);
    return;
  }

  data.candidates.forEach((candidate, index) =>
    renderCandidate(candidate, request, index),
  );
}

main();
