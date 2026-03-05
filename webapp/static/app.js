/* History API client */
const historyApi = {
  async list(page, filters) {
    const params = new URLSearchParams({ limit: 20, offset: (page - 1) * 20 });
    if (filters.date_from) params.append("date_from", filters.date_from);
    if (filters.date_to) params.append("date_to", filters.date_to);
    if (filters.lot_number) params.append("lot_number", filters.lot_number);
    if (filters.mfg_name) params.append("mfg_name", filters.mfg_name);
    if (filters.inspection_result) params.append("inspection_result", filters.inspection_result);
    const res = await fetch("/api/history?" + params.toString());
    return res.ok ? await res.json() : { success: false };
  },
  async get(id) {
    const res = await fetch("/api/history/" + id);
    return res.ok ? await res.json() : { success: false };
  },
  async delete(id) {
    const res = await fetch("/api/history/" + id, { method: "DELETE" });
    return res.ok ? await res.json() : { success: false };
  },
  async bulkDelete(ids) {
    const res = await fetch("/api/history/bulk", {
      method: "DELETE",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(ids),
    });
    return res.ok ? await res.json() : { success: false };
  },
  async export(body) {
    const res = await fetch("/api/history/export", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(body),
    });
    return res.ok ? await res.json() : { success: false };
  },
};

/* Logs API client */
const logsApi = {
  async tail(limit = 200) {
    const res = await fetch("/api/logs?limit=" + encodeURIComponent(limit));
    if (!res.ok) return { success: false, lines: [] };
    return res.json();
  },
};

/* Inspection run API (Start / Stop / Single) */
const inspectionApi = {
  async status() {
    const res = await fetch("/api/inspection/status");
    return res.ok ? await res.json() : { success: false };
  },
  async start(flipDurationSec) {
    const res = await fetch("/api/inspection/start", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ flip_duration: flipDurationSec }),
    });
    return res.ok ? await res.json() : { success: false };
  },
  async stop() {
    const res = await fetch("/api/inspection/stop", { method: "POST" });
    return res.ok ? await res.json() : { success: false };
  },
  async stopImmediate() {
    const res = await fetch("/api/inspection/stop-immediate", { method: "POST" });
    return res.ok ? await res.json() : { success: false };
  },
  async single(flipDurationSec) {
    const res = await fetch("/api/inspection/single-inspection", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ flip_duration: flipDurationSec }),
    });
    return res.ok ? await res.json() : { success: false };
  },
};

/* Lights API client (Jetson control_lights backend) */
const lightsApi = {
  async set(lightId, intensityPercent) {
    const res = await fetch("/api/lights/set", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ light_id: lightId, intensity: intensityPercent }),
    });
    return res.ok ? await res.json() : { success: false };
  },
  async off() {
    const res = await fetch("/api/lights/off", { method: "POST" });
    return res.ok ? await res.json() : { success: false };
  },
  async onAll() {
    const res = await fetch("/api/lights/on-all", { method: "POST" });
    return res.ok ? await res.json() : { success: false };
  },
};

/* Actuator state: whole box colored by state, one sentence inside */
function setActuatorBoxState(act, state, movingLabel) {
  const num = act.replace("ACT", "");
  const box = document.getElementById("actuator-box-" + act.toLowerCase());
  if (!box) return;
  box.className = "actuator-state-box state-" + state;
  const labels = {
    retracted: "Actuator " + num + " State: Retracted",
    extended: "Actuator " + num + " State: Extended",
    moving: "Actuator " + num + " State: " + (movingLabel || "Moving…"),
  };
  box.textContent = labels[state] || "Actuator " + num + " State: " + state;
}

function setSafetyBoxState(state, message) {
  const box = document.getElementById("actuator-safety-box");
  if (!box) return;
  box.className = "actuator-state-box state-" + state;
  box.textContent = message != null ? message : (state === "retracted" ? "Safety: Safe to extend others" : state === "extended" ? "Safety: One actuator extended" : "Safety: Command sent");
}

