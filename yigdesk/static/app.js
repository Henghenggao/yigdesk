import { YigdeskSession } from "/session.js";
import "/yig-grid.js";
import "/yig-model-inspector.js";

const session = new YigdeskSession();
const state = {
  packet: null,
  agent: null,
  agentRun: null,
  source: null,
  council: null,
  copyResetTimer: null,
};
const byId = (id) => document.getElementById(id);
const delay = (milliseconds) => new Promise((resolve) => window.setTimeout(resolve, milliseconds));

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
    if (index === activeIndex) item.setAttribute("aria-current", "step");
    else item.removeAttribute("aria-current");
  });
}

function renderStory(payload) {
  const scenario = payload.scenario;
  const source = payload.source || {};
  state.source = source;
  byId("requester").textContent = scenario.requester;
  byId("requester-role").textContent = scenario.requester_role;
  byId("recipient").textContent = scenario.recipient;
  byId("sent-at").textContent = scenario.sent_at;
  byId("subject").textContent = scenario.subject;
  byId("message").textContent = scenario.message;
  document.querySelectorAll("[data-scenario]").forEach((button) => {
    const isActive = button.dataset.scenario === payload.scenario_id;
    button.classList.toggle("is-active", isActive);
    button.setAttribute("aria-pressed", String(isActive));
  });
  const sourceHash = source.sha256 || payload.workbook.fingerprint;
  const sheetCount = Array.isArray(source.sheet_names) ? source.sheet_names.length : 0;
  const uploaded = source.kind === "uploaded-synthetic-xlsx";
  byId("source-pill").textContent = uploaded ? "UPLOADED · SYNTHETIC" : "SYNTHETIC FIXTURE";
  byId("attachment-name").textContent = source.filename || "Northstar-renewal.xlsx";
  byId("attachment-meta").textContent = `${sheetCount || 2} sheets · ${uploaded ? "parsed from uploaded bytes" : "generated synthetic fixture"}`;
  byId("attachment-state").textContent = uploaded ? "parsed · read only" : "read only";
  byId("workbook-name").textContent = (source.filename || "Northstar renewal").replace(/\.xlsx$/i, "");
  byId("fingerprint").textContent = `source sha256 · ${sourceHash.slice(0, 12)}`;
  configureAgent(payload.agent);
}

function configureAgent(agent) {
  state.agent = agent || {
    available: false,
    mode: "local-preview",
    model: null,
    reasoning_effort: null,
  };
  const label = byId("analyze").querySelector(".action-label");
  if (state.agent.available) {
    label.textContent = "Analyze with Codex";
    byId("read-wall").innerHTML = '<span aria-hidden="true">&#9673;</span> Codex is tool-limited; analysis can never alter the source';
  } else {
    label.textContent = "Preview consequence locally";
    byId("read-wall").innerHTML = '<span aria-hidden="true">&#9673;</span> Codex runtime is not configured · local deterministic preview';
  }
}

function revokeProof() {
  state.packet = null;
  state.agentRun = null;
  state.council = null;
  byId("decision-empty").hidden = false;
  byId("decision-content").hidden = true;
  byId("packet-export").hidden = true;
  byId("decision-reason").textContent = "";
  byId("net-arr").textContent = "—";
  byId("arr-impact").textContent = "—";
  byId("gross-margin").textContent = "—";
  byId("headroom").textContent = "—";
  byId("byte-proof").textContent = "No proof packet";
  byId("evidence-list").replaceChildren();
  byId("workbook-grid").packet = null;
  byId("model-inspector").packet = null;
  byId("agent-proof").hidden = true;
  byId("agent-mode").textContent = "Codex + Yigdesk MCP";
  byId("agent-meta").textContent = "run pending";
  byId("agent-verified").textContent = "NOT VERIFIED";
  byId("packet-id").textContent = "cpkt-pending";
  byId("packet-scope").textContent = "synthetic adapter";
  byId("packet-mark").textContent = "✓";
  byId("packet-eyebrow").textContent = "Portable proof ready";
  byId("packet-heading").textContent = "ConsequencePacket prepared.";
  byId("memo-status").textContent = "NOT PREPARED";
  byId("memo-body").textContent = "";
  const copyButton = byId("copy-packet");
  if (state.copyResetTimer !== null) {
    window.clearTimeout(state.copyResetTimer);
    state.copyResetTimer = null;
  }
  copyButton.disabled = true;
  copyButton.firstElementChild.textContent = "Copy proof packet";
  copyButton.dataset.state = "idle";
  byId("a2a-scorecards").hidden = true;
  byId("a2a-state").textContent = "AWAITING PROOF";
  byId("a2a-intro").textContent = "Run the consequence preview to expose proposal, boundary, rounding, and stress-test facts. These are deterministic tool results—not simulated agent dialogue.";
  byId("board-revision").textContent = "revision · pending";
  byId("copy-council").disabled = true;
}

