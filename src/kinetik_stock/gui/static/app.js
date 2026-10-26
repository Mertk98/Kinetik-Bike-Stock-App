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
let busy = false, brandList = [];
const runnable = (b) => !b.coming_soon && b.credentials_present;
async function loadBrands() {
  ({ brands: brandList } = await api("/api/brands"));
  $("#brand-list").innerHTML = brandList.map((b) => {
    const note = b.coming_soon || b.credentials_present ? "" : "missing in .env: " + esc(b.missing.join(", "));
    return `<div class="row ${b.coming_soon ? "soon" : ""}"><span class="name">${esc(b.name)}<small>${note}</small></span>` +
      (b.coming_soon ? `<span class="badge">In progress</span>` : `<button data-run="${esc(b.key)}" ${runnable(b) ? "" : "disabled"}>Run</button>`) + `</div>`;
  }).join("");
  document.querySelectorAll("[data-run]").forEach((btn) => btn.addEventListener("click", () => start([btn.dataset.run])));
  setBusy(busy);
}
function setBusy(v) {
  busy = v;
  const any = brandList.some(runnable);
  $("#run-all").disabled = v || !any;
  document.querySelectorAll("[data-run]").forEach((b) => {
    const brand = brandList.find((x) => x.key === b.dataset.run);
    b.disabled = v || !(brand && runnable(brand));
  });
}
async function start(brands) {
  const body = { brands };
  try { await api("/api/run", { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify(body) }); }
  catch (e) { toast(e.message); return; }
  logFrom = 0; $("#job-log").textContent = "";
  poll();
}
$("#run-all").addEventListener("click", () => start(brandList.filter(runnable).map((b) => b.key)));

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
  renderResults(job, running);
  lastOutput = job.outputs[0] || null;
  $("#job-open").hidden = running || !job.outputs.length;
  setBusy(running);
  if (running) pollTimer = setTimeout(poll, 1500);
}
$("#job-open").addEventListener("click", () => { showTab("results"); });

// One line per brand: a download link if it produced a file, otherwise why not.
function renderResults(job, running) {
  const html = job.results.map((r) => r.ok
    ? `<div class="row"><span class="name">${esc(r.name)}<small>${r.rows} rows</small></span><a class="btn" href="/download/${encodeURIComponent(r.file)}">Download</a></div>`
    : `<div class="row"><span class="name">${esc(r.name)}<small class="err">${r.error ? "Failed: " + esc(r.error) : "Running..."}</small></span></div>`).join("");
  $("#job-results").innerHTML = html;
  $("#last-run").hidden = !job.results.length;
  $("#last-run h2").textContent = "Last run - " + job.label + (running ? " (running)" : "");
  $("#last-run-results").innerHTML = html;
}

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