function updateActuatorStatus(act, dir) {
  setActuatorBoxState(act, dir === "extend" ? "extended" : "retracted");
  setSafetyBoxState(dir === "extend" ? "extended" : "retracted");
}

async function controlActuator(act, dir) {
  try {
    setActuatorBoxState(act, "moving", dir === "extend" ? "Extending…" : "Retracting…");
    setSafetyBoxState("moving", "Safety: Command sent");

    const res = await fetch("/api/actuators/control", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ actuator_name: act, action: dir, duration: 2.0 }),
    });
    if (!res.ok) {
      setSafetyBoxState("extended", "Safety: Retract other actuators first");
      return;
    }
    updateActuatorStatus(act, dir);
  } catch (e) {
    // ignore for now
  }
}
async function retractAllActuators() {
  try {
    ["ACT1", "ACT2", "ACT3"].forEach((a) => setActuatorBoxState(a, "retracted"));
    setSafetyBoxState("retracted", "Safety: Safe to extend others");
    await fetch("/api/actuators/retract-all", { method: "POST" });
  } catch (e) {
    // ignore for now
  }
}
async function clearStage() {
  try {
    setSafetyBoxState("moving", "Safety: Clearing stage…");
    const res = await fetch("/api/actuators/clear-stage", { method: "POST" });
    const data = res.ok ? await res.json() : { success: false };
    if (data.success) {
      ["ACT1", "ACT2", "ACT3"].forEach((a) => setActuatorBoxState(a, "retracted"));
      setSafetyBoxState("retracted", "Safety: Safe to extend others");
    } else {
      setSafetyBoxState(
        "extended",
        res.status === 409 ? "Safety: Retract all actuators first" : "Safety: Clear stage failed"
      );
    }
  } catch (e) {
    setSafetyBoxState("extended", "Safety: Clear stage failed");
  }
}

function getFlipDurationSec() {
  const input = document.getElementById("flip-duration-input");
  const ms = input ? Number(input.value) || 250 : 250;
  return ms / 1000;
}

async function startInspection() {
  try {
    const result = await inspectionApi.start(getFlipDurationSec());
    if (result.success) {
      document.getElementById("run-state").textContent = "Running";
    }
  } catch (e) {
    // ignore
  }
}

async function stopInspection() {
  try {
    const result = await inspectionApi.stop();
    if (result.success) document.getElementById("run-state").textContent = "Stopping…";
  } catch (e) {
    // ignore
  }
}

async function stopInspectionImmediate() {
  try {
    const result = await inspectionApi.stopImmediate();
    if (result.success) document.getElementById("run-state").textContent = "Idle";
  } catch (e) {
    // ignore
  }
}

async function singleInspection() {
  try {
    await inspectionApi.single(getFlipDurationSec());
    updateInspectionStatus();
  } catch (e) {
    // ignore
  }
}

function saveMetadata() {
  const metadata = {
    lotNumber: (document.getElementById("lot-number") && document.getElementById("lot-number").value.trim()) || "",
    mfgName: (document.getElementById("mfg-name") && document.getElementById("mfg-name").value.trim()) || "",
    mfgPart: (document.getElementById("mfg-part") && document.getElementById("mfg-part").value.trim()) || "",
    material: (document.getElementById("material") && document.getElementById("material").value.trim()) || "",
    ballDiameter: (document.getElementById("ball-diameter") && document.getElementById("ball-diameter").value) || "",
    customerName: (document.getElementById("customer-name") && document.getElementById("customer-name").value.trim()) || "",
  };
  try {
    sessionStorage.setItem("inspectionMetadata", JSON.stringify(metadata));
  } catch (err) {
    // ignore
  }
  window.currentMetadata = metadata;

  // Also send metadata to backend so inspection cycles can be saved with context
  try {
    fetch("/api/inspection/metadata", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(metadata),
    });
  } catch (e) {
    console.warn("Failed to send inspection metadata to backend", e);
  }
}

