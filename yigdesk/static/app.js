// Yigdesk board view + selector-aware human gate.
// Renders the deterministic board projection and lets a human drive the two
// gate ops (cast approval, request resolve) through the BoardSession client.
// All domain labels (questions, metric labels, roles) come from board data;
// this file holds no business vocabulary of its own.
import { BoardSession } from "/session.js";

const session = new BoardSession();
const POLL_MS = 4000;

const state = {
  board: { decisions: {} },
  selectedId: null,
  role: null,
  outcomes: {}, // decisionId -> { pending } | { record, replayed }
  error: null,
};

let root = null;

// One-shot deep link. `?decision_id=<id>` picks that decision on first load even
// when several are open. It is consumed the first time the board is non-empty
// (whether or not it matched) and by any manual choice, so a background poll can
// never re-apply it over the decision the user later selected.
const deepLink = { id: null, pending: true };

/* ------------------------------ DOM helper ------------------------------ */

function el(tag, attrs = {}, kids = []) {
  const node = document.createElement(tag);
  for (const [key, value] of Object.entries(attrs)) {
    if (value === null || value === undefined || value === false) continue;
    if (key === "text") node.textContent = value;
    else if (key === "class") node.className = value;
    else if (key === "onClick") node.addEventListener("click", value);
    else if (key === "onChange") node.addEventListener("change", value);
    else node.setAttribute(key, value === true ? "" : String(value));
  }
  for (const kid of Array.isArray(kids) ? kids : [kids]) {
    if (kid === null || kid === undefined || kid === false) continue;
    node.append(kid instanceof Node ? kid : document.createTextNode(String(kid)));
  }
  return node;
}

/* ------------------------------ selectors ------------------------------- */

function decisions() {
  return (state.board && state.board.decisions) || {};
}

function decisionList() {
  return Object.values(decisions());
}

function requiredRoles(d) {
  return [...new Set((d.policy?.required_approvals || []).map((a) => a.role))];
}

function isResolved(d) {
  return (
    d.status === "resolved" ||
    Boolean(d.resolution) ||
    Boolean(state.outcomes[d.id]?.record)
  );
}

function findMetric(candidate, id) {
  return (candidate.consequence?.metrics || []).find((m) => m.id === id) || null;
}

function metricNumber(candidate, id) {
  const m = findMetric(candidate, id);
  if (!m || m.after === null || m.after === undefined) return null;
  const n = Number(m.after);
  return Number.isNaN(n) ? null : n;
}

// Mirror the backend "max:<metric>" selector: eligible candidates are those
// whose consequence verdict is "ok"; the winner has the greatest metric.after,
// breaking ties on the greater candidate id.
function policyWinner(d, metric) {
  let best = null;
  for (const c of Object.values(d.candidates || {})) {
    if (!c.consequence || c.consequence.verdict !== "ok") continue;
    const value = metricNumber(c, metric);
    if (value === null) continue;
    if (
      best === null ||
      value > best.value ||
      (value === best.value && c.id > best.candidate.id)
    ) {
      best = { candidate: c, value };
    }
  }
  return best ? best.candidate : null;
}

/* -------------------------------- render -------------------------------- */

function readDeepLinkId() {
  try {
    return new URLSearchParams(window.location.search).get("decision_id");
  } catch {
    return null; // no URL parsing available: fall back to the default rules
  }
}

function reconcileSelection(list) {
  if (list.length === 0) {
    state.selectedId = null;
    return; // nothing to match against yet; the deep link stays pending
  }
  const ids = new Set(list.map((d) => d.id));

  if (deepLink.pending) {
    const target = deepLink.id;
    deepLink.pending = false; // one shot, whether or not it named a real decision
    if (target && ids.has(target)) {
      state.selectedId = target; // valid deep link wins, even with several decisions
      return;
    }
  }

  if (list.length === 1) {
    state.selectedId = list[0].id; // exactly one decision auto-selects
    return;
  }
  if (state.selectedId && !ids.has(state.selectedId)) state.selectedId = null;
  // Otherwise keep the current selection (preserved across polls).
}

function render() {
  if (!root) return;
  const list = decisionList();
  reconcileSelection(list);

  root.replaceChildren(renderToolbar(), renderError());

  if (list.length === 0) {
    root.append(
      el("div", { "data-testid": "board-empty", class: "decision-empty" }, [
        el("h3", { text: "No decisions on the board" }),
        el("p", {
          text: "Nothing has been proposed yet. Refresh once a decision is opened.",
        }),
      ]),
    );
    return;
  }

  if (list.length > 1) root.append(renderChooser(list));

  const selected = state.selectedId ? decisions()[state.selectedId] : null;
  if (!selected) {
    root.append(
      el("p", {
        class: "board-hint",
        text: "Choose a decision above to review its candidates and drive the gate.",
      }),
    );
    return;
  }
  root.append(renderDecision(selected));
}

