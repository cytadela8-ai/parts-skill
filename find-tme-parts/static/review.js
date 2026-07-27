const state = {
  details: null,
  detailError: "",
  rows: [],
  selected: null,
  sortDirection: "asc",
  sortKey: "Reference",
};

const table = document.querySelector("#parts-table");
const columns = [
  ["Reference", "Reference"],
  ["Value", "Value"],
  ["Footprint", "KiCad footprint"],
  ["Qty", "Original Qty"],
  ["Final Item Count", "Final Item Count"],
  ["TME Symbol", "Selected TME part"],
  ["Unit Price PLN", "Unit Price PLN"],
  ["Line Total PLN", "Line Total PLN"],
  ["TME Match Status", "Status"],
];
const visibleColumns = new Set(columns.map(([key]) => key));
const numericColumns = new Set([
  "Qty",
  "Final Item Count",
  "Line Total PLN",
  "Unit Price PLN",
]);

async function requestJson(url, options) {
  const response = await fetch(url, options);
  const data = await response.json();
  if (!response.ok) throw new Error(data.error || "The request failed.");
  return data;
}

function compareRows(left, right) {
  const leftValue = left[state.sortKey] || "";
  const rightValue = right[state.sortKey] || "";
  if (!leftValue || !rightValue) return leftValue ? -1 : rightValue ? 1 : 0;
  const comparison = numericColumns.has(state.sortKey)
    ? Number(leftValue) - Number(rightValue)
    : String(leftValue).localeCompare(String(rightValue));
  return state.sortDirection === "asc" ? comparison : -comparison;
}

function sortedRows() {
  return [...state.rows].sort(compareRows);
}

function rowCell(value, column) {
  const cell = document.createElement("td");
  cell.dataset.column = column;
  cell.hidden = !visibleColumns.has(column);
  cell.textContent = value || "—";
  return cell;
}

function priceCell(value, column) {
  const cell = document.createElement("td");
  cell.dataset.column = column;
  cell.hidden = !visibleColumns.has(column);
  if (!value) {
    cell.textContent = "—";
    return cell;
  }
  const amount = Number(value);
  cell.textContent = `${amount.toLocaleString("pl-PL", {
    maximumFractionDigits: 2,
    minimumFractionDigits: 2,
  })} zł`;
  return cell;
}

function finalLineTotal(row) {
  const price = Number(row["Unit Price PLN"]);
  const count = Number(row["Final Item Count"]);
  return Number.isFinite(price) && Number.isFinite(count) ? String(price * count) : "";
}

function renderRows() {
  table.replaceChildren();
  document.querySelector("#row-count").textContent = `${state.rows.length} parts`;
  for (const row of sortedRows()) {
    table.append(dataRow(row));
    if (row.index === state.selected) table.append(detailRow(row));
  }
}

function dataRow(row) {
  const element = document.createElement("tr");
  element.tabIndex = 0;
  element.setAttribute("aria-expanded", String(row.index === state.selected));
  element.className = "part-row";
  element.append(
    rowCell(row.Reference, "Reference"),
    rowCell(row.Value, "Value"),
    rowCell(row.Footprint, "Footprint"),
    rowCell(row.Qty, "Qty"),
    rowCell(row["Final Item Count"], "Final Item Count"),
    rowCell(row["TME Symbol"], "TME Symbol"),
    priceCell(row["Unit Price PLN"], "Unit Price PLN"),
    priceCell(finalLineTotal(row), "Line Total PLN"),
    statusCell(row),
    actionsCell(row),
  );
  element.addEventListener("click", () => selectRow(row.index));
  element.addEventListener("keydown", (event) => {
    if (event.key === "Enter" || event.key === " ") selectRow(row.index);
  });
  return element;
}

function actionsCell(row) {
  const cell = document.createElement("td");
  cell.dataset.column = "TME Match Status";
  cell.hidden = !visibleColumns.has("TME Match Status");
  cell.className = "row-actions";
  cell.append(rowAction("Approve", row.index, "approve"));
  cell.append(rowAction("Not needed", row.index, "not-needed"));
  return cell;
}