function setEmptyDecision(heading, message) {
  byId("decision-empty-heading").textContent = heading;
  byId("decision-empty-message").textContent = message;
}

function clearPreview() {
  revokeProof();
  document.body.dataset.outcome = "idle";
  byId("verdict").className = "verdict idle";
  byId("verdict").textContent = "NOT ANALYZED";
  byId("packet-status").textContent = "PREVIEW ONLY";
  setEmptyDecision(
    "Nothing inferred yet.",
    "Run a read-only preview to bind all five output cells to one source packet.",
  );
  setStep("read");
}

function showAnalysisPending() {
  revokeProof();
  document.body.dataset.outcome = "analyzing";
  byId("verdict").className = "verdict idle";
  byId("verdict").textContent = "ANALYZING";
  byId("packet-status").textContent = "VERIFYING";
  setEmptyDecision(
    "Building a fresh proof…",
    "The previous result has been revoked while the engine verifies this run.",
  );
  setStep("calculate");
}

function showAnalysisError(isAgent) {
  revokeProof();
  document.body.dataset.outcome = "error";
  byId("verdict").className = "verdict error";
  byId("verdict").textContent = isAgent ? "AGENT REJECTED" : "PREVIEW ERROR";
  byId("packet-status").textContent = "NO VERIFIED PACKET";
  setEmptyDecision(
    "Analysis could not be verified.",
    "No proof packet is available. Retry to produce a fresh, engine-matched result.",
  );
  setStep("trace");
}

function renderPacket(packet, agentRun = null) {
  state.packet = packet;
  state.agentRun = agentRun;
  const consequence = packet.consequence;
  document.body.dataset.outcome = consequence.complete ? "ready" : "hold";
  byId("decision-empty").hidden = true;
  byId("decision-content").hidden = false;
  byId("verdict").textContent = consequence.verdict;
  byId("verdict").className = `verdict ${consequence.complete ? "ready" : "hold"}`;
  byId("decision-reason").textContent = consequence.reason;
  byId("net-arr").textContent = consequence.display.net_arr;
  byId("arr-impact").textContent = consequence.display.arr_impact;
  byId("gross-margin").textContent = consequence.display.gross_margin;
  byId("headroom").textContent = consequence.display.headroom === "Unavailable"
    ? "Floor check unavailable"
    : `${consequence.display.headroom} above floor`;
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
  if (agentRun?.agent) {
    const trace = agentRun.agent;
    const usage = trace.usage || {};
    const tokens = (usage.input_tokens || 0) + (usage.output_tokens || 0);
    byId("agent-mode").textContent = `Codex + Yigdesk MCP · ${trace.model}`;
    byId("agent-meta").textContent = `${agentRun.run_id} · ${trace.tool_calls.length} tools · ${trace.latency_ms} ms · ${tokens} tokens`;
    byId("agent-verified").textContent = "ENGINE MATCH VERIFIED";
    byId("agent-proof").hidden = false;
  } else {
    byId("agent-proof").hidden = true;
  }
  byId("packet-id").textContent = packet.packet_id;
  byId("packet-scope").textContent = packet.implementation_scope;
  byId("packet-mark").textContent = consequence.complete ? "✓" : "!";
  byId("packet-eyebrow").textContent = consequence.complete ? "Portable proof ready" : "Refusal proof ready";
  byId("packet-heading").textContent = consequence.complete ? "ConsequencePacket prepared." : "Hold packet prepared.";
  byId("packet-export").hidden = false;
  byId("copy-packet").disabled = false;
  byId("memo-status").textContent = consequence.complete ? "DRAFT · NOT SENT" : "NOT PREPARED";
  byId("memo-body").textContent = consequence.complete
    ? `Requested terms preview at ${consequence.display.net_arr}, ${consequence.display.gross_margin} gross margin, ${consequence.display.headroom} above the floor.`
    : "The memo is withheld because cost evidence is incomplete.";
  setStep(consequence.complete ? "review" : "trace");
  void renderDecisionBoard(packet);
}