function renderToolbar() {
  return el("div", { class: "board-toolbar" }, [
    el(
      "button",
      {
        "data-testid": "refresh",
        type: "button",
        class: "upload-action",
        onClick: onRefresh,
      },
      [
        el("span", { text: "Refresh board" }),
        el("span", { "aria-hidden": "true", text: "↻" }),
      ],
    ),
  ]);
}

function renderError() {
  const box = el("p", {
    "data-testid": "error",
    class: "upload-status",
    "data-state": "error",
    role: "alert",
    "aria-live": "assertive",
  });
  if (state.error) box.textContent = state.error;
  else box.hidden = true;
  return box;
}

function renderChooser(list) {
  const chooser = el("div", {
    "data-testid": "decision-chooser",
    class: "scenario-switch",
    role: "group",
    "aria-label": "Open decisions",
  });
  for (const d of list) {
    const active = d.id === state.selectedId;
    chooser.append(
      el(
        "button",
        {
          "data-testid": "decision-option",
          "data-decision-id": d.id,
          type: "button",
          class: "scenario-button" + (active ? " is-active" : ""),
          "aria-pressed": String(active),
          onClick: () => onSelectDecision(d.id),
        },
        [
          el("span", {}, [
            el("strong", { text: d.question || d.id }),
            el("small", { text: `${d.decision_type} · ${d.status}` }),
          ]),
        ],
      ),
    );
  }
  return chooser;
}

function renderDecision(d) {
  const view = el("section", {
    "data-testid": "decision-view",
    "data-decision-id": d.id,
    class: "panel decision-panel",
  });
  view.append(
    el("header", { class: "panel-heading" }, [
      el("div", {}, [
        el("p", { class: "eyebrow", text: d.decision_type }),
        el("h2", { text: d.question || d.id }),
      ]),
      el("span", {
        class: "disk-state",
        "data-decision-status": d.status,
        text: d.status,
      }),
    ]),
  );
  view.append(renderCandidates(d));
  view.append(renderClaims(d));
  view.append(renderApprovals(d));
  view.append(renderGate(d));
  return view;
}

function renderCandidates(d) {
  const cands = Object.values(d.candidates || {});
  const wrap = el("div", { class: "candidate-list" });
  wrap.append(
    el("div", { class: "evidence-header" }, [
      el("span", { text: "Candidates" }),
      el("span", { text: `${cands.length} priced` }),
    ]),
  );
  if (cands.length === 0) {
    wrap.append(el("p", { class: "board-hint", text: "No candidates proposed." }));
    return wrap;
  }
  for (const c of cands) wrap.append(renderCandidate(c));
  return wrap;
}

function renderCandidate(c) {
  const verdict = c.consequence ? c.consequence.verdict : "hold";
  const tone = verdict === "ok" ? "ready" : "hold";
  const card = el("article", {
    "data-testid": "candidate",
    "data-candidate-id": c.id,
    class: "candidate",
    "data-verdict": verdict,
  });
  card.append(
    el("div", { class: "candidate-head" }, [
      el("div", {}, [
        el("strong", { text: c.id }),
        c.author ? el("small", { text: ` · ${c.author}` }) : null,
      ]),
      el("span", { class: `verdict ${tone}`, text: verdict }),
    ]),
  );

  const metrics = c.consequence?.metrics || [];
  if (metrics.length) {
    const grid = el("div", { class: "metrics" });
    for (const m of metrics) {
      grid.append(
        el("div", { class: "metric" }, [
          el("span", { text: m.label }),
          el("strong", {
            text: m.value === null || m.value === undefined ? "—" : String(m.value),
          }),
          el("small", { text: m.unit || "" }),
        ]),
      );
    }
    card.append(grid);
  }

  const refs = c.consequence?.evidence_refs || [];
  if (refs.length) {
    card.append(
      el("div", { class: "candidate-evidence" }, [
        el("span", { class: "evidence-label", text: "Evidence: " }),
        ...refs.map((r) => el("code", { text: r })),
      ]),
    );
  }
  return card;
}

function renderClaims(d) {
  const claims = Object.values(d.claims || {});
  const wrap = el("div", { class: "claim-list" });
  wrap.append(
    el("div", { class: "evidence-header" }, [el("span", { text: "Grounded claims" })]),
  );
  if (claims.length === 0) {
    wrap.append(el("p", { class: "board-hint", text: "No grounded claims." }));
    return wrap;
  }
  for (const cl of claims) {
    wrap.append(
      el("div", { "data-testid": "claim", class: "claim" }, [
        el("span", { class: "scope-chip", text: cl.type }),
        el("p", { class: "claim-body", text: cl.body }),
      ]),
    );
  }
  return wrap;
}