function loadMetadata() {
  try {
    const saved = sessionStorage.getItem("inspectionMetadata");
    if (saved) {
      const metadata = JSON.parse(saved);
      const lot = document.getElementById("lot-number");
      const mfg = document.getElementById("mfg-name");
      const part = document.getElementById("mfg-part");
      const mat = document.getElementById("material");
      const ball = document.getElementById("ball-diameter");
      const cust = document.getElementById("customer-name");
      if (lot) lot.value = metadata.lotNumber || "";
      if (mfg) mfg.value = metadata.mfgName || "";
      if (part) part.value = metadata.mfgPart || "";
      if (mat) mat.value = metadata.material || "";
      if (ball) ball.value = metadata.ballDiameter || "";
      if (cust) cust.value = metadata.customerName || "";
      window.currentMetadata = metadata;
    }
  } catch (err) {
    // ignore
  }
}

async function updateInspectionStatus() {
  try {
    const data = await inspectionApi.status();
    if (!data || !data.success) return;
    const cycles = document.getElementById("run-cycles");
    const total = document.getElementById("run-balls-total");
    const good = document.getElementById("run-balls-good");
    const bad = document.getElementById("run-balls-bad");
    const state = document.getElementById("run-state");
    const last = document.getElementById("run-last-result");
    if (cycles) cycles.textContent = String(data.cycle_count ?? 0);
    if (total) total.textContent = (data.total_balls ?? 0) + " total";
    if (good) good.textContent = (data.good_balls ?? 0) + " good";
    if (bad) bad.textContent = (data.bad_balls ?? 0) + " bad";
    if (state) {
      if (data.running) state.textContent = "Running";
      else if (data.state === "stopping") state.textContent = "Stopping…";
      else state.textContent = "Idle";
    }
    if (last) last.textContent = data.last_result || "–";

    // Update processed images (4 views) if available
    const processed = data.processed_images || {};
    const viewMap = [
      { id: "processed-cam-a-top", key: "CAMERA_A_TOP" },
      { id: "processed-cam-b-top", key: "CAMERA_B_TOP" },
      { id: "processed-cam-a-bot", key: "CAMERA_A_BOT" },
      { id: "processed-cam-b-bot", key: "CAMERA_B_BOT" },
    ];
    viewMap.forEach(({ id, key }) => {
      const img = document.getElementById(id);
      if (!img) return;
      const cell = img.closest(".image-cell");
      const placeholder = cell && cell.querySelector(".image-cell-placeholder");
      const url = processed[key];
      if (url) {
        img.src = url + "?t=" + Date.now(); // cache-bust to show latest
        img.style.display = "block";
        if (placeholder) placeholder.style.display = "none";
      } else {
        img.style.display = "none";
        if (placeholder) placeholder.style.display = "flex";
      }
    });
  } catch (e) {
    // ignore
  }
}

async function turnOnAllLights() {
  // Update sliders and labels to reflect full intensity
  for (let n = 1; n <= 4; n += 1) {
    const slider = document.getElementById("light" + n + "-slider");
    if (slider) slider.value = 100;
    const label = document.getElementById("light" + n + "-value");
    if (label) label.textContent = "100%";
  }
  try {
    await lightsApi.onAll();
  } catch (e) {
    // ignore for now; UI already updated
  }
}
async function turnOffAllLights() {
  // Update sliders and labels to reflect zero intensity
  for (let n = 1; n <= 4; n += 1) {
    const slider = document.getElementById("light" + n + "-slider");
    if (slider) slider.value = 0;
    const label = document.getElementById("light" + n + "-value");
    if (label) label.textContent = "0%";
  }
  try {
    await lightsApi.off();
  } catch (e) {
    // ignore for now; UI already updated
  }
}
async function activateKickMotor() {
  try {
    await fetch("/api/motors/kick", { method: "POST" });
  } catch (e) {
    // ignore
  }
}
async function activateFlipMotor() {
  try {
    const input = document.getElementById("flip-duration-input");
    const duration = input ? Number(input.value) || 0.25 : 0.25;
    await fetch("/api/motors/flip", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ motor: "m1", action: "extend", duration }),
    });
  } catch (e) {
    // ignore
  }
}
async function applyConfiguration() {
  try {
    const exposure = Number(document.getElementById("exposure-slider").value);
    const red = Number(document.getElementById("red-gain-slider").value);
    const blue = Number(document.getElementById("blue-gain-slider").value);
    await fetch("/api/cameras/configure", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({
        exposure_ms: exposure,
        red_gain: red,
        blue_gain: blue,
        analogue_gain: 4.0,
      }),
    });
  } catch (e) {
    // ignore for now
  }
}

