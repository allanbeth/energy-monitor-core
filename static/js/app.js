(function () {
  const serverInstanceKey = "energy-monitor-core:server-instance-id";

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

  function syncServerInstanceId() {
    const currentServerInstanceId = String(window.EM_SERVER_INSTANCE_ID || "").trim();
    if (!currentServerInstanceId) {
      return;
    }

    const previousServerInstanceId = sessionStorage.getItem(serverInstanceKey);
    if (!previousServerInstanceId) {
      sessionStorage.setItem(serverInstanceKey, currentServerInstanceId);
      return;
    }

    if (previousServerInstanceId !== currentServerInstanceId) {
      sessionStorage.clear();
      window.location.reload();
    }
  }

  async function heartbeatServerInstance() {
    try {
      await request("/health", { method: "GET" });
    } catch (error) {
      void error;
    }
  }

  let serverHeartbeatTimer = null;

  function startServerHeartbeat() {
    if (serverHeartbeatTimer !== null) {
      return;
    }

    heartbeatServerInstance();
    serverHeartbeatTimer = window.setInterval(heartbeatServerInstance, 10000);
    window.addEventListener("focus", heartbeatServerInstance);
    document.addEventListener("visibilitychange", () => {
      if (!document.hidden) {
        heartbeatServerInstance();
      }
    });
  }

  async function request(path, options = {}) {
    const requestPath = resolvePath(path);
    const headers = Object.assign({ "Content-Type": "application/json" }, options.headers || {});

    const response = await fetch(requestPath, Object.assign({}, options, {
      headers,
      credentials: "same-origin",
    }));
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

  function showStatusMessage(element, message, tone = "success") {
    if (!element) return;
    element.textContent = String(message || "");
    element.classList.remove("text-danger", "text-success", "text-warning");
    if (tone === "error") {
      element.classList.add("text-danger");
      return;
    }
    if (tone === "warning") {
      element.classList.add("text-warning");
      return;
    }
    element.classList.add("text-success");
  }

  function showStatusMessages(elements, message, tone = "success") {
    const seen = new Set();
    elements.forEach((element) => {
      if (!element || seen.has(element)) {
        return;
      }
      seen.add(element);
      showStatusMessage(element, message, tone);
    });
  }

  function formatTimestampToSecond(value) {
    const raw = String(value || "").trim();
    if (!raw) {
      return "";
    }
    const parsed = new Date(raw);
    if (Number.isNaN(parsed.getTime())) {
      return raw.replace("T", " ").replace("Z", "").split(".")[0];
    }
    const pad = (part) => String(part).padStart(2, "0");
    return `${parsed.getFullYear()}-${pad(parsed.getMonth() + 1)}-${pad(parsed.getDate())} ${pad(parsed.getHours())}:${pad(parsed.getMinutes())}:${pad(parsed.getSeconds())}`;
  }

  function inferConnectionIcon(sensor) {
    const source = `${sensor?.source_topic || ""} ${sensor?.variant || ""} ${sensor?.type || ""}`.toLowerCase();
    if (source.includes("ble") || source.includes("bluetooth") || source.includes("wifi") || source.includes("wireless")) {
      return "fa-wifi";
    }
    return "fa-network-wired";
  }

  function toBool(value) {
    if (typeof value === "boolean") return value;
    const text = String(value || "").trim().toLowerCase();
    return text === "true" || text === "1" || text === "yes";
  }

  function normalizeIdentifier(value) {
    if (value === undefined || value === null) {
      return "";
    }
    return String(value).trim();
  }

  function escapeHtml(value) {
    return String(value || "")
      .replaceAll("&", "&amp;")
      .replaceAll("<", "&lt;")
      .replaceAll(">", "&gt;")
      .replaceAll('"', "&quot;")
      .replaceAll("'", "&#39;");
  }

  function findModuleDevice(snapshot, sensor) {
    const moduleConfig = snapshot && typeof snapshot === "object" ? snapshot.module_config : null;
    const devices = moduleConfig && Array.isArray(moduleConfig.devices) ? moduleConfig.devices : [];
    const sensorDeviceId = normalizeIdentifier(sensor?.device_id);
    if (!sensorDeviceId) return null;

    return devices.find((device) => {
      if (!device || typeof device !== "object") return false;
      const id = normalizeIdentifier(device.id);
      return id && id === sensorDeviceId;
    }) || null;
  }

  function resolveDeviceName(moduleName, snapshot, sensor) {
    const moduleKey = String(moduleName || "").trim().toLowerCase();
    const device = findModuleDevice(snapshot, sensor);
    if (device && String(device.name || "").trim()) {
      return String(device.name || "").trim();
    }
    if (moduleKey === "victron" && String(sensor?.name || "").includes(" Charger")) {
      return String(sensor.name || "").replace(/\s+charger$/i, "").trim();
    }
    if (normalizeIdentifier(sensor?.device_id)) {
      return normalizeIdentifier(sensor.device_id);
    }
    return String(sensor?.name || "Sensor").trim() || "Sensor";
  }

  function resolveConnectionTransport(moduleName, snapshot, sensor) {
    const moduleKey = String(moduleName || "").trim().toLowerCase();
    if (moduleKey === "ina") {
      const device = findModuleDevice(snapshot, sensor);
      const isRemote = !!(device && (toBool(device.remote_gpio) || String(device.gpio_address || "").trim()));
      return {
        kind: isRemote ? "remote" : "local",
        icon: isRemote ? "fa-wifi" : "fa-network-wired",
        label: isRemote ? "Remote" : "Local",
      };
    }

    const source = `${sensor?.source_topic || ""} ${sensor?.status_detail || ""} ${sensor?.variant || ""} ${sensor?.type || ""}`.toLowerCase();
    if (moduleKey === "victron" || source.includes("ble") || source.includes("bluetooth")) {
      return { kind: "wireless", icon: "fa-bluetooth-b", label: "Bluetooth" };
    }
    if (source.includes("wifi") || source.includes("wireless")) {
      return { kind: "wireless", icon: "fa-wifi", label: "Wireless" };
    }
    return { kind: "wired", icon: inferConnectionIcon(sensor), label: "Wired" };
  }

  function resolveConnectionState(sensor, moduleName = "") {
    const connected = !!sensor?.connected;
    const deviceConnected = !!sensor?.device_connected;
    const moduleKey = String(moduleName || "").trim().toLowerCase();
    const statusRaw = String(sensor?.status || "").trim().toLowerCase();
    const detailRaw = String(sensor?.status_detail || "").trim().toLowerCase();
    const watts = Number(sensor?.watts ?? sensor?.power ?? 0);
    const voltage = Number(sensor?.voltage ?? 0);
    const current = Number(sensor?.current ?? 0);

    if (!connected || statusRaw === "disconnected") {
      if (deviceConnected && moduleKey === "victron") {
        return { state: "connected", label: "Connected" };
      }
      return { state: "disconnected", label: "Disconnected" };
    }

    const hasNoDataHint = detailRaw.includes("no-data") || detailRaw.includes("no-signal") || detailRaw.includes("waiting-telemetry") || detailRaw.includes("read-failed") || detailRaw.includes("timeout");
    const hasMetrics = Number.isFinite(watts) && Number.isFinite(voltage) && Number.isFinite(current) && (Math.abs(watts) > 0 || Math.abs(voltage) > 0 || Math.abs(current) > 0);
    if ((moduleKey === "ina" || moduleKey === "victron") && (hasNoDataHint || deviceConnected)) {
      return { state: "connected", label: "Connected" };
    }
    if (statusRaw === "partial" || hasNoDataHint || !hasMetrics) {
      return { state: "partial", label: "Connected (No Data)" };
    }

    return { state: "connected", label: "Connected" };
  }

  function setConnectionIndicator(cardElement, sensor, snapshot = null) {
    if (!cardElement || !sensor) {
      return;
    }
    const moduleName = String(window.EM_MODULE_NAME || "");
    const transport = resolveConnectionTransport(moduleName, snapshot, sensor);
    const connection = resolveConnectionState(sensor, moduleName);
    const iconNode = cardElement.querySelector("[data-field='connection-icon']");
    const textNode = cardElement.querySelector("[data-field='connection-text']");
    const stateNode = cardElement.querySelector("[data-field='connection-state']");
    const deviceName = resolveDeviceName(moduleName, snapshot, sensor);
    if (iconNode) {
      iconNode.classList.remove("fa-wifi", "fa-network-wired", "fa-bluetooth-b", "status-connected", "status-partial", "status-disconnected", "connection-local", "connection-remote", "connection-wired", "connection-wireless");
      iconNode.classList.add(transport.icon);
      iconNode.classList.add(`status-${connection.state}`);
      iconNode.classList.add(`connection-${transport.kind}`);
    }
    if (textNode) {
      textNode.textContent = `${deviceName} - ${connection.label}`;
    }
    if (stateNode) {
      stateNode.classList.remove("status-connected", "status-partial", "status-disconnected");
      stateNode.classList.add(`status-${connection.state}`);
    }
    cardElement.dataset.sensorConnected = sensor && sensor.connected ? "1" : "0";
  }

  function updateChargeCycleIndicator(cardElement, sensor) {
    if (!cardElement || !sensor) {
      return;
    }
    const stateText = String(sensor.charge_mode || sensor.charging_state || sensor.status_detail || sensor.status || "").trim().toLowerCase();
    const normalizedStage = stateText.includes("bulk") ? "bulk"
      : stateText.includes("absorption") ? "absorption"
      : stateText.includes("float") ? "float"
      : stateText.includes("storage") ? "storage"
      : stateText.includes("off") ? "off"
      : "off";

    const stateNode = cardElement.querySelector("[data-field='charge-state']");
    if (stateNode) {
      const rawState = String(sensor.charging_state || sensor.charge_mode || (sensor.connected ? "Connected" : "Disconnected") || "").trim();
      stateNode.textContent = rawState ? rawState.replace(/_/g, " ").replace(/\b\w/g, (char) => char.toUpperCase()) : "";
    }

    const detailNode = cardElement.querySelector("[data-field='status-detail']");
    if (detailNode) {
      detailNode.textContent = String(sensor.status_detail || sensor.status || "");
    }

    const signalNode = cardElement.querySelector("[data-field='signal']");
    if (signalNode) {
      signalNode.textContent = sensor.rssi !== undefined && sensor.rssi !== null && String(sensor.rssi).trim() !== ""
        ? `${sensor.rssi} dBm`
        : "0 dBm";
    }

    const connectionNode = cardElement.querySelector("[data-field='connection-detail']");
    if (connectionNode) {
      connectionNode.textContent = String(sensor.status_detail || (sensor.connected ? "Connected" : "Disconnected") || "");
    }

    cardElement.querySelectorAll("[data-charge-stage]").forEach((stageNode) => {
      const stage = String(stageNode.getAttribute("data-charge-stage") || "").trim().toLowerCase();
      stageNode.classList.toggle("is-active", stage === normalizedStage);
    });
  }

    function normalizeInaAddress(value) {
      const text = String(value ?? "").trim().toLowerCase();
      if (!text) {
        return "";
      }
      if (text.startsWith("0x")) {
        const parsed = Number.parseInt(text, 16);
        return Number.isFinite(parsed) ? `0x${parsed.toString(16).padStart(2, "0")}` : text;
      }
      const parsedDec = Number.parseInt(text, 10);
      if (Number.isFinite(parsedDec)) {
        return `0x${parsedDec.toString(16).padStart(2, "0")}`;
      }
      const parsedHex = Number.parseInt(text, 16);
      return Number.isFinite(parsedHex) ? `0x${parsedHex.toString(16).padStart(2, "0")}` : text;
    }

    function getModuleSnapshot() {
      return window.EM_MODULE_SNAPSHOT || {};
    }

    function getModuleSensorConfig() {
      const snapshot = getModuleSnapshot();
      return Array.isArray(snapshot.sensor_config) ? snapshot.sensor_config : [];
    }

    function getSensorCardElement(sensorIndex) {
      const suffix = sensorIndex === "new" ? "new" : String(sensorIndex);
      return document.querySelector(`[data-sensor-row="${suffix}"]`);
    }

    function getSensorCardSection(sensorIndex, section) {
      const suffix = sensorIndex === "new" ? "new" : String(sensorIndex);
      return document.getElementById(`sensor-card-${section}-${suffix}`);
    }

    function getSensorActionContainer(sensorIndex) {
      const suffix = sensorIndex === "new" ? "new" : String(sensorIndex);
      return document.getElementById(`sensor-actions-${suffix}`);
    }

    function setSensorCardFeedback(sensorIndex, message, isError = false) {
      const feedback = document.getElementById(`sensor-feedback-${sensorIndex}`);
      if (!feedback) {
        return;
      }
      feedback.textContent = message;
      feedback.classList.toggle("hidden", !message);
      feedback.classList.toggle("is-error", !!isError);
    }

    function setSensorCardMode(sensorIndex, mode) {
      const card = getSensorCardElement(sensorIndex);
      if (!card) {
        return;
      }

      const normalizedMode = mode || "view";
      card.dataset.cardMode = normalizedMode;

      const viewSection = getSensorCardSection(sensorIndex, "view");
      const configSection = getSensorCardSection(sensorIndex, "config");
      const logsSection = getSensorCardSection(sensorIndex, "logs");

      if (viewSection) viewSection.classList.toggle("hidden", normalizedMode !== "view");
      if (configSection) configSection.classList.toggle("hidden", normalizedMode !== "config");
      if (logsSection) logsSection.classList.toggle("hidden", normalizedMode !== "logs");

      if (normalizedMode === "feedback") {
        if (viewSection) viewSection.classList.add("hidden");
        if (configSection) configSection.classList.add("hidden");
        if (logsSection) logsSection.classList.add("hidden");
      }

      const actionContainer = getSensorActionContainer(sensorIndex);
      if (actionContainer) {
        if (normalizedMode === "config") {
          actionContainer.innerHTML = `
            <button type="button" class="sensor-action-btn" data-sensor-card-action="save" data-sensor-index="${sensorIndex}" title="Save sensor"><i class="fa-solid fa-floppy-disk"></i></button>
            <button type="button" class="sensor-action-btn" data-sensor-card-action="cancel" data-sensor-index="${sensorIndex}" title="Cancel"><i class="fa-solid fa-xmark"></i></button>
          `;
        } else if (normalizedMode === "logs") {
          actionContainer.innerHTML = `
            <button type="button" class="sensor-action-btn" data-sensor-card-action="back" data-sensor-index="${sensorIndex}" title="Back"><i class="fa-solid fa-arrow-left"></i></button>
          `;
        } else if (normalizedMode === "feedback") {
          actionContainer.innerHTML = "";
        } else {
          actionContainer.innerHTML = `
            <button type="button" class="sensor-action-btn" data-sensor-card-action="edit" data-sensor-index="${sensorIndex}" title="Edit sensor"><i class="fa-solid fa-gear"></i></button>
            <button type="button" class="sensor-action-btn" data-sensor-card-action="logs" data-sensor-index="${sensorIndex}" title="Recent sensor data"><i class="fa-solid fa-book"></i></button>
          `;
        }
      }

      if (normalizedMode !== "config" && normalizedMode !== "feedback") {
        setSensorCardFeedback(sensorIndex, "");
      }
    }

    function enforceSensorCardModeState() {
      document.querySelectorAll("[data-sensor-row]").forEach((card) => {
        const sensorIndex = String(card.getAttribute("data-sensor-row") || "").trim();
        if (!sensorIndex) {
          return;
        }
        const mode = String(card.getAttribute("data-card-mode") || "view").trim() || "view";
        setSensorCardMode(sensorIndex, mode);
      });
    }

    function showSensorCardTransientFeedback(sensorIndex, message, isError = false, returnMode = "view", timeoutMs = 1350) {
      setSensorCardFeedback(sensorIndex, message, isError);
      setSensorCardMode(sensorIndex, "feedback");
      window.setTimeout(() => {
        setSensorCardFeedback(sensorIndex, "", isError);
        setSensorCardMode(sensorIndex, returnMode);
      }, timeoutMs);
    }

    function getInaAvailableAddresses(deviceId, selectedAddress = "", sensorIndex = null) {
      const selected = normalizeInaAddress(selectedAddress);
      const usedAddresses = new Set();
      const sensorConfig = getModuleSensorConfig();
      const normalizedDeviceId = normalizeIdentifier(deviceId);

      sensorConfig.forEach((sensor, index) => {
        if (!sensor || typeof sensor !== "object") {
          return;
        }
        if (sensorIndex !== null && String(index) === String(sensorIndex)) {
          return;
        }
        if (normalizeIdentifier(sensor.device_id) !== normalizedDeviceId) {
          return;
        }
        const address = normalizeInaAddress(sensor.address);
        if (address) {
          usedAddresses.add(address);
        }
      });

      return ["0x40", "0x41", "0x42", "0x43", "0x44", "0x45", "0x46", "0x47", "0x48", "0x49", "0x4a", "0x4b", "0x4c", "0x4d", "0x4e", "0x4f"]
        .filter((address) => !usedAddresses.has(address) || address === selected);
    }

    function populateInaAddressSelect(form, sensorIndex) {
      if ((window.EM_MODULE_NAME || "") !== "ina" || !form) {
        return;
      }

      const deviceSelect = form.querySelector("[data-ina-device-select]");
      const addressSelect = form.querySelector("[data-ina-address-select]");
      if (!deviceSelect || !addressSelect) {
        return;
      }

      const deviceId = deviceSelect.value;
      const selectedAddress = normalizeInaAddress(addressSelect.value || form.querySelector("[name='address']")?.value || "");
      const currentIndex = sensorIndex === "new" ? null : Number(sensorIndex);

      if (!deviceId) {
        addressSelect.innerHTML = '<option value="" disabled selected>Select device first</option>';
        addressSelect.disabled = true;
        return;
      }

      const availableAddresses = getInaAvailableAddresses(deviceId, selectedAddress, currentIndex);
      addressSelect.innerHTML = availableAddresses.length
        ? availableAddresses.map((address) => `<option value="${address}" ${address === selectedAddress ? "selected" : ""}>${address}</option>`).join("")
        : '<option value="" disabled selected>No available I2C addresses</option>';
      addressSelect.disabled = availableAddresses.length === 0;
      if (selectedAddress && availableAddresses.includes(selectedAddress)) {
        addressSelect.value = selectedAddress;
      } else if (availableAddresses.length > 0) {
        addressSelect.value = availableAddresses[0];
      }
    }

    function refreshInaAddressSelectors() {
      document.querySelectorAll(".sensor-config-form").forEach((form) => {
        const sensorIndex = form.getAttribute("data-sensor-index") || "";
        populateInaAddressSelect(form, sensorIndex);
      });
    }
    function syncInaExternalShuntControls(form) {
      if ((window.EM_MODULE_NAME || "") !== "ina" || !form) {
        return;
      }
      const toggle = form.querySelector("[data-ina-external-shunt-toggle]");
      const fields = form.querySelector("[data-ina-external-shunt-fields]");
      const select = form.querySelector("[data-ina-external-shunt-select]");
      const variantField = form.querySelector("[name='variant']");
      if (!toggle || !fields || !select) {
        return;
      }

      const variant = String(variantField?.value || "INA219").trim().toUpperCase();
      const supportsExternalShunt = variant === "INA219";
      const enabled = supportsExternalShunt && !!toggle.checked;
      fields.classList.toggle("hidden", !enabled);
      toggle.disabled = !supportsExternalShunt;
      if (!supportsExternalShunt) {
        toggle.checked = false;
        select.value = "";
      }
      select.disabled = !enabled;
      if (!enabled) {
        select.value = "";
      }
    }

    function refreshInaExternalShuntControls() {
      document.querySelectorAll(".sensor-config-form").forEach((form) => {
        syncInaExternalShuntControls(form);
      });
    }

  function showLoadingScreen(message = "Loading...") {
    const screen = document.getElementById("loading-screen");
    const messageNode = document.getElementById("loading-message");
    const stepNode = document.getElementById("loading-step-message");
    if (screen) {
      screen.hidden = false;
    }
    if (messageNode) {
      messageNode.textContent = message;
    }
    if (stepNode) {
      stepNode.textContent = "";
    }
  }

  function createStagedProgress(messages, onStep, intervalMs = 450) {
    const steps = Array.isArray(messages) ? messages.filter((item) => String(item || "").trim()) : [];
    let index = 0;
    if (steps.length && typeof onStep === "function") {
      onStep(steps[0], 0);
      index = 1;
    }

    if (steps.length <= 1) {
      return { stop: () => void 0 };
    }

    const timer = window.setInterval(() => {
      if (index >= steps.length) {
        window.clearInterval(timer);
        return;
      }
      if (typeof onStep === "function") {
        onStep(steps[index], index);
      }
      index += 1;
    }, Math.max(250, Number(intervalMs) || 450));

    return {
      stop: () => {
        window.clearInterval(timer);
      },
    };
  }

  function hideLoadingScreen() {
    const screen = document.getElementById("loading-screen");
    const stepNode = document.getElementById("loading-step-message");
    if (screen) {
      screen.hidden = true;
    }
    if (stepNode) {
      stepNode.textContent = "";
    }
  }

  function setLoadingStepMessage(message = "") {
    const stepNode = document.getElementById("loading-step-message");
    if (!stepNode) {
      return;
    }
    stepNode.textContent = String(message || "");
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
    const trends = status.dashboard_trends || {};
    const derived = aggregate.derived || {};
    const systemsSummary = aggregate.systems_summary || {};
    const systems = aggregate.systems || {};

    const globalSystemsGeneration = document.querySelector('[data-systems-global-generation]');
    const globalSystemsWatts = document.querySelector('[data-systems-global-watts]');
    const globalSystemsSolar = document.querySelector('[data-systems-global-solar]');
    const globalSystemsWind = document.querySelector('[data-systems-global-wind]');
    const globalSystemsVoltage = document.querySelector('[data-systems-global-voltage]');
    const globalSystemsCurrent = document.querySelector('[data-systems-global-current]');
    const globalSystemsSoc = document.querySelector('[data-systems-global-soc]');
    const globalSystemsState = document.querySelector('[data-systems-global-state]');
    const globalSystemsBatteryFlow = document.querySelector('[data-systems-global-battery-flow]');
    const globalSystemsCount = document.querySelector('[data-systems-global-count]');
    const globalSystemsActive = document.querySelector('[data-systems-global-active]');
    const globalSystemsLocations = document.querySelector('[data-systems-global-locations]');

    const solarWatts = Number((aggregate.solar || {}).watts ?? 0);
    const windWatts = Number((aggregate.wind || {}).watts ?? 0);
    const generationWatts = solarWatts + windWatts;

    if (globalSystemsGeneration) globalSystemsGeneration.textContent = String(generationWatts);
    if (globalSystemsWatts) globalSystemsWatts.textContent = String((aggregate.overall || {}).watts ?? 0);
    if (globalSystemsSolar) globalSystemsSolar.textContent = String(solarWatts);
    if (globalSystemsWind) globalSystemsWind.textContent = String(windWatts);
    if (globalSystemsVoltage) globalSystemsVoltage.textContent = String(derived.battery_bank_voltage ?? 0);
    if (globalSystemsCurrent) globalSystemsCurrent.textContent = String(derived.battery_bank_current ?? 0);
    if (globalSystemsSoc) globalSystemsSoc.textContent = String(derived.battery_bank_soc ?? 0);
    if (globalSystemsBatteryFlow) globalSystemsBatteryFlow.textContent = String(derived.battery_bank_watts ?? 0);
    if (globalSystemsState) {
      const state = String(derived.battery_bank_state ?? "idle");
      globalSystemsState.textContent = state.charAt(0).toUpperCase() + state.slice(1);
    }
    if (globalSystemsCount) globalSystemsCount.textContent = String(systemsSummary.configured_system_count ?? 0);
    if (globalSystemsActive) globalSystemsActive.textContent = String(systemsSummary.active_system_count ?? 0);
    if (globalSystemsLocations) globalSystemsLocations.textContent = String(systemsSummary.configured_location_count ?? 0);

    Object.entries(systems).forEach(([systemId, system]) => {
      const summary = system && typeof system === "object" ? system : {};
      const systemDerived = summary.derived || {};
      const byAttr = (name) => document.querySelector(`[${name}="${systemId}"]`);

      const overallWattsNode = byAttr("data-system-overall-watts");
      const generationWattsNode = byAttr("data-system-generation-watts");
      const solarWattsNode = byAttr("data-system-solar-watts");
      const windWattsNode = byAttr("data-system-wind-watts");
      const voltageNode = byAttr("data-system-voltage");
      const currentNode = byAttr("data-system-current");
      const socNode = byAttr("data-system-soc");
      const stateNode = byAttr("data-system-state");
      const batteryFlowNode = byAttr("data-system-battery-flow");
      const countNode = byAttr("data-system-count");

      const systemSolarWatts = Number((summary.solar || {}).watts ?? 0);
      const systemWindWatts = Number((summary.wind || {}).watts ?? 0);
      const systemGenerationWatts = systemSolarWatts + systemWindWatts;

      if (overallWattsNode) overallWattsNode.textContent = String((summary.overall || {}).watts ?? 0);
      if (generationWattsNode) generationWattsNode.textContent = String(systemGenerationWatts);
      if (solarWattsNode) solarWattsNode.textContent = String(systemSolarWatts);
      if (windWattsNode) windWattsNode.textContent = String(systemWindWatts);
      if (voltageNode) voltageNode.textContent = String(systemDerived.battery_bank_voltage ?? 0);
      if (currentNode) currentNode.textContent = String(systemDerived.battery_bank_current ?? 0);
      if (socNode) socNode.textContent = String(systemDerived.battery_bank_soc ?? 0);
      if (batteryFlowNode) batteryFlowNode.textContent = String(systemDerived.battery_bank_watts ?? 0);
      if (stateNode) {
        const state = String(systemDerived.battery_bank_state ?? "idle");
        stateNode.textContent = state.charAt(0).toUpperCase() + state.slice(1);
      }
      if (countNode) countNode.textContent = String((summary.overall || {}).sensor_count ?? 0);
    });

    const overall = aggregate.overall || {};
    const overallWatts = document.querySelector('[data-aggregate-watts="overall"]');
    const overallVoltage = document.querySelector('[data-aggregate-voltage="overall"]');
    const overallCurrent = document.querySelector('[data-aggregate-current="overall"]');
    const overallCount = document.querySelector('[data-aggregate-count="overall"]');
    if (overallWatts) overallWatts.textContent = String(overall.watts ?? 0);
    if (overallVoltage) overallVoltage.textContent = String(overall.voltage ?? 0);
    if (overallCurrent) overallCurrent.textContent = String(overall.current ?? 0);
    if (overallCount) overallCount.textContent = String(overall.sensor_count ?? 0);

    ["solar", "wind", "battery"].forEach((sensorType) => {
      const summary = aggregate[sensorType] || {};
      const wattsNode = document.querySelector(`[data-aggregate-watts="${sensorType}"]`);
      const voltageNode = document.querySelector(`[data-aggregate-voltage="${sensorType}"]`);
      const currentNode = document.querySelector(`[data-aggregate-current="${sensorType}"]`);
      const countNode = document.querySelector(`[data-aggregate-count="${sensorType}"]`);
      if (wattsNode) {
        wattsNode.textContent = String(summary.watts ?? 0);
      }
      if (voltageNode) {
        voltageNode.textContent = String(summary.voltage ?? 0);
      }
      if (currentNode) {
        currentNode.textContent = String(summary.current ?? 0);
      }
      if (countNode) {
        countNode.textContent = String(summary.sensor_count ?? 0);
      }
    });

    const chargerSummary = aggregate.charger || {};
    const chargerWatts = document.querySelector('[data-aggregate-watts="charger"]');
    const chargerVoltage = document.querySelector('[data-aggregate-voltage="charger"]');
    const chargerCurrent = document.querySelector('[data-aggregate-current="charger"]');
    const chargerCount = document.querySelector('[data-aggregate-count="charger"]');
    const chargerSlot = document.querySelector('[data-dashboard-charger-slot]');
    const chargerCard = document.querySelector('[data-dashboard-charger-card]');
    const hideCharger = Number(chargerSummary.connected_count ?? 0) <= 0;
    if (chargerSlot) {
      chargerSlot.classList.toggle("hidden", hideCharger);
    }
    if (chargerCard) {
      chargerCard.classList.toggle("hidden", hideCharger);
    }
    if (chargerWatts) chargerWatts.textContent = String(chargerSummary.watts ?? 0);
    if (chargerVoltage) chargerVoltage.textContent = String(chargerSummary.voltage ?? 0);
    if (chargerCurrent) chargerCurrent.textContent = String(chargerSummary.current ?? 0);
    if (chargerCount) chargerCount.textContent = String(chargerSummary.sensor_count ?? 0);

    const batteryDischarge = document.querySelector('[data-derived-watts="battery-discharge"]');
    const batteryCharge = document.querySelector('[data-derived-watts="battery-charge"]');
    const estimatedLoad = document.querySelector('[data-derived-watts="estimated-load"]');
    const flowSensorType = document.querySelector('[data-derived-sensor-type]');
    const batterySensorCount = document.querySelector('[data-derived-count="battery-sensors"]');
    const batteryCurrentSensors = document.querySelector('[data-derived-count="battery-current-sensors"]');
    const batteryVoltageSensors = document.querySelector('[data-derived-count="battery-voltage-sensors"]');
    const batteryBankVoltage = document.querySelector('[data-derived-battery-voltage]');
    const batteryBankCurrent = document.querySelector('[data-derived-battery-current]');
    const batteryBankSoc = document.querySelector('[data-derived-battery-soc]');
    const batteryBankState = document.querySelector('[data-derived-battery-state]');
    if (batteryDischarge) batteryDischarge.textContent = String(derived.battery_discharge_watts ?? 0);
    if (batteryCharge) batteryCharge.textContent = String(derived.battery_charge_watts ?? 0);
    if (estimatedLoad) estimatedLoad.textContent = String(derived.estimated_load_watts ?? 0);
    if (flowSensorType) {
      const flowType = String(derived.flow_sensor_type ?? "battery");
      flowSensorType.textContent = flowType.charAt(0).toUpperCase() + flowType.slice(1);
    }
    if (batterySensorCount) batterySensorCount.textContent = String(derived.battery_sensor_count ?? 0);
    if (batteryCurrentSensors) batteryCurrentSensors.textContent = String(derived.battery_current_sensor_count ?? 0);
    if (batteryVoltageSensors) batteryVoltageSensors.textContent = String(derived.battery_voltage_sensor_count ?? 0);
    if (batteryBankVoltage) batteryBankVoltage.textContent = String(derived.battery_bank_voltage ?? 0);
    if (batteryBankCurrent) batteryBankCurrent.textContent = String(derived.battery_bank_current ?? 0);
    if (batteryBankSoc) batteryBankSoc.textContent = String(derived.battery_bank_soc ?? 0);
    if (batteryBankState) {
      const state = String(derived.battery_bank_state ?? "idle");
      batteryBankState.textContent = state.charAt(0).toUpperCase() + state.slice(1);
    }

    Object.entries(trends).forEach(([sensorType, points]) => {
      const strip = document.querySelector(`[data-trend-series="${sensorType}"]`);
      if (!strip) {
        return;
      }
      const list = Array.isArray(points) ? points : [];
      const wattsSeries = list.map((point) => Number(point && point.watts) || 0);
      const maxWatts = wattsSeries.reduce((max, value) => Math.max(max, value), 0);
      const minWatts = wattsSeries.reduce((min, value) => Math.min(min, value), Number.POSITIVE_INFINITY);
      const range = Math.max(1, maxWatts - (Number.isFinite(minWatts) ? minWatts : 0));

      if (!wattsSeries.length) {
        strip.innerHTML = "";
        return;
      }

      const width = 100;
      const height = 24;
      const pointsAttr = wattsSeries.map((watts, index) => {
        const x = wattsSeries.length === 1 ? 0 : (index / (wattsSeries.length - 1)) * width;
        const y = height - (((watts - (Number.isFinite(minWatts) ? minWatts : 0)) / range) * height);
        return `${x.toFixed(2)},${Math.max(1, Math.min(height - 1, y)).toFixed(2)}`;
      }).join(" ");

      strip.innerHTML = `
        <svg viewBox="0 0 ${width} ${height}" preserveAspectRatio="none" class="trend-line-chart" role="img" aria-label="${sensorType} trend">
          <polyline points="${pointsAttr}" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"></polyline>
        </svg>
      `;
    });

    document.querySelectorAll("[data-module-status]").forEach((node) => {
      const moduleName = node.getAttribute("data-module-status");
      const moduleData = status.active_modules && status.active_modules[moduleName];
      if (!moduleData) {
        return;
      }
      const snapshot = status.live_data && status.live_data[moduleName];
      if (snapshot && node.querySelector("[data-module-connected-count]")) {
        const connectedDevices = Number(snapshot.connected_device_count ?? 0);
        const connectedSensors = Number(snapshot.connected_sensor_count ?? 0);
        const displayCount = Number(snapshot.device_count || 0) > 0 ? connectedDevices : connectedSensors;
        node.querySelector("[data-module-connected-count]").textContent = String(displayCount);
      }
      const statusLabel = node.querySelector("[data-module-connection]");
      const statusIcon = node.querySelector("[data-module-connection-icon]");
      if (statusLabel && snapshot) {
        const hasDeviceStats = Number(snapshot.device_count || 0) > 0;
        const connectedCount = hasDeviceStats ? Number(snapshot.connected_device_count || 0) : Number(snapshot.connected_sensor_count || 0);
        const totalCount = hasDeviceStats ? Number(snapshot.device_count || 0) : Number(snapshot.sensor_count || 0);
        const pairedCount = Number(snapshot.paired_device_count || 0);
        const moduleState = hasDeviceStats
          ? (connectedCount <= 0 ? (pairedCount > 0 && moduleName === "victron" ? "partial" : "disconnected") : (connectedCount < totalCount ? "partial" : "connected"))
          : (connectedCount <= 0 ? "disconnected" : "connected");
        const statusText = moduleState === "connected" ? "Connected" : (moduleName === "victron" && pairedCount > 0 && connectedCount <= 0 ? "Paired" : (moduleState === "partial" ? "Partial" : "Disconnected"));
        const statusTextNode = statusLabel.querySelector("[data-module-connection-label]");
        if (statusTextNode) {
          statusTextNode.textContent = statusText;
        } else {
          statusLabel.textContent = statusText;
        }
        statusLabel.classList.remove("status-connected", "status-partial", "status-disconnected");
        statusLabel.classList.add(`status-${moduleState}`);
        if (statusIcon) {
          statusIcon.classList.remove("status-connected", "status-partial", "status-disconnected", "fa-circle-check", "fa-circle-exclamation", "fa-circle-xmark");
          statusIcon.classList.add(`status-${moduleState}`);
          statusIcon.classList.add(moduleState === "connected" ? "fa-circle-check" : (moduleState === "partial" ? "fa-circle-exclamation" : "fa-circle-xmark"));
        }
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

  function toLogText(payload) {
    if (!payload || typeof payload !== "object") {
      return "";
    }

    if (typeof payload.lines === "string") {
      return payload.lines;
    }

    if (Array.isArray(payload.lines)) {
      return payload.lines.map((line) => String(line || "")).join("\n");
    }

    const collectRecentEntries = (value, output = []) => {
      if (!value || typeof value !== "object") {
        return output;
      }
      if (Array.isArray(value)) {
        value.forEach((item) => collectRecentEntries(item, output));
        return output;
      }
      if (Array.isArray(value.recent_entries)) {
        value.recent_entries.forEach((entry) => {
          const line = String(entry || "").trim();
          if (line) {
            output.push(line);
          }
        });
      }
      Object.values(value).forEach((child) => collectRecentEntries(child, output));
      return output;
    };

    const entries = collectRecentEntries(payload, []);
    if (entries.length) {
      return entries.join("\n");
    }

    return "";
  }

  async function refreshLogCard(moduleName = "core") {
    const normalizedModule = String(moduleName || "core").trim().toLowerCase();
    const isCore = normalizedModule === "core";
    const endpoint = isCore
      ? "/api/logs/recent"
      : `/api/logs/recent?module=${encodeURIComponent(normalizedModule)}`;
    const output = isCore
      ? document.getElementById("logs-core-output")
      : document.getElementById(`logs-module-output-${normalizedModule}`);
    const message = isCore
      ? document.getElementById("logs-core-message")
      : document.getElementById(`logs-module-message-${normalizedModule}`);

    if (output) {
      output.textContent = "Loading logs...";
    }

    try {
      const data = await request(endpoint, { method: "GET" });
      const rendered = toLogText(data);
      if (output) {
        output.textContent = rendered || "No log entries available.";
      }
      showStatusMessage(message, `Updated ${isCore ? "core" : normalizedModule} logs.`, "success");
    } catch (err) {
      if (output) {
        output.textContent = "Unable to load logs.";
      }
      showStatusMessage(message, err.message || "Failed to refresh logs.", "error");
    }
  }

  async function refreshLogs() {
    const coreOutput = document.getElementById("logs-core-output");
    if (!coreOutput) {
      return;
    }
    const tasks = [refreshLogCard("core")];
    document.querySelectorAll("[data-logs-refresh]").forEach((button) => {
      const target = String(button.getAttribute("data-logs-refresh") || "").trim().toLowerCase();
      if (target && target !== "core") {
        tasks.push(refreshLogCard(target));
      }
    });
    await Promise.allSettled(tasks);
  }

  async function refreshAll() {
    await Promise.allSettled([refreshStatus(), refreshBackups(), refreshLogs()]);
  }

  async function handleLogin(event) {
    event.preventDefault();
    const username = document.getElementById("login-username");
    const password = document.getElementById("login-password");
    const error = document.getElementById("login-error");
    const progress = document.getElementById("login-progress");
    const submit = document.getElementById("login-submit");
    const submitText = document.getElementById("login-submit-text");
    const normalizedUsername = String((username && username.value) || username?.placeholder || "").trim();
    const normalizedPassword = String((password && password.value) || "");

    showMessage(error, "");
    if (progress) {
      progress.textContent = "";
      progress.classList.remove("text-danger", "text-success", "text-warning");
      progress.classList.add("text-secondary");
    }

    if (submit) {
      submit.disabled = true;
    }
    if (submitText) {
      submitText.textContent = "Authenticating...";
    }

    const stagedProgress = createStagedProgress(
      [
        "Step 1/3: Validating credentials...",
        "Step 2/3: Establishing secure session...",
        "Step 3/3: Preparing dashboard...",
      ],
      (message, index) => {
        if (progress) {
          progress.textContent = message;
        }
        setLoadingStepMessage(message);
        if (index === 0) {
          showLoadingScreen("Signing in...");
        }
      },
      500,
    );

    try {
      showLoadingScreen("Signing in...");
      setLoadingStepMessage("Step 1/3: Validating credentials...");
      const payload = await request("/api/auth/login", {
        method: "POST",
        body: JSON.stringify({ username: normalizedUsername, password: normalizedPassword }),
        headers: {},
      });
      void payload;
      stagedProgress.stop();
      if (progress) {
        progress.textContent = "Authentication successful. Redirecting...";
        progress.classList.remove("text-secondary", "text-danger", "text-warning");
        progress.classList.add("text-success");
      }
      showLoadingScreen("Authentication successful.");
      setLoadingStepMessage("Redirecting to dashboard...");
      window.location.href = resolvePath("/");
    } catch (err) {
      stagedProgress.stop();
      hideLoadingScreen();
      showMessage(error, err.payload && err.payload.error ? err.payload.error : err.message, true);
      if (progress) {
        progress.textContent = "Authentication failed.";
        progress.classList.remove("text-secondary", "text-success", "text-warning");
        progress.classList.add("text-danger");
      }
      if (submitText) {
        submitText.textContent = "Authenticate";
      }
      if (submit) {
        submit.disabled = false;
      }
    }
  }

  function parseSensorIndex(value) {
    const numeric = Number(value);
    return Number.isFinite(numeric) ? numeric : Number.POSITIVE_INFINITY;
  }

  async function saveCoreSettings() {
    const form = document.getElementById("core-settings-form");
    const message = document.getElementById("core-settings-message");
    try {
      const payload = form ? formToNestedJson(form, "") : {};
      await request("/api/config", { method: "PUT", body: JSON.stringify(payload) });
      showMessage(message, "Core configuration saved.");
      await refreshAll();
      return true;
    } catch (err) {
      showMessage(message, err.message, true);
      return false;
    }
  }

  async function loadCoreBackups() {
    const select = document.getElementById("core-backup-select");
    if (!select) {
      return;
    }

    try {
      const payload = await request("/api/backups", { method: "GET" });
      const backups = Array.isArray(payload?.core) ? payload.core : [];
      if (!backups.length) {
        select.innerHTML = '<option value="">No core backups found</option>';
        select.disabled = true;
        return;
      }

      select.disabled = false;
      select.innerHTML = backups.map((entry) => {
        const name = String(entry?.name || "").trim();
        const label = String(entry?.created_at || name || "").trim();
        return `<option value="${name}">${label}</option>`;
      }).join("");
    } catch (err) {
      select.innerHTML = '<option value="">Failed to load backups</option>';
      select.disabled = true;
      void err;
    }
  }

  async function restoreSelectedCoreBackup() {
    const select = document.getElementById("core-backup-select");
    const message = document.getElementById("core-settings-message");
    const backupName = String(select?.value || "").trim();
    if (!backupName) {
      showMessage(message, "Select a backup to restore.", true);
      return;
    }

    if (!window.confirm(`Restore core config from ${backupName}?`)) {
      return;
    }

    showLoadingScreen("Restoring core backup...");
    try {
      await request(`/api/backups/core/${encodeURIComponent(backupName)}/restore`, { method: "POST" });
      showMessage(message, `Restored ${backupName}. Reloading...`);
      await loadCoreBackups();
      window.setTimeout(() => window.location.reload(), 500);
    } catch (err) {
      hideLoadingScreen();
      showMessage(message, err.message, true);
    }
  }

  function exportCoreConfig() {
    const form = document.getElementById("core-settings-form");
    const message = document.getElementById("core-settings-message");
    try {
      const payload = form ? formToNestedJson(form, "") : {};
      const content = JSON.stringify(payload, null, 2);
      const blob = new Blob([content], { type: "application/json" });
      const url = URL.createObjectURL(blob);
      const link = document.createElement("a");
      const stamp = new Date().toISOString().replace(/[:.]/g, "-");
      link.href = url;
      link.download = `core-settings-export-${stamp}.json`;
      document.body.appendChild(link);
      link.click();
      link.remove();
      URL.revokeObjectURL(url);
      showMessage(message, "Exported current settings JSON.");
    } catch (err) {
      showMessage(message, err.message || "Unable to export settings.", true);
    }
  }

  async function runSelectedCoreBackup() {
    const target = String(document.getElementById("core-backup-target")?.value || "core").toLowerCase();
    if (target === "all") {
      await backupAll();
      await loadCoreBackups();
      showMessage(document.getElementById("core-settings-message"), "Created backup for core and active modules.");
      return;
    }
    await backupCore();
    showMessage(document.getElementById("core-settings-message"), "Created core configuration backup.");
  }

  async function saveInlineModuleSettings(moduleName, form, messageElement) {
    try {
      let payload = form ? formToNestedJson(form, "module_config") : {};
      const moduleJsonField = form ? form.querySelector("[data-module-json-config]") : null;
      if (moduleJsonField) {
        const raw = String(moduleJsonField.value || "").trim();
        if (!raw) {
          payload = {};
        } else {
          let parsed;
          try {
            parsed = JSON.parse(raw);
          } catch {
            throw new Error("Invalid module_config JSON. Fix JSON syntax before saving.");
          }

          if (!parsed || typeof parsed !== "object" || Array.isArray(parsed)) {
            throw new Error("module_config JSON must be an object.");
          }

          payload = parsed;
        }
      }
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

  function toggleVictronDeviceEditor(index, open) {
    document.querySelectorAll("[data-victron-device-panel]").forEach((panel) => {
      const shouldOpen = String(panel.getAttribute("data-victron-device-panel") || "") === String(index);
      if (open && shouldOpen) {
        panel.classList.remove("hidden");
      } else {
        panel.classList.add("hidden");
      }
    });
  }

  async function reconnectVictronDevice(deviceId, messageElementId, triggerButton = null) {
    const message = messageElementId ? document.getElementById(messageElementId) : null;
    const moduleName = String(window.EM_MODULE_NAME || "").trim() || "victron";
    const moduleMessage = document.getElementById(`module-core-settings-message-${moduleName}`) || document.getElementById("core-settings-message");
    const cardMessage = triggerButton && typeof triggerButton.closest === "function"
      ? triggerButton.closest(".module-settings-card")?.querySelector("[data-victron-devices-message]")
      : null;
    const statusTargets = [message, cardMessage, moduleMessage];

    if (triggerButton) {
      triggerButton.disabled = true;
    }
    showStatusMessages(statusTargets, "Reconnecting device...", "warning");

    try {
      const result = await request(`/api/modules/${encodeURIComponent(moduleName)}/devices/${encodeURIComponent(String(deviceId || ""))}/reconnect`, {
        method: "POST",
      });
      const connected = !!result?.connected;
      const paired = !!result?.paired;
      const success = connected || paired;
      const text = String(result?.message || (connected ? "Device reconnected." : paired ? "Device paired." : "Reconnect failed."));
      showStatusMessages(statusTargets, text, success ? "success" : "warning");
      await refreshStatus();
    } catch (err) {
      showStatusMessages(statusTargets, err.message, "error");
    } finally {
      if (triggerButton) {
        triggerButton.disabled = false;
      }
    }
  }

  function collectSensorConfigFromCards() {
    const forms = Array.from(document.querySelectorAll(".sensor-config-form"));
    forms.sort((a, b) => parseSensorIndex(a.getAttribute("data-sensor-index")) - parseSensorIndex(b.getAttribute("data-sensor-index")));
    return forms.map((form) => {
      const formData = new FormData(form);
      const entry = {};
      for (const [key, value] of formData.entries()) {
        entry[key] = parseScalar(value);
      }
      return entry;
    });
  }

  async function saveSensorConfigCard(sensorIndex) {
    const moduleName = window.EM_MODULE_NAME || "";
    const form = document.querySelector(`.sensor-config-form[data-sensor-index="${sensorIndex}"]`);
    const message = document.getElementById(`sensor-config-message-${sensorIndex}`);
    try {
      const snapshot = getModuleSnapshot();
      const sensorConfig = Array.isArray(snapshot.sensor_config) ? snapshot.sensor_config.map((entry) => Object.assign({}, entry)) : [];
      const formData = form ? new FormData(form) : new FormData();
      const payload = {};
      for (const [key, value] of formData.entries()) {
        payload[key] = key === "address" && moduleName === "ina" ? normalizeInaAddress(value) : parseScalar(value);
      }
      if (sensorIndex === "new") {
        sensorConfig.push(payload);
      } else {
        sensorConfig[Number(sensorIndex)] = Object.assign({}, sensorConfig[Number(sensorIndex)] || {}, payload);
      }
      await request(`/api/modules/${encodeURIComponent(moduleName)}`, {
        method: "PUT",
        body: JSON.stringify({ sensor_config: sensorConfig }),
      });
      showMessage(message, "Sensor configuration saved.");
      await refreshModuleSnapshot();
      showSensorCardTransientFeedback(sensorIndex, "Save successful.", false, "view");
    } catch (err) {
      showMessage(message, err.message, true);
      showSensorCardTransientFeedback(sensorIndex, err.message, true, "config", 1800);
    }
  }

  async function loadSensorHistory(sensorIndex, sensorName) {
    const panel = document.getElementById(`sensor-card-logs-${sensorIndex}`);
    const list = document.getElementById(`sensor-history-list-${sensorIndex}`);
    const moduleName = window.EM_MODULE_NAME || "";
    if (!panel || !list) return;

    panel.hidden = false;
    list.innerHTML = '<div class="text-secondary small">Loading recent readings...</div>';
    try {
      const payload = await request(`/api/modules/${encodeURIComponent(moduleName)}/history`, { method: "GET" });
      const history = Array.isArray(payload.history) ? payload.history : [];
      const entries = history
        .slice(-6)
        .reverse()
        .map((snapshot) => {
          const row = Array.isArray(snapshot.sensor_rows) ? snapshot.sensor_rows.find((item) => String(item.name || "") === String(sensorName || "")) : null;
          if (!row) return null;
          return {
            when: snapshot.updated_at || row.last_seen || "",
            watts: row.watts ?? 0,
            voltage: row.voltage ?? 0,
            current: row.current ?? 0,
            status: row.status || "disconnected",
          };
        })
        .filter(Boolean);

      if (!entries.length) {
        list.innerHTML = '<div class="text-secondary small">No recent readings available.</div>';
        return;
      }

      list.innerHTML = entries.map((entry) => `
        <div class="sensor-history-entry">
          <strong>${entry.watts} W</strong>
          <span>V ${entry.voltage} | A ${entry.current} | ${entry.status}</span>
          <span>${formatTimestampToSecond(entry.when)}</span>
        </div>
      `).join("");
      setSensorCardMode(sensorIndex, "logs");
    } catch (err) {
      list.innerHTML = `<div class="text-danger small">${err.message}</div>`;
    }
  }

  function togglePanelById(panelId, show) {
    const panel = document.getElementById(panelId);
    if (panel) {
      panel.hidden = !show;
    }
  }

  function addSensorCardToGrid() {
    const template = document.getElementById("add-sensor-card-template");
    const grid = document.getElementById("live-sensor-grid");
    if (!template || !grid || document.getElementById("sensor-row-new-sensor")) {
      return;
    }
    const fragment = template.content.cloneNode(true);
    const card = fragment.querySelector("article");
    if (card) {
      card.id = "sensor-row-new-sensor";
      grid.appendChild(fragment);
      const newCard = document.getElementById("sensor-row-new-sensor");
      setSensorCardMode("new", "config");
      const newForm = document.querySelector(".sensor-config-form[data-sensor-index='new']");
      populateInaAddressSelect(newForm, "new");
      syncInaExternalShuntControls(newForm);
      newCard?.scrollIntoView({ behavior: "smooth", block: "center" });
    }
  }

  async function refreshModuleSnapshot() {
    const moduleName = window.EM_MODULE_NAME || "";
    const snapshot = await request(`/api/modules/${encodeURIComponent(moduleName)}/snapshot`, { method: "GET" });
    window.EM_MODULE_SNAPSHOT = snapshot || {};
    const updatedAt = document.getElementById("live-updated-at");
    if (updatedAt && snapshot && snapshot.updated_at) {
      updatedAt.textContent = formatTimestampToSecond(snapshot.updated_at);
    }
    const sensorRows = Array.isArray(snapshot.sensor_rows) ? snapshot.sensor_rows : [];
    const sensorByName = new Map(sensorRows.map((sensor) => [String(sensor.name || ""), sensor]));
    const sensorConfigRows = Array.isArray(snapshot.sensor_config) ? snapshot.sensor_config : [];
    const sensorTypeClassList = ["sensor-type-solar", "sensor-type-wind", "sensor-type-battery", "sensor-type-charger", "sensor-type-system"];
    const sensorTypeIconMap = {
      solar: "fa-solar-panel",
      wind: "fa-wind",
      battery: "fa-car-battery",
      charger: "fa-charging-station",
      system: "fa-right-left",
    };

    document.querySelectorAll("[data-sensor-row]").forEach((row) => {
      const sensorRowId = String(row.getAttribute("data-sensor-row") || "");
      if (sensorRowId === "new") {
        return;
      }

      const sensorName = String(row.getAttribute("data-sensor-name") || "");
      let sensor = null;
      const sensorIndex = Number.parseInt(sensorRowId, 10);
      if (Number.isInteger(sensorIndex) && sensorIndex >= 0 && sensorIndex < sensorRows.length) {
        sensor = sensorRows[sensorIndex];
      }
      if (!sensor) {
        sensor = sensorByName.get(sensorName);
      }
      if (!sensor) return;

      const normalizedType = String(sensor.type || "unknown").trim().toLowerCase();
      row.setAttribute("data-sensor-name", String(sensor.name || sensorName));
      row.setAttribute("data-sensor-type", normalizedType);
      row.dataset.sensorConnected = sensor.connected ? "1" : "0";

      row.classList.remove(...sensorTypeClassList);
      if (sensorTypeClassList.includes(`sensor-type-${normalizedType}`)) {
        row.classList.add(`sensor-type-${normalizedType}`);
      }

      const titleNode = row.querySelector(".app-card-title");
      if (titleNode) {
        titleNode.textContent = String(sensor.name || sensorName);
      }

      const iconNode = row.querySelector(".app-card-header-icon i");
      if (iconNode) {
        iconNode.classList.remove("fa-microchip", "fa-solar-panel", "fa-wind", "fa-car-battery", "fa-charging-station", "fa-right-left");
        iconNode.classList.add(sensorTypeIconMap[normalizedType] || "fa-microchip");
      }

      const form = row.querySelector(".sensor-config-form");
      if (form) {
        form.setAttribute("data-sensor-name", String(sensor.name || sensorName));
        form.setAttribute("data-sensor-type", normalizedType);
        const cardMode = String(row.getAttribute("data-card-mode") || "view").trim().toLowerCase();
        if (cardMode !== "config") {
          const indexConfig = Number.isInteger(sensorIndex) && sensorIndex >= 0 ? sensorConfigRows[sensorIndex] : null;
          const variantValue = String((indexConfig && indexConfig.variant) || sensor.variant || "").trim();
          const variantField = form.querySelector("[name='variant']");
          if (variantField) {
            variantField.value = variantValue;
          }
          const externalShuntToggle = form.querySelector("[data-ina-external-shunt-toggle]");
          if (externalShuntToggle) {
            const enabledValue = (indexConfig && indexConfig.external_shunt_enabled) ?? sensor.external_shunt_enabled ?? false;
            externalShuntToggle.checked = enabledValue === true || String(enabledValue).toLowerCase() === "true";
          }
          const externalShuntSelect = form.querySelector("[data-ina-external-shunt-select]");
          if (externalShuntSelect) {
            externalShuntSelect.value = String((indexConfig && indexConfig.external_shunt_variant) || sensor.external_shunt_variant || "");
          }
          const typeField = form.querySelector("[name='type']");
          if (typeField && normalizedType) {
            typeField.value = normalizedType;
          }
          const systemField = form.querySelector("[name='system_id']");
          if (systemField) {
            const assignedSystem = String((indexConfig && indexConfig.system_id) || sensor.system_id || "");
            if (assignedSystem) {
              systemField.value = assignedSystem;
            }
          }
          const nameField = form.querySelector("[name='name']");
          if (nameField) {
            nameField.value = String((indexConfig && indexConfig.name) || sensor.name || "");
          }
        }
      }

      setConnectionIndicator(row, sensor, snapshot);
      row.querySelector("[data-field='watts']")?.replaceChildren(document.createTextNode(String(sensor.watts ?? 0)));
      row.querySelector("[data-field='voltage']")?.replaceChildren(document.createTextNode(String(sensor.voltage ?? 0)));
      row.querySelector("[data-field='current']")?.replaceChildren(document.createTextNode(String(sensor.current ?? 0)));
      const powerTrendNode = row.querySelector("[data-field='power-trend']");
      if (powerTrendNode) {
        const trend = Number(sensor.power_trend ?? 0);
        const trendText = `${trend > 0 ? "+" : ""}${Number.isFinite(trend) ? trend.toFixed(2) : "0.00"} W`;
        powerTrendNode.replaceChildren(document.createTextNode(trendText));
      }
      const socNode = row.querySelector("[data-field='soc']");
      if (socNode) {
        const socValue = Number(sensor.soc ?? 0);
        socNode.replaceChildren(document.createTextNode(`${Number.isFinite(socValue) ? socValue : 0}%`));
      }
      const flowStateNode = row.querySelector("[data-field='flow-state']");
      if (flowStateNode) {
        const flowState = String(sensor.charging_state || "idle").replace(/_/g, " ");
        flowStateNode.replaceChildren(document.createTextNode(flowState.charAt(0).toUpperCase() + flowState.slice(1)));
      }
      const lastUpdated = row.querySelector("[data-field='last-updated']");
      if (lastUpdated) {
        lastUpdated.textContent = formatTimestampToSecond(sensor.last_seen || snapshot.updated_at || "");
      }

      if (String(sensor.type || "").trim().toLowerCase() === "charger") {
        updateChargeCycleIndicator(row, sensor);
      }
      syncInaExternalShuntControls(form);
    });

    refreshInaAddressSelectors();
    refreshInaExternalShuntControls();
    enforceSensorCardModeState();
    applyModuleFilter(window.EM_MODULE_FILTER || null);
  }

  function updateVisibleSensorCount() {
    const sensorCount = document.getElementById("sensor-count");
    if (!sensorCount) return;
    const visibleCards = Array.from(document.querySelectorAll("[data-sensor-row]"))
      .filter((row) => row.getAttribute("data-sensor-row") !== "new")
      .filter((row) => !row.classList.contains("hidden"));
    sensorCount.textContent = String(visibleCards.length);
  }

  function applyModuleFilter(filterType = null) {
    const normalized = filterType ? String(filterType).toLowerCase() : null;
    const clearButton = document.getElementById("clear-module-filter-btn");
    const showUnconnected = !!window.EM_SHOW_UNCONNECTED;
    document.querySelectorAll("[data-sensor-row]").forEach((row) => {
      if (row.getAttribute("data-sensor-row") === "new") {
        return;
      }
      const rowType = String(row.getAttribute("data-sensor-type") || "").toLowerCase();
      const isConnected = String(row.dataset.sensorConnected || "0") === "1";
      const hideByType = !!(normalized && rowType !== normalized);
      const hideByConnection = !showUnconnected && !isConnected;
      const shouldHide = hideByType || hideByConnection;
      row.classList.toggle("hidden", shouldHide);
    });
    clearButton?.classList.toggle("hidden", !normalized);
    window.EM_MODULE_FILTER = normalized;
    updateVisibleSensorCount();
  }

  function bindCredentialsModal() {
    const overlay = document.getElementById("credentials-modal-overlay");
    const form = document.getElementById("credentials-modal-form");
    const saveButton = document.getElementById("credentials-modal-save-btn");
    const closeButton = document.getElementById("credentials-modal-close-btn");
    const message = document.getElementById("credentials-modal-message");
    const currentPasswordInput = document.getElementById("credentials-current-password");
    const usernameInput = document.getElementById("credentials-new-username");
    const newPasswordInput = document.getElementById("credentials-new-password");
    const confirmPasswordInput = document.getElementById("credentials-confirm-password");

    if (!overlay || !form || !saveButton || !closeButton || !message || !currentPasswordInput || !usernameInput || !newPasswordInput || !confirmPasswordInput) {
      return;
    }

    if (overlay.dataset.bound === "true") {
      return;
    }

    const closeModal = () => {
      overlay.classList.add("hidden");
      overlay.setAttribute("aria-hidden", "true");
      form.reset();
      showMessage(message, "");
    };

    const openModal = () => {
      const defaultUsername = String(document.body.dataset.username || "admin").trim() || "admin";
      usernameInput.value = defaultUsername;
      currentPasswordInput.value = "";
      newPasswordInput.value = "";
      confirmPasswordInput.value = "";
      showMessage(message, "");
      overlay.classList.remove("hidden");
      overlay.setAttribute("aria-hidden", "false");
      currentPasswordInput.focus();
    };

    const submitModal = async () => {
      const currentPassword = String(currentPasswordInput.value || "");
      const newUsername = String(usernameInput.value || "").trim();
      const newPassword = String(newPasswordInput.value || "");
      const confirmPassword = String(confirmPasswordInput.value || "");

      if (!currentPassword || !newUsername || !newPassword || !confirmPassword) {
        showMessage(message, "All fields are required.", true);
        return;
      }
      if (newPassword !== confirmPassword) {
        showMessage(message, "New password and confirm password must match.", true);
        return;
      }

      showLoadingScreen("Updating credentials...");
      try {
        await request("/api/auth/credentials", {
          method: "PUT",
          body: JSON.stringify({
            current_password: currentPassword,
            new_username: newUsername,
            new_password: newPassword,
          }),
        });
        closeModal();
        window.location.reload();
      } catch (err) {
        hideLoadingScreen();
        showMessage(message, err.message, true);
      }
    };

    saveButton.addEventListener("click", submitModal);
    closeButton.addEventListener("click", closeModal);
    form.addEventListener("submit", (event) => {
      event.preventDefault();
      submitModal();
    });
    overlay.addEventListener("click", (event) => {
      if (event.target === overlay) {
        closeModal();
      }
    });
    document.addEventListener("keydown", (event) => {
      if (event.key === "Escape" && !overlay.classList.contains("hidden")) {
        closeModal();
      }
    });

    overlay.dataset.bound = "true";
    window.EM_OPEN_CREDENTIALS_MODAL = openModal;
  }

  async function updateCredentials() {
    const openModal = window.EM_OPEN_CREDENTIALS_MODAL;
    if (typeof openModal === "function") {
      openModal();
      return;
    }
  }

  async function restartWebserver() {
    if (!window.confirm("Restart the webserver now?")) return;
    showLoadingScreen("Restarting webserver...");
    try {
      await request("/api/webserver/restart", { method: "POST" });
      setTimeout(() => window.location.reload(), 2500);
    } catch (err) {
      hideLoadingScreen();
      alert(err.message);
    }
  }

  async function backupCore() {
    await request("/api/backups/core", { method: "POST" });
    await loadCoreBackups();
    await refreshAll();
  }

  async function backupAll() {
    await request("/api/backups/all", { method: "POST" });
    await refreshAll();
  }

  function setMqttPanelMode(mode) {
    const mainPanel = document.getElementById("mqtt-main-panel");
    const credentialsPanel = document.getElementById("mqtt-credentials-panel");
    const advancedPanel = document.getElementById("mqtt-advanced-panel");
    const saveMainButton = document.getElementById("mqtt-save-btn");
    const editCredentialsButton = document.getElementById("mqtt-edit-credentials-btn");
    const editAdvancedButton = document.getElementById("mqtt-edit-advanced-btn");
    const cancelCredentialsButton = document.getElementById("mqtt-cancel-credentials-btn");
    const saveCredentialsButton = document.getElementById("mqtt-save-credentials-btn");
    const cancelAdvancedButton = document.getElementById("mqtt-cancel-advanced-btn");
    const saveAdvancedButton = document.getElementById("mqtt-save-advanced-btn");
    const mqttPassword = document.querySelector('input[name="mqtt[password]"]');
    const mqttPasswordConfirm = document.getElementById("mqtt-password-confirm");

    if (!mainPanel || !credentialsPanel || !advancedPanel) {
      return;
    }

    const normalized = String(mode || "main").toLowerCase();
    mainPanel.classList.toggle("hidden", normalized !== "main");
    credentialsPanel.classList.toggle("hidden", normalized !== "credentials");
    advancedPanel.classList.toggle("hidden", normalized !== "advanced");

    if (saveMainButton) saveMainButton.classList.toggle("hidden", normalized !== "main");
    if (editCredentialsButton) editCredentialsButton.classList.toggle("hidden", normalized !== "main");
    if (editAdvancedButton) editAdvancedButton.classList.toggle("hidden", normalized !== "main");
    if (cancelCredentialsButton) cancelCredentialsButton.classList.toggle("hidden", normalized !== "credentials");
    if (saveCredentialsButton) saveCredentialsButton.classList.toggle("hidden", normalized !== "credentials");
    if (cancelAdvancedButton) cancelAdvancedButton.classList.toggle("hidden", normalized !== "advanced");
    if (saveAdvancedButton) saveAdvancedButton.classList.toggle("hidden", normalized !== "advanced");

    if (normalized === "main" && mqttPassword instanceof HTMLInputElement && mqttPasswordConfirm instanceof HTMLInputElement) {
      mqttPasswordConfirm.value = mqttPassword.value;
    }
  }

  function mqttCredentialsMatch() {
    const mqttPassword = document.querySelector('input[name="mqtt[password]"]');
    const mqttPasswordConfirm = document.getElementById("mqtt-password-confirm");
    if (!(mqttPassword instanceof HTMLInputElement) || !(mqttPasswordConfirm instanceof HTMLInputElement)) {
      return true;
    }
    return String(mqttPassword.value || "") === String(mqttPasswordConfirm.value || "");
  }

  function toggleSystemOverviewModuleEdit() {
    const panel = document.getElementById("system-overview-module-edit");
    const button = document.getElementById("system-overview-toggle-btn");
    if (!panel || !button) {
      return;
    }

    const willShow = panel.classList.contains("hidden");
    panel.classList.toggle("hidden", !willShow);

    const icon = button.querySelector("i");
    if (icon) {
      icon.classList.toggle("fa-gear", !willShow);
      icon.classList.toggle("fa-times", willShow);
    }
    button.title = willShow ? "Close module edit" : "Edit modules";
  }

  function nextIndexedRow(container, selector) {
    const rows = Array.from(container.querySelectorAll(selector));
    if (!rows.length) {
      return 0;
    }
    const indexes = rows
      .map((row) => Number(row.getAttribute("data-row-index")))
      .filter((value) => Number.isFinite(value));
    if (!indexes.length) {
      return rows.length;
    }
    return Math.max(...indexes) + 1;
  }

  function toSystemSlug(value, fallback) {
    const normalized = String(value || "")
      .trim()
      .toLowerCase()
      .replace(/[^a-z0-9]+/g, "-")
      .replace(/^-+|-+$/g, "");
    return normalized || fallback;
  }

  function getSystemsCardElements() {
    return {
      card: document.getElementById("system-group-settings-card"),
      inputs: document.getElementById("systems-inputs"),
      listPanel: document.getElementById("systems-list-panel"),
      addPanel: document.getElementById("systems-add-panel"),
      editPanel: document.getElementById("systems-edit-panel"),
      feedbackPanel: document.getElementById("systems-feedback-panel"),
      list: document.getElementById("systems-list"),
      addName: document.getElementById("systems-add-name"),
      editName: document.getElementById("systems-edit-name"),
      editIndex: document.getElementById("systems-edit-index"),
      feedbackMessage: document.getElementById("systems-feedback-message"),
      addBtn: document.getElementById("systems-add-btn"),
      saveAddBtn: document.getElementById("systems-save-add-btn"),
      cancelAddBtn: document.getElementById("systems-cancel-add-btn"),
      saveEditBtn: document.getElementById("systems-save-edit-btn"),
      backEditBtn: document.getElementById("systems-back-edit-btn"),
    };
  }

  function collectSystemsFromInputs(container) {
    if (!container) {
      return [];
    }
    const map = new Map();
    container.querySelectorAll('input[name^="systems["]').forEach((input) => {
      const match = String(input.name || "").match(/^systems\[(\d+)\]\[(id|location_id|name)\]$/);
      if (!match) {
        return;
      }
      const index = Number.parseInt(match[1], 10);
      const key = match[2];
      const entry = map.get(index) || { id: "", location_id: "", name: "" };
      entry[key] = String(input.value || "");
      map.set(index, entry);
    });
    return Array.from(map.entries())
      .sort((a, b) => a[0] - b[0])
      .map(([, value]) => ({
        id: String(value.id || "").trim(),
        location_id: String(value.location_id || "").trim(),
        name: String(value.name || "").trim(),
      }))
      .filter((entry) => entry.id || entry.name);
  }

  function renderSystemsInputs(container, systems) {
    if (!container) {
      return;
    }
    container.innerHTML = "";
    systems.forEach((system, index) => {
      ["id", "location_id", "name"].forEach((key) => {
        const input = document.createElement("input");
        input.type = "hidden";
        input.name = `systems[${index}][${key}]`;
        input.value = String(system[key] || "");
        container.appendChild(input);
      });
    });
  }

  function renderSystemsList(listElement, systems) {
    if (!listElement) {
      return;
    }
    if (!systems.length) {
      listElement.innerHTML = "<li>No systems configured.</li>";
      return;
    }
    listElement.innerHTML = systems
      .map((system, index) => `
        <li class="settings-entry">
          <span>${escapeHtml(String(system.name || `System ${index + 1}`))}</span>
          <button type="button" class="btn btn-outline-secondary btn-sm action-icon-btn" data-systems-edit-index="${index}" title="Edit system">
            <i class="fa-solid fa-gear"></i>
          </button>
        </li>
      `)
      .join("");
  }

  function setSystemsCardMode(elements, mode) {
    if (!elements.card) {
      return;
    }
    const normalized = ["list", "add", "edit", "feedback"].includes(mode) ? mode : "list";
    elements.card.setAttribute("data-systems-mode", normalized);

    elements.listPanel?.classList.toggle("hidden", normalized !== "list");
    elements.addPanel?.classList.toggle("hidden", normalized !== "add");
    elements.editPanel?.classList.toggle("hidden", normalized !== "edit");
    elements.feedbackPanel?.classList.toggle("hidden", normalized !== "feedback");

    elements.addBtn?.classList.toggle("hidden", normalized !== "list");
    elements.saveAddBtn?.classList.toggle("hidden", normalized !== "add");
    elements.cancelAddBtn?.classList.toggle("hidden", normalized !== "add");
    elements.saveEditBtn?.classList.toggle("hidden", normalized !== "edit");
    elements.backEditBtn?.classList.toggle("hidden", normalized !== "edit");
  }

  function showSystemsFeedback(elements, message, isError = false, returnMode = "list", timeoutMs = 1400) {
    if (elements.feedbackMessage) {
      elements.feedbackMessage.textContent = String(message || "");
      elements.feedbackMessage.classList.toggle("status-disconnected", !!isError);
      elements.feedbackMessage.classList.toggle("status-connected", !isError);
    }
    setSystemsCardMode(elements, "feedback");
    window.setTimeout(() => {
      setSystemsCardMode(elements, returnMode);
    }, timeoutMs);
  }

  function bindSystemsCard() {
    const elements = getSystemsCardElements();
    if (!elements.card || !elements.inputs || !elements.list) {
      return;
    }
    if (elements.card.dataset.bound === "true") {
      return;
    }

    const getSystems = () => collectSystemsFromInputs(elements.inputs);
    const saveSystems = async () => saveCoreSettings();

    renderSystemsList(elements.list, getSystems());
    setSystemsCardMode(elements, "list");

    elements.addBtn?.addEventListener("click", () => {
      if (elements.addName) {
        elements.addName.value = "";
        elements.addName.focus();
      }
      setSystemsCardMode(elements, "add");
    });

    elements.cancelAddBtn?.addEventListener("click", () => {
      setSystemsCardMode(elements, "list");
    });

    elements.saveAddBtn?.addEventListener("click", async () => {
      const name = String(elements.addName?.value || "").trim();
      if (!name) {
        showSystemsFeedback(elements, "System name is required.", true, "add");
        return;
      }
      const systems = getSystems();
      const baseSlug = toSystemSlug(name, `system-${systems.length + 1}`);
      let slug = baseSlug;
      let suffix = 2;
      const existing = new Set(systems.map((item) => String(item.id || "").trim().toLowerCase()));
      while (existing.has(slug.toLowerCase())) {
        slug = `${baseSlug}-${suffix}`;
        suffix += 1;
      }

      const locationIdField = document.querySelector('input[name="locations[0][id]"]');
      const locationId = String(locationIdField && "value" in locationIdField ? locationIdField.value : "home").trim() || "home";
      systems.push({ id: slug, location_id: locationId, name });
      renderSystemsInputs(elements.inputs, systems);
      renderSystemsList(elements.list, systems);

      const ok = await saveSystems();
      showSystemsFeedback(elements, ok ? "System added." : "Unable to save systems.", !ok, ok ? "list" : "add");
    });

    elements.list.addEventListener("click", (event) => {
      const button = event.target instanceof Element ? event.target.closest("button[data-systems-edit-index]") : null;
      if (!button) {
        return;
      }
      const index = Number.parseInt(String(button.getAttribute("data-systems-edit-index") || ""), 10);
      const systems = getSystems();
      const system = Number.isInteger(index) ? systems[index] : null;
      if (!system) {
        return;
      }
      if (elements.editIndex) {
        elements.editIndex.value = String(index);
      }
      if (elements.editName) {
        elements.editName.value = String(system.name || "");
        elements.editName.focus();
      }
      setSystemsCardMode(elements, "edit");
    });

    elements.backEditBtn?.addEventListener("click", () => {
      setSystemsCardMode(elements, "list");
    });

    elements.saveEditBtn?.addEventListener("click", async () => {
      const index = Number.parseInt(String(elements.editIndex?.value || ""), 10);
      const name = String(elements.editName?.value || "").trim();
      const systems = getSystems();
      if (!Number.isInteger(index) || index < 0 || index >= systems.length) {
        showSystemsFeedback(elements, "Invalid system selection.", true, "list");
        return;
      }
      if (!name) {
        showSystemsFeedback(elements, "System name is required.", true, "edit");
        return;
      }
      systems[index].name = name;
      renderSystemsInputs(elements.inputs, systems);
      renderSystemsList(elements.list, systems);

      const ok = await saveSystems();
      showSystemsFeedback(elements, ok ? "System updated." : "Unable to save systems.", !ok, ok ? "list" : "edit");
    });

    elements.card.dataset.bound = "true";
  }

  function bindDashboard() {
    showLoadingScreen("Loading dashboard...");
    setLoadingStepMessage("Step 1/2: Loading live status...");
    startServerHeartbeat();
    bindCredentialsModal();
    document.getElementById("refresh-btn")?.addEventListener("click", refreshAll);
    document.getElementById("backup-all-btn")?.addEventListener("click", backupAll);
    document.getElementById("update-credentials-btn")?.addEventListener("click", updateCredentials);
    document.getElementById("restart-webserver-btn")?.addEventListener("click", restartWebserver);
    document.getElementById("logout-btn")?.addEventListener("click", async function () {
      await request("/api/auth/logout", { method: "POST" });
      window.location.reload();
    });
    refreshStatus()
      .catch(() => void 0)
      .finally(() => {
        setLoadingStepMessage("Step 2/2: Rendering dashboard...");
        hideLoadingScreen();
      });

    refreshBackups().catch(() => void 0);
    refreshLogs().catch(() => void 0);
    setInterval(refreshStatus, 3000);
  }

  function bindCoreSettings() {
    showLoadingScreen("Loading settings...");
    startServerHeartbeat();
    bindCredentialsModal();
    document.querySelectorAll('[data-core-action="save"]').forEach((button) => {
      button.addEventListener("click", saveCoreSettings);
    });
    document.querySelectorAll('[data-core-action="backup"]').forEach((button) => {
      button.addEventListener("click", runSelectedCoreBackup);
    });
    document.querySelectorAll('[data-core-action="restart"]').forEach((button) => {
      button.addEventListener("click", restartWebserver);
    });
    document.querySelectorAll('[data-core-action="overview-toggle"]').forEach((button) => {
      button.addEventListener("click", toggleSystemOverviewModuleEdit);
    });
    document.querySelectorAll('[data-core-action="credentials"]').forEach((button) => {
      button.addEventListener("click", updateCredentials);
    });
    document.querySelectorAll('[data-core-action="export"]').forEach((button) => {
      button.addEventListener("click", exportCoreConfig);
    });
    bindSystemsCard();
    document.querySelectorAll('[data-core-action="mqtt-credentials"]').forEach((button) => {
      button.addEventListener("click", () => setMqttPanelMode("credentials"));
    });
    document.querySelectorAll('[data-core-action="mqtt-advanced"]').forEach((button) => {
      button.addEventListener("click", () => setMqttPanelMode("advanced"));
    });
    document.querySelectorAll('[data-core-action="mqtt-main"]').forEach((button) => {
      button.addEventListener("click", () => setMqttPanelMode("main"));
    });
    document.querySelectorAll('[data-core-action="mqtt-save-main"]').forEach((button) => {
      button.addEventListener("click", saveCoreSettings);
    });
    document.querySelectorAll('[data-core-action="mqtt-save-credentials"]').forEach((button) => {
      button.addEventListener("click", async () => {
        const message = document.getElementById("core-settings-message");
        if (!mqttCredentialsMatch()) {
          showMessage(message, "MQTT password confirmation does not match.", true);
          return;
        }
        await saveCoreSettings();
        setMqttPanelMode("main");
      });
    });
    document.querySelectorAll('[data-core-action="mqtt-save-advanced"]').forEach((button) => {
      button.addEventListener("click", async () => {
        await saveCoreSettings();
        setMqttPanelMode("main");
      });
    });
    document.getElementById("restore-core-btn")?.addEventListener("click", restoreSelectedCoreBackup);
    document.getElementById("refresh-core-backups-btn")?.addEventListener("click", loadCoreBackups);
    document.querySelectorAll("[data-module-save]").forEach((button) => {
      button.addEventListener("click", async () => {
        const moduleName = button.getAttribute("data-module-save") || "";
        const form = document.querySelector(`form[data-module-name="${moduleName}"]`);
        const message = document.getElementById(`module-core-settings-message-${moduleName}`);
        await saveInlineModuleSettings(moduleName, form, message);
      });
    });
    document.querySelectorAll("[data-victron-device-edit]").forEach((button) => {
      button.addEventListener("click", () => {
        const index = button.getAttribute("data-victron-device-edit");
        toggleVictronDeviceEditor(index, true);
      });
    });
    document.querySelectorAll("[data-victron-device-close]").forEach((button) => {
      button.addEventListener("click", () => {
        const index = button.getAttribute("data-victron-device-close");
        toggleVictronDeviceEditor(index, false);
      });
    });
    document.querySelectorAll("[data-victron-device-reconnect]").forEach((button) => {
      button.addEventListener("click", async () => {
        const deviceId = button.getAttribute("data-victron-device-reconnect");
        const messageElementId = button.getAttribute("data-victron-device-message");
        await reconnectVictronDevice(deviceId, messageElementId, button);
      });
    });
    document.getElementById("logout-btn")?.addEventListener("click", async function () {
      await request("/api/auth/logout", { method: "POST" });
      window.location.reload();
    });
    setMqttPanelMode("main");
    loadCoreBackups();
    refreshStatus();
    setInterval(refreshStatus, 3000);
    hideLoadingScreen();
  }

  function bindModulePage() {
    showLoadingScreen("Loading module data...");
    startServerHeartbeat();
    document.getElementById("clear-module-filter-btn")?.addEventListener("click", () => applyModuleFilter(null));
    document.getElementById("add-sensor-btn")?.addEventListener("click", addSensorCardToGrid);
    document.querySelectorAll("[data-sensor-filter]").forEach((button) => {
      button.addEventListener("click", () => applyModuleFilter(button.getAttribute("data-sensor-filter")));
    });
    window.EM_SHOW_UNCONNECTED = false;
    const showUnconnectedToggle = document.getElementById("show-unconnected-sensors");
    if (showUnconnectedToggle) {
      showUnconnectedToggle.checked = false;
      showUnconnectedToggle.addEventListener("change", () => {
        window.EM_SHOW_UNCONNECTED = !!showUnconnectedToggle.checked;
        applyModuleFilter(window.EM_MODULE_FILTER || null);
      });
    }
    const grid = document.getElementById("live-sensor-grid");
    grid?.addEventListener("click", async (event) => {
      const target = event.target instanceof Element ? event.target.closest("button, i") : null;
      const button = target instanceof HTMLElement ? target.closest("button") : null;
      if (!button) return;

      const action = button.getAttribute("data-sensor-card-action");
      const sensorIndex = button.getAttribute("data-sensor-index");
      if (action && sensorIndex !== null) {
        if (action === "edit") {
          setSensorCardMode(sensorIndex, "config");
          populateInaAddressSelect(document.querySelector(`.sensor-config-form[data-sensor-index="${sensorIndex}"]`), sensorIndex);
          return;
        }
        if (action === "logs") {
          const sensorRow = button.closest("[data-sensor-row]");
          const sensorName = sensorRow?.getAttribute("data-sensor-name") || "";
          await loadSensorHistory(sensorIndex, sensorName);
          return;
        }
        if (action === "back" || action === "cancel") {
          setSensorCardMode(sensorIndex, "view");
          return;
        }
        if (action === "save") {
          await saveSensorConfigCard(sensorIndex);
          return;
        }
      }

      const configToggle = button.getAttribute("data-sensor-config-toggle");
      if (configToggle !== null) {
        const panelId = configToggle === "new" ? "sensor-card-config-new" : `sensor-card-config-${configToggle}`;
        const panel = document.getElementById(panelId);
        if (panel) panel.hidden = !panel.hidden;
        return;
      }

      const historyToggle = button.getAttribute("data-sensor-history-toggle");
      if (historyToggle !== null) {
        const sensorRow = button.closest("[data-sensor-row]");
        const sensorName = sensorRow?.getAttribute("data-sensor-name") || "";
        await loadSensorHistory(historyToggle, sensorName);
        return;
      }

      const saveIndex = button.getAttribute("data-save-sensor-index");
      if (saveIndex !== null) {
        await saveSensorConfigCard(Number(saveIndex));
      }
    });
    grid?.addEventListener("change", (event) => {
      const target = event.target instanceof HTMLElement ? event.target : null;
      const form = target?.closest(".sensor-config-form");
      if (!form) {
        return;
      }
      if (target.matches("[data-ina-device-select]")) {
        populateInaAddressSelect(form, form.getAttribute("data-sensor-index") || "");
      }
      if (target.matches("[data-ina-external-shunt-toggle], [name='variant']")) {
        syncInaExternalShuntControls(form);
      }
    });
    document.getElementById("logout-btn")?.addEventListener("click", async function () {
      await request("/api/auth/logout", { method: "POST" });
      window.location.reload();
    });
    refreshModuleSnapshot().finally(() => {
      enforceSensorCardModeState();
      applyModuleFilter(window.EM_MODULE_FILTER || null);
      if ((window.EM_MODULE_NAME || "") === "ina") {
        refreshInaAddressSelectors();
      }
      refreshInaExternalShuntControls();
      hideLoadingScreen();
    });
    setInterval(refreshModuleSnapshot, 2000);
  }

  function bindLogsPage() {
    showLoadingScreen("Loading logs...");
    startServerHeartbeat();
    document.querySelectorAll("[data-logs-refresh]").forEach((button) => {
      button.addEventListener("click", async () => {
        const target = button.getAttribute("data-logs-refresh") || "core";
        await refreshLogCard(target);
      });
    });
    document.getElementById("refresh-logs-btn")?.addEventListener("click", refreshLogs);
    document.getElementById("logout-btn")?.addEventListener("click", async function () {
      await request("/api/auth/logout", { method: "POST" });
      window.location.reload();
    });
    refreshLogs().finally(hideLoadingScreen);
  }

  document.addEventListener("DOMContentLoaded", () => {
    syncServerInstanceId();
    bindCredentialsModal();

    const loginForm = document.getElementById("login-form");
    if (loginForm) {
      loginForm.addEventListener("submit", handleLogin);
      return;
    }

    const shell = document.querySelector(".app-shell");
    document.getElementById("burger-menu")?.addEventListener("click", () => {
      shell?.classList.toggle("sidebar-collapsed");
    });
    shell?.classList.add("sidebar-collapsed");

    const page = document.body.dataset.page || "dashboard";
    if (page === "dashboard") {
      bindDashboard();
      return;
    }
    if (page === "core-settings") {
      bindCoreSettings();
      return;
    }
    if (page === "module-settings") {
      bindCoreSettings();
      return;
    }
    if (page === "module") {
      bindModulePage();
      return;
    }
    if (page === "logs") {
      bindLogsPage();
      return;
    }
  });
})();
