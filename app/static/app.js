// Urja Meter Ops 2.0 Web Client
document.addEventListener("DOMContentLoaded", () => {
  let currentPage = 1;
  const pageSize = 20;
  let totalRecords = 0;
  let allMetersCache = [];

  // DOM Elements
  const metersTableBody = document.getElementById("metersTableBody");
  const searchInput = document.getElementById("searchInput");
  const statusFilter = document.getElementById("statusFilter");
  const makeFilter = document.getElementById("makeFilter");
  const phaseFilter = document.getElementById("phaseFilter");
  const clearFiltersBtn = document.getElementById("clearFiltersBtn");
  const prevPageBtn = document.getElementById("prevPageBtn");
  const nextPageBtn = document.getElementById("nextPageBtn");
  const currentPageDisplay = document.getElementById("currentPageDisplay");
  const pageInfo = document.getElementById("pageInfo");

  // Health Pill
  const healthIndicator = document.getElementById("healthIndicator");
  const healthText = document.getElementById("healthText");

  // KPI elements
  const kpiTotalMeters = document.getElementById("kpiTotalMeters");
  const kpiInstalled = document.getElementById("kpiInstalled");
  const kpiInstalledPct = document.getElementById("kpiInstalledPct");
  const kpiFaulty = document.getElementById("kpiFaulty");
  const kpiFaultyPct = document.getElementById("kpiFaultyPct");
  const kpiDecom = document.getElementById("kpiDecom");
  const kpiDecomPct = document.getElementById("kpiDecomPct");

  // Modal
  const inspectorModal = document.getElementById("inspectorModal");
  const closeModalBtn = document.getElementById("closeModalBtn");

  // Init
  initTabs();
  checkHealth();
  loadMeters(1);
  loadHierarchy();
  loadTransformers();

  // Polling health check
  setInterval(checkHealth, 30000);

  // Tab management
  function initTabs() {
    const tabBtns = document.querySelectorAll(".tab-btn");
    const tabPanels = document.querySelectorAll(".tab-panel");

    tabBtns.forEach(btn => {
      btn.addEventListener("click", () => {
        tabBtns.forEach(b => b.classList.remove("active"));
        tabPanels.forEach(p => p.classList.remove("active"));

        btn.classList.add("active");
        const targetId = btn.dataset.tab;
        const panel = document.getElementById(targetId);
        if (panel) panel.classList.add("active");
      });
    });
  }

  // Health probe
  async function checkHealth() {
    try {
      const res = await fetch("/api/v1/health");
      const data = await res.json();
      if (data.status === "ok") {
        healthIndicator.className = "health-pill ok";
        healthText.textContent = `Online • ${data.cachedMetersCount} indexed`;
      } else {
        healthIndicator.className = "health-pill degraded";
        healthText.textContent = "Degraded (Upstream Syncing)";
      }
    } catch {
      healthIndicator.className = "health-pill degraded";
      healthText.textContent = "Offline";
    }
  }

  // Load Meters
  async function loadMeters(page = 1) {
    currentPage = page;
    metersTableBody.innerHTML = `<tr><td colspan="8" class="loading-cell">Fetching meters from API...</td></tr>`;

    const params = new URLSearchParams({
      page: page,
      page_size: pageSize
    });

    if (searchInput.value.trim()) params.append("q", searchInput.value.trim());
    if (statusFilter.value) params.append("status", statusFilter.value);
    if (makeFilter.value) params.append("make", makeFilter.value);
    if (phaseFilter.value) params.append("phase", phaseFilter.value);

    try {
      const res = await fetch(`/api/v1/meters?${params.toString()}`);
      const payload = await res.json();
      totalRecords = payload.total;
      renderTable(payload.data);
      updatePagination(payload.total, payload.page, payload.totalPages);

      // Update KPI stats if initial unfiltered fetch
      if (!searchInput.value && !statusFilter.value && !makeFilter.value && !phaseFilter.value && page === 1) {
        updateKpis(payload.total);
      }
    } catch (err) {
      metersTableBody.innerHTML = `<tr><td colspan="8" class="text-center text-danger loading-cell">Failed to load meters: ${err.message}</td></tr>`;
    }
  }

  function renderTable(meters) {
    if (!meters || meters.length === 0) {
      metersTableBody.innerHTML = `<tr><td colspan="8" class="text-center loading-cell">No smart meters match the active filters.</td></tr>`;
      return;
    }

    metersTableBody.innerHTML = meters.map(m => {
      const statusClass = m.installStatus.toLowerCase();
      return `
        <tr data-meter-id="${m.meterId}">
          <td><span class="meter-link">${m.meterId}</span></td>
          <td>${m.serialNo}</td>
          <td><strong>${m.make}</strong></td>
          <td><span class="phase-pill">${m.phaseType}</span></td>
          <td><span class="status-badge ${statusClass}">● ${m.installStatus}</span></td>
          <td><code>${m.dtCode}</code></td>
          <td><span class="phase-pill">${m.build || 'legacy'}</span></td>
          <td><button class="btn-inspect" onclick="openInspector('${m.meterId}')">Inspect &rarr;</button></td>
        </tr>
      `;
    }).join("");

    // Click on row to inspect
    metersTableBody.querySelectorAll("tr").forEach(tr => {
      tr.addEventListener("click", (e) => {
        if (e.target.tagName !== "BUTTON") {
          const mId = tr.dataset.meterId;
          if (mId) openInspector(mId);
        }
      });
    });
  }

  function updatePagination(total, page, totalPages) {
    currentPageDisplay.textContent = page;
    const start = total === 0 ? 0 : (page - 1) * pageSize + 1;
    const end = Math.min(page * pageSize, total);
    pageInfo.textContent = `Showing ${start}–${end} of ${total} meters`;
    prevPageBtn.disabled = page <= 1;
    nextPageBtn.disabled = page >= totalPages;
  }

  // Update KPIs via background aggregation
  async function updateKpis(total) {
    kpiTotalMeters.textContent = total;
    try {
      const [instRes, faultyRes, decomRes] = await Promise.all([
        fetch("/api/v1/meters?status=Installed&page_size=1"),
        fetch("/api/v1/meters?status=Faulty&page_size=1"),
        fetch("/api/v1/meters?status=Decommissioned&page_size=1")
      ]);
      const instData = await instRes.json();
      const faultyData = await faultyRes.json();
      const decomData = await decomRes.json();

      kpiInstalled.textContent = instData.total;
      kpiInstalledPct.textContent = `${Math.round((instData.total / total) * 100)}% of fleet`;

      kpiFaulty.textContent = faultyData.total;
      kpiFaultyPct.textContent = `${Math.round((faultyData.total / total) * 100)}% anomaly rate`;

      kpiDecom.textContent = decomData.total;
      kpiDecomPct.textContent = `${Math.round((decomData.total / total) * 100)}% decommissioned`;
    } catch (e) {
      console.warn("Could not compute detailed KPIs", e);
    }
  }

  // Debounced Search
  let searchTimeout = null;
  searchInput.addEventListener("input", () => {
    clearTimeout(searchTimeout);
    searchTimeout = setTimeout(() => loadMeters(1), 300);
  });

  statusFilter.addEventListener("change", () => loadMeters(1));
  makeFilter.addEventListener("change", () => loadMeters(1));
  phaseFilter.addEventListener("change", () => loadMeters(1));

  clearFiltersBtn.addEventListener("click", () => {
    searchInput.value = "";
    statusFilter.value = "";
    makeFilter.value = "";
    phaseFilter.value = "";
    loadMeters(1);
  });

  prevPageBtn.addEventListener("click", () => {
    if (currentPage > 1) loadMeters(currentPage - 1);
  });

  nextPageBtn.addEventListener("click", () => {
    loadMeters(currentPage + 1);
  });

  // --------------------------------------------------------------------------
  // Meter Inspector Modal
  // --------------------------------------------------------------------------
  window.openInspector = async function(meterId) {
    inspectorModal.classList.add("open");
    document.getElementById("modalMeterId").textContent = `Meter ${meterId}`;
    document.getElementById("modalSpecsGrid").innerHTML = `<p class="loading-text">Loading specifications...</p>`;
    document.getElementById("modalHierarchyTrail").innerHTML = `<span>Loading grid path...</span>`;
    document.getElementById("modalGeoCoords").textContent = `Loading coordinates...`;
    document.getElementById("chartContainer").innerHTML = `<p class="loading-chart">Loading interval consumption profile...</p>`;

    try {
      // 1. Fetch meter detail
      const res = await fetch(`/api/v1/meters/${meterId}`);
      if (!res.ok) throw new Error("Meter not found");
      const meter = await res.json();

      // Render Specs
      const specs = meter.nameplate || {};
      const specsHtml = Object.entries(specs).map(([k, v]) => `
        <div class="spec-item">
          <dt>${k}</dt>
          <dd>${v || '—'}</dd>
        </div>
      `).join("");
      document.getElementById("modalSpecsGrid").innerHTML = specsHtml || `<p class="text-muted">No nameplate specs found.</p>`;

      // Render Hierarchy Trail
      if (meter.hierarchy) {
        const h = meter.hierarchy;
        const levels = [h.zone, h.circle, h.division, h.subdivision, h.substation, h.feeder, h.dt]
          .filter(Boolean)
          .map(l => l.name);
        document.getElementById("modalHierarchyTrail").innerHTML = levels.map((lvl, i) => `
          <span>${lvl}</span>${i < levels.length - 1 ? '<span class="sep">&rsaquo;</span>' : ''}
        `).join("");
      } else {
        document.getElementById("modalHierarchyTrail").innerHTML = `<span>Hierarchy data unavailable</span>`;
      }

      // Render Geo
      if (meter.geo) {
        const { latitude, longitude } = meter.geo;
        document.getElementById("modalGeoCoords").textContent = `${latitude.toFixed(6)}, ${longitude.toFixed(6)}`;
        document.getElementById("modalMapsLink").href = `https://www.google.com/maps/search/?api=1&query=${latitude},${longitude}`;
        document.getElementById("modalMapsLink").style.display = "inline";
      } else {
        document.getElementById("modalGeoCoords").textContent = "No GPS fix";
        document.getElementById("modalMapsLink").style.display = "none";
      }

      // 2. Fetch Consumption
      loadMeterConsumption(meterId);

    } catch (err) {
      document.getElementById("modalSpecsGrid").innerHTML = `<p class="text-danger">Failed to load meter details: ${err.message}</p>`;
    }
  };

  async function loadMeterConsumption(meterId) {
    try {
      const res = await fetch(`/api/v1/meters/${meterId}/consumption`);
      if (!res.ok) throw new Error("No consumption readings recorded.");
      const data = await res.json();

      const a = data.analytics;
      document.getElementById("modalConsumptionWindow").textContent = `${a.windowStart || ''} → ${a.windowEnd || ''}`;
      document.getElementById("statReadingsCount").textContent = a.totalReadings;
      document.getElementById("statLatestKwh").textContent = a.latestKwh ? `${a.latestKwh.toFixed(1)} kWh` : '--';
      document.getElementById("statNetKwh").textContent = a.netKwhConsumption ? `${a.netKwhConsumption.toFixed(2)} kWh` : '--';
      document.getElementById("statAvgVolt").textContent = a.averageVoltageVolts ? `${a.averageVoltageVolts.toFixed(1)} V` : '--';

      // Render SVG Chart
      renderSvgChart(data.readings);

    } catch (err) {
      document.getElementById("chartContainer").innerHTML = `<p class="text-danger text-center">${err.message}</p>`;
      document.getElementById("statReadingsCount").textContent = '0';
      document.getElementById("statLatestKwh").textContent = '--';
      document.getElementById("statNetKwh").textContent = '--';
      document.getElementById("statAvgVolt").textContent = '--';
    }
  }

  function renderSvgChart(readings) {
    if (!readings || readings.length === 0) {
      document.getElementById("chartContainer").innerHTML = `<p class="text-muted text-center">No interval readings in default window.</p>`;
      return;
    }

    const validReadings = readings.filter(r => r.kwh !== null && r.kwh !== undefined);
    if (validReadings.length < 2) {
      document.getElementById("chartContainer").innerHTML = `<p class="text-muted text-center">Insufficient readings to render curve.</p>`;
      return;
    }

    const kwhVals = validReadings.map(r => r.kwh);
    const minVal = Math.min(...kwhVals);
    const maxVal = Math.max(...kwhVals);
    const range = (maxVal - minVal) || 1;

    const width = 560;
    const height = 180;
    const padding = 25;

    const points = validReadings.map((r, i) => {
      const x = padding + (i / (validReadings.length - 1)) * (width - 2 * padding);
      const y = height - padding - ((r.kwh - minVal) / range) * (height - 2 * padding);
      return `${x.toFixed(1)},${y.toFixed(1)}`;
    }).join(" ");

    const areaPoints = `${padding},${height - padding} ${points} ${(width - padding).toFixed(1)},${height - padding}`;

    const svg = `
      <svg viewBox="0 0 ${width} ${height}" xmlns="http://www.w3.org/2000/svg">
        <defs>
          <linearGradient id="curveGradient" x1="0" y1="0" x2="0" y2="1">
            <stop offset="0%" stop-color="#3b82f6" stop-opacity="0.4"/>
            <stop offset="100%" stop-color="#3b82f6" stop-opacity="0.0"/>
          </linearGradient>
        </defs>
        <!-- Grid lines -->
        <line x1="${padding}" y1="${padding}" x2="${width - padding}" y2="${padding}" stroke="rgba(255,255,255,0.08)" stroke-dasharray="3"/>
        <line x1="${padding}" y1="${height / 2}" x2="${width - padding}" y2="${height / 2}" stroke="rgba(255,255,255,0.08)" stroke-dasharray="3"/>
        <line x1="${padding}" y1="${height - padding}" x2="${width - padding}" y2="${height - padding}" stroke="rgba(255,255,255,0.15)"/>
        
        <!-- Y-Axis labels -->
        <text x="5" y="${padding + 4}" fill="#6b7280" font-size="10" font-family="monospace">${maxVal.toFixed(0)}</text>
        <text x="5" y="${height - padding + 4}" fill="#6b7280" font-size="10" font-family="monospace">${minVal.toFixed(0)}</text>
        
        <!-- Shaded Area -->
        <polygon points="${areaPoints}" fill="url(#curveGradient)" />
        
        <!-- Curve Line -->
        <polyline fill="none" stroke="#3b82f6" stroke-width="2.5" stroke-linecap="round" stroke-linejoin="round" points="${points}" />
      </svg>
    `;

    document.getElementById("chartContainer").innerHTML = svg;
  }

  closeModalBtn.addEventListener("click", () => {
    inspectorModal.classList.remove("open");
  });

  inspectorModal.addEventListener("click", (e) => {
    if (e.target === inspectorModal) inspectorModal.classList.remove("open");
  });

  // --------------------------------------------------------------------------
  // Tab 2: Hierarchy Tree
  // --------------------------------------------------------------------------
  async function loadHierarchy() {
    const container = document.getElementById("hierarchyTreeContainer");
    try {
      const res = await fetch("/api/v1/hierarchy");
      const tree = await res.json();
      container.innerHTML = renderTreeBranch(tree, "tree-root");
      attachTreeListeners();
    } catch (err) {
      container.innerHTML = `<p class="text-danger">Failed to load grid topology: ${err.message}</p>`;
    }
  }

  function renderTreeBranch(obj, idPrefix) {
    let html = "";
    for (const [key, val] of Object.entries(obj)) {
      if (val && typeof val === "object" && !("meters" in val)) {
        html += `
          <div class="tree-node">
            <span class="tree-label"><strong>📁 ${key}</strong></span>
            <div class="tree-children">${renderTreeBranch(val, idPrefix + "-" + key)}</div>
          </div>
        `;
      } else if (val && "meters" in val) {
        html += `
          <div class="tree-node">
            <span class="tree-label">🔌 <strong>${key}</strong> <span class="tree-badge">${val.metersCount} meters</span></span>
          </div>
        `;
      }
    }
    return html;
  }

  function attachTreeListeners() {
    // Expand / collapse could be added if needed
  }

  // --------------------------------------------------------------------------
  // Tab 3: Proximity Radar
  // --------------------------------------------------------------------------
  document.getElementById("runRadarBtn").addEventListener("click", async () => {
    const lat = document.getElementById("radarLat").value;
    const lng = document.getElementById("radarLng").value;
    const radius = document.getElementById("radarRadius").value;
    const container = document.getElementById("nearbyResults");

    container.innerHTML = `<p class="loading-text">Computing spatial distances...</p>`;

    try {
      const res = await fetch(`/api/v1/meters/nearby?latitude=${lat}&longitude=${lng}&radius_km=${radius}&limit=30`);
      const items = await res.json();

      if (!items || items.length === 0) {
        container.innerHTML = `<p class="text-muted">No smart meters detected within ${radius} km of selected coordinates.</p>`;
        return;
      }

      container.innerHTML = items.map(item => `
        <div class="nearby-card-item" onclick="openInspector('${item.meter.meterId}')" style="cursor: pointer;">
          <div>
            <div class="meter-link">${item.meter.meterId}</div>
            <div class="text-sub" style="font-size: 0.75rem;">${item.meter.make} • ${item.meter.phaseType} • ${item.meter.dtCode}</div>
          </div>
          <div class="dist-badge">${item.distanceKm} km</div>
        </div>
      `).join("");
    } catch (err) {
      container.innerHTML = `<p class="text-danger">Proximity search failed: ${err.message}</p>`;
    }
  });

  // --------------------------------------------------------------------------
  // Tab 4: Transformers
  // --------------------------------------------------------------------------
  async function loadTransformers() {
    const tbody = document.getElementById("dtTableBody");
    try {
      const res = await fetch("/api/v1/transformers");
      const dts = await res.json();

      tbody.innerHTML = dts.map(d => `
        <tr>
          <td><code>${d.code}</code></td>
          <td><strong>${d.name}</strong></td>
          <td><code>${d.feederCode}</code></td>
          <td>${d.capacityKva} kVA</td>
          <td><span class="tree-badge">${d.connectedMetersCount} meters</span></td>
        </tr>
      `).join("");
    } catch (err) {
      tbody.innerHTML = `<tr><td colspan="5" class="text-danger loading-cell">Failed to load transformers: ${err.message}</td></tr>`;
    }
  }
});
