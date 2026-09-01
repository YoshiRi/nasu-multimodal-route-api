const MODE_COLORS = {
  bus: "#2266cc",
  walking: "#22aa66",
  taxi: "#e0a030",
  stay: "#9966cc",
  wait: "#cc4444",
};

const MODE_LABELS = {
  bus: "バス",
  walking: "徒歩",
  taxi: "タクシー",
  stay: "滞在",
  wait: "待機",
};

function escapeHtml(text) {
  const div = document.createElement("div");
  div.textContent = text;
  return div.innerHTML;
}

function parseHHMM(value) {
  const [hours, minutes] = value.split(":").map(Number);
  return hours * 60 + minutes;
}

function loadLatestPlan() {
  const raw = localStorage.getItem("latest_route_plan");
  if (!raw) return null;
  try {
    return JSON.parse(raw);
  } catch {
    return null;
  }
}

function renderEmptyState(message) {
  document.getElementById("plan-title").textContent = "ルート結果";
  document.getElementById("plan-status").textContent = message;
}

function renderLegend(container) {
  const legend = document.createElement("div");
  legend.id = "timeline-legend";
  for (const mode of Object.keys(MODE_COLORS)) {
    const item = document.createElement("div");
    item.className = "legend-item";
    const swatch = document.createElement("span");
    swatch.className = "legend-swatch";
    swatch.style.background = MODE_COLORS[mode];
    const label = document.createElement("span");
    label.textContent = MODE_LABELS[mode];
    item.appendChild(swatch);
    item.appendChild(label);
    legend.appendChild(item);
  }
  container.appendChild(legend);
}

function renderMap(steps) {
  const map = L.map("map");
  L.tileLayer("https://{s}.tile.openstreetmap.org/{z}/{x}/{y}.png", {
    maxZoom: 19,
    attribution: "&copy; OpenStreetMap contributors",
  }).addTo(map);

  const layersByStep = new Map();
  const allPoints = [];

  steps.forEach((step) => {
    const from = [step.from_lat, step.from_lng];
    const to = [step.to_lat, step.to_lng];
    const hasPath = step.path && step.path.length >= 2;
    allPoints.push(...(hasPath ? step.path : [from, to]));

    let layer;
    if (step.from_lat === step.to_lat && step.from_lng === step.to_lng) {
      layer = L.circleMarker(from, {
        radius: 10,
        color: MODE_COLORS[step.mode] || "#666",
        fillColor: MODE_COLORS[step.mode] || "#666",
        fillOpacity: 0.7,
        weight: 2,
      }).addTo(map);
    } else {
      const points = hasPath ? step.path : [from, to];
      layer = L.polyline(points, {
        color: MODE_COLORS[step.mode] || "#666",
        weight: 4,
        opacity: 0.8,
      }).addTo(map);
    }
    layer.bindPopup(`<strong>${escapeHtml(step.title)}</strong><br>${escapeHtml(step.details)}`);

    const startMarker = L.marker(from, {
      icon: L.divIcon({
        className: "step-order-icon",
        html: `<div style="background:#333;color:#fff;border-radius:50%;width:18px;height:18px;
          display:flex;align-items:center;justify-content:center;font-size:10px;">${step.step_order}</div>`,
        iconSize: [18, 18],
      }),
    }).addTo(map);

    layersByStep.set(step.step_order, { layer, startMarker });
  });

  if (allPoints.length) {
    map.fitBounds(L.latLngBounds(allPoints), { padding: [24, 24] });
  } else {
    map.setView([37.05, 140.0], 11);
  }

  return layersByStep;
}

function renderTimeline(steps, layersByStep) {
  const timelineEl = document.getElementById("timeline");
  renderLegend(timelineEl);

  const minStart = Math.min(...steps.map((s) => parseHHMM(s.start_time)));
  const maxEnd = Math.max(...steps.map((s) => parseHHMM(s.end_time)));
  const totalMinutes = Math.max(1, maxEnd - minStart);

  for (const step of steps) {
    const start = parseHHMM(step.start_time);
    const end = parseHHMM(step.end_time);
    const leftPct = ((start - minStart) / totalMinutes) * 100;
    const widthPct = Math.max(0.5, ((end - start) / totalMinutes) * 100);

    const row = document.createElement("div");
    row.className = "timeline-row";
    row.style.cursor = "pointer";

    const label = document.createElement("div");
    label.className = "timeline-label";
    label.textContent = `${step.step_order}. ${step.title}`;

    const track = document.createElement("div");
    track.className = "timeline-track";
    const bar = document.createElement("div");
    bar.className = "timeline-bar";
    bar.style.left = `${leftPct}%`;
    bar.style.width = `${widthPct}%`;
    bar.style.background = MODE_COLORS[step.mode] || "#666";
    track.appendChild(bar);

    const timeLabel = document.createElement("div");
    timeLabel.className = "timeline-time";
    timeLabel.textContent = `${step.start_time}-${step.end_time}`;

    row.appendChild(label);
    row.appendChild(track);
    row.appendChild(timeLabel);
    timelineEl.appendChild(row);

    const details = document.createElement("div");
    details.className = "timeline-details";
    details.textContent = step.details;
    timelineEl.appendChild(details);

    row.addEventListener("click", () => {
      const entry = layersByStep.get(step.step_order);
      if (!entry) return;
      entry.layer.openPopup();
      if (entry.layer.getBounds) {
        entry.layer._map.fitBounds(entry.layer.getBounds(), { padding: [40, 40], maxZoom: 16 });
      } else {
        entry.layer._map.setView(entry.layer.getLatLng(), 16);
      }
    });
  }
}

function main() {
  const plan = loadLatestPlan();
  if (!plan) {
    renderEmptyState("ルート結果がありません。メイン画面からルートを検索してください。");
    return;
  }

  document.getElementById("plan-status").textContent = plan.ai_hint || "";

  if (plan.status !== "success" || !plan.plans || !plan.plans.length) {
    document.getElementById("plan-title").textContent = "ルート不成立";
    return;
  }

  const planData = plan.plans[0];
  document.getElementById("plan-title").textContent = planData.title;

  const layersByStep = renderMap(planData.steps);
  renderTimeline(planData.steps, layersByStep);
}

main();
