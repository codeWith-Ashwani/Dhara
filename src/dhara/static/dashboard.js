"use strict";

const state = { cases: [], selectedId: null, dossier: null };
const queue = document.querySelector("#queue");
const caseCount = document.querySelector("#case-count");
const emptyState = document.querySelector("#empty-state");
const caseContent = document.querySelector("#case-content");
const panels = document.querySelector("#panels");
const actionStatus = document.querySelector("#action-status");

document.querySelector("#refresh").addEventListener("click", loadQueue);
document.querySelectorAll("[data-action]").forEach((button) => {
  button.addEventListener("click", () => submitAction(button.dataset.action));
});

async function loadQueue() {
  try {
    const response = await fetch("/v1/triage", { headers: { Accept: "application/json" } });
    if (!response.ok) throw new Error("Triage service is unavailable");
    state.cases = await response.json();
    renderQueue();
    if (!state.selectedId && state.cases.length) await selectCase(state.cases[0].alert_id);
    if (state.selectedId && !state.cases.some((item) => item.alert_id === state.selectedId)) {
      state.selectedId = null;
      showEmpty();
    }
  } catch (error) {
    queue.replaceChildren(messageNode(error.message, "queue-empty"));
  }
}

function renderQueue() {
  caseCount.value = String(state.cases.length);
  queue.replaceChildren();
  if (!state.cases.length) {
    queue.append(messageNode("Waiting for the first fusion evaluation.", "queue-empty"));
    showEmpty();
    return;
  }
  state.cases.forEach((item) => {
    const button = document.createElement("button");
    button.type = "button";
    button.className = `queue-item${item.alert_id === state.selectedId ? " selected" : ""}`;
    button.addEventListener("click", () => selectCase(item.alert_id));
    const top = document.createElement("div");
    const tier = document.createElement("strong");
    tier.textContent = item.committed_tier.toUpperCase();
    const score = document.createElement("span");
    score.className = "queue-score";
    score.textContent = Number(item.fused_confidence).toFixed(2);
    top.append(tier, score);
    const cell = document.createElement("small");
    cell.textContent = item.cell_id;
    const meta = document.createElement("small");
    meta.textContent = `${item.distinct_devices} devices · ${item.status.replaceAll("_", " ")}`;
    button.append(top, cell, meta);
    queue.append(button);
  });
}

async function selectCase(alertId) {
  state.selectedId = alertId;
  renderQueue();
  actionStatus.textContent = "";
  try {
    const response = await fetch(`/v1/alerts/${encodeURIComponent(alertId)}/dossier`);
    if (!response.ok) throw new Error("The evidence dossier could not be loaded");
    state.dossier = await response.json();
    renderCase();
  } catch (error) {
    actionStatus.textContent = error.message;
    actionStatus.className = "action-status error";
  }
}

function renderCase() {
  const selected = state.cases.find((item) => item.alert_id === state.selectedId);
  if (!selected || !state.dossier) return showEmpty();
  emptyState.hidden = true;
  caseContent.hidden = false;
  document.querySelector("#case-title").textContent = `Cell ${selected.cell_id}`;
  document.querySelector("#case-kicker").textContent = `${selected.status.replaceAll("_", " ")} · evidence dossier`;
  document.querySelector("#narrative").textContent = state.dossier.narrative;
  document.querySelector("#fused-score").textContent = Number(selected.fused_confidence).toFixed(2);
  const badge = document.querySelector("#tier-badge");
  badge.textContent = selected.committed_tier;
  badge.className = `tier-badge ${selected.committed_tier}`;
  setMeter("sensor", selected.sensor_confidence);
  setMeter("crowd", selected.crowd_confidence);
  const review = document.querySelector("#review-callout");
  review.hidden = !selected.requires_human_review && !selected.requires_officer_signoff;
  review.textContent = selected.requires_officer_signoff
    ? "Warning draft requires authorised officer sign-off."
    : "Evidence streams diverge. Review the diagnostic question before acting.";
  renderMap(state.dossier.panels.find((panel) => panel.name === "crowd_evidence"));
  renderPanels();
  document.querySelector('[data-action="escalate_publish_cap"]').disabled =
    selected.committed_tier !== "warning";
}

