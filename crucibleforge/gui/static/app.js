/* CrucibleForge GUI — vanilla JS, no dependencies. */
(() => {
  "use strict";
  const $ = (s, r = document) => r.querySelector(s);
  const $$ = (s, r = document) => Array.from(r.querySelectorAll(s));
  const esc = (s) => String(s ?? "").replace(/[&<>"']/g, c => ({"&":"&amp;","<":"&lt;",">":"&gt;",'"':"&quot;","'":"&#39;"}[c]));
  let STATE = null;
  let logSeq = 0;
  let lastJobRunning = false;

  // ---------------------------------------------------------------- http
  async function api(path, opts = {}) {
    const init = { method: opts.method || "GET", headers: {} };
    if (opts.body !== undefined) {
      init.method = "POST";
      init.headers["Content-Type"] = "application/json";
      init.body = JSON.stringify(opts.body);
    }
    const r = await fetch(path, init);
    let data = null;
    try { data = await r.json(); } catch (e) { data = { error: "bad response" }; }
    if (!r.ok) throw new Error(data.error || (r.status + " " + r.statusText));
    return data;
  }
  function toast(msg, err = false) {
    const t = $("#toast");
    t.textContent = msg; t.className = "toast" + (err ? " err" : ""); t.hidden = false;
    clearTimeout(t._h); t._h = setTimeout(() => t.hidden = true, err ? 7000 : 3500);
  }
  const guarded = (fn) => async (...a) => { try { await fn(...a); } catch (e) { toast(e.message, true); } };

  // ---------------------------------------------------------------- tabs
  $$("#tabs button").forEach(b => b.addEventListener("click", () => showTab(b.dataset.tab)));
  function showTab(name) {
    $$("#tabs button").forEach(b => b.classList.toggle("active", b.dataset.tab === name));
    $$(".tab").forEach(t => t.classList.toggle("active", t.id === "tab-" + name));
    location.hash = name;
    if (name === "results") loadReport();
    if (name === "cases") loadCases();
  }

  // --------------------------------------------------------------- state
  async function refresh() {
    STATE = await api("/api/state");
    $("#revision").textContent = STATE.revision;
    $("#paths").textContent = `config: ${STATE.config_path} · results: ${STATE.results_dir}` +
      (STATE.legacy ? " · (v2 registry auto-upgraded — run `crucibleforge config --upgrade`)" : "");
    renderDash(); renderRunForm(); renderModelsTab();
    updateJob(STATE.job);
  }

  function pill(alive) {
    if (alive === true) return `<span class="pill up">UP</span>`;
    if (alive === false) return `<span class="pill down">DOWN</span>`;
    return `<span class="pill unk">?</span>`;
  }

  function renderDash() {
    const pt = $("#provtable tbody");
    pt.innerHTML = STATE.providers.map(p => p.error
      ? `<tr><td>${esc(p.name)}</td><td colspan=4 class="mono">${esc(p.error)}</td></tr>`
      : `<tr><td><b>${esc(p.name)}</b></td><td>${esc(p.type)}</td><td class="mono">${esc(p.base_url)}</td>
         <td class="mono">${p.api_key_env ? esc("$" + p.api_key_env) + (p.key_set ? " ✓" : ' <span class="pill down">not set</span>') : "—"}</td>
         <td>${pill(p.alive)}</td></tr>`).join("");
    const j = STATE.judge;
    $("#judgeinfo").innerHTML = (j.candidates || []).length
      ? `<ol style="margin:.2rem 0 .2rem 1.2rem">${j.candidates.map(c => `<li class="mono">${esc(c.model_id)} <span class="muted">@ ${esc(c.provider)}${c.thinking ? " · thinking" : ""}</span></li>`).join("")}</ol>
         <span class="muted">temperature ${j.temperature ?? 0.1} · samples ${j.samples ?? 1} · max_tokens ${j.max_tokens ?? "-"}</span>`
      : `<span class="pill down">no judge candidates</span> — add one on the Models tab.`;
    const ct = $("#casetable tbody");
    ct.innerHTML = Object.entries(STATE.cases).map(([k, v]) => `<tr><td>${esc(k)}</td><td>${v.total}</td><td>${v.hard}</td><td>${v.smoke}</td></tr>`).join("") +
      `<tr><th>total</th><th>${STATE.n_cases}</th><th>${Object.values(STATE.cases).reduce((a, v) => a + v.hard, 0)}</th><th>${Object.values(STATE.cases).reduce((a, v) => a + v.smoke, 0)}</th></tr>`;
    const mt = $("#dashmodels tbody");
    mt.innerHTML = STATE.models.map(m => `<tr class="${m.enabled ? "" : "muted"}"><td><b>${esc(m.name)}</b>${m.enabled ? "" : " (off)"}</td><td>${esc(m.provider)}</td>
      <td class="mono">${esc(m.model_id)}</td><td class="mono">${m.context_length ?? ""}</td>
      <td class="mono">${m.price ? `${m.price.input}/${m.price.output}` : "—"}</td>
      <td>${STATE.results_models.includes(m.name) ? `<a href="#" data-drill="${esc(m.name)}">rows</a>` : "—"}</td></tr>`).join("");
    $$("[data-drill]", mt).forEach(a => a.addEventListener("click", (e) => { e.preventDefault(); showTab("results"); $("#drillmodel").value = a.dataset.drill; drill(); }));
  }

  // ------------------------------------------------------------ providers
  $("#refreshprov").addEventListener("click", guarded(async () => {
    $("#refreshprov").disabled = true;
    try { await api("/api/providers?refresh=1"); await refresh(); } finally { $("#refreshprov").disabled = false; }
  }));

  // -------------------------------------------------------------- run tab
  function renderRunForm() {
    const rm = $("#runmodels");
    const prev = new Set($$("input", rm).filter(i => i.checked).map(i => i.value));
    rm.innerHTML = STATE.models.map(m => `<label class="chk"><input type="checkbox" name="model" value="${esc(m.name)}" ${prev.size ? (prev.has(m.name) ? "checked" : "") : (m.enabled ? "checked" : "")}>
      ${esc(m.name)} <span class="sub">${esc(m.provider)}</span></label>`).join("");
    const rc = $("#runcats");
    const prevc = new Set($$("input", rc).filter(i => i.checked).map(i => i.value));
    rc.innerHTML = STATE.categories.map(c => `<label class="chk"><input type="checkbox" name="cat" value="${esc(c)}" ${prevc.size ? (prevc.has(c) ? "checked" : "") : "checked"}>
      ${esc(c)} <span class="sub">${STATE.cases[c]?.total ?? 0}</span></label>`).join("");
    const ps = $("#profilesel"); const pcur = ps.value;
    ps.innerHTML = `<option value="">none — pick categories/tiers below</option>` +
      (STATE.profiles || []).map(p => `<option value="${esc(p.name)}">${esc(p.name)} — ${p.n_cases} cases</option>`).join("");
    ps.value = pcur;
    ps.onchange = () => { const p = (STATE.profiles || []).find(x => x.name === ps.value);
      $("#profilehint").textContent = p ? p.description : "";
      if (p) { $$("#runcats input").forEach(i => i.checked = p.categories.includes(i.value)); } };
    const js = $("#judgesel"); const cur = js.value;
    js.innerHTML = `<option value="">auto (first available candidate)</option>` +
      (STATE.judge.candidates || []).map(c => `<option value="${esc(c.provider + ":" + c.model_id)}">${esc(c.model_id)} @ ${esc(c.provider)}</option>`).join("");
    js.value = cur;
    $("#samples").value = STATE.judge.samples ?? 1;
    const dm = $("#drillmodel"); const dcur = dm.value;
    dm.innerHTML = `<option value="">drill-down: pick a model…</option>` + STATE.results_models.map(m => `<option>${esc(m)}</option>`).join("");
    dm.value = dcur;
  }
  $("#selall").addEventListener("click", () => $$("#runmodels input").forEach(i => { i.checked = STATE.models.find(m => m.name === i.value)?.enabled; }));
  $("#selnone").addEventListener("click", () => $$("#runmodels input").forEach(i => i.checked = false));

  function runSelection() {
    return {
      models: $$("#runmodels input:checked").map(i => i.value),
      categories: $$("#runcats input:checked").map(i => i.value),
      difficulty: $$("input[name=diff]:checked").map(i => i.value),
      profile: $("#profilesel").value || null,
      smoke: $("#smoke").checked, fresh: $("#fresh").checked,
      then_judge: $("#thenjudge").checked, no_link_check: $("#nolink").checked,
      judge: $("#judgesel").value || null,
      samples: parseInt($("#samples").value || "1", 10),
    };
  }
  $("#startrun").addEventListener("click", guarded(async () => {
    const sel = runSelection();
    if (!sel.models.length) throw new Error("select at least one model");
    if (!sel.categories.length) throw new Error("select at least one category");
    if (sel.fresh && !confirm("Fresh run: archive prior transcripts for the selected models?")) return;
    const r = await api("/api/run", { body: sel });
    toast(`started: ${r.models.length} model(s) × ${r.n_cases} cases`);
    updateJob(r.job);
  }));
  $("#judgeonly").addEventListener("click", guarded(async () => {
    const sel = runSelection();
    const r = await api("/api/judge", { body: { models: sel.models, samples: sel.samples, judge: sel.judge } });
    toast("judging pending rows"); updateJob(r.job);
  }));
  $("#reportonly").addEventListener("click", guarded(async () => {
    await api("/api/report", { body: {} }); toast("report re-rendered"); showTab("results");
  }));
  $("#pairwisebtn").addEventListener("click", guarded(async () => {
    const sel = runSelection();
    if (sel.models.length < 2) throw new Error("pairwise needs 2+ models selected");
    const cats = sel.categories.filter(c => c === "rp" || c === "nsfw");
    const r = await api("/api/pairwise", { body: { models: sel.models, categories: cats.length ? cats : ["rp", "nsfw"], judge: sel.judge } });
    toast("pairwise started"); updateJob(r.job);
  }));
  $("#stopbtn").addEventListener("click", guarded(async () => { await api("/api/stop", { body: {} }); toast("stop requested — finishing in-flight case"); }));
  $("#clearlog").addEventListener("click", () => { $("#log").textContent = ""; });

  // ---------------------------------------------------------- job + log
  function updateJob(job) {
    const dot = $("#jobdot"), txt = $("#jobtext"), stop = $("#stopbtn");
    if (!job) return;
    if (job.running) {
      dot.className = "dot running"; txt.textContent = `${job.kind} running…`; stop.hidden = false;
      $("#startrun").disabled = true;
    } else {
      $("#startrun").disabled = false; stop.hidden = true;
      if (job.error) { dot.className = "dot error"; txt.textContent = `${job.kind}: ${job.error}`; }
      else if (job.kind) { dot.className = "dot done"; txt.textContent = `${job.kind} finished`; }
      else { dot.className = "dot idle"; txt.textContent = "idle"; }
    }
    if (lastJobRunning && !job.running) {
      toast(job.error ? `${job.kind} failed: ${job.error}` : `${job.kind} finished`, !!job.error);
      refresh(); if ($("#tab-results").classList.contains("active")) loadReport();
    }
    lastJobRunning = !!job.running;
  }
  async function pollLog() {
    try {
      const r = await api(`/api/log?since=${logSeq}`);
      if (r.lines.length) {
        const pre = $("#log");
        const atBottom = pre.scrollHeight - pre.scrollTop - pre.clientHeight < 40;
        for (const ln of r.lines) {
          const span = document.createElement("span");
          span.textContent = ln + "\n";
          if (/ ERROR /.test(ln)) span.className = "err"; else if (/ WARNING /.test(ln)) span.className = "warn";
          pre.appendChild(span);
        }
        while (pre.childNodes.length > 3000) pre.removeChild(pre.firstChild);
        if (atBottom) pre.scrollTop = pre.scrollHeight;
        logSeq = r.seq;
      }
      updateJob(r.job);
    } catch (e) { /* server away — keep polling */ }
    setTimeout(pollLog, lastJobRunning ? 1000 : 2500);
  }

  // -------------------------------------------------------------- results
  async function loadReport() {
    const r = await api("/api/report");
    $("#report").innerHTML = r.html;
    const pw = await api("/api/pairwise");
    $("#pairwisecard").hidden = !pw.md; if (pw.md) $("#pairwise").innerHTML = pw.html;
  }
  $("#drillgo").addEventListener("click", guarded(drill));
  async function drill() {
    const label = $("#drillmodel").value; if (!label) throw new Error("pick a model");
    const r = await api(`/api/rows?model=${encodeURIComponent(label)}&failed=${$("#drillfailed").checked ? 1 : 0}`);
    $("#drillcard").hidden = false;
    $("#drillcount").textContent = `${label}: ${r.n} rows`;
    const el = $("#drill");
    el.innerHTML = r.rows.map(row => {
      const grade = row.grade || ((row.judge && !row.judge.judge_failed) ? "judged" : (row.judge ? "judge-fail" : "—"));
      const j = row.judge && row.judge.scores ? Object.entries(row.judge.scores).filter(([k]) => k !== "note").map(([k, v]) => `${k}=${v}`).join(" ") : "";
      const m = row.metrics || {};
      return `<details class="drillrow"><summary>
        <span class="pill ${esc(row.grade || "")}">${esc(grade)}</span>
        <b>${esc(row.case_id)}</b> <span class="muted">${esc(row.category)} · ${esc(row.difficulty)} · r${row.repeat}${row.turn ? " t" + row.turn : ""}</span>
        <span class="muted">${esc(row.grade_detail || (row.judge && row.judge.scores && row.judge.scores.note) || "")}</span>
        <span class="muted" style="margin-left:auto">${m.tok_per_s ? m.tok_per_s.toFixed(1) + " tok/s" : ""} ${m.completion_tokens ? m.completion_tokens + " tok" : ""} ${row.truncated ? "· TRUNCATED" : ""}</span>
      </summary><div class="body">
        ${row.system ? `<div><div class="lbl">system</div><pre>${esc(row.system)}</pre></div>` : ""}
        <div><div class="lbl">prompt</div><pre>${esc(row.prompt)}</pre></div>
        <div><div class="lbl">response</div><pre>${esc(row.response || "(empty)")}</pre></div>
        ${row.tool_calls && row.tool_calls.length ? `<div><div class="lbl">tool calls</div><pre>${esc(JSON.stringify(row.tool_calls, null, 1))}</pre></div>` : ""}
        ${row.reasoning ? `<details><summary class="lbl">reasoning (${row.reasoning.length} chars)</summary><pre>${esc(row.reasoning.slice(0, 6000))}</pre></details>` : ""}
        ${j ? `<div><div class="lbl">judge</div><pre>${esc(j)}${row.judge.scores.note ? "\n" + esc(row.judge.scores.note) : ""}</pre></div>` : ""}
      </div></details>`;
    }).join("") || `<p class="muted">no rows</p>`;
  }

  // --------------------------------------------------------- models tab
  function renderModelsTab() {
    const pe = $("#provedit tbody");
    pe.innerHTML = STATE.providers.map(p => `<tr><td><b>${esc(p.name)}</b></td><td>${esc(p.type || "")}</td><td class="mono">${esc(p.base_url || p.error || "")}</td>
      <td class="mono">${p.api_key_env ? "$" + esc(p.api_key_env) : "—"}</td>
      <td><button class="small ghost" data-ptest="${esc(p.name)}">test</button> <button class="small ghost" data-pedit="${esc(p.name)}">edit</button> <button class="small ghost" data-pdel="${esc(p.name)}">✕</button></td></tr>`).join("");
    $$("[data-ptest]", pe).forEach(b => b.addEventListener("click", guarded(async () => {
      b.disabled = true; b.textContent = "…";
      try { const r = await api("/api/providers/test", { body: { name: b.dataset.ptest } });
        toast(`${b.dataset.ptest}: ${r.alive ? "UP" : "DOWN"}${r.alive ? `, ${r.n_models} models listed` : ""}`, !r.alive); await refresh(); }
      finally { b.disabled = false; b.textContent = "test"; }
    })));
    $$("[data-pedit]", pe).forEach(b => b.addEventListener("click", () => {
      const p = STATE.providers.find(x => x.name === b.dataset.pedit); const f = $("#provform");
      f.name.value = p.name; f.type.value = p.type; f.base_url.value = p.base_url; f.api_key_env.value = p.api_key_env || ""; f.concurrency.value = p.concurrency || 1;
      f.closest("details").open = true; f.name.focus();
    }));
    $$("[data-pdel]", pe).forEach(b => b.addEventListener("click", guarded(async () => {
      if (!confirm(`Delete provider ${b.dataset.pdel}?`)) return;
      await api("/api/providers", { body: { name: b.dataset.pdel, base_url: "x", delete: true } }); await refresh();
    })));
    const provOpts = STATE.providers.filter(p => !p.error).map(p => `<option>${esc(p.name)}</option>`).join("");
    for (const id of ["#modelprov", "#judgeprov"]) { const cur = $(id).value; $(id).innerHTML = provOpts; if (cur) $(id).value = cur; }

    const me = $("#modeledit tbody");
    me.innerHTML = STATE.models.map(m => `<tr class="${m.enabled ? "" : "muted"}">
      <td><input type="checkbox" data-toggle="${esc(m.name)}" ${m.enabled ? "checked" : ""}></td>
      <td><b>${esc(m.name)}</b></td><td>${esc(m.provider)}</td><td class="mono">${esc(m.model_id)}</td>
      <td class="mono">${m.context_length ?? ""}</td><td class="mono">${m.price ? `${m.price.input}/${m.price.output}` : "—"}</td>
      <td><button class="small ghost" data-mdel="${esc(m.name)}">✕</button></td></tr>`).join("");
    $$("[data-toggle]", me).forEach(i => i.addEventListener("change", guarded(async () => {
      await api("/api/models/update", { body: { name: i.dataset.toggle, enabled: i.checked } }); await refresh();
    })));
    $$("[data-mdel]", me).forEach(b => b.addEventListener("click", guarded(async () => {
      if (!confirm(`Remove ${b.dataset.mdel} from the registry? (results are kept)`)) return;
      await api("/api/models/update", { body: { name: b.dataset.mdel, delete: true } }); await refresh();
    })));

    const je = $("#judgeedit tbody");
    const cands = STATE.judge.candidates || [];
    je.innerHTML = cands.map((c, i) => `<tr><td>${i + 1}</td><td>${esc(c.provider)}</td><td class="mono">${esc(c.model_id)}</td><td class="mono">${c.context_length ?? ""}</td><td>${c.thinking ? "yes" : ""}</td>
      <td><button class="small ghost" data-jup="${i}" ${i === 0 ? "disabled" : ""}>↑</button> <button class="small ghost" data-jdel="${i}">✕</button></td></tr>`).join("") || `<tr><td colspan=6 class="muted">none — add one below</td></tr>`;
    const saveCands = guarded(async (list) => { await api("/api/judge-candidates", { body: { candidates: list } }); await refresh(); });
    $$("[data-jup]", je).forEach(b => b.addEventListener("click", () => { const i = +b.dataset.jup; const l = cands.slice(); [l[i - 1], l[i]] = [l[i], l[i - 1]]; saveCands(l); }));
    $$("[data-jdel]", je).forEach(b => b.addEventListener("click", () => { const l = cands.slice(); l.splice(+b.dataset.jdel, 1); saveCands(l); }));
  }
  $("#provform").addEventListener("submit", guarded(async (e) => {
    e.preventDefault(); const f = e.target;
    await api("/api/providers", { body: { name: f.name.value, type: f.type.value, base_url: f.base_url.value, api_key_env: f.api_key_env.value, api_key: f.api_key.value, concurrency: f.concurrency.value, headers: f.headers.value || null } });
    toast("provider saved"); await refresh();
  }));
  $("#provtest").addEventListener("click", guarded(async () => {
    const f = $("#provform"); const out = $("#provtestout"); out.textContent = "testing…";
    // save-then-test keeps a single code path; a failed test still leaves a valid entry to edit
    await api("/api/providers", { body: { name: f.name.value, type: f.type.value, base_url: f.base_url.value, api_key_env: f.api_key_env.value, api_key: f.api_key.value, concurrency: f.concurrency.value, headers: f.headers.value || null } });
    const r = await api("/api/providers/test", { body: { name: f.name.value } });
    out.textContent = r.alive ? `UP — ${r.n_models} model(s) listed${r.models.length ? ": " + r.models.slice(0, 8).join(", ") + (r.models.length > 8 ? " …" : "") : " (no listing endpoint)"}` : "DOWN — unreachable or unauthorized";
    await refresh();
  }));
  $("#discover").addEventListener("click", guarded(async () => {
    const prov = $("#modelprov").value; const r = await api(`/api/discover?provider=${encodeURIComponent(prov)}`);
    $("#discovered").innerHTML = r.models.map(m => `<option value="${esc(m)}">`).join("");
    toast(r.models.length ? `${r.models.length} models on ${prov} — type in the model id box to pick` : `${prov}: no model listing available`);
  }));
  $("#modelform").addEventListener("submit", guarded(async (e) => {
    e.preventDefault(); const f = e.target;
    await api("/api/models", { body: { name: f.name.value, provider: f.provider.value, model_id: f.model_id.value, context_length: f.context_length.value, price_in: f.price_in.value, price_out: f.price_out.value, extra_body: f.extra_body.value || null, tags: f.tags.value } });
    toast("model added"); f.reset(); await refresh();
  }));
  $("#modeltest").addEventListener("click", guarded(async () => {
    const f = $("#modelform"); const out = $("#modeltestout"); out.textContent = "probing…";
    const r = await api("/api/providers/test", { body: { name: f.provider.value, model_id: f.model_id.value } });
    if (!r.alive) { out.textContent = "provider DOWN"; return; }
    if (!r.probe) { out.textContent = "provider UP (enter a model id to probe)"; return; }
    out.textContent = r.probe.ok ? `OK — served ${r.probe.served_model || "?"}, TTFT ${r.probe.ttft_ms} ms, said: ${r.probe.text}` : `FAILED — ${r.probe.error}`;
  }));
  $("#judgeform").addEventListener("submit", guarded(async (e) => {
    e.preventDefault(); const f = e.target;
    const list = (STATE.judge.candidates || []).slice();
    list.push({ provider: f.provider.value, model_id: f.model_id.value, context_length: f.context_length.value || null, thinking: f.thinking.checked });
    await api("/api/judge-candidates", { body: { candidates: list } }); f.model_id.value = ""; toast("judge candidate added"); await refresh();
  }));

  // ---------------------------------------------------------- cases tab
  let CASES = null;
  async function loadCases() {
    if (!CASES) { CASES = (await api("/api/cases")).cases;
      $("#casecat").innerHTML = `<option value="">all categories</option>` + [...new Set(CASES.map(c => c.category))].map(c => `<option>${esc(c)}</option>`).join(""); }
    renderCases();
  }
  function renderCases() {
    const q = $("#casefilter").value.toLowerCase(); const cat = $("#casecat").value;
    const rows = CASES.filter(c => (!cat || c.category === cat) && (!q || (c.id + " " + c.prompt).toLowerCase().includes(q)));
    $("#caselist tbody").innerHTML = rows.map(c => `<tr><td>${esc(c.category)}</td><td><span class="pill ${esc(c.difficulty)}">${esc(c.difficulty)}</span>${c.smoke ? ' <span class="pill">smoke</span>' : ""}</td>
      <td class="mono">${esc(c.grader)}</td><td class="mono">${esc(c.id)}</td><td class="muted">${esc(c.prompt)}</td></tr>`).join("");
  }
  $("#casefilter").addEventListener("input", renderCases);
  $("#casecat").addEventListener("change", renderCases);

  // ---------------------------------------------------------------- boot
  refresh().then(() => {
    const h = location.hash.replace("#", ""); if (h) showTab(h);
    api("/api/providers").then(refresh).catch(() => {});
  }).catch(e => toast(e.message, true));
  pollLog();
})();