async function captureImage(cameraName) {
  try {
    const res = await fetch("/api/cameras/capture", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ camera_name: cameraName }),
    });
    const data = res.ok ? await res.json() : { success: false };
    if (!data.success) return;

    const which = cameraName.toLowerCase().includes("b") ? "B" : "A";
    const cell = document.querySelector(
      '.manual-images-panel .image-cell[data-label="CAM ' + which + '"]'
    );
    if (cell) {
      const img = document.createElement("img");
      img.src = "/static/captures/cam" + which + "_latest.jpg?ts=" + Date.now();
      img.style.width = "100%";
      img.style.height = "100%";
      img.style.objectFit = "contain";
      cell.innerHTML = "";
      cell.appendChild(img);
    }
  } catch (e) {
    // ignore for now
  }
}

async function captureAllCamerasSequentially() {
  // Fire both captures in parallel, like the old Pi version did.
  await Promise.all([captureImage("camera A"), captureImage("camera B")]);
}
function updateFlipDurationValue(_v) {}

async function updateLightValue(n, v) {
  const el = document.getElementById("light" + n + "-value");
  if (el) el.textContent = v + "%";
  const numeric = Number(v);
  if (Number.isNaN(numeric)) return;
  try {
    await lightsApi.set(n, numeric);
  } catch (e) {
    // ignore errors for now; UI still reflects requested value
  }
}
function updateExposureValue(v) {
  const el = document.getElementById("exposure-value");
  if (el) el.textContent = v + "ms";
}
function updateRedGainValue(v) {
  const el = document.getElementById("red-gain-value");
  if (el) el.textContent = v;
}
function updateBlueGainValue(v) {
  const el = document.getElementById("blue-gain-value");
  if (el) el.textContent = v;
}

const tabs = document.querySelectorAll(".tab");
const title = document.getElementById("view-title");
const subtitle = document.getElementById("view-subtitle");
const panels = document.querySelectorAll(".content-panel");

const copy = {
  manual: {
    title: "Manual Control",
    subtitle:
      "Direct control of motors, actuators, and lights using the Jetson header and Motor HAT.",
  },
  history: {
    title: "History",
    subtitle:
      "Will replay past inspections, composite images, and defect decisions from the local database.",
  },
};

function setTabAria(activeKey) {
  tabs.forEach((t) => {
    t.setAttribute("aria-selected", t.dataset.tab === activeKey ? "true" : "false");
  });
  panels.forEach((p) => {
    const isActive = p.id === "panel-" + activeKey;
    p.setAttribute("aria-hidden", isActive ? "false" : "true");
  });
}

tabs.forEach((tab) => {
  tab.addEventListener("click", () => {
    const key = tab.dataset.tab;
    tabs.forEach((t) => t.classList.remove("active"));
    tab.classList.add("active");
    panels.forEach((p) => p.classList.remove("active"));
    const panel = document.getElementById("panel-" + key);
    if (panel) panel.classList.add("active");
    setTabAria(key);

    const data = copy[key];
    if (data) {
      title.textContent = data.title;
      subtitle.textContent = data.subtitle;
    } else if (key === "run") {
      title.textContent = "Main Control Panel";
      subtitle.textContent = "Run inspections and monitor system status from a single view.";
    }
    if (key === "history") {
      loadHistoryData(1, {});
    }
  });
});

