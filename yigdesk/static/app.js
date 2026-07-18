import { YigdeskSession } from "/session.js";
import "/yig-grid.js";
import "/yig-model-inspector.js";

const session = new YigdeskSession();
const state = { packet: null };
const byId = (id) => document.getElementById(id);

async function demoApi(path, payload) {
  const response = await fetch(path, {
    method: payload ? "POST" : "GET",
    headers: { "Content-Type": "application/json", "X-Yigdesk-Action": "demo-fixture" },
    body: payload ? JSON.stringify(payload) : undefined,
  });
  const body = await response.json();
  if (!response.ok) throw new Error(body.error || body.code || "Request failed");
  return body;
}

function setStep(active) {
  const order = ["read", "calculate", "trace", "review"];
  const activeIndex = order.indexOf(active);
  document.querySelectorAll(".process-strip li").forEach((item) => {
    const index = order.indexOf(item.dataset.step);
    item.classList.toggle("is-current", index === activeIndex);
    item.classList.toggle("is-done", index < activeIndex);
  });
}

function renderStory(payload) {
  const scenario = payload.scenario;
  byId("requester").textContent = scenario.requester;
  byId("requester-role").textContent = scenario.requester_role;
  byId("recipient").textContent = scenario.recipient;
  byId("sent-at").textContent = scenario.sent_at;
  byId("subject").textContent = scenario.subject;
  byId("message").textContent = scenario.message;
  document.querySelectorAll("[data-scenario]").forEach((button) => {
    button.classList.toggle("is-active", button.dataset.scenario === payload.scenario_id);
  });
  byId("fingerprint").textContent = `sha256 · ${payload.workbook.fingerprint.slice(0, 12)}`;
}

function clearPreview() {
  state.packet = null;
  byId("decision-empty").hidden = false;
  byId("decision-content").hidden = true;
  byId("packet-export").hidden = true;
  byId("verdict").className = "verdict idle";
  byId("verdict").textContent = "NOT ANALYZED";
  byId("packet-status").textContent = "PREVIEW ONLY";
  byId("workbook-grid").packet = null;
  byId("model-inspector").packet = null;
  setStep("read");
}

function renderPacket(packet) {
  state.packet = packet;
  const consequence = packet.consequence;
  byId("decision-empty").hidden = true;
  byId("decision-content").hidden = false;
  byId("verdict").textContent = consequence.verdict;
  byId("verdict").className = `verdict ${consequence.complete ? "ready" : "hold"}`;
  byId("decision-reason").textContent = consequence.reason;
  byId("net-arr").textContent = consequence.display.net_arr;
  byId("arr-impact").textContent = consequence.display.arr_impact;
  byId("gross-margin").textContent = consequence.display.gross_margin;
  byId("headroom").textContent = `${consequence.display.headroom} above floor`;
  byId("byte-proof").textContent = packet.analysis_bytes_unchanged
    ? "Workbook bytes unchanged"
    : "Source drift detected";
  byId("packet-status").textContent = `${consequence.evidence_cells.length} CELLS · ${consequence.complete ? "COMPLETE" : "PARTIAL"}`;

  const evidence = byId("evidence-list");
  evidence.replaceChildren();
  consequence.evidence_cells.forEach((cell) => {
    const item = document.createElement("li");
    const label = document.createElement("span");
    const values = document.createElement("strong");
    const address = document.createElement("code");
    label.textContent = cell.role;
    values.textContent = `${cell.before} → ${cell.after}`;
    address.textContent = cell.address;
    item.append(label, values, address);
    evidence.append(item);
  });

  byId("workbook-grid").packet = packet;
  byId("model-inspector").packet = packet;
  byId("packet-id").textContent = packet.packet_id;
  byId("packet-scope").textContent = packet.implementation_scope;
  byId("packet-export").hidden = false;
  byId("memo-status").textContent = consequence.complete ? "DRAFT · NOT SENT" : "NOT PREPARED";
  byId("memo-body").textContent = consequence.complete
    ? `Requested terms preview at ${consequence.display.net_arr}, ${consequence.display.gross_margin} gross margin, ${consequence.display.headroom} above the floor.`
    : "The memo is withheld because cost evidence is incomplete.";
  setStep(consequence.complete ? "review" : "trace");
}

async function analyze() {
  const button = byId("analyze");
  button.disabled = true;
  button.firstElementChild.textContent = "Reading + tracing…";
  setStep("calculate");
  try {
    const response = await session.previewConsequence();
    renderPacket(response.packet);
    button.firstElementChild.textContent = "Analyze again with Codex";
  } catch (error) {
    button.firstElementChild.textContent = error.message;
  } finally {
    button.disabled = false;
  }
}

async function chooseScenario(scenarioId) {
  document.querySelectorAll("[data-scenario]").forEach((button) => { button.disabled = true; });
  try {
    const payload = await demoApi("/api/reset", { scenario_id: scenarioId });
    clearPreview();
    renderStory(payload);
    await byId("workbook-grid").refresh();
  } finally {
    document.querySelectorAll("[data-scenario]").forEach((button) => { button.disabled = false; });
  }
}

async function copyPacket() {
  if (!state.packet) return;
  await navigator.clipboard.writeText(JSON.stringify(state.packet, null, 2));
  byId("copy-packet").textContent = "Packet copied";
}

document.addEventListener("DOMContentLoaded", async () => {
  const grid = byId("workbook-grid");
  const inspector = byId("model-inspector");
  grid.session = session;
  inspector.session = session;
  inspector.selectedRef = "Deal Model!B2";
  grid.addEventListener("yig-selection-change", (event) => {
    inspector.selectedRef = event.detail.address;
  });
  byId("analyze").addEventListener("click", analyze);
  byId("copy-packet").addEventListener("click", copyPacket);
  document.querySelectorAll("[data-scenario]").forEach((button) => {
    button.addEventListener("click", () => chooseScenario(button.dataset.scenario));
  });
  const payload = await demoApi("/api/state");
  clearPreview();
  renderStory(payload);
});
