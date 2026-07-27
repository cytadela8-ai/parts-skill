const state = { rows: [], selected: null };

const table = document.querySelector("#parts-table");
const content = document.querySelector("#detail-content");
const empty = document.querySelector("#detail-empty");
const message = document.querySelector("#form-message");

async function requestJson(url, options) {
  const response = await fetch(url, options);
  const data = await response.json();
  if (!response.ok) throw new Error(data.error || "The request failed.");
  return data;
}

function rowCell(value) {
  const cell = document.createElement("td");
  cell.textContent = value || "—";
  return cell;
}

function renderRows() {
  table.replaceChildren();
  document.querySelector("#row-count").textContent = `${state.rows.length} parts`;
  for (const row of state.rows) {
    const element = document.createElement("tr");
    element.tabIndex = 0;
    element.setAttribute("aria-selected", String(row.index === state.selected));
    element.append(rowCell(row.Reference), rowCell(row.Value), rowCell(row["TME Symbol"]));
    element.append(rowCell(row["Final Item Count"]));
    const status = document.createElement("span");
    const statusName = (row["TME Match Status"] || "unknown").replaceAll(" ", "_");
    status.className = `status status-${statusName}`;
    status.textContent = row["TME Match Status"] || "unassigned";
    const statusCell = document.createElement("td");
    statusCell.append(status);
    element.append(statusCell);
    element.addEventListener("click", () => selectRow(row.index));
    element.addEventListener("keydown", (event) => {
      if (event.key === "Enter" || event.key === " ") selectRow(row.index);
    });
    table.append(element);
  }
}

function renderDetails(row, product) {
  empty.hidden = true;
  content.hidden = false;
  document.querySelector("#part-title").textContent = product.symbol || row["TME Symbol"];
  document.querySelector("#part-description").textContent =
    product.description || "No TME description supplied.";
  document.querySelector("#final-count").value = row["Final Item Count"];
  const links = document.querySelector("#part-links");
  links.replaceChildren();
  const externalLinks = [
    ["Open at TME", product.product_url],
    ["Datasheet", product.datasheet_url],
  ];
  for (const [label, url] of externalLinks) {
    if (!url) continue;
    const link = document.createElement("a");
    link.href = url;
    link.target = "_blank";
    link.rel = "noreferrer";
    link.textContent = label;
    links.append(link);
  }
  const properties = document.querySelector("#parameters");
  properties.replaceChildren();
  for (const parameter of product.parameters || []) {
    const name = document.createElement("dt");
    name.textContent = parameter.name;
    const value = document.createElement("dd");
    value.textContent = parameter.value;
    properties.append(name, value);
  }
}

function renderUnselectedDetails(row) {
  empty.hidden = true;
  content.hidden = false;
  document.querySelector("#part-title").textContent = row.Reference;
  document.querySelector("#part-description").textContent =
    "No TME part is selected. Paste a replacement TME code below to continue.";
  document.querySelector("#final-count").value = row["Final Item Count"];
  document.querySelector("#part-links").replaceChildren();
  document.querySelector("#parameters").replaceChildren();
}

async function selectRow(index) {
  state.selected = index;
  message.textContent = "";
  renderRows();
  const row = state.rows[index];
  try {
    const product = await requestJson(`/api/rows/${index}/details`);
    renderDetails(row, product);
  } catch (error) {
    renderUnselectedDetails(row);
    message.textContent = error.message;
  }
}

async function reloadRows() {
  state.rows = (await requestJson("/api/rows")).rows;
  renderRows();
}

document.querySelector("#count-form").addEventListener("submit", async (event) => {
  event.preventDefault();
  try {
    const count = document.querySelector("#final-count").value;
    await requestJson(`/api/rows/${state.selected}/count`, {
      method: "PATCH",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ count }),
    });
    message.textContent = "Final count saved.";
    await reloadRows();
  } catch (error) {
    message.textContent = error.message;
  }
});

document.querySelector("#substitution-form").addEventListener("submit", async (event) => {
  event.preventDefault();
  try {
    const symbol = document.querySelector("#replacement-symbol").value;
    const result = await requestJson(`/api/rows/${state.selected}/substitution`, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ symbol }),
    });
    document.querySelector("#replacement-symbol").value = "";
    message.textContent = "Replacement saved.";
    await reloadRows();
    renderDetails(result.row, result.product);
  } catch (error) {
    message.textContent = error.message;
  }
});

reloadRows().catch((error) => {
  message.textContent = error.message;
});
