/* TransitRescue Phase 2 dashboard interactions
   All dispatch distances, ETAs and RFID scans are DEMONSTRATION SIMULATIONS.
*/
const $ = (id) => document.getElementById(id);
const esc = (s) => String(s ?? "").replace(/[&<>"']/g, c => ({
  "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;"
}[c]));

let toastTimer;
let refreshInProgress = false;

function toast(msg) {
  const el = $("toast");
  if (!el) { console.info(msg); return; }
  el.textContent = msg;
  el.classList.add("show");
  clearTimeout(toastTimer);
  toastTimer = setTimeout(() => el.classList.remove("show"), 3200);
}

async function api(url, method = "GET", data = null) {
  const opts = { method, headers: { "Content-Type": "application/json" } };
  if (data !== null) opts.body = JSON.stringify(data);

  const res = await fetch(url, opts);
  let out;
  try {
    out = await res.json();
  } catch {
    throw new Error(`Server returned an unexpected response (${res.status}).`);
  }
  if (!res.ok) throw new Error(out.error || `Request failed (${res.status}).`);
  return out;
}

function statusClass(status) {
  if (["RESOLVED", "AVAILABLE"].includes(status)) return "status status-green";
  if (["EN_ROUTE", "SHUTTLE_DISPATCHED", "ASSIGNED"].includes(status)) return "status status-blue";
  if (["ARRIVED", "ON_SCENE"].includes(status)) return "status status-purple";
  if (["MAINTENANCE", "CANCELLED"].includes(status)) return "status status-muted";
  return "status";
}

function severityClass(severity) {
  return `severity severity-${String(severity || "High").toLowerCase()}`;
}

function statusLabel(status) {
  const labels = {
    REPORTED: "Reported",
    VERIFYING: "Verifying",
    ASSIGNED: "Assigned",
    EN_ROUTE: "En Route",
    SHUTTLE_DISPATCHED: "En Route",
    ARRIVED: "Arrived",
    RESOLVED: "Resolved",
    CANCELLED: "Cancelled",
    AVAILABLE: "Available",
    MAINTENANCE: "Maintenance",
    ON_SCENE: "On Scene"
  };
  return labels[status] || String(status || "Unknown").replaceAll("_", " ");
}

function isOpen(incident) {
  return !["RESOLVED", "CANCELLED"].includes(incident.status);
}

function renderIncidents(items) {
  const openCount = items.filter(isOpen).length;
  if ($("navIncidentCount")) $("navIncidentCount").textContent = openCount;
  if ($("incidentQueueTag")) $("incidentQueueTag").textContent = `${openCount} OPEN`;

  if (!$("incidentList")) return;
  if (!items.length) {
    $("incidentList").innerHTML = '<div class="empty">No incidents. Report a breakdown to start a demo.</div>';
    return;
  }

  $("incidentList").innerHTML = items.map(i => {
    const closed = !isOpen(i);
    const assigned = Boolean(i.shuttle_id);
    const actions = [];

    if (!closed && i.status === "REPORTED") {
      actions.push(`<button class="mini-btn" data-action="verify" data-id="${i.id}">Verify</button>`);
    }
    if (!closed && !assigned) {
      actions.push(`<button class="mini-btn" data-action="auto-dispatch" data-id="${i.id}">Find best shuttle</button>`);
    }
    if (!closed && assigned && i.status !== "ARRIVED") {
      actions.push(`<button class="mini-btn" data-action="arrived" data-id="${i.id}">Mark arrived</button>`);
    }
    if (!closed) {
      actions.push(`<button class="mini-btn" data-action="resolve" data-id="${i.id}">Resolve</button>`);
    }

    return `
      <article class="incident-card">
        <div class="incident-card-top">
          <div>
            <h4>Route ${esc(i.route)} Â· ${esc(i.bus_id)}</h4>
            <div class="incident-meta">âŒ– ${esc(i.stop)}<br>
              Estimated delay: ${esc(i.delay_min)} min
            </div>
            <div class="incident-badges">
              <span class="${severityClass(i.severity)}">${esc(i.severity || "High")} priority</span>
              ${assigned ? `<span class="assignment-chip">Shuttle ${esc(i.shuttle_id)}</span>` : ""}
              ${i.eta_min != null ? `<span class="assignment-chip">ETA ${esc(i.eta_min)} min</span>` : ""}
            </div>
          </div>
          <span class="${statusClass(i.status)}">${esc(statusLabel(i.status))}</span>
        </div>
        ${actions.length ? `<div class="incident-actions">${actions.join("")}</div>` : ""}
      </article>`;
  }).join("");
}