function renderApprovals(d) {
  const approvals = d.approvals || [];
  const wrap = el("div", { class: "claim-list" });
  wrap.append(
    el("div", { class: "evidence-header" }, [el("span", { text: "Approvals" })]),
  );
  if (approvals.length === 0) {
    wrap.append(el("p", { class: "board-hint", text: "No approvals recorded." }));
    return wrap;
  }
  for (const approval of approvals) {
    wrap.append(
      el("div", { "data-testid": "approval", class: "claim" }, [
        el("span", { class: "scope-chip", text: approval.verdict }),
        el("p", {
          class: "claim-body",
          text: `${approval.role || "unattributed"} · ${approval.scope}`,
        }),
      ]),
    );
  }
  return wrap;
}

/* --------------------------------- gate --------------------------------- */

function renderGate(d) {
  const resolved = isResolved(d);
  const selector = d.policy?.candidate_selector || "human_selected";
  const gate = el("section", { "data-testid": "gate-panel", class: "gate-panel" });
  gate.append(
    el("div", { class: "evidence-header" }, [
      el("span", { text: "Human gate" }),
      el("span", { text: selector }),
    ]),
  );
  gate.append(renderRoleSelect(d));
  gate.append(
    selector.startsWith("max:")
      ? renderPolicyControls(d, selector.slice(4), resolved)
      : renderHumanSelectControls(d, resolved),
  );
  gate.append(
    el(
      "button",
      {
        "data-testid": "resolve",
        type: "button",
        class: "primary-action",
        disabled: resolved,
        onClick: () => onResolve(d.id),
      },
      [
        el("span", {
          class: "action-label",
          text: resolved ? "Decision resolved" : "Resolve decision",
        }),
      ],
    ),
  );
  gate.append(renderOutcome(d));
  return gate;
}

function renderRoleSelect(d) {
  const roles = requiredRoles(d);
  const options = roles.length ? roles : [""];
  if (!options.includes(state.role)) state.role = options[0];
  const select = el("select", {
    "data-testid": "role-select",
    class: "role-select",
    "aria-label": "Acting role",
    onChange: (event) => {
      state.role = event.target.value;
    },
  });
  for (const r of options) {
    select.append(el("option", { value: r, text: r || "(no attributed role)" }));
  }
  select.value = state.role;
  // The role choices come from the policy, but picking one is local attribution
  // only — nothing here proves who the operator is. Say so next to the control.
  return el("div", { class: "role-field" }, [
    el("label", { class: "role-field-label" }, [
      el("span", { class: "evidence-label", text: "Acting as" }),
      select,
    ]),
    el("small", {
      "data-testid": "role-attribution-note",
      class: "gate-note",
      text:
        "Demo boundary: the role is local attribution recorded with your action, " +
        "not an authenticated identity. Anyone using this board can pick any role.",
    }),
  ]);
}

function renderHumanSelectControls(d, resolved) {
  const cands = Object.values(d.candidates || {});
  const wrap = el("div", { class: "gate-actions" });
  if (cands.length === 0) {
    wrap.append(el("p", { class: "board-hint", text: "No candidates to approve." }));
    return wrap;
  }
  for (const c of cands) {
    const eligible = Boolean(c.consequence && c.consequence.verdict === "ok");
    wrap.append(
      el("div", { class: "gate-row" }, [
        el(
          "button",
          {
            "data-testid": "approve-candidate",
            "data-candidate-id": c.id,
            type: "button",
            class: "upload-action",
            disabled: resolved || !eligible,
            onClick: () => onApprove(d.id, c.id),
          },
          [
            el("span", { text: `Approve ${c.id}` }),
            el("span", { "aria-hidden": "true", text: "→" }),
          ],
        ),
        el("button", {
          "data-testid": "hold-candidate",
          "data-candidate-id": c.id,
          type: "button",
          class: "ghost-button",
          disabled: resolved,
          onClick: () => onHold(d.id, c.id),
          text: "Hold",
        }),
        el("small", { class: "gate-note", text: eligible ? "eligible" : "on hold" }),
      ]),
    );
  }
  return wrap;
}