/* Initialize ARIA for initial state */
setTabAria("run");

// Periodically refresh server logs into the Logs & Health panel
const logsBox = document.querySelector(".logs-box");

async function refreshLogs() {
  if (!logsBox) return;
  try {
    const data = await logsApi.tail(200);
    if (!data || !data.success || !Array.isArray(data.lines)) return;
    logsBox.innerHTML = "";
    data.lines.forEach((line) => {
      const row = document.createElement("div");
      row.classList.add("log-line");
      let level = "info";
      if (line.includes(" [ERROR]")) level = "error";
      else if (line.includes(" [WARNING]") || line.includes(" [WARN]")) level = "warn";
      row.classList.add("log-level-" + level);
      row.textContent = line;
      logsBox.appendChild(row);
    });
    logsBox.scrollTop = logsBox.scrollHeight;
  } catch (err) {
    // Ignore log polling errors; UI should remain responsive
  }
}

if (logsBox) {
  refreshLogs();
  setInterval(refreshLogs, 2000);
}

loadMetadata();
updateInspectionStatus();
setInterval(updateInspectionStatus, 2000);

// Initialize actuator state boxes
["ACT1", "ACT2", "ACT3"].forEach((a) => setActuatorBoxState(a, "retracted"));
setSafetyBoxState("retracted");

let currentPage = 1;
let totalPages = 1;
let currentFilters = {};
let selectedCycles = new Set();

async function loadHistoryData(page, filters) {
  try {
    const response = await historyApi.list(page, filters);
    if (response.success && response.data !== undefined) {
      displayHistoryResults(response.data);
      if (response.statistics) updateHistoryStatistics(response.statistics);
      if (response.pagination) updatePagination(response.pagination);
    } else {
      displayHistoryResults([]);
      updateHistoryStatistics({ total_cycles: 0, good_balls: 0, bad_balls: 0, no_balls: 0 });
      updatePagination({ total: 0, limit: 20, offset: 0 });
    }
  } catch (err) {
    displayHistoryResults([]);
    updateHistoryStatistics({ total_cycles: 0, good_balls: 0, bad_balls: 0, no_balls: 0 });
    updatePagination({ total: 0, limit: 20, offset: 0 });
  }
}

function displayHistoryResults(data) {
  const tbody = document.getElementById("history-results");
  tbody.innerHTML = "";
  if (!data || data.length === 0) {
    tbody.innerHTML =
      '<tr><td colspan="7" style="text-align:center;color:var(--text-muted);">No inspection records found</td></tr>';
    return;
  }
  data.forEach((record) => {
    const row = document.createElement("tr");
    const ts = record.timestamp ? new Date(record.timestamp).toLocaleString() : "–";
    const resultClass = "result-" + (record.inspection_result || "").replace("_", "-");
    const resultText = (record.inspection_result || "N/A").replace("_", " ").toUpperCase();
    row.innerHTML =
      '<td><input type="checkbox" value="' +
      record.id +
      '" onchange="toggleCycleSelection(' +
      record.id +
      ')"></td>' +
      "<td>" +
      ts +
      "</td>" +
      "<td>" +
      (record.lot_number || "N/A") +
      "</td>" +
      "<td>" +
      (record.mfg_name || "N/A") +
      "</td>" +
      "<td>" +
      (record.mfg_part_number || "N/A") +
      "</td>" +
      '<td><span class="result-badge ' +
      resultClass +
      '">' +
      resultText +
      "</span></td>" +
      '<td><button class="btn btn-secondary" onclick="viewInspectionDetails(' +
      record.id +
      ')" style="padding:4px 8px;font-size:11px;">View</button> ' +
      '<button class="btn btn-danger" onclick="event.stopPropagation();deleteCycle(' +
      record.id +
      ')" style="padding:4px 8px;font-size:11px;">Delete</button></td>';
    row.setAttribute("data-cycle-id", record.id);
    row.addEventListener("click", (e) => {
      if (e.target.tagName === "BUTTON" || e.target.type === "checkbox") return;
      document.querySelectorAll("#history-results tr").forEach((r) => r.classList.remove("selected"));
      row.classList.add("selected");
      showCompositeImage(record);
    });
    tbody.appendChild(row);
  });
}

