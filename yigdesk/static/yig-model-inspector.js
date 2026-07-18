export class YigModelInspector extends HTMLElement {
  #session = null;
  #address = null;
  #packet = null;

  set session(value) {
    this.#session = value;
    if (this.isConnected && this.#address) void this.refresh();
  }

  set selectedRef(value) {
    this.#address = value;
    if (this.isConnected) void this.refresh();
  }

  set packet(value) {
    this.#packet = value;
    if (this.isConnected && this.#address) void this.refresh();
  }

  connectedCallback() {
    this.setAttribute("data-yig-state", "idle");
    this.#renderMessage("Select an object to inspect its source and lineage.");
  }

  async refresh() {
    if (!this.#session || !this.#address) return;
    this.setAttribute("data-yig-state", "loading");
    try {
      const result = await this.#session.inspectRef(this.#address);
      this.setAttribute("data-yig-state", "ready");
      this.#render(result);
    } catch (error) {
      this.setAttribute("data-yig-state", "unavailable");
      this.#renderMessage(error.message);
    }
  }

  #render(result) {
    const list = document.createElement("dl");
    const preview = (this.#packet?.consequence?.evidence_cells || []).find(
      (item) => item.address === result.address
    );
    this.#row(list, "Identity", result.address);
    this.#row(list, "Value", result.value);
    this.#row(list, "Preview", preview?.after || "—");
    this.#row(list, "Formula", result.formula || "Input value");
    this.#row(list, "Precedents", result.precedents.length ? result.precedents.join(" · ") : "None");
    this.#row(list, "Dependents", result.dependents.length ? result.dependents.join(" · ") : "None");
    this.#row(list, "Trust", result.value_verified ? "Engine verified" : "Unknown");
    this.replaceChildren(list);
  }

  #row(list, term, value) {
    const dt = document.createElement("dt");
    const dd = document.createElement("dd");
    dt.textContent = term;
    dd.textContent = value;
    list.append(dt, dd);
  }

  #renderMessage(text) {
    const message = document.createElement("p");
    message.textContent = text;
    this.replaceChildren(message);
  }
}

if (!customElements.get("yig-model-inspector")) {
  customElements.define("yig-model-inspector", YigModelInspector);
}