function renderPolicyControls(d, metric, resolved) {
  const winner = policyWinner(d, metric);
  const wrap = el("div", { class: "gate-actions" });

  const winnerBox = el("div", { "data-testid": "policy-winner", class: "hero-proof" });
  winnerBox.append(el("span", { text: `Policy selector · max:${metric}` }));
  if (winner) {
    const m = findMetric(winner, metric);
    const detail = m ? `${m.value ?? "—"} ${m.unit || ""}`.trim() : "";
    winnerBox.append(el("strong", { text: winner.id }));
    winnerBox.append(el("small", { text: detail ? `${metric} ${detail}` : metric }));
  } else {
    winnerBox.append(el("strong", { text: "No eligible candidate" }));
  }
  wrap.append(winnerBox);

  wrap.append(
    el("div", { class: "gate-row" }, [
      el(
        "button",
        {
          "data-testid": "authorize-policy",
          type: "button",
          class: "upload-action",
          disabled: resolved,
          onClick: () => onApprove(d.id, d.id), // decision-scoped approve
        },
        [
          el("span", { text: "Authorize policy selection" }),
          el("span", { "aria-hidden": "true", text: "→" }),
        ],
      ),
      el("button", {
        "data-testid": "hold-policy",
        type: "button",
        class: "ghost-button",
        disabled: resolved,
        onClick: () => onHold(d.id, d.id),
        text: "Hold",
      }),
    ]),
  );
  return wrap;
}

function renderOutcome(d) {
  const box = el("div", {
    "data-testid": "resolve-outcome",
    class: "resolve-outcome",
    role: "status",
    "aria-live": "polite",
  });
  const outcome = state.outcomes[d.id] || {};
  const record = d.resolution || outcome.record || null;
  const pending = outcome.pending || null;

  if (record) {
    box.setAttribute("data-outcome", "committed");
    box.append(el("p", { class: "outcome-line", text: "Decision resolved." }));
    box.append(
      el("dl", { class: "outcome-record" }, [
        el("dt", { text: "Chosen candidate" }),
        el("dd", { text: record.chosen_candidate_id }),
        el("dt", { text: "Closed by" }),
        el("dd", { text: record.closed_by }),
      ]),
    );
    if (record.rationale) {
      box.append(el("p", { class: "outcome-rationale", text: record.rationale }));
    }
    if (outcome.replayed) {
      box.append(el("small", { class: "gate-note", text: "replayed (idempotent)" }));
    }
  } else if (pending) {
    box.setAttribute("data-outcome", "pending");
    box.append(el("p", { class: "outcome-line", text: "Not resolved yet." }));
    box.append(el("p", { class: "outcome-reason", text: pending }));
  } else {
    box.hidden = true;
  }
  return box;
}

/* ------------------------------ controller ------------------------------ */

function onSelectDecision(id) {
  state.selectedId = id;
  deepLink.pending = false; // an explicit choice always outranks the URL parameter
  state.error = null;
  render();
}

async function onRefresh() {
  try {
    state.error = null;
    state.board = await session.getBoard();
  } catch (err) {
    state.error = errorText(err);
  }
  render();
}

function onApprove(decisionId, scope) {
  return runOp(decisionId, false, () =>
    session.castApproval(decisionId, { verdict: "approve", scope, role: state.role }),
  );
}

function onHold(decisionId, scope) {
  return runOp(decisionId, false, () =>
    session.castApproval(decisionId, { verdict: "hold", scope, role: state.role }),
  );
}

function onResolve(decisionId) {
  return runOp(decisionId, true, () => session.requestResolve(decisionId));
}

async function runOp(decisionId, isResolve, fn) {
  try {
    state.error = null;
    const res = await fn();
    if (res && res.board) state.board = res.board;
    if (isResolve) state.outcomes[decisionId] = (res && res.result) || {};
    else delete state.outcomes[decisionId]; // gate state changed; drop stale pending
  } catch (err) {
    state.error = errorText(err);
  }
  render();
}

function errorText(err) {
  return err && err.message ? err.message : String(err);
}

/* --------------------------------- boot --------------------------------- */

async function pollBoard() {
  try {
    const board = await session.getBoard();
    // Re-render only on real change; selection is preserved by reconcileSelection.
    if (JSON.stringify(board) !== JSON.stringify(state.board)) {
      state.board = board;
      render();
    }
  } catch (err) {
    // Background poll: stay quiet and keep the last good render.
    console.error(err);
  }
}

async function init() {
  root = document.querySelector('[data-testid="board-root"]');
  if (!root) return;
  deepLink.id = readDeepLinkId(); // read once, at load; later polls never re-read it
  try {
    state.board = await session.getBoard();
  } catch (err) {
    state.error = errorText(err);
  }
  render();
  window.setInterval(pollBoard, POLL_MS);
}

if (document.readyState === "loading") {
  document.addEventListener("DOMContentLoaded", init);
} else {
  init();
}