async function renderDecisionBoard(packet) {
  byId("a2a-state").textContent = "CALCULATING";
  byId("a2a-intro").textContent = "Yigdesk is evaluating the submitted proposal, exact floor boundary, first unsafe step, and one explicit COGS stress assumption.";
  try {
    const requested = packet.consequence.display.requested_discount.replace("%", "");
    const [boundaryResult, stressResult] = await Promise.all([
      demoApi("/api/proposals/boundary?step_pct=0.01"),
      demoApi("/api/proposals/stress-test", {
        requested_discount_pct: requested,
        cogs_change_pct: "5",
      }),
    ]);
    if (state.packet !== packet) return;
    const boundary = boundaryResult.boundary;
    const stress = stressResult.stress_test;
    const submittedPass = packet.consequence.constraint_pass;
    byId("board-requested").textContent = packet.consequence.display.requested_discount;
    byId("board-requested-note").textContent = submittedPass
      ? `${packet.consequence.display.gross_margin} exact-policy pass`
      : `${packet.consequence.display.gross_margin} exact-policy fail`;
    if (boundary.status === "READY") {
      const safe = boundary.largest_safe_proposal;
      const unsafe = boundary.first_unsafe_proposal;
      byId("board-boundary").textContent = `${boundary.largest_safe_step_pct}%`;
      byId("board-boundary-note").textContent = `exact ceiling ${boundary.exact_max_discount_pct}% · step 0.01`;
      byId("board-rounding").textContent = `${unsafe.requested_discount_pct}%`;
      byId("board-rounding-note").textContent = `${unsafe.display.gross_margin} displayed · ${unsafe.exact.gross_margin_pct}% exact · FAIL`;
      state.council = { packet, boundary, stress };
    } else {
      byId("board-boundary").textContent = "HOLD";
      byId("board-boundary-note").textContent = boundary.reason;
      byId("board-rounding").textContent = "—";
      byId("board-rounding-note").textContent = "No grounded boundary";
      state.council = { packet, boundary, stress };
    }
    const stressProposal = stress.proposal;
    byId("board-stress").textContent = stressProposal
      ? stressProposal.display.gross_margin
      : "HOLD";
    byId("board-stress-note").textContent = stressProposal
      ? `${stressProposal.verdict} · temporary assumption`
      : stress.reason;
    byId("board-revision").textContent = `${packet.revision_id} · ${packet.source_fingerprint.slice(0, 16)} · ${packet.packet_id}`;
    byId("a2a-scorecards").hidden = false;
    byId("a2a-state").textContent = "A2A READY";
    byId("a2a-intro").textContent = "Finance, sales, risk, and optimizer agents can now challenge one another through these same read-only tools and must return matching revision identifiers.";
    byId("copy-council").disabled = false;
  } catch (error) {
    if (state.packet !== packet) return;
    byId("a2a-state").textContent = "BOARD UNAVAILABLE";
    byId("a2a-intro").textContent = "The deterministic proposal board did not complete, so no A2A-ready claim is released.";
    console.error(error);
  }
}

async function uploadWorkbook(event) {
  event.preventDefault();
  const form = event.currentTarget;
  const button = byId("upload-action");
  const status = byId("upload-status");
  button.disabled = true;
  status.dataset.state = "loading";
  status.textContent = "Reading workbook bytes and validating the synthetic P&L schema…";
  try {
    const response = await fetch("/api/upload", {
      method: "POST",
      headers: { "X-Yigdesk-Action": "synthetic-upload" },
      body: new FormData(form),
    });
    const payload = await response.json();
    if (!response.ok) throw new Error(payload.error || payload.code || "Upload failed");
    clearPreview();
    renderStory(payload);
    await byId("workbook-grid").refresh();
    status.dataset.state = "ready";
    status.textContent = `${payload.source.filename} · ${(payload.source.size_bytes / 1024 / 1024).toFixed(2)} MB · ${payload.source.extraction.revenue_cells.length + payload.source.extraction.cogs_cells.length} FY2024 source cells · sha256 ${payload.source.sha256.slice(0, 12)}`;
  } catch (error) {
    status.dataset.state = "error";
    status.textContent = error instanceof Error ? error.message : "Workbook upload failed safely.";
    console.error(error);
  } finally {
    button.disabled = false;
  }
}

async function copyCouncilPrompt() {
  if (!state.council || !state.packet) return;
  const packet = state.packet;
  const prompt = `Use the project Yigdesk decision council for the current Northwind request. Spawn finance_analyst, sales_advocate, and risk_challenger in parallel. On every call pass the matching actor. Finance must make exactly: get_deal_context, find_feasible_boundary(0.01), evaluate_proposal(submitted), inspect_evidence(Deal Model!B4). Sales must make exactly: get_deal_context, evaluate_proposal(submitted), evaluate_proposal(one alternative). Risk must make exactly: get_deal_context, list_missing_evidence, find_feasible_boundary(0.01), stress_test_assumption(submitted,+5% COGS), inspect_evidence(Deal Model!B4). Require revision_id ${packet.revision_id}, source_fingerprint ${packet.source_fingerprint}, and packet_id ${packet.packet_id} from all three, then spawn decision_optimizer for exactly: get_deal_context, compare_proposals(unique role proposals), inspect_evidence(Deal Model!B4). No other Yigdesk calls. Distinguish financial feasibility from commercial optimality; do not invent market evidence and do not approve, send, or write back.`;
  try {
    await navigator.clipboard.writeText(prompt);
    byId("copy-council").firstChild.textContent = "Council prompt copied ";
  } catch (error) {
    console.error(error);
  }
}