function rowAction(label, index, action) {
  const button = document.createElement("button");
  button.className = action === "approve" ? "row-approve" : "row-not-needed";
  button.textContent = label;
  button.addEventListener("click", async (event) => {
    event.stopPropagation();
    await requestJson(`/api/rows/${index}/${action}`, { method: "POST" });
    await reloadRows();
  });
  return button;
}

function statusCell(row) {
  const cell = document.createElement("td");
  const status = document.createElement("span");
  const statusName = (row["TME Match Status"] || "unknown").replaceAll(" ", "_");
  status.className = `status status-${statusName}`;
  status.textContent = row["TME Match Status"] || "unassigned";
  cell.append(status);
  return cell;
}

function detailRow(row) {
  const rowElement = document.createElement("tr");
  rowElement.className = "detail-row";
  const cell = document.createElement("td");
  cell.colSpan = visibleColumns.size + 1;
  cell.append(detailPanel(row));
  rowElement.append(cell);
  return rowElement;
}

function detailPanel(row) {
  const panel = document.createElement("section");
  panel.className = "inline-details";
  panel.append(aiComment(row));
  const tmeDetails = document.createElement("section");
  tmeDetails.className = "tme-details";
  if (state.detailError) {
    tmeDetails.append(detailMessage(state.detailError));
  } else if (state.details) {
    tmeDetails.append(productSummary(state.details));
    tmeDetails.append(parameterList(state.details.parameters || []));
  } else {
    tmeDetails.append(detailMessage("Loading current TME details…"));
  }
  panel.append(tmeDetails, kicadPanel(row), reviewControls(row));
  return panel;
}

function aiComment(row) {
  const callout = document.createElement("section");
  callout.className = "ai-comment";
  const label = document.createElement("strong");
  label.textContent = "AI review note";
  const note = document.createElement("p");
  note.textContent = row["TME Match Notes"] || "No AI review note was supplied.";
  callout.append(label, note);
  return callout;
}

function kicadPanel(row) {
  const panel = document.createElement("section");
  panel.className = "kicad-details";
  const heading = document.createElement("h2");
  heading.textContent = "KiCad cross-check";
  const values = [
    ["References", row.Reference],
    ["Description", row.Opis],
    ["Value", row.Value],
    ["Footprint", row.Footprint],
  ];
  const list = document.createElement("dl");
  for (const [label, value] of values) {
    const name = document.createElement("dt");
    name.textContent = label;
    const detail = document.createElement("dd");
    detail.textContent = value || "—";
    list.append(name, detail);
  }
  panel.append(heading, list);
  return panel;
}

function productSummary(product) {
  const summary = document.createElement("div");
  summary.className = "product-summary";
  if (product.photo_url) {
    const photo = document.createElement("img");
    photo.className = "product-photo";
    photo.src = product.photo_url;
    photo.alt = product.symbol;
    summary.append(photo);
  }
  const copy = document.createElement("div");
  const heading = document.createElement("h2");
  heading.textContent = product.symbol;
  const description = document.createElement("p");
  description.textContent = product.description || "No TME description supplied.";
  const link = document.createElement("a");
  link.href = product.product_url;
  link.target = "_blank";
  link.rel = "noreferrer";
  link.textContent = "Open at TME";
  copy.append(heading, description, link);
  summary.append(copy);
  return summary;
}

function parameterList(parameters) {
  const details = document.createElement("details");
  details.open = true;
  const summary = document.createElement("summary");
  summary.textContent = "Full TME properties";
  const list = document.createElement("dl");
  for (const parameter of parameters) {
    const name = document.createElement("dt");
    name.textContent = parameter.name;
    const value = document.createElement("dd");
    value.textContent = parameter.value;
    list.append(name, value);
  }
  details.append(summary, list);
  return details;
}

