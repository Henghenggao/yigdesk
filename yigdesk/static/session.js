// Thin client for the deterministic board surface. It consumes trusted manifests
// and sends typed human intent through the local adapter, never raw board ops.
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

  getDecisionView() {
    return this.#json("/api/decision-view");
  }

  sendAction(action) {
    return this.#json("/api/agent-actions", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(action),
    });
  }
}