function updateHistoryStatistics(stats) {
  document.getElementById("total-cycles").textContent = stats.total_cycles ?? 0;
  document.getElementById("good-balls").textContent = stats.good_balls ?? 0;
  document.getElementById("bad-balls").textContent = stats.bad_balls ?? 0;
  document.getElementById("no-balls").textContent = stats.no_balls ?? 0;
}

function updatePagination(pagination) {
  const total = pagination.total ?? 0;
  const limit = pagination.limit ?? 20;
  totalPages = Math.max(1, Math.ceil(total / limit));
  currentPage = Math.min(currentPage, totalPages);
  currentPage = Math.max(1, Math.floor((pagination.offset ?? 0) / limit) + 1);
  document.getElementById("page-info").textContent = "Page " + currentPage + " of " + totalPages;
  document.getElementById("prev-btn").disabled = currentPage <= 1;
  document.getElementById("next-btn").disabled = currentPage >= totalPages;
}

function toggleSelectAll() {
  const selectAll = document.getElementById("select-all");
  const checkboxes = document.querySelectorAll('#history-results input[type="checkbox"]');
  const deleteBtn = document.getElementById("delete-selected-btn");
  checkboxes.forEach((cb) => {
    cb.checked = selectAll.checked;
    const id = parseInt(cb.value, 10);
    if (selectAll.checked) selectedCycles.add(id);
    else selectedCycles.delete(id);
  });
  deleteBtn.style.display = selectedCycles.size > 0 ? "inline-block" : "none";
}

function toggleCycleSelection(cycleId) {
  const deleteBtn = document.getElementById("delete-selected-btn");
  if (selectedCycles.has(cycleId)) selectedCycles.delete(cycleId);
  else selectedCycles.add(cycleId);
  const checkboxes = document.querySelectorAll('#history-results input[type="checkbox"]');
  const selectAll = document.getElementById("select-all");
  selectAll.checked = checkboxes.length > 0 && Array.from(checkboxes).every((cb) => cb.checked);
  deleteBtn.style.display = selectedCycles.size > 0 ? "inline-block" : "none";
}

async function deleteCycle(cycleId) {
  if (!confirm("Delete inspection cycle " + cycleId + "? This cannot be undone.")) return;
  try {
    const response = await historyApi.delete(cycleId);
    if (response.success) loadHistoryData(currentPage, currentFilters);
  } catch (e) {}
  loadHistoryData(currentPage, currentFilters);
}

async function deleteSelectedCycles() {
  if (selectedCycles.size === 0) return;
  if (!confirm("Delete " + selectedCycles.size + " selected cycle(s)? This cannot be undone."))
    return;
  try {
    const response = await historyApi.bulkDelete(Array.from(selectedCycles));
    if (response.success) {
      selectedCycles.clear();
      document.getElementById("select-all").checked = false;
      document.getElementById("delete-selected-btn").style.display = "none";
      loadHistoryData(currentPage, currentFilters);
    }
  } catch (e) {}
  loadHistoryData(currentPage, currentFilters);
}

function applyHistoryFilters() {
  currentFilters = {
    date_from: document.getElementById("date-from").value || undefined,
    date_to: document.getElementById("date-to").value || undefined,
    lot_number: document.getElementById("lot-filter").value.trim() || undefined,
    mfg_name: document.getElementById("mfg-filter").value.trim() || undefined,
    inspection_result: document.getElementById("result-filter").value || undefined,
  };
  currentPage = 1;
  loadHistoryData(1, currentFilters);
}