function countEditor(row) {
  const form = document.createElement("form");
  form.className = "editor";
  const count = row["Final Item Count"];
  form.innerHTML = [
    `<label>Final Item Count <input min="1" required type="number" value="${count}"></label>`,
    "<button>Save count</button>",
  ].join("");
  form.addEventListener("submit", (event) => saveCount(event, row.index));
  return form;
}

function substitutionEditor(row) {
  const form = document.createElement("form");
  form.className = "editor";
  form.innerHTML = [
    '<label>Replace with TME code <input placeholder="Paste TME symbol" required></label>',
    "<button>Replace</button>",
  ].join("");
  form.addEventListener("submit", (event) => substitute(event, row.index));
  return form;
}

function reviewControls(row) {
  const controls = document.createElement("section");
  controls.className = "review-controls";
  controls.append(countEditor(row), substitutionEditor(row), approveButton(row));
  return controls;
}

function approveButton(row) {
  const button = document.createElement("button");
  button.className = "approve-button";
  button.textContent = "Approve row";
  button.addEventListener("click", () => approveRow(row.index));
  return button;
}

function detailMessage(text) {
  const message = document.createElement("p");
  message.className = "message";
  message.textContent = text;
  return message;
}

async function selectRow(index) {
  state.selected = state.selected === index ? null : index;
  state.details = null;
  state.detailError = "";
  renderRows();
  if (state.selected === null) return;
  try {
    state.details = await requestJson(`/api/rows/${index}/details`);
  } catch (error) {
    state.detailError = error.message;
  }
  if (state.selected === index) renderRows();
}

async function saveCount(event, index) {
  event.preventDefault();
  const count = event.currentTarget.querySelector("input").value;
  try {
    await requestJson(`/api/rows/${index}/count`, {
      method: "PATCH",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ count }),
    });
    await reloadRows();
  } catch (error) {
    state.detailError = error.message;
    renderRows();
  }
}

async function substitute(event, index) {
  event.preventDefault();
  const symbol = event.currentTarget.querySelector("input").value;
  try {
    const result = await requestJson(`/api/rows/${index}/substitution`, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ symbol }),
    });
    state.details = result.product;
    state.detailError = "";
    await reloadRows();
  } catch (error) {
    state.detailError = error.message;
    renderRows();
  }
}

async function approveRow(index) {
  try {
    await requestJson(`/api/rows/${index}/approve`, { method: "POST" });
    await reloadRows();
  } catch (error) {
    state.detailError = error.message;
    renderRows();
  }
}

async function reloadRows() {
  state.rows = (await requestJson("/api/rows")).rows;
  renderRows();
}

for (const button of document.querySelectorAll(".sort-button")) {
  button.addEventListener("click", () => {
    const key = button.dataset.key;
    state.sortDirection = state.sortKey === key && state.sortDirection === "asc" ? "desc" : "asc";
    state.sortKey = key;
    renderRows();
  });
}

function renderColumnSettings() {
  const settings = document.querySelector("#column-settings");
  for (const [key, label] of columns) {
    const field = document.createElement("label");
    const checkbox = document.createElement("input");
    checkbox.checked = visibleColumns.has(key);
    checkbox.type = "checkbox";
    checkbox.addEventListener("change", () => {
      if (checkbox.checked) visibleColumns.add(key);
      else visibleColumns.delete(key);
      updateColumnVisibility(key);
      renderRows();
    });
    field.append(checkbox, label);
    settings.append(field);
  }
}

function updateColumnVisibility(column) {
  const visible = visibleColumns.has(column);
  for (const cell of document.querySelectorAll(`[data-column="${column}"]`)) {
    cell.hidden = !visible;
  }
}

document.querySelector("#settings-button").addEventListener("click", () => {
  document.querySelector("#settings-dialog").showModal();
});

renderColumnSettings();

reloadRows().catch((error) => {
  table.append(detailMessage(error.message));
});
