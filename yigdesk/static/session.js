export class YigdeskSession {
  constructor(baseUrl = "") {
    this.baseUrl = baseUrl.replace(/\/$/, "");
    this.capabilities = Object.freeze(["read_view", "inspect", "preview_consequence"]);
  }

  async getView() {
    const payload = await this.#request("/api/state");
    return Object.freeze({
      revision: payload.workbook.fingerprint,
      rows: payload.workbook.cells.map((cell) => Object.freeze({ ...cell })),
    });
  }

  async inspectRef(address) {
    if (typeof address !== "string" || !address.includes("!")) {
      throw new Error("A canonical object address is required.");
    }
    return this.#request(`/api/inspect?address=${encodeURIComponent(address)}`);
  }

  async previewConsequence() {
    return this.#request("/api/analyze", { method: "POST", body: "{}" });
  }

  async getCouncilStatus() {
    return this.#request("/api/council-status");
  }

  async startAgentRun(requestToken) {
    const headers = typeof requestToken === "string" && requestToken
      ? { "X-Yigdesk-Agent-Token": requestToken }
      : {};
    return this.#request("/api/agent-runs", { method: "POST", body: "{}", headers });
  }

  async getAgentRun(runId) {
    if (typeof runId !== "string" || !runId.startsWith("arun-")) {
      throw new Error("A canonical agent run id is required.");
    }
    return this.#request(`/api/agent-runs/${encodeURIComponent(runId)}`);
  }

  async #request(path, options = {}) {
    const response = await fetch(`${this.baseUrl}${path}`, {
      ...options,
      headers: {
        "Content-Type": "application/json",
        "X-Yigdesk-Action": "public-preview",
        ...(options.headers || {}),
      },
    });
    const body = await response.json();
    if (!response.ok) throw new Error(body.error || body.code || "Request failed");
    return body;
  }
}
