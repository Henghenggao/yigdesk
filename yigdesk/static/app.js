// Manifest renderer for a focused decision surface. Every string arrives as data
// and is inserted with textContent; no agent payload can execute in this page.
import { BoardSession } from "/session.js";

const session = new BoardSession();
const POLL_MS = 4000;
const ACTION_VERSION = "yigdesk-agent-action/v1";
const state = { manifest: { decisions: [] }, selectedId: null, role: null, notice: null, error: null };
let root = null;

function el(tag, attrs = {}, kids = []) {
  const node = document.createElement(tag);
  for (const [key, value] of Object.entries(attrs)) {
    if (value === null || value === undefined || value === false) continue;
    if (key === "text") node.textContent = value;
    else if (key === "class") node.className = value;
    else if (key === "onClick") node.addEventListener("click", value);
    else node.setAttribute(key, value === true ? "" : String(value));
  }
  for (const kid of Array.isArray(kids) ? kids : [kids]) if (kid !== null && kid !== undefined && kid !== false) node.append(kid instanceof Node ? kid : document.createTextNode(String(kid)));
  return node;
}

function decisions() { return state.manifest.decisions || []; }
function selected() { return decisions().find((d) => d.id === state.selectedId) || null; }
function selectDefault() {
  const list = decisions();
  if (list.length === 1) state.selectedId = list[0].id;
  else if (state.selectedId && !list.some((d) => d.id === state.selectedId)) state.selectedId = null;
}
function format(value, unit = "") {
  if (value === null || value === undefined) return "—";
  const n = Number(value);
  if (Number.isNaN(n)) return String(value);
  return `${n.toFixed(2)}${unit === "pt" ? " pt" : unit === "%" ? "%" : ""}`;
}

function render() {
  if (!root) return;
  selectDefault();
  root.replaceChildren(renderToolbar(), renderMessage());
  if (!decisions().length) {
    root.append(el("section", { class: "decision-empty", "data-testid": "board-empty" }, [el("h2", { text: "No decision is ready for review" }), el("p", { text: "The workbench will update when an agent opens a decision." })]));
    return;
  }
  if (decisions().length > 1) root.append(renderHistory());
  const decision = selected();
  if (!decision) { root.append(el("p", { class: "board-hint", text: "Choose a decision from history to focus the workbench." })); return; }
  root.append(renderDecision(decision));
}