function renderShuttles(items, incidents) {
  if ($("metricShuttles")) {
    $("metricShuttles").textContent = items.filter(s => s.status === "AVAILABLE").length;
  }
  if (!$("shuttleList")) return;

  const active = incidents.find(i => isOpen(i) && !i.shuttle_id) ||
                 incidents.find(isOpen) || null;

  $("shuttleList").innerHTML = items.map(s => {
    const linked = incidents.find(i => i.shuttle_id === s.shuttle_id && isOpen(i));
    let action = '<button class="btn btn-secondary" disabled>Unavailable</button>';

    if (s.status === "AVAILABLE" && active) {
      action = `<button class="btn btn-primary" data-action="manual-dispatch"
                  data-shuttle="${esc(s.shuttle_id)}" data-id="${active.id}">Dispatch to incident</button>`;
    } else if (s.status === "EN_ROUTE" && linked) {
      action = `<button class="btn btn-secondary" data-action="arrived" data-id="${linked.id}">Mark arrived</button>`;
    } else if (s.status === "ON_SCENE" && linked) {
      action = `<button class="btn btn-secondary" data-action="resolve" data-id="${linked.id}">Resolve incident</button>`;
    } else if (s.status === "AVAILABLE") {
      action = '<button class="btn btn-secondary" disabled>No open incident</button>';
    }

    return `
      <article class="shuttle-card">
        <div class="shuttle-head">
          <div class="shuttle-illustration">â–°</div>
          <span class="${statusClass(s.status)}">${esc(statusLabel(s.status))}</span>
        </div>
        <h4>${esc(s.shuttle_id)} <span class="muted">/ Rescue shuttle</span></h4>
        <div class="shuttle-sub">âŒ– ${esc(s.location)}${s.destination ? " â†’ " + esc(s.destination) : ""}</div>
        <div class="shuttle-stats">
          <span>Capacity <strong>${esc(s.capacity)}</strong></span>
          <span>ETA <strong>${s.eta_min == null ? "â€”" : esc(s.eta_min) + " min"}</strong></span>
        </div>
        ${linked ? `<small class="assignment-chip">Assigned to incident #${esc(linked.id)}</small>` : ""}
        ${action}
      </article>`;
  }).join("");
}

function renderEvents(items) {
  if (!$("eventList")) return;
  $("eventList").innerHTML = items.length ? items.map(e => `
    <div class="event-row">
      <div class="event-icon">${e.event_type === "VEHICLE_CHECKPOINT" ? "â–£" : "âŒ"}</div>
      <div>
        <strong>${esc(e.event_type === "VEHICLE_CHECKPOINT" ? "Vehicle checkpoint" : "Passenger boarding")} Â· ${esc(e.entity_id)}</strong>
        <small>Tag ${esc(e.tag_id)} Â· ${esc(e.location)} Â· Simulated</small>
      </div>
      <span class="event-time">${esc(new Date(e.created_at).toLocaleTimeString([], { hour: "2-digit", minute: "2-digit" }))}</span>
    </div>`).join("") : '<div class="empty">No RFID scans yet. Use the simulator below.</div>';
}

function renderRequests(items) {
  if (!$("requestList")) return;
  $("requestList").innerHTML = items.length ? items.map(r => `
    <div class="request-item ${String(r.priority || "Normal").toLowerCase() === "urgent" ? "request-urgent" : ""}">
      <strong>${esc(r.request_type)} <span class="priority-label">${esc(r.priority || "Normal")}</span></strong>
      <small>${esc(r.stop)} Â· ${esc(r.note || "No additional note")}</small>
    </div>`).join("") : '<div class="empty">No assistance requests yet.</div>';
}

async function refresh(showError = false) {
  if (refreshInProgress) return;
  refreshInProgress = true;
  try {
    const d = await api("/api/dashboard");
    if ($("metricIncidents")) $("metricIncidents").textContent = d.metrics.active_incidents;
    if ($("metricRFID")) $("metricRFID").textContent = d.metrics.rfid_events;
    if ($("metricBoarding")) $("metricBoarding").textContent = d.metrics.boarding_scans;
    renderIncidents(d.incidents || []);
    renderShuttles(d.shuttles || [], d.incidents || []);
    renderEvents(d.events || []);
    renderRequests(d.requests || []);
    if ($("lastUpdated")) {
      $("lastUpdated").textContent = `Updated ${new Date().toLocaleTimeString([], { hour: "2-digit", minute: "2-digit", second: "2-digit" })}`;
    }
  } catch (e) {
    console.error("TransitRescue refresh failed:", e);
    if (showError) toast(`Could not refresh dashboard: ${e.message}`);
  } finally {
    refreshInProgress = false;
  }
}

async function setStatus(id, status) {
  try {
    await api(`/api/incidents/${id}/status`, "POST", { status });
    toast("Incident status updated.");
    await refresh();
  } catch (e) { toast(e.message); }
}

async function dispatchFor(id, shuttleId = null) {
  if (!id) { toast("Report or select an incident first."); return; }
  try {
    let out;
    if (shuttleId) {
      out = await api(`/api/shuttles/${encodeURIComponent(shuttleId)}/dispatch`, "POST", { incident_id: Number(id) });
    } else {
      out = await api(`/api/incidents/${id}/auto-dispatch`, "POST", {});
    }
    toast(`${out.message || "Rescue dispatch started."} Simulation only.`);
    await refresh();
  } catch (e) { toast(e.message); }
}

async function markArrived(id) {
  try {
    const out = await api(`/api/incidents/${id}/arrived`, "POST", {});
    toast(`${out.message || "Shuttle marked as arrived."} Simulation only.`);
    await refresh();
  } catch (e) { toast(e.message); }
}

async function resolveIncident(id) {
  try {
    const out = await api(`/api/incidents/${id}/resolve`, "POST", {});
    toast(out.message || "Incident resolved.");
    await refresh();
  } catch (e) { toast(e.message); }
}

async function resetShuttle(id) {
  try {
    const out = await api(`/api/shuttles/${encodeURIComponent(id)}/reset`, "POST", {});
    toast(out.message);
    await refresh();
  } catch (e) { toast(e.message); }
}

function bindIfPresent(id, eventName, handler) {
  const el = $(id);
  if (el) el.addEventListener(eventName, handler);
}

bindIfPresent("incidentForm", "submit", async e => {
  e.preventDefault();
  const form = e.currentTarget;
  const data = Object.fromEntries(new FormData(form).entries());
  data.delay_min = Number(data.delay_min || 20);

  try {
    const out = await api("/api/incidents", "POST", data);
    toast(out.message || "Breakdown incident created.");
    form.reset();
    await refresh();
  } catch (err) { toast(err.message); }
});

bindIfPresent("vehicleScan", "click", async () => {
  try {
    const out = await api("/api/rfid/vehicle", "POST", {
      tag_id: "V-BUS-5007", vehicle_id: "BUS-5007", location: "Richmond Circle Checkpoint"
    });
    if ($("rfidFeedback")) {
      $("rfidFeedback").textContent = `${out.message} Hardware reader is not connected; this is a simulation.`;
      $("rfidFeedback").classList.add("success");
    }
    toast("Vehicle RFID scan simulated.");
    await refresh();
  } catch (e) { toast(e.message); }
});

bindIfPresent("passengerScan", "click", async () => {
  try {
    const out = await api("/api/rfid/passenger", "POST", {
      tag_id: "P-DEMO-104", stop: "Richmond Circle"
    });
    if ($("rfidFeedback")) {
      $("rfidFeedback").textContent = `${out.message} No real passenger identity is stored.`;
      $("rfidFeedback").classList.add("success");
    }
    toast("Passenger boarding scan simulated.");
    await refresh();
  } catch (e) { toast(e.message); }
});

bindIfPresent("requestForm", "submit", async e => {
  e.preventDefault();
  const form = e.currentTarget;
  const data = Object.fromEntries(new FormData(form).entries());
  try {
    const out = await api("/api/requests", "POST", data);
    toast(out.message || "Passenger request submitted.");
    form.reset();
    await refresh();
  } catch (err) { toast(err.message); }
});

bindIfPresent("resetDemo", "click", async () => {
  if (!confirm("Reset the demo incidents, RFID events, and passenger requests?")) return;
  try {
    const out = await api("/api/demo/reset", "POST", {});
    toast(out.message || "Demo activity reset.");
    await refresh();
  } catch (e) { toast(e.message); }
});

/* Event delegation keeps dynamically rendered incident/shuttle buttons working. */
document.addEventListener("click", e => {
  const button = e.target.closest("[data-action]");
  if (!button) return;
  const action = button.dataset.action;
  const id = button.dataset.id;
  if (action === "verify") setStatus(id, "VERIFYING");
  if (action === "auto-dispatch") dispatchFor(id);
  if (action === "manual-dispatch") dispatchFor(id, button.dataset.shuttle);
  if (action === "arrived") markArrived(id);
  if (action === "resolve") resolveIncident(id);
});

refresh(true);
setInterval(() => refresh(false), 10000);