function renderMap(crowdPanel) {
  const map = document.querySelector("#evidence-map");
  map.querySelectorAll(".report-point").forEach((point) => point.remove());
  const reports = crowdPanel?.content?.reports ?? [];
  document.querySelector("#map-caption").textContent = reports.length
    ? `${reports.length} reports · trust-weighted points`
    : "No report coordinates";
  if (!reports.length) return;
  const latitudes = reports.map((item) => Number(item.latitude));
  const longitudes = reports.map((item) => Number(item.longitude));
  const minLat = Math.min(...latitudes);
  const maxLat = Math.max(...latitudes);
  const minLon = Math.min(...longitudes);
  const maxLon = Math.max(...longitudes);
  reports.forEach((report) => {
    const point = document.createElement("span");
    point.className = `report-point${report.status === "quarantined" ? " quarantined" : ""}`;
    const x = 12 + 76 * normalise(Number(report.longitude), minLon, maxLon);
    const y = 88 - 76 * normalise(Number(report.latitude), minLat, maxLat);
    point.style.left = `${x}%`;
    point.style.top = `${y}%`;
    point.style.setProperty("--size", `${12 + Number(report.trust) * 14}px`);
    point.title = `${report.report_id}: trust ${Number(report.trust).toFixed(2)}, ${report.status}`;
    map.append(point);
  });
}

function renderPanels() {
  panels.replaceChildren();
  state.dossier.panels.forEach((panel) => {
    const article = document.createElement("article");
    article.className = "dossier-panel";
    const number = document.createElement("span");
    number.className = "panel-number";
    number.textContent = `PANEL ${panel.number}`;
    const heading = document.createElement("h3");
    heading.textContent = panel.name.replaceAll("_", " ");
    article.append(number, heading, facts(panel.content));
    panels.append(article);
  });
}

function facts(object) {
  const list = document.createElement("dl");
  list.className = "fact-list";
  Object.entries(object).forEach(([key, value]) => {
    const row = document.createElement("div");
    row.className = "fact-row";
    const term = document.createElement("dt");
    term.textContent = key.replaceAll("_", " ");
    const description = document.createElement("dd");
    description.textContent = displayValue(value);
    row.append(term, description);
    list.append(row);
  });
  return list;
}

async function submitAction(action) {
  actionStatus.className = "action-status";
  const actorId = document.querySelector("#actor-id").value.trim();
  const reason = document.querySelector("#reason").value.trim();
  if (!actorId || reason.length < 3) {
    actionStatus.textContent = "Officer ID and a reason are required.";
    actionStatus.classList.add("error");
    return;
  }
  const validUntil = new Date(Date.now() + 2 * 60 * 60 * 1000).toISOString();
  const payload = {
    action,
    actor_id: actorId,
    reason,
    language: document.querySelector("#language").value,
    place: document.querySelector("#place").value,
    road: document.querySelector("#road").value,
    depth: "knee",
    valid_until: validUntil,
  };
  try {
    const response = await fetch(`/v1/alerts/${encodeURIComponent(state.selectedId)}/actions`, {
      method: "POST",
      headers: { "Content-Type": "application/json", Accept: "application/json" },
      body: JSON.stringify(payload),
    });
    const result = await response.json();
    if (!response.ok) throw new Error(result.detail ?? "The decision was not accepted");
    actionStatus.textContent = result.delivery
      ? "Signed decision recorded. Push, SMS, IVR and CAP were recorded in the sandbox."
      : `Decision recorded: ${result.case_status.replaceAll("_", " ")}.`;
    await loadQueue();
  } catch (error) {
    actionStatus.textContent = error.message;
    actionStatus.classList.add("error");
  }
}

function setMeter(name, value) {
  const bounded = Math.max(0, Math.min(1, Number(value)));
  document.querySelector(`#${name}-value`).textContent = bounded.toFixed(2);
  document.querySelector(`#${name}-meter`).style.width = `${bounded * 100}%`;
}

function displayValue(value) {
  if (value === null || value === undefined) return "Not available";
  if (typeof value === "boolean") return value ? "Yes" : "No";
  if (typeof value === "number") return Number.isInteger(value) ? String(value) : value.toFixed(3);
  if (Array.isArray(value)) return value.length ? value.map(displayValue).join(" · ") : "None";
  if (typeof value === "object") return JSON.stringify(value);
  return String(value).replaceAll("_", " ");
}

function normalise(value, minimum, maximum) {
  return maximum === minimum ? 0.5 : (value - minimum) / (maximum - minimum);
}

function messageNode(text, className) {
  const node = document.createElement("div");
  node.className = className;
  node.textContent = text;
  return node;
}

function showEmpty() {
  emptyState.hidden = false;
  caseContent.hidden = true;
}

loadQueue();
setInterval(loadQueue, 15000);
