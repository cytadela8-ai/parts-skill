/* Stable row IDs govern selection and edits; sorting never changes procedure targets. */
const $ = (id) => document.getElementById(id);
let data = { rows: [], runs: [], jobs: [], events: [] };
let project = "",
  active = "",
  anchor = "",
  descending = false,
  detailRevision = "";
const selected = new Set();
let lastRefresh = 0;
let runRenderKey = "";
let activityExpanded = false;
async function api(path, options = {}) {
  const response = await fetch(path, options);
  const body = await response.json();
  if (!response.ok) throw new Error(body.error || "Request failed.");
  return body;
}
const json = (body, method = "POST") => ({
  method,
  headers: { "Content-Type": "application/json" },
  body: JSON.stringify(body),
});
function safe(action) {
  return async (...args) => {
    try {
      $("error").textContent = "";
      await action(...args);
    } catch (error) {
      $("error").textContent = error.message;
    }
  };
}
function node(tag, text = "") {
  const element = document.createElement(tag);
  element.textContent = text;
  return element;
}
function button(text, action) {
  const element = node("button", text);
  element.onclick = safe(async () => {
    element.disabled = true;
    element.setAttribute("aria-busy", "true");
    try {
      await action();
    } finally {
      element.disabled = false;
      element.removeAttribute("aria-busy");
    }
  });
  return element;
}
function visible() {
  return data.rows
    .filter(
      (row) =>
        row.project === project &&
        JSON.stringify(row).toLowerCase().includes($("filter").value.toLowerCase()),
    )
    .sort(
      (a, b) =>
        (descending ? -1 : 1) *
        a.fields.Reference.localeCompare(b.fields.Reference, undefined, { numeric: true }),
    );
}
async function refresh() {
  data = await api("/api/state");
  lastRefresh = Date.now();
  const projects = [...new Set(data.rows.map((row) => row.project))];
  if (!projects.includes(project)) project = projects.at(-1) || "";
  $("project").replaceChildren(
    ...projects.map((id, index) => {
      const option = node(
        "option",
        `BOM ${index + 1} · ${data.rows.filter((r) => r.project === id).length} rows`,
      );
      option.value = id;
      return option;
    }),
  );
  $("project").value = project;
  render();
}
function render() {
  const rows = visible();
  $("rows").replaceChildren();
  for (const row of rows) {
    const tr = node("tr");
    const checkCell = node("td");
    const check = node("input");
    check.type = "checkbox";
    check.checked = selected.has(row.id);
    check.setAttribute("aria-label", `Select ${row.fields.Reference}`);
    check.onclick = (event) => {
      const range =
        event.shiftKey && anchor
          ? rows.slice(
              Math.min(
                rows.findIndex((r) => r.id === anchor),
                rows.indexOf(row),
              ),
              Math.max(
                rows.findIndex((r) => r.id === anchor),
                rows.indexOf(row),
              ) + 1,
            )
          : [row];
      for (const item of range) {
        if (check.checked) selected.add(item.id);
        else selected.delete(item.id);
      }
      anchor = row.id;
      render();
    };
    checkCell.append(check);
    tr.append(checkCell);
    const ref = node("td");
    ref.append(
      button(row.fields.Reference, () => {
        active = row.id;
        detail();
      }),
    );
    ref.className = "reference";
    ref.firstChild.title = row.fields.Reference;
    const job = data.jobs.find(
      (item) => item.row_id === row.id && ["running", "queued", "interrupted"].includes(item.state),
    );
    if (job) {
      const state = node("small", job.state === "running" ? "↻ Working" : "Ⅱ Queued");
      state.className = "row-progress";
      ref.append(state);
    }
    tr.append(ref);
    const value = node("td", row.fields.Value);
    value.append(node("small", row.fields.Footprint));
    tr.append(value);
    tr.append(
      node("td", row.selection || "—"),
      node("td", row.review ? row.review.replaceAll("_", " ") : "Not reviewed"),
      node("td"),
    );
    tr.lastChild.append(badge(row.compatibility));
    if (row.id === active) tr.classList.add("active-row");
    $("rows").append(tr);
  }
  $("selection").textContent = `${selected.size} selected`;
  $("export").href = `/api/export/${project}.csv`;
  $("basket").href = `/api/export/${project}.csv?basket=1`;
  renderRuns();
  void prefetchProducts();
  $("events").textContent = data.events
    .map(
      (event) =>
        `${event.created} ${event.kind}\n${JSON.stringify(JSON.parse(event.payload), null, 2)}`,
    )
    .join("\n\n");
  if (!document.querySelector("#detail input:focus, #detail textarea:focus")) detail();
}
const productCache = new Map();
const productRequests = new Map();
const productTimes = new Map();
const productErrors = new Map();
const productKey = (row) => `${row.id}:${row.selection}:${row.fields["Final Item Count"]}`;
const statusText = {
  pass: "✓ Pass",
  attention: "⚠ Attention",
  fail: "✕ Fail",
  not_checked: "Not checked",
  unknown: "⚠ Attention",
  not_applicable: "— Not applicable",
};
function badge(status) {
  const item = node("span", statusText[status] || status);
  item.className = `badge ${status === "unknown" ? "attention" : status}`;
  return item;
}
function propertyTable(entries) {
  const table = node("table");
  table.className = "properties";
  const body = node("tbody");
  for (const [name, value] of entries) {
    const row = node("tr");
    const label = node("th", name);
    label.scope = "row";
    const cell = node("td");
    if (typeof value === "string" && /^https?:\/\//.test(value))
      cell.append(externalLink(value, "Open document"));
    else
      cell.textContent =
        value === null || value === undefined || value === "" ? "—" : String(value);
    row.append(label, cell);
    body.append(row);
  }
  table.append(body);
  return table;
}
function externalLink(url, title) {
  const link = node("a", title);
  if (/^https?:\/\//.test(url || "")) link.href = url;
  link.target = "_blank";
  link.rel = "noreferrer";
  return link;
}
async function loadProduct(row) {
  const key = productKey(row);
  if (!row.selection || productRequests.has(key)) return;
  if (productCache.has(key) && Date.now() - productTimes.get(key) < 900000) return;
  const pending = api(`/api/rows/${row.id}/product`)
    .then((result) => {
      productCache.set(key, result.product);
      productTimes.set(key, Date.now());
      productErrors.delete(key);
    })
    .catch((error) => {
      productErrors.set(key, error.message);
    })
    .finally(() => {
      productRequests.delete(key);
      if (active === row.id) renderProduct(row);
    });
  productRequests.set(key, pending);
  if (active === row.id) renderProduct(row);
  await pending;
}
let prefetching = false;
async function prefetchProducts() {
  if (prefetching) return;
  prefetching = true;
  try {
    const candidates = visible()
      .filter((row) => row.selection)
      .slice(0, 5);
    for (const row of candidates) {
      if (!productErrors.has(productKey(row))) await loadProduct(row);
    }
  } finally {
    prefetching = false;
  }
}
function renderProduct(row) {
  const panel = $("supplier");
  if (!panel || panel.dataset.key !== productKey(row)) return;
  panel.replaceChildren(node("h3", "TME selected component"));
  if (!row.selection) {
    panel.append(node("p", "No component selected."));
    return;
  }
  const key = productKey(row);
  const product = productCache.get(key) || row.evidence.product;
  const error = productErrors.get(key);
  if (productRequests.has(key)) panel.append(node("p", "↻ Loading current TME data…"));
  if (error) {
    panel.append(
      node("p", `Could not refresh TME data: ${error}`),
      button("Retry loading", () => loadProduct(row)),
    );
  }
  if (!product) return;
  if (!productCache.has(key)) panel.append(node("small", "Showing the saved supplier snapshot."));
  panel.append(externalLink(product.product_url, "Open TME product & datasheets ↗"));
  panel.append(
    propertyTable([
      ["TME symbol", product.symbol],
      ["Description", product.description],
      ["Manufacturer part number", (product.manufacturer_part_numbers || []).join(", ")],
      ["Unit price, PLN", product.unit_price_pln],
      ["Order total, PLN", product.line_total_pln],
      ["Order quantity", product.order_quantity],
      ["Stock quantity", product.stock_quantity],
      ...(product.parameters || []).map((parameter) => [
        parameter.name,
        (parameter.values || []).map((value) => value.value).join(", "),
      ]),
    ]),
  );
  if (product.attention_note) panel.append(node("p", product.attention_note));
  if (product.photo_url?.startsWith("https://")) {
    const photo = node("img");
    photo.src = product.photo_url;
    photo.alt = product.symbol;
    photo.className = "product-photo";
    panel.append(photo);
  }
}
function renderChecklist(row, panel) {
  const heading = node("h3", "Agent compatibility checks");
  heading.append(badge(row.compatibility));
  panel.append(heading);
  const validation = row.validation;
  if (!validation) {
    panel.append(node("p", "Run validation to get a compatibility checklist."));
    return;
  }
  panel.append(node("p", validation.comment));
  panel.append(
    node(
      "small",
      "Pass: applicable checks passed. Attention: unresolved checks. Fail: a conflict was found. Not-applicable checks are ignored; approval is your decision.",
    ),
  );
  const table = node("table");
  table.className = "checklist";
  const head = node("tr");
  for (const label of ["Check / requirement", "Status", "Agent comment"])
    head.append(node("th", label));
  const thead = node("thead");
  thead.append(head);
  table.append(thead);
  const body = node("tbody");
  for (const check of validation.checks || []) {
    const tr = node("tr");
    const requirement = node(
      "td",
      check.category === "polarity_pinout" ? "Pinout" : check.category.replaceAll("_", " "),
    );
    requirement.append(node("small", check.requirement));
    const outcome = node("td");
    outcome.append(badge(check.outcome));
    const comment = node(
      "td",
      check.evidence
        .replace(
          /(?:product\.)?mapping(?:\.[\w-]+)?:\s*\[\[.*?\]\]/g,
          "Footprint mapping: recorded constraints checked",
        )
        .replaceAll("product.parameters.", "")
        .replaceAll("deterministic_value_check.outcome", "Value comparison")
        .replaceAll("product.", "TME ")
        .replaceAll("bom.", "KiCad "),
    );
    if (check.sources?.length)
      comment.append(
        node(
          "small",
          `Sources: ${check.sources.map((source) => source.replace("product.parameters.", "TME: ").replace("bom.", "KiCad: ").replace("product.", "TME: ")).join(", ")}`,
        ),
      );
    tr.append(requirement, outcome, comment);
    body.append(tr);
  }
  table.append(body);
  panel.append(table);
  if (validation.citation_notes?.length) {
    const details = node("details");
    details.append(node("summary", "Evidence reference notes"));
    details.append(
      node(
        "p",
        "Citation formatting issues are listed here; missing evidence appears in the checklist.",
      ),
    );
    const list = node("ul");
    for (const text of validation.citation_notes) list.append(node("li", text));
    details.append(list);
    panel.append(details);
  }
  const uncertainties = (validation.uncertainties || []).filter(
    (text) => !text.startsWith("Application confidence cap:"),
  );
  if (uncertainties.length) {
    panel.append(node("h4", "Evidence notes & uncertainties"));
    const list = node("ul");
    for (const text of uncertainties) list.append(node("li", text));
    panel.append(list);
  }
}
function duration(seconds) {
  const elapsed = Math.max(0, Math.floor(seconds));
  return elapsed < 60 ? `${elapsed}s` : `${Math.floor(elapsed / 60)}m ${elapsed % 60}s`;
}
function clock(start, end = null) {
  const item = node("span");
  if (start) {
    item.dataset.clock = start;
    if (end) item.dataset.end = end;
    item.textContent = duration((end || Date.now() / 1000) - start);
  } else item.textContent = "—";
  return item;
}
function updateClocks() {
  for (const item of document.querySelectorAll("[data-clock]")) {
    item.textContent = duration(
      (Number(item.dataset.end) || Date.now() / 1000) - Number(item.dataset.clock),
    );
  }
  for (const item of document.querySelectorAll("[data-synced]")) {
    const age = (Date.now() - lastRefresh) / 1000;
    item.textContent =
      age > 10
        ? `Connection interrupted; last status received ${duration(age)} ago.`
        : `Status received ${duration(age)} ago`;
    item.classList.toggle("connection-warning", age > 10);
  }
}
function renderRunActivity(card, run, jobs) {
  const live = ["running", "paused", "interrupted", "canceled"].includes(run.state);
  const current =
    jobs.find((job) => job.state === "running") || jobs.find((job) => job.state === "queued");
  const metadata = jobs.map((job) => JSON.parse(job.result));
  const starts = metadata.map((item) => item.queued_at || item.started_at).filter(Boolean);
  const ends = metadata.map((item) => item.finished_at).filter(Boolean);
  if (starts.length) {
    const elapsed = node("span", "Elapsed: ");
    const ended = jobs.every((job) =>
      ["completed", "failed", "stale", "canceled", "skipped"].includes(job.state),
    );
    elapsed.append(clock(Math.min(...starts), ended && ends.length ? Math.max(...ends) : null));
    card.append(elapsed);
  }
  if (!live || !current) return;
  const detail = JSON.parse(current.result);
  const row = data.rows.find((item) => item.id === current.row_id);
  const activity = node("section");
  activity.className = "run-activity";
  const title = node("strong", row?.fields.Reference || "Selected component");
  title.className = current.state === "running" ? "working" : "";
  activity.append(title);
  let message =
    detail.progress?.message || "Queued; waiting for earlier rows or another run to finish.";
  if (run.state === "paused")
    message = `Paused. ${current.state === "running" ? "An in-flight request may still finish." : "Resume to continue."}`;
  if (run.state === "interrupted") message = "Interrupted by app restart. Resume to continue.";
  if (run.state === "canceled")
    message = "Canceled. Finishing any in-flight work; results will not replace the row.";
  activity.append(node("p", message));
  if (detail.progress?.at && current.state === "running" && run.state === "running") {
    const waiting = node("small", "Time in this step: ");
    waiting.append(clock(detail.progress.at));
    activity.append(
      waiting,
      node(
        "small",
        "TME requests can wait up to 30s each; model requests up to 60s. A row can need several requests.",
      ),
    );
  }
  if (detail.activity?.length > 1) {
    const history = node("details");
    history.append(node("summary", "Recent steps"));
    history.open = activityExpanded;
    history.ontoggle = () => {
      activityExpanded = history.open;
    };
    const list = node("ol");
    for (const item of detail.activity.slice(-5))
      list.append(node("li", `${new Date(item.at * 1000).toLocaleTimeString()} ${item.message}`));
    history.append(list);
    activity.append(history);
  }
  const synced = node("small");
  synced.dataset.synced = "true";
  activity.append(synced);
  card.append(activity);
  updateClocks();
}

function renderRowActions(card, jobs) {
  const details = node("details");
  details.className = "run-row-actions";
  details.open = jobs.some((job) => job.state === "skipped");
  const planned = jobs.every((job) => JSON.parse(job.result).procedure);
  details.append(
    node(
      "summary",
      planned ? `All ${jobs.length} selected rows` : `${jobs.length} recorded rows (older run)`,
    ),
  );
  const names = {
    search: "Search",
    validate: "Validate existing selection",
    both: "Search then validate",
    skip: "Skipped",
  };
  const entries = jobs.map((job) => {
    const metadata = JSON.parse(job.result);
    const row = data.rows.find((item) => item.id === job.row_id);
    return [
      row?.fields.Reference || job.row_id,
      `${names[metadata.procedure] || "Procedure"}: ${job.state}. ${metadata.reason || job.error || ""}`,
    ];
  });
  details.append(propertyTable(entries));
  card.append(details);
}

function selectionCoverage(jobs) {
  if (!jobs.every((job) => JSON.parse(job.result).procedure)) {
    return "original selection/skip counts unavailable for this older run";
  }
  return `${jobs.filter((job) => job.state === "skipped").length} skipped (${jobs.length} selected)`;
}
function renderRuns() {
  const projectIds = new Set(
    data.rows.filter((row) => row.project === project).map((row) => row.id),
  );
  const jobs = data.jobs.filter((job) => projectIds.has(job.row_id));
  const ids = new Set(jobs.map((job) => job.run));
  const runs = data.runs.filter((run) => ids.has(run.id));
  const spend = data.spend[project] || 0;
  $("spend").textContent = `BOM inference spend: $${spend.toFixed(5)}`;
  const run =
    runs.find((item) => ["running", "paused", "interrupted"].includes(item.state)) || runs[0];
  if (!run) {
    $("runs").replaceChildren();
    runRenderKey = "";
    return;
  }
  const runJobs = data.jobs.filter((job) => job.run === run.id);
  const signature = JSON.stringify([project, run, runJobs]);
  if (signature === runRenderKey) return;
  runRenderKey = signature;
  $("runs").replaceChildren();
  const eligible = runJobs.filter((job) => job.state !== "skipped");
  const finished = eligible.filter((job) =>
    ["completed", "failed", "stale", "canceled"].includes(job.state),
  ).length;
  const failed = runJobs.filter((job) => job.state === "failed").length;
  const names = {
    search: "Searching TME",
    validate: "Validating selections",
    both: "Searching and validating",
  };
  const icons = { running: "↻", completed: "✓", paused: "Ⅱ", canceled: "■", interrupted: "⚠" };
  const card = node("div");
  card.className = `run-card ${run.state}`;
  const title = node(
    "strong",
    `${icons[run.state] || "⚠"} ${run.state === "completed" ? "Completed" : run.state[0].toUpperCase() + run.state.slice(1)}: ${names[run.procedure]}`,
  );
  card.append(
    title,
    node(
      "span",
      `${finished} of ${eligible.length} rows processed; ${selectionCoverage(runJobs)}${failed ? `; ${failed} failed` : ""}`,
    ),
  );
  const progress = node("progress");
  progress.max = Math.max(1, eligible.length);
  progress.value = finished;
  card.append(progress);
  card.append(
    node(
      "small",
      `$${run.spent.toFixed(5)} spent${run.reserved ? `; up to $${run.reserved.toFixed(5)} pending charges` : ""}`,
    ),
  );
  renderRunActivity(card, run, runJobs);
  renderRowActions(card, runJobs);
  const allowed = {
    pause: run.state === "running",
    resume: ["paused", "interrupted"].includes(run.state),
    cancel: ["running", "paused", "interrupted"].includes(run.state),
  };
  for (const [action, label] of [
    ["pause", "Ⅱ Pause"],
    ["resume", "▶ Resume"],
    ["cancel", "■ Cancel"],
  ]) {
    const control = button(label, async () => {
      await api(`/api/runs/${run.id}/${action}`, json({}));
      await refresh();
    });
    control.disabled = !allowed[action];
    card.append(control);
  }
  for (const job of runJobs.filter((item) => item.error)) card.append(node("small", job.error));
  $("runs").append(card);
  updateClocks();
}
function detail() {
  const row = data.rows.find((item) => item.id === active);
  if (!row) {
    $("detail").replaceChildren(
      node("p", "Select a component to inspect its evidence and review it."),
    );
    return;
  }
  const revision = `${row.id}:${row.revision}`;
  if (detailRevision === revision) return;
  detailRevision = revision;
  const panel = $("detail");
  const title = node("h2", row.fields.Reference);
  title.append(badge(row.compatibility));
  panel.replaceChildren(title);
  const comparison = node("div");
  comparison.className = "comparison";
  const kicad = node("section");
  kicad.append(
    node("h3", "KiCad properties"),
    propertyTable(Object.entries(row.bom || row.fields)),
  );
  const supplier = node("section");
  supplier.id = "supplier";
  supplier.dataset.key = productKey(row);
  comparison.append(kicad, supplier);
  panel.append(comparison);
  renderChecklist(row, panel);
  renderProduct(row);
  void loadProduct(row);
  const editPanel = node("section");
  editPanel.className = "edit-panel";
  editPanel.append(node("h3", "Edit requirements & review"));
  const edits = {};
  for (const field of ["Final Item Count", "Value", "Footprint", "Comment"]) {
    const label = node("label", field);
    const input = node("input");
    input.value = row.fields[field] || "";
    input.oninput = () => {
      edits[field] = input.value;
    };
    label.append(input);
    editPanel.append(label);
  }
  const patch = async (changes) => {
    await api(`/api/rows/${row.id}`, json({ revision: row.revision, ...changes }, "PATCH"));
    await refresh();
  };
  editPanel.append(
    button("Save edits", () => patch({ fields: edits })),
    button("Approve", () => patch({ review: "approved" })),
    button("Not needed", () => patch({ review: "not needed" })),
    button("Allow automated replacement", () => patch({ review: "", unlock: true })),
    node(
      "small",
      "Allow automated replacement keeps this part selected and lets a future search replace it.",
    ),
  );
  const label = node("label", "Replacement TME symbol");
  const symbol = node("input");
  symbol.value = row.selection;
  label.append(symbol);
  editPanel.append(label);
  editPanel.append(
    button("Save replacement", async () => {
      await api(
        `/api/rows/${row.id}/substitute`,
        json({ symbol: symbol.value, revision: row.revision }),
      );
      await refresh();
    }),
  );
  panel.append(editPanel);
  renderMapping(row, panel);
}
function renderMapping(row, panel) {
  const proposal = row.evidence.proposed_mapping;
  if (proposal && Object.values(proposal).some((items) => items.length)) {
    panel.append(
      node("h3", "Proposed footprint constraints"),
      propertyTable(
        Object.entries(proposal).map(([name, items]) => [
          name,
          items.map((item) => item.join(" / ")).join("; "),
        ]),
      ),
    );
    panel.append(
      node(
        "p",
        "Accept these only after comparing them with the KiCad footprint. Acceptance saves this " +
          "row's mapping; it does not approve the component or update global mappings. " +
          "Prior validation and human review are cleared. " +
          "Run validation afterwards, then review the part again.",
      ),
    );
    const accepted = Boolean(row.evidence.mapping_approved);
    const accept = button(
      accepted
        ? "Footprint constraints accepted for this row"
        : "Accept footprint constraints for this row",
      async () => {
        await api(`/api/rows/${row.id}/mapping`, json({ revision: row.revision }));
        await refresh();
      },
    );
    accept.disabled = accepted;
    panel.append(accept);
  }
}

let pendingImport;
$("upload").onsubmit = safe(async (event) => {
  event.preventDefault();
  pendingImport = new FormData(event.target);
  const result = await api("/api/import?preview=1", { method: "POST", body: pendingImport });
  $("import-summary").textContent =
    `${result.rows.length} rows. Original columns and existing review statuses will be preserved. CSV imports do not contain the original API evidence; use database backups for full restoration.`;
  $("import-preview").textContent = result.rows
    .slice(0, 8)
    .map(
      (row) =>
        `${row.fields.Reference}: ${row.fields.Value} / ${row.fields.Footprint} / ${row.verdict || "not searched"}`,
    )
    .join("\n");
  $("import-dialog").showModal();
});
$("confirm-import").onclick = safe(async () => {
  const result = await api("/api/import", { method: "POST", body: pendingImport });
  project = result.project;
  selected.clear();
  $("import-dialog").close();
  await refresh();
});
$("cancel-import").onclick = () => $("import-dialog").close();
$("project").onchange = () => {
  project = $("project").value;
  selected.clear();
  active = "";
  render();
};
$("filter").oninput = render;
$("sort").onclick = () => {
  descending = !descending;
  render();
};
$("all").onclick = () => {
  for (const row of visible()) selected.add(row.id);
  render();
};
$("none").onclick = () => {
  selected.clear();
  render();
};
let pendingRun;
$("preview").onclick = safe(async () => {
  pendingRun = { rows: [...selected], procedure: $("procedure").value };
  const plan = await api("/api/runs/preview", json(pendingRun));
  const eligible = plan.rows.filter((row) => row.procedure !== "skip");
  $("allowance").value = (eligible.length * 0.008).toFixed(3);
  $("run-summary").textContent =
    `${plan.rows.length} selected: ${eligible.length} will run, ${plan.rows.length - eligible.length} skipped. Model: Gemini 2.5 Flash Lite. Maximum $0.008 inference per processed row.`;
  const names = {
    search: "Search TME",
    validate: "Validate existing selection",
    both: "Search then validate",
    skip: "Skip",
  };
  $("run-plan").replaceChildren(
    propertyTable(
      plan.rows.map((row) => [row.reference, `${names[row.procedure]}. ${row.reason}`]),
    ),
  );
  $("run").disabled = !eligible.length;
  $("run").textContent = `Run procedures (${eligible.length} rows)`;
  $("run-dialog").showModal();
});
$("close").onclick = () => $("run-dialog").close();
$("run").onclick = safe(async () => {
  await api(
    "/api/runs",
    json({
      ...pendingRun,
      allowance: Number($("allowance").value),
    }),
  );
  $("run-dialog").close();
  await refresh();
});
setInterval(safe(refresh), 2000);
setInterval(updateClocks, 1000);
safe(refresh)();