async function runWithCodex(label) {
  let run = await session.startAgentRun(state.agent?.request_token);
  const deadline = Date.now() + 130000;
  const phaseLabels = {
    starting_codex: "Codex · starting…",
    calling_yigdesk_tools: "Codex · calling Yigdesk tools…",
    verifying_result: "Codex · verifying result…",
  };
  while (run.status === "queued" || run.status === "running") {
    label.textContent = phaseLabels[run.stage] || "Codex · working…";
    if (Date.now() >= deadline) throw new Error("Codex run timed out.");
    await delay(250);
    run = await session.getAgentRun(run.run_id);
  }
  if (run.status !== "completed") {
    throw new Error(run.error?.message || "Codex output was rejected.");
  }
  return run;
}

async function analyze() {
  const button = byId("analyze");
  const label = button.querySelector(".action-label");
  button.disabled = true;
  button.dataset.state = "loading";
  button.setAttribute("aria-busy", "true");
  byId("workspace").setAttribute("aria-busy", "true");
  document.querySelectorAll("[data-scenario]").forEach((scenarioButton) => { scenarioButton.disabled = true; });
  label.textContent = "Reading + tracing…";
  showAnalysisPending();
  try {
    if (state.agent?.available) {
      const run = await runWithCodex(label);
      renderPacket(run.packet, run);
      label.textContent = "Analyze again with Codex";
    } else {
      const response = await session.previewConsequence();
      renderPacket(response.packet);
      label.textContent = "Preview again locally";
    }
  } catch (error) {
    label.textContent = state.agent?.available ? "Retry Codex analysis" : "Retry local preview";
    showAnalysisError(Boolean(state.agent?.available));
    console.error(error);
  } finally {
    button.disabled = false;
    button.dataset.state = "idle";
    button.removeAttribute("aria-busy");
    byId("workspace").removeAttribute("aria-busy");
    document.querySelectorAll("[data-scenario]").forEach((scenarioButton) => { scenarioButton.disabled = false; });
  }
}

async function chooseScenario(scenarioId) {
  const scenarioSwitch = document.querySelector(".scenario-switch");
  scenarioSwitch.setAttribute("aria-busy", "true");
  document.querySelectorAll("[data-scenario]").forEach((button) => { button.disabled = true; });
  try {
    const payload = await demoApi("/api/reset", { scenario_id: scenarioId });
    clearPreview();
    renderStory(payload);
    await byId("workbook-grid").refresh();
    byId("upload-status").dataset.state = "";
    byId("upload-status").textContent = "Built-in synthetic fixture restored.";
  } finally {
    document.querySelectorAll("[data-scenario]").forEach((button) => { button.disabled = false; });
    scenarioSwitch.removeAttribute("aria-busy");
  }
}

async function copyPacket() {
  if (!state.packet) return;
  const packet = state.packet;
  const button = byId("copy-packet");
  const label = button.firstElementChild;
  try {
    const proof = state.agentRun || { packet: state.packet, mode: "local-preview" };
    await navigator.clipboard.writeText(JSON.stringify(proof, null, 2));
    if (state.packet !== packet) return;
    label.textContent = "Packet copied";
    button.dataset.state = "copied";
    state.copyResetTimer = window.setTimeout(() => {
      state.copyResetTimer = null;
      if (state.packet !== packet) return;
      label.textContent = "Copy proof packet";
      button.dataset.state = "idle";
    }, 1800);
  } catch (error) {
    label.textContent = "Copy unavailable";
    console.error(error);
  }
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
  byId("copy-council").addEventListener("click", copyCouncilPrompt);
  byId("upload-form").addEventListener("submit", uploadWorkbook);
  byId("workbook-upload").addEventListener("change", (event) => {
    const file = event.target.files?.[0];
    byId("upload-file-label").textContent = file?.name || "Choose synthetic FY2024 workbook";
    byId("upload-file-meta").textContent = file
      ? `${(file.size / 1024 / 1024).toFixed(2)} MB · ready to send to local parser`
      : "The browser sends the selected file to this local demo process.";
  });
  document.querySelectorAll("[data-scenario]").forEach((button) => {
    button.addEventListener("click", () => chooseScenario(button.dataset.scenario));
  });
  const payload = await demoApi("/api/state");
  clearPreview();
  renderStory(payload);
});