function renderToolbar() {
  const version = (state.manifest.version || "decision view").split("/").at(-1);
  return el("div", { class: "board-toolbar" }, [el("span", { class: "manifest-tag" }, [el("i", { "aria-hidden": "true" }), `Live decision manifest · ${version}`]), el("button", { type: "button", class: "quiet-action", "data-testid": "refresh", onClick: refresh, text: "Refresh view" })]);
}
function renderMessage() {
  const box = el("div", { class: "workbench-message", role: "status", "aria-live": "polite", "data-testid": "action-status" });
  if (state.error) { box.classList.add("is-error"); box.textContent = state.error; }
  else if (state.notice) box.textContent = state.notice;
  else box.hidden = true;
  return box;
}
function renderHistory() {
  return el("nav", { class: "decision-history", "data-testid": "decision-chooser", "aria-label": "Decision history" }, decisions().map((d) => el("button", { type: "button", class: d.id === state.selectedId ? "history-item active" : "history-item", "data-testid": "decision-option", "data-decision-id": d.id, "aria-pressed": String(d.id === state.selectedId), onClick: () => { state.selectedId = d.id; render(); } }, [el("strong", { text: d.title }), el("small", { text: d.status })])));
}
function block(decision, type) { return decision.blocks.find((b) => b.type === type); }
function renderDecision(decision) {
  const view = el("article", { class: "focused-decision", "data-testid": "decision-view", "data-decision-id": decision.id });
  view.append(el("header", { class: "decision-hero" }, [el("p", { class: "eyebrow", text: "Decision workbench" }), el("div", { class: "hero-title" }, [el("h1", { text: decision.title }), el("span", { class: `disk-state ${decision.status}`, text: decision.status.replaceAll("_", " ") })]), el("p", { class: "executive-conclusion", "data-testid": "executive-conclusion", text: decision.conclusion })]));
  view.append(renderComparison(block(decision, "comparison")));
  view.append(renderProof(block(decision, "proof")));
  view.append(renderEvidence(block(decision, "evidence"), block(decision, "warning")));
  view.append(renderActions(decision, block(decision, "actions")));
  return view;
}
function renderComparison(block) {
  const section = el("section", { class: "comparison-section", "data-testid": "candidate-comparison" }, [el("h2", { text: block.title })]);
  const table = el("table", { class: "comparison-table" });
  table.append(el("thead", {}, el("tr", {}, [el("th", { text: "Candidate" }), el("th", { text: "Gross margin" }), el("th", { text: "Headroom" }), el("th", { text: "Status" })])));
  const body = el("tbody");
  for (const candidate of block.candidates) body.append(el("tr", { "data-testid": "candidate", "data-candidate-id": candidate.id, "data-verdict": candidate.verdict }, [el("th", { scope: "row" }, el("span", { class: "candidate-identity" }, [el("strong", { text: candidate.name }), el("small", { text: candidate.label })])), el("td", { "data-label": "Gross margin", text: format(candidate.gross_margin, "%") }), el("td", { "data-label": "Headroom", text: format(candidate.headroom, "pt") }), el("td", { "data-label": "Status" }, el("span", { class: `verdict ${candidate.verdict}`, text: candidate.verdict === "ok" ? "Eligible" : "Hold" }))]));
  table.append(body); section.append(table); return section;
}
function compactId(value) {
  if (!value) return "Pending commit";
  const text = String(value);
  return text.length > 24 ? `${text.slice(0, 14)}…${text.slice(-6)}` : text;
}
function renderProof(proof) {
  const section = el("section", { class: "decision-proof", "data-testid": "decision-proof", "aria-label": "Decision proof" });
  section.append(el("div", { class: "proof-heading" }, [el("div", {}, [el("p", { class: "eyebrow", text: "Different perspectives. One measurable truth." }), el("h2", { text: proof.title })]), el("p", { class: "proof-summary", text: proof.summary })]));
  const track = el("ol", { class: "proof-track" });
  for (const item of proof.items) {
    track.append(el("li", { class: `proof-step ${item.status}`, "data-testid": `proof-${item.id}`, "data-proof-status": item.status }, [el("span", { class: "proof-marker", "aria-hidden": "true" }), el("div", { class: "proof-copy" }, [el("span", { class: "proof-status", text: item.status }), el("strong", { text: item.label }), el("small", { text: item.detail })])]));
  }
  section.append(track);
  const record = proof.record;
  section.append(el("dl", { class: "proof-record", "data-testid": "proof-record" }, [
    el("div", {}, [el("dt", { text: "Source fingerprint" }), el("dd", { title: record.source_fingerprint || "", text: compactId(record.source_fingerprint) })]),
    el("div", {}, [el("dt", { text: "Evaluator revision" }), el("dd", { title: record.evaluator_revision || "", text: compactId(record.evaluator_revision) })]),
    el("div", {}, [el("dt", { text: "Input cutoff" }), el("dd", { text: record.cutoff_seq === null ? "Collecting" : `#${record.cutoff_seq}` })]),
    el("div", {}, [el("dt", { text: "Ledger sequence" }), el("dd", { text: record.ledger_seq === null ? "Not committed" : `#${record.ledger_seq}` })]),
    el("div", {}, [el("dt", { text: "Closed by" }), el("dd", { text: record.closed_by || "Pending" })]),
    el("div", {}, [el("dt", { text: "Agent provenance" }), el("dd", { text: record.agent_count ? `${record.agent_count} declared` : "Not declared" })]),
  ]));
  return section;
}
function renderEvidence(evidence, warning) {
  const section = el("section", { class: "evidence-section" }, [el("div", {}, [el("p", { class: "eyebrow", text: evidence.title }), ...evidence.items.map((item) => el("div", { class: "evidence-item", "data-testid": "claim" }, [el("strong", { text: item.type || "evidence" }), el("p", { text: item.body }), el("small", { text: (item.refs || []).join(" · ") })]))]), el("aside", { class: "risk-boundary", "data-testid": "risk-boundary" }, [el("p", { class: "eyebrow", text: warning.title }), el("p", { text: warning.body }), el("small", { text: (warning.refs || []).join(" · ") })])]);
  return section;
}
function renderActions(decision, actions) {
  const section = el("section", { class: "human-gate", "data-testid": "gate-panel" }, [el("div", { class: "gate-title" }, [el("div", {}, [el("p", { class: "eyebrow", text: "Human checkpoint" }), el("h2", { text: actions.title })]), el("p", { text: "Your intent is written to the append-only ledger. Identity is declared locally in this public demo." })])]);
  state.role = actions.role || state.role || "reviewer";
  const role = el("select", { class: "role-select", "data-testid": "role-select", "aria-label": "Acting role" }, [el("option", { value: state.role, text: state.role })]);
  section.append(role);
  const list = el("div", { class: "context-actions" });
  for (const action of actions.items) {
    const testId = action.action_type === "resolve" ? "resolve" : action.action_type === "approve_candidate" ? "approve-candidate" : action.action_type;
    const primary = action.action_type === "resolve" || action.action_type === "approve_candidate";
    list.append(el("div", { class: "context-action" }, [el("button", { type: "button", class: primary ? "primary-action" : "secondary-action", "data-testid": testId, "data-candidate-id": action.candidate_id, disabled: !action.enabled, onClick: () => runAction(decision.id, action), text: action.label })]));
  }
  const reasons = [...new Set(actions.items.filter((action) => !action.enabled && action.reason).map((action) => action.reason))];
  section.append(list);
  if (reasons.length) section.append(el("p", { class: "gate-note", text: reasons.join(" ") }));
  return section;
}
function id() { return window.crypto?.randomUUID?.() || `action-${Date.now()}-${Math.random().toString(16).slice(2)}`; }
async function runAction(decisionId, action) {
  try {
    state.error = null;
    const actionId = id();
    const verdict = action.action_type === "approve_candidate" ? "approve" : action.action_type === "hold" ? "hold" : action.action_type === "request_revision" ? "reject" : undefined;
    const response = await session.sendAction({ version: ACTION_VERSION, action_id: actionId, correlation_id: actionId, decision_id: decisionId, action_type: action.action_type, candidate_id: action.candidate_id, human: { role: state.role, ...(verdict ? { verdict } : {}) } });
    state.notice = response.result?.record
      ? `Resolved as ${response.result.record.chosen_candidate_id}.`
      : response.result?.pending
        ? `Not resolved: ${response.result.pending}.`
        : action.action_type === "approve_candidate"
          ? "Approval recorded. Waiting for Codex to run the deterministic gate."
          : action.action_type === "hold"
            ? "Hold recorded. No further execution will run."
            : "Revision request recorded for the active Codex task.";
    await refresh(false);
  } catch (error) { state.error = error?.message || String(error); render(); }
}
async function refresh(clear = true) { try { if (clear) { state.error = null; state.notice = null; } state.manifest = await session.getDecisionView(); } catch (error) { state.error = error?.message || String(error); } render(); }
async function poll() { try { const next = await session.getDecisionView(); if (JSON.stringify(next) !== JSON.stringify(state.manifest)) { state.manifest = next; render(); } } catch { /* retain last useful view */ } }
function init() { root = document.querySelector('[data-testid="board-root"]'); refresh(); window.setInterval(poll, POLL_MS); }
if (document.readyState === "loading") document.addEventListener("DOMContentLoaded", init); else init();
