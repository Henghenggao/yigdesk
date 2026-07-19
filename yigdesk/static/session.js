// Thin client for the deterministic board surface. Domain-neutral: no business terms.
// Reads the board projection and posts the two human ops (cast_approval, request_resolve).
export class BoardSession {
  constructor(baseUrl = "") {
    this.baseUrl = baseUrl.replace(/\/$/, "");
  }

  async #json(path, options = {}) {
    const response = await fetch(`${this.baseUrl}${path}`, options);
    const data = await response.json().catch(() => ({}));
    if (!response.ok) {
      const error = new Error(data.error || data.code || `Request failed (${response.status})`);
      error.code = data.code;
      error.status = response.status;
      throw error;
    }
    return data;
  }

  getBoard() {
    return this.#json("/api/board");
  }

  #op(decisionId, kind, payload) {
    return this.#json("/api/board/op", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ decision_id: decisionId, kind, payload }),
    });
  }

  castApproval(decisionId, { verdict, scope, role }) {
    return this.#op(decisionId, "cast_approval", { verdict, scope, role });
  }

  requestResolve(decisionId) {
    return this.#op(decisionId, "request_resolve", {});
  }
}
