(function () {
  const authTokenKey = "energy-monitor-core:auth-token";
  const serverInstanceKey = "energy-monitor-core:server-instance-id";

  function getToken() {
    return sessionStorage.getItem(authTokenKey) || "";
  }

  function setToken(token) {
    const normalized = String(token || "").trim();
    if (normalized) {
      sessionStorage.setItem(authTokenKey, normalized);
    }
  }

  function clearToken() {
    sessionStorage.removeItem(authTokenKey);
  }

  function getBaseHref() {
    const value = String(window.EM_BASE_HREF || "/").trim() || "/";
    return value.endsWith("/") ? value : `${value}/`;
  }

  function resolvePath(path) {
    const normalized = String(path || "").trim().replace(/^\/+/, "");
    if (!normalized) {
      return getBaseHref();
    }
    if (/^(https?:)?\/\//i.test(normalized) || normalized.startsWith("./") || normalized.startsWith("../")) {
      return normalized;
    }
    return new URL(normalized, new URL(getBaseHref(), window.location.origin)).pathname;
  }

  function handleServerInstanceFromResponse(response) {
    if (!response || typeof response.headers?.get !== "function") {
      return;
    }

    const serverInstanceId = String(response.headers.get("X-Monitor-Server-Instance-Id") || "").trim();
    if (!serverInstanceId) {
      return;
    }

    const previousServerInstanceId = sessionStorage.getItem(serverInstanceKey);
    if (!previousServerInstanceId) {
      sessionStorage.setItem(serverInstanceKey, serverInstanceId);
      return;
    }

    if (previousServerInstanceId === serverInstanceId) {
      return;
    }

    sessionStorage.clear();
    window.location.reload();
    throw new Error("Server restarted; reloading session");
  }

  async function request(path, options = {}) {
    const requestPath = resolvePath(path);
    const headers = Object.assign({ "Content-Type": "application/json" }, options.headers || {});
    const token = getToken();
    if (token && !requestPath.endsWith("/api/auth/login")) {
      headers.Authorization = token;
    }

    const response = await fetch(requestPath, Object.assign({}, options, { headers }));
    handleServerInstanceFromResponse(response);
    const payload = await response.json().catch(() => null);
    if (!response.ok) {
      const error = new Error((payload && payload.error) || response.statusText || "Request failed");
      error.payload = payload;
      throw error;
    }
    return payload;
  }

  function showMessage(element, message, isError = false) {
    if (!element) return;
    element.textContent = message;
    element.classList.toggle("text-danger", !!isError);
    element.classList.toggle("text-success", !isError);
  }

  function parseScalar(value) {
    const normalized = String(value ?? "").trim();
    if (!normalized) {
      return "";
    }
    if (normalized === "true") {
      return true;
    }
    if (normalized === "false") {
      return false;
    }
    if (/^-?\d+(?:\.\d+)?$/.test(normalized)) {
      const number = Number(normalized);
      return Number.isNaN(number) ? normalized : number;
    }
    return normalized;
  }

  function setNested(target, path, value) {
    const segments = path.split(/\[|\]/).filter(Boolean);
    let cursor = target;
    for (let index = 0; index < segments.length; index += 1) {
      const segment = segments[index];
      const isLast = index === segments.length - 1;
      const nextIsIndex = /^\d+$/.test(segments[index + 1] || "");
      if (isLast) {
        if (Array.isArray(cursor)) {
          cursor[Number(segment)] = value;
        } else {
          cursor[segment] = value;
        }
        return;
      }

      if (/^\d+$/.test(segment)) {
        const arrayIndex = Number(segment);
        if (!Array.isArray(cursor)) {
          return;
        }
        if (cursor[arrayIndex] === undefined) {
          cursor[arrayIndex] = nextIsIndex ? [] : {};
        }
        cursor = cursor[arrayIndex];
        continue;
      }

      if (cursor[segment] === undefined) {
        cursor[segment] = nextIsIndex ? [] : {};
      }
      cursor = cursor[segment];
    }
  }

  function formToNestedJson(form, prefix) {
    const output = {};
    const formData = new FormData(form);
    for (const [key, value] of formData.entries()) {
      if (!key.startsWith(prefix)) {
        continue;
      }
      const path = key.slice(prefix.length);
      if (!path) {
        continue;
      }
      setNested(output, path, parseScalar(value));
    }
    return output;
  }

  function updateAggregateCards(status) {
    const aggregate = status.aggregate_totals || {};

    ["solar", "wind", "battery"].forEach((sensorType) => {
      const summary = aggregate[sensorType] || {};
      const wattsNode = document.querySelector(`[data-aggregate-watts="${sensorType}"]`);
      const voltageNode = document.querySelector(`[data-aggregate-voltage="${sensorType}"]`);
      const currentNode = document.querySelector(`[data-aggregate-current="${sensorType}"]`);
      if (wattsNode) {
        wattsNode.textContent = String(summary.watts ?? 0);
      }
      if (voltageNode) {
        voltageNode.textContent = String(summary.voltage ?? 0);
      }
      if (currentNode) {
        currentNode.textContent = String(summary.current ?? 0);
      }
    });

    document.querySelectorAll("[data-module-status]").forEach((node) => {
      const moduleName = node.getAttribute("data-module-status");
      const moduleData = status.active_modules && status.active_modules[moduleName];
      if (!moduleData) {
        return;
      }
      const snapshot = status.live_data && status.live_data[moduleName];
      if (snapshot && node.querySelector("[data-module-connected-count]")) {
        node.querySelector("[data-module-connected-count]").textContent = String(snapshot.connected_sensor_count || 0);
      }
      const statusLabel = node.querySelector("[data-module-connection]");
      if (statusLabel && snapshot) {
        statusLabel.textContent = snapshot.status || "disconnected";
      }
    });
  }

  async function refreshStatus() {
    const status = await request("/api/status");
    updateAggregateCards(status);
  }

  async function refreshBackups() {
    const data = await request("/api/backups");
    const summary = document.getElementById("backups-summary");
    const list = document.getElementById("backup-list");
    if (summary) {
      const coreCount = (data.core || []).length;
      const moduleCount = Object.values(data.modules || {}).reduce((total, entries) => total + entries.length, 0);
      summary.textContent = `${coreCount} core backups, ${moduleCount} module backups`;
    }

    if (list) {
      list.innerHTML = "";
      const createBlock = (title, entries, moduleName = null) => {
        const wrap = document.createElement("div");
        wrap.className = "backup-group";
        wrap.innerHTML = `<div class="fw-bold mb-2">${title}</div>`;
        if (!entries || !entries.length) {
          wrap.insertAdjacentHTML("beforeend", '<div class="text-secondary small">No backups available.</div>');
          return wrap;
        }
        entries.forEach((entry) => {
          const row = document.createElement("div");
          row.className = "d-flex justify-content-between align-items-center gap-2 py-2 border-bottom border-secondary-subtle";
          const button = document.createElement("button");
          button.className = "btn btn-outline-warning btn-sm";
          button.textContent = "Restore";
          button.addEventListener("click", async () => {
            const endpoint = moduleName
              ? `/api/backups/module/${encodeURIComponent(moduleName)}/${encodeURIComponent(entry.name)}/restore`
              : `/api/backups/core/${encodeURIComponent(entry.name)}/restore`;
            await request(endpoint, { method: "POST" });
            await refreshAll();
          });
          const label = document.createElement("div");
          label.innerHTML = `<div class="fw-semibold">${entry.name}</div><div class="text-secondary small">${(entry.files || []).join(', ')}</div>`;
          row.appendChild(label);
          row.appendChild(button);
          wrap.appendChild(row);
        });
        return wrap;
      };

      list.appendChild(createBlock("Core backups", data.core || []));
      Object.entries(data.modules || {}).forEach(([moduleName, entries]) => {
        list.appendChild(createBlock(`${moduleName} backups`, entries || [], moduleName));
      });
    }
  }

  async function refreshLogs() {
    const data = await request("/api/logs/recent");
    const logsOutput = document.getElementById("logs-output");
    if (logsOutput) {
      logsOutput.textContent = data.lines || "";
    }
  }

  async function refreshAll() {
    await Promise.allSettled([refreshStatus(), refreshBackups(), refreshLogs()]);
  }

  async function handleLogin(event) {
    event.preventDefault();
    const username = document.getElementById("login-username");
    const password = document.getElementById("login-password");
    const error = document.getElementById("login-error");
    const normalizedUsername = String((username && username.value) || username?.placeholder || "").trim();
    const normalizedPassword = String((password && password.value) || "");
    try {
      const payload = await request("/api/auth/login", {
        method: "POST",
        body: JSON.stringify({ username: normalizedUsername, password: normalizedPassword }),
        headers: {},
      });
      if (payload && payload.auth_token) {
        setToken(payload.auth_token);
      }
      window.location.reload();
    } catch (err) {
      showMessage(error, err.payload && err.payload.error ? err.payload.error : err.message, true);
    }
  }

  async function saveCoreSettings() {
    const form = document.getElementById("core-settings-form");
    const message = document.getElementById("core-settings-message");
    try {
      const payload = form ? formToNestedJson(form, "") : {};
      await request("/api/config", { method: "PUT", body: JSON.stringify(payload) });
      showMessage(message, "Core configuration saved.");
      await refreshAll();
    } catch (err) {
      showMessage(message, err.message, true);
    }
  }

  async function saveModuleSettings() {
    const form = document.getElementById("module-settings-form");
    const message = document.getElementById("module-settings-message");
    const moduleName = window.EM_MODULE_NAME || "";
    try {
      const payload = form ? formToNestedJson(form, "module_config") : {};
      await request(`/api/modules/${encodeURIComponent(moduleName)}`, {
        method: "PUT",
        body: JSON.stringify({ module_config: payload }),
      });
      showMessage(message, "Module settings saved.");
      await refreshAll();
    } catch (err) {
      showMessage(message, err.message, true);
    }
  }

  async function saveInlineModuleSettings(moduleName, form, messageElement) {
    try {
      const payload = form ? formToNestedJson(form, "module_config") : {};
      await request(`/api/modules/${encodeURIComponent(moduleName)}`, {
        method: "PUT",
        body: JSON.stringify({ module_config: payload }),
      });
      showMessage(messageElement, "Module settings saved.");
      await refreshAll();
    } catch (err) {
      showMessage(messageElement, err.message, true);
    }
  }

  async function saveModuleSensors() {
    const form = document.getElementById("module-sensor-form");
    const message = document.getElementById("module-sensor-message");
    const moduleName = window.EM_MODULE_NAME || "";
    try {
      const payload = form ? formToNestedJson(form, "sensor_config") : [];
      const sensorConfig = Object.values(payload);
      await request(`/api/modules/${encodeURIComponent(moduleName)}`, {
        method: "PUT",
        body: JSON.stringify({ sensor_config: sensorConfig }),
      });
      showMessage(message, "Sensor configuration saved.");
      await refreshModuleSnapshot();
    } catch (err) {
      showMessage(message, err.message, true);
    }
  }

  async function refreshModuleSnapshot() {
    const moduleName = window.EM_MODULE_NAME || "";
    const snapshot = await request(`/api/modules/${encodeURIComponent(moduleName)}/snapshot`, { method: "GET" });
    const updatedAt = document.getElementById("live-updated-at");
    if (updatedAt && snapshot && snapshot.updated_at) {
      updatedAt.textContent = snapshot.updated_at;
    }
    const sensorCount = document.getElementById("sensor-count");
    if (sensorCount) {
      sensorCount.textContent = String(snapshot.sensor_count || 0);
    }

    ["solar", "wind", "battery"].forEach((sensorType) => {
      const summary = snapshot.sensor_type_summary && snapshot.sensor_type_summary[sensorType];
      const summaryNode = document.querySelector(`[data-summary-watts="${sensorType}"]`);
      if (!summaryNode || !summary) return;
      summaryNode.textContent = String(summary.watts ?? 0);
      const card = summaryNode.closest(".metric-card");
      if (!card) return;
      const small = card.querySelector(".metric-small");
      if (small) {
        small.textContent = `V ${summary.voltage ?? 0} | A ${summary.current ?? 0}`;
      }
    });

    document.querySelectorAll("[data-sensor-row]").forEach((row, index) => {
      const sensor = snapshot.sensor_rows && snapshot.sensor_rows[index];
      if (!sensor) return;
      const statusLabel = row.querySelector("[data-field='connection-status']");
      if (statusLabel) {
        statusLabel.textContent = sensor.connected ? "Connected" : "Disconnected";
        statusLabel.classList.toggle("text-bg-success", !!sensor.connected);
        statusLabel.classList.toggle("text-bg-secondary", !sensor.connected);
      }
      row.querySelector("[data-field='watts']")?.replaceChildren(document.createTextNode(String(sensor.watts ?? 0)));
      row.querySelector("[data-field='voltage']")?.replaceChildren(document.createTextNode(String(sensor.voltage ?? 0)));
      row.querySelector("[data-field='current']")?.replaceChildren(document.createTextNode(String(sensor.current ?? 0)));
    });
  }

  async function activateModule() {
    const moduleName = window.EM_MODULE_NAME || "";
    await request(`/api/modules/${encodeURIComponent(moduleName)}/activate`, { method: "POST" });
    window.location.reload();
  }

  async function deactivateModule() {
    const moduleName = window.EM_MODULE_NAME || "";
    await request(`/api/modules/${encodeURIComponent(moduleName)}/deactivate`, { method: "POST" });
    window.location.href = resolvePath("/");
  }

  async function backupCore() {
    await request("/api/backups/core", { method: "POST" });
    await refreshAll();
  }

  async function backupAll() {
    await request("/api/backups/all", { method: "POST" });
    await refreshAll();
  }

  function bindDashboard() {
    document.getElementById("refresh-btn")?.addEventListener("click", refreshAll);
    document.getElementById("backup-all-btn")?.addEventListener("click", backupAll);
    document.getElementById("logout-btn")?.addEventListener("click", async function () {
      await request("/api/auth/logout", { method: "POST" });
      clearToken();
      window.location.reload();
    });
    refreshAll();
    setInterval(refreshStatus, 15000);
  }

  function bindCoreSettings() {
    document.getElementById("save-core-settings-btn")?.addEventListener("click", saveCoreSettings);
    document.getElementById("backup-core-btn")?.addEventListener("click", backupCore);
    document.querySelectorAll("[data-module-save]").forEach((button) => {
      button.addEventListener("click", async () => {
        const moduleName = button.getAttribute("data-module-save") || "";
        const form = document.querySelector(`form[data-module-name="${moduleName}"]`);
        const message = document.getElementById(`module-core-settings-message-${moduleName}`);
        await saveInlineModuleSettings(moduleName, form, message);
      });
    });
    document.getElementById("logout-btn")?.addEventListener("click", async function () {
      await request("/api/auth/logout", { method: "POST" });
      clearToken();
      window.location.reload();
    });
  }

  function bindModulePage() {
    document.getElementById("refresh-module-btn")?.addEventListener("click", refreshModuleSnapshot);
    document.getElementById("save-module-sensors-btn")?.addEventListener("click", saveModuleSensors);
    document.getElementById("logout-btn")?.addEventListener("click", async function () {
      await request("/api/auth/logout", { method: "POST" });
      clearToken();
      window.location.reload();
    });
    document.getElementById("module-backup-btn")?.addEventListener("click", async function () {
      await request(`/api/backups/module/${encodeURIComponent(window.EM_MODULE_NAME || "")}`, { method: "POST" });
      await refreshAll();
    });
    document.getElementById("module-settings-link")?.addEventListener("click", function () {
      window.location.href = this.getAttribute("href") || "/";
    });
    document.getElementById("module-activate-btn")?.addEventListener("click", activateModule);
    document.getElementById("module-deactivate-btn")?.addEventListener("click", deactivateModule);
    refreshModuleSnapshot();
    setInterval(refreshModuleSnapshot, 10000);
  }

  function bindModuleSettings() {
    document.getElementById("save-module-settings-btn")?.addEventListener("click", saveModuleSettings);
    document.getElementById("logout-btn")?.addEventListener("click", async function () {
      await request("/api/auth/logout", { method: "POST" });
      clearToken();
      window.location.reload();
    });
  }

  document.addEventListener("DOMContentLoaded", () => {
    const loginForm = document.getElementById("login-form");
    if (loginForm) {
      loginForm.addEventListener("submit", handleLogin);
      return;
    }

    const page = document.body.dataset.page || "dashboard";
    if (page === "dashboard") {
      bindDashboard();
      return;
    }
    if (page === "core-settings") {
      bindCoreSettings();
      return;
    }
    if (page === "module") {
      bindModulePage();
      return;
    }
    if (page === "module-settings") {
      bindModuleSettings();
    }
  });
})();
