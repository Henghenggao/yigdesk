export class YigGrid extends HTMLElement {
  #session = null;
  #packet = null;
  #view = null;
  #selectedAddress = null;

  set session(value) {
    this.#session = value;
    if (this.isConnected) void this.refresh();
  }

  set packet(value) {
    this.#packet = value;
    this.#render();
  }

  connectedCallback() {
    this.setAttribute("data-yig-state", "loading");
    void this.refresh();
  }

  async refresh() {
    if (!this.#session) {
      this.setAttribute("data-yig-state", "idle");
      return;
    }
    try {
      this.#view = await this.#session.getView();
      this.setAttribute("data-yig-revision", this.#view.revision);
      this.setAttribute("data-yig-state", "ready");
      this.#render();
    } catch (error) {
      this.setAttribute("data-yig-state", "unavailable");
      this.replaceChildren(this.#message(error.message));
    }
  }

  #render() {
    if (!this.#view) return;
    const changed = new Map(
      (this.#packet?.consequence?.evidence_cells || []).map((item) => [item.address, item])
    );
    const table = document.createElement("table");
    const caption = document.createElement("caption");
    caption.className = "visually-hidden";
    caption.textContent = "Read-only workbook objects with on-disk and preview values";
    const head = document.createElement("thead");
    const headRow = document.createElement("tr");
    ["Object", "On disk", "Preview"].forEach((label) => {
      const cell = document.createElement("th");
      cell.scope = "col";
      cell.textContent = label;
      headRow.append(cell);
    });
    head.append(headRow);
    const body = document.createElement("tbody");
    this.#view.rows.forEach((row) => {
      const tr = document.createElement("tr");
      tr.tabIndex = 0;
      tr.dataset.address = row.address;
      tr.setAttribute("aria-label", `${row.label}, on disk ${row.value}`);
      tr.setAttribute("aria-selected", String(row.address === this.#selectedAddress));
      if (row.address === this.#selectedAddress) tr.classList.add("is-selected");
      const object = document.createElement("td");
      const label = document.createElement("strong");
      label.textContent = row.label;
      const address = document.createElement("code");
      address.textContent = row.address;
      object.append(label, address);
      const current = document.createElement("td");
      current.textContent = row.value;
      const preview = document.createElement("td");
      const item = changed.get(row.address);
      preview.textContent = item ? item.after : "—";
      if (item) tr.classList.add("is-affected");
      const select = () => {
        this.#selectedAddress = row.address;
        this.querySelectorAll("tr[data-address]").forEach((candidate) => {
          const selected = candidate.dataset.address === row.address;
          candidate.classList.toggle("is-selected", selected);
          candidate.setAttribute("aria-selected", String(selected));
        });
        this.dispatchEvent(new CustomEvent("yig-selection-change", {
          bubbles: true,
          composed: true,
          detail: { address: row.address, revision: this.#view.revision },
        }));
      };
      tr.addEventListener("click", select);
      tr.addEventListener("keydown", (event) => {
        if (event.key === "Enter" || event.key === " ") {
          event.preventDefault();
          select();
        }
      });
      tr.append(object, current, preview);
      body.append(tr);
    });
    table.append(caption, head, body);
    this.replaceChildren(table);
  }

  #message(text) {
    const message = document.createElement("p");
    message.textContent = text;
    return message;
  }
}

if (!customElements.get("yig-grid")) customElements.define("yig-grid", YigGrid);
