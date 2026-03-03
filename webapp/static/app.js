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

/* Stubs for Manual Control (wire to FastAPI when ready) */
function controlActuator(_act, _dir) {}
function retractAllActuators() {}
function clearStage() {}
function turnOnAllLights() {}
function turnOffAllLights() {}
function activateKickMotor() {}
function activateFlipMotor() {}
function applyConfiguration() {}
function captureImage(_cam) {}
function captureAllCamerasSequentially() {}
function updateFlipDurationValue(_v) {}

function updateLightValue(n, v) {
  const el = document.getElementById("light" + n + "-value");
  if (el) el.textContent = v + "%";
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