async function exportHistoryReport() {
  try {
    const response = await historyApi.export({
      filters: currentFilters,
      format: "csv",
    });
    if (response.success && response.data && response.data.content) {
      const blob = new Blob([response.data.content], { type: "text/csv" });
      const url = URL.createObjectURL(blob);
      const a = document.createElement("a");
      a.href = url;
      a.download = response.data.filename || "history-export.csv";
      a.click();
      URL.revokeObjectURL(url);
    }
  } catch (e) {}
}

async function viewInspectionDetails(cycleId) {
  try {
    const response = await historyApi.get(cycleId);
    if (response.success && response.data) {
      showCompositeImage(response.data);
      document.querySelectorAll("#history-results tr").forEach((r) => r.classList.remove("selected"));
      const row = document.querySelector('#history-results tr[data-cycle-id="' + cycleId + '"]');
      if (row) row.classList.add("selected");
    }
  } catch (e) {}
}

function showCompositeImage(record) {
  const placeholder = document.getElementById("composite-placeholder");
  if (!placeholder) return;
  const imageContainer = document.getElementById("composite-image");
  const compositeImg = document.getElementById("composite-img");
  const detailsContainer = document.getElementById("composite-details");
  if (record && record.composite_image_path) {
    placeholder.style.display = "none";
    if (imageContainer) imageContainer.style.display = "flex";
    if (detailsContainer) detailsContainer.style.display = "block";
    const filename = record.composite_image_path.split("/").pop();
    if (compositeImg) {
      compositeImg.src = "/api/images/view/" + filename;
      compositeImg.style.display = "block";
    }
    const detailCycleId = document.getElementById("detail-cycle-id");
    if (detailCycleId) detailCycleId.textContent = record.id ?? "–";
    const detailLot = document.getElementById("detail-lot");
    if (detailLot) detailLot.textContent = record.lot_number || "N/A";
    const detailMfg = document.getElementById("detail-mfg");
    if (detailMfg) detailMfg.textContent = record.mfg_name || "N/A";
    const detailPart = document.getElementById("detail-part");
    if (detailPart) detailPart.textContent = record.mfg_part_number || "N/A";
    const detailMaterial = document.getElementById("detail-material");
    if (detailMaterial) detailMaterial.textContent = record.material || "N/A";
    const detailDiameter = document.getElementById("detail-diameter");
    if (detailDiameter)
      detailDiameter.textContent = record.ball_diameter_mm
        ? record.ball_diameter_mm + " mm"
        : record.ball_diameter
          ? record.ball_diameter + " in"
          : "N/A";
    const detailResult = document.getElementById("detail-result");
    if (detailResult)
      detailResult.textContent = (record.inspection_result || "").replace("_", " ").toUpperCase();
    const detailTimestamp = document.getElementById("detail-timestamp");
    if (detailTimestamp)
      detailTimestamp.textContent = record.timestamp
        ? new Date(record.timestamp).toLocaleString()
        : "–";
    if (compositeImg) {
      compositeImg.onerror = function () {
        placeholder.style.display = "flex";
        placeholder.innerHTML =
          '<span class="placeholder-text">Failed to load image</span>';
        if (imageContainer) imageContainer.style.display = "none";
      };
    }
  } else {
    placeholder.style.display = "flex";
    placeholder.innerHTML =
      '<span class="placeholder-text">No composite image for this cycle</span>';
    if (imageContainer) imageContainer.style.display = "none";
    if (detailsContainer) detailsContainer.style.display = "none";
  }
}

function previousPage() {
  if (currentPage > 1) {
    currentPage--;
    loadHistoryData(currentPage, currentFilters);
  }
}

function nextPage() {
  if (currentPage < totalPages) {
    currentPage++;
    loadHistoryData(currentPage, currentFilters);
  }
}
