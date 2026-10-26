"use strict";
const $ = (s) => document.querySelector(s);
const esc = (s) => String(s).replace(/[&<>"']/g, (c) => ({"&":"&amp;","<":"&lt;",">":"&gt;",'"':"&quot;","'":"&#39;"}[c]));

async function api(url, opts) {
  const r = await fetch(url, opts);
  if (r.status === 401) { location.href = "/login"; throw new Error("login required"); }
  const data = await r.json().catch(() => ({}));
  if (!r.ok) throw new Error(data.error || r.statusText);
  return data;
}
function toast(msg) {
  const t = $("#toast"); t.textContent = msg; t.hidden = false;
  clearTimeout(toast.t); toast.t = setTimeout(() => (t.hidden = true), 4000);
}

// ---- tabs
function showTab(name) {
  document.querySelectorAll("#tabs button").forEach((b) => b.classList.toggle("active", b.dataset.tab === name));
  ["run", "results", "inputs"].forEach((t) => ($("#tab-" + t).hidden = t !== name));
  if (name === "results") loadFiles();
  if (name === "inputs") loadInputs();
}
document.querySelectorAll("#tabs button").forEach((b) => b.addEventListener("click", () => showTab(b.dataset.tab)));

// ---- run tab
let busy = false;
async function loadBrands() {
  const { brands } = await api("/api/brands");
  $("#brand-list").innerHTML = brands.map((b) => `
    <label class="row">
      <input type="checkbox" value="${esc(b.key)}" ${b.enabled && b.credentials_present ? "checked" : ""} ${b.credentials_present ? "" : "disabled"}>
      <span class="name">${esc(b.name)}<small>${b.credentials_present ? (b.enabled ? "" : "disabled in brands.yaml (runs if ticked)") : "missing in .env: " + esc(b.missing.join(", "))}</small></span>
    </label>`).join("");
  $("#report-list").innerHTML = brands.filter((b) => b.has_report).map((b) => `
    <div class="row"><span class="name">${esc(b.name)}</span>
    <button data-report="${esc(b.key)}" ${b.credentials_present ? "" : "disabled"}>Run report</button></div>`).join("");
  document.querySelectorAll("[data-report]").forEach((btn) =>
    btn.addEventListener("click", () => start({ kind: "report", brand: btn.dataset.report })));
  setBusy(busy);
}
function setBusy(v) {
  busy = v;
  $("#run-stock").disabled = v;
  document.querySelectorAll("[data-report]").forEach((b) => { if (v) b.disabled = true; });
}
async function start(body) {
  body.show_browser = $("#show-browser").checked;
  try { await api("/api/run", { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify(body) }); }
  catch (e) { toast(e.message); return; }
  logFrom = 0; $("#job-log").textContent = "";
  poll();
}
$("#run-stock").addEventListener("click", () => {
  const brands = [...document.querySelectorAll("#brand-list input:checked")].map((i) => i.value);
  if (!brands.length) return toast("Tick at least one brand");
  start({ kind: "stock", brands });
});

let logFrom = 0, lastJobId = null, pollTimer = null, lastOutput = null;
async function poll() {
  clearTimeout(pollTimer);
  let job;
  try { ({ job } = await api("/api/job?log_from=" + logFrom)); } catch { pollTimer = setTimeout(poll, 4000); return; }
  if (!job) return;
  if (job.id !== lastJobId) { lastJobId = job.id; logFrom = 0; $("#job-log").textContent = ""; }
  $("#job-card").hidden = false;
  $("#job-title").textContent = job.label;
  const st = $("#job-status");
  st.className = "status " + job.status;
  const running = job.status === "running" || job.status === "queued";
  st.textContent = running ? "Running..." : job.status === "done" ? `Done - ${job.rows} rows` : "Failed";
  if (job.log.length) {
    const log = $("#job-log");
    log.textContent += job.log.join("\n") + "\n";
    log.scrollTop = log.scrollHeight;
  }
  logFrom = job.log_len;
  $("#job-errors").innerHTML = job.errors.map((e) => `<li>${esc(e)}</li>`).join("");
  lastOutput = job.output;
  $("#job-open").hidden = running || !job.output;
  setBusy(running);
  if (running) pollTimer = setTimeout(poll, 1500);
}
$("#job-open").addEventListener("click", () => { showTab("results"); });

// ---- results tab
let tableData = { columns: [], rows: [] };
async function loadFiles() {
  const { files } = await api("/api/files");
  const sel = $("#file-select");
  const prev = lastOutput || sel.value;
  sel.innerHTML = files.map((f) => `<option value="${esc(f.name)}">${esc(f.name)} - ${esc(f.modified)}</option>`).join("");
  if (!files.length) { $("#table").innerHTML = ""; $("#table-note").textContent = "No result files yet - run something first."; $("#file-download").hidden = true; return; }
  if (files.some((f) => f.name === prev)) sel.value = prev;
  loadTable();
}
async function loadTable() {
  const name = $("#file-select").value;
  if (!name) return;
  $("#file-download").hidden = false;
  $("#file-download").href = "/download/" + encodeURIComponent(name);
  tableData = await api("/api/file/" + encodeURIComponent(name));
  renderTable();
}
function renderTable() {
  const q = $("#filter").value.trim().toLowerCase();
  const rows = q ? tableData.rows.filter((r) => r.join(" ").toLowerCase().includes(q)) : tableData.rows;
  $("#table").innerHTML = "<thead><tr>" + tableData.columns.map((c) => `<th>${esc(c)}</th>`).join("") + "</tr></thead><tbody>" +
    rows.map((r) => "<tr>" + r.map((c) => `<td>${esc(c)}</td>`).join("") + "</tr>").join("") + "</tbody>";
  $("#table-note").textContent = `${rows.length} of ${tableData.rows.length} rows` + (tableData.truncated ? " (preview capped - download for everything)" : "");
}
$("#file-select").addEventListener("change", loadTable);
$("#filter").addEventListener("input", renderTable);

// ---- SKU lists tab
async function loadInputs() {
  const { inputs } = await api("/api/inputs");
  $("#input-list").innerHTML = inputs.map((i) => `
    <div class="card"><h2>${esc(i.brand)}</h2>
    <p class="muted">${i.exists ? i.rows + " items in current list" : "No list uploaded yet"}</p>
    ${i.exists ? `<a class="btn" href="/inputs/${esc(i.brand)}">Download current</a> ` : ""}
    <label class="btn">Upload replacement CSV<input type="file" accept=".csv,text/csv" data-up="${esc(i.brand)}" hidden></label></div>`).join("");
  document.querySelectorAll("[data-up]").forEach((inp) => inp.addEventListener("change", async () => {
    if (!inp.files[0]) return;
    const fd = new FormData(); fd.append("file", inp.files[0]);
    try { await api("/api/inputs/" + inp.dataset.up, { method: "POST", body: fd }); toast("Uploaded (previous list kept as .bak)"); loadInputs(); }
    catch (e) { toast(e.message); }
  }));
}

loadBrands().then(poll);
