/* RevGuard 运行回放：按真实运行记录逐步播放，不调用任何后端 API。 */
(() => {
  "use strict";

  const STAGE_LABELS = {
    intake: "受理",
    evidence: "取证",
    policy: "政策",
    calculation: "计算",
    rootcause: "根因",
    risk: "风险",
    approval: "真人审批",
    execution: "受限执行",
    verification: "独立复核",
    recovery: "冲销与恢复",
    closing: "结案",
  };
  const ALERT_STAGES = new Set(["recovery", "verification"]);
  const BASE_STEP_MS = 4200;
  const CASES = ["case-2026-0001", "case-2026-0008"];
  const requested = new URLSearchParams(window.location.search).get("case");
  const DEFAULT_CASE = CASES.includes(requested) ? requested : "case-2026-0001";

  const el = (id) => document.getElementById(id);
  const text = (node, value) => { node.textContent = value == null || value === "" ? "—" : String(value); };

  const state = { bundle: null, index: 0, playing: false, speed: 1, timer: null };

  function clockOf(iso) {
    if (!iso) return "—";
    const date = new Date(iso);
    if (Number.isNaN(date.getTime())) return iso;
    return date.toLocaleTimeString("zh-CN", { hour12: false }) + "." + String(date.getMilliseconds()).padStart(3, "0");
  }

  function durationOf(ms) {
    if (ms == null) return "—";
    if (ms < 1000) return ms + " ms";
    return (ms / 1000).toFixed(2) + " s";
  }

  function table(headers, rows, rowClass) {
    if (!rows.length) return "";
    const head = headers.map((h) => `<th>${h}</th>`).join("");
    const body = rows.map((cells) => {
      const cls = rowClass ? rowClass(cells) : "";
      return `<tr class="${cls}">` + cells.map((c) => `<td>${c == null ? "—" : c}</td>`).join("") + "</tr>";
    }).join("");
    return `<table><thead><tr>${head}</tr></thead><tbody>${body}</tbody></table>`;
  }

  function facts() {
    const b = state.bundle;
    const evidenceStep = b.steps.find((s) => s.stage === "evidence");
    const headline = b.headline;
    const statusTone = b.case.status === "CLOSED" ? "ok" : "warn";
    const variance = parseFloat(String(headline.variance || "0"));
    el("facts").innerHTML = [
      `<div class="fact ${statusTone}"><span>案件终态</span><strong>${b.case.case_id} · ${b.case.status}</strong></div>`,
      `<div class="fact"><span>台账原值</span><strong class="amount">${headline.posted}</strong></div>`,
      `<div class="fact"><span>复核后台账</span><strong class="amount">${headline.verified}</strong></div>`,
      `<div class="fact ${variance ? "bad" : "ok"}"><span>复核差额</span><strong class="amount">${headline.variance}</strong></div>`,
      `<div class="fact"><span>跨系统证据</span><strong>${(evidenceStep?.evidence || []).length} 条强证据</strong></div>`,
      `<div class="fact"><span>真人审批</span><strong>${headline.approved_by || "—"} · ${headline.approval_status || "—"}</strong></div>`,
      `<div class="fact"><span>运行记录</span><strong>${b.case.recording_id || "—"}</strong></div>`,
      `<div class="fact"><span>运行时长 / 跨度</span><strong>${durationOf(b.run.wall_duration_ms)} · ${b.run.span_count} spans</strong></div>`,
    ].join("");
  }

  function stages() {
    const steps = state.bundle.steps;
    el("stages").innerHTML = steps.map((step, i) => {
      const cls = ["stage"];
      if (ALERT_STAGES.has(step.stage)) cls.push("alert");
      if (i < state.index) cls.push("done");
      if (i === state.index) cls.push("active");
      return `<li class="${cls.join(" ")}">${STAGE_LABELS[step.stage] || step.stage}</li>`;
    }).join("");
    el("step-total").textContent = String(steps.length);
  }

  function detailBlocks(step) {
    if (step.facts) {
      const rows = Object.entries(step.facts).map(([k, v]) => [k, `<span class="amount">${v == null ? "—" : v}</span>`]);
      return table(["案件事实", "记录值"], rows);
    }
    if (step.evidence) {
      return table(["来源系统", "单据类型", "单据编号", "证据强度", "内容哈希"],
        step.evidence.map((e) => [e.source, e.type, e.document, e.provenance || e.strength, `<span class="hash">${String(e.hash || "").slice(0, 26)}…</span>`]));
    }
    if (step.policy) {
      const rows = [];
      (step.policy["引用条款"] || []).forEach((clause) => rows.push(["引用条款", clause]));
      (step.policy["排除版本"] || []).forEach((item) => rows.push(["排除版本", item]));
      if (step.policy["等级解析"]) rows.push(["等级解析", step.policy["等级解析"]]);
      return table(["政策要素", "记录值"], rows);
    }
    if (step.components) {
      return table(["佣金组件", "确定性金额", "是否适用"],
        step.components.map((c) => [c.name, `<span class="amount">${c.amount}</span>`, c.applied ? "是" : "否"]));
    }
    if (step.diffs) {
      return table(["组件", "台账", "应有", "差额"],
        step.diffs.map((d) => [d.component, `<span class="amount">${d.posted}</span>`, `<span class="amount">${d.expected}</span>`, `<span class="amount">${d.delta}</span>`]));
    }
    if (step.quota) {
      return table(["授权组件", "逐组件额度"], step.quota.map((q) => [q.component, `<span class="amount">${q.limit}</span>`]));
    }
    if (step.executions) {
      return table(["操作", "组件", "金额", "状态", "幂等键"],
        step.executions.map((x) => [x.action_id, x.component, `<span class="amount">${x.amount}</span>`, x.ledger_status || x.status, x.idempotency_key || "—"]));
    }
    if (step.checks) {
      return table(["组件", "预期", "复核实际", "结论"],
        step.checks.map((c) => [c.component, c.expected, c.actual, c.passed ? "通过" : "不一致"]),
        (cells) => (cells[3] === "通过" ? "pass" : "fail"));
    }
    return "";
  }

  function stepMarkup(step) {
    const bullets = (step.bullets || []).map((b) => `<li>${b}</li>`).join("");
    const extra = step.action_summary ? `<p class="muted">审批动作摘要：${step.action_summary}</p>` : "";
    return `
      <div class="step-top">
        <span class="step-kicker">STEP ${state.index + 1} · ${STAGE_LABELS[step.stage] || step.stage}</span>
        <span class="step-time">记录时点 ${clockOf(step.at)}</span>
      </div>
      <h2>${step.title}</h2>
      <p class="step-sub">${step.subtitle ? "结果：" + step.subtitle : ""}</p>
      <ul class="step-bullets">${bullets}</ul>
      ${extra}
      ${detailBlocks(step)}`;
  }

  function renderStep() {
    const step = state.bundle.steps[state.index];
    el("step-card").innerHTML = stepMarkup(step);
    el("step-index").textContent = String(state.index + 1);
    const pct = Math.round(((state.index + 1) / state.bundle.steps.length) * 100);
    el("progress-bar").style.width = pct + "%";
    document.querySelector(".progress").setAttribute("aria-valuenow", String(pct));
    stages();
    renderEvidence();
    el("btn-prev").disabled = state.index === 0;
    el("btn-next").disabled = state.index === state.bundle.steps.length - 1;
    if (window.location.hash !== "#steps" && state.index > 0) return;
  }

  function renderEvidence() {
    const reached = state.bundle.steps.findIndex((s) => s.stage === "evidence") <= state.index;
    const step = state.bundle.steps.find((s) => s.stage === "evidence");
    const rows = step?.evidence || [];
    if (!reached || !rows.length) {
      el("side-evidence").innerHTML = '<p class="muted">回放到达取证步骤后显示。</p>';
      return;
    }
    el("side-evidence").innerHTML = rows.map((e) => `
      <div class="side-row">
        <b>${e.source} · ${e.type}</b>
        <code>${e.document}</code><br>
        <span class="hash">${String(e.hash || "").slice(0, 40)}</span><br>
        <span class="muted">回执 ${e.receipt || "—"} · ${e.latency_ms ?? "—"} ms · ${e.provenance || ""}</span>
      </div>`).join("");
  }

  function renderAudit() {
    const a = state.bundle.audit;
    el("side-audit").innerHTML = `
      <div class="side-row"><b>本次运行事件</b>${a.count} 条（序号 ${a.first_seq}–${a.last_seq}）</div>
      <div class="side-row"><b>哈希链校验</b>${a.chain_ok ? "连续，未被改写" : "存在断点，需人工核查"}</div>
      <div class="side-row"><b>链头哈希</b><span class="hash">${a.head_hash || "—"}</span></div>
      <div class="side-row"><b>数据包校验</b><span class="hash">${(state.bundle.provenance?.snapshot_sha256 || "").slice(0, 46)}…</span></div>`;
  }

  function renderTrace() {
    const spans = state.bundle.trace.spans || [];
    el("trace-table").innerHTML = table(["#", "类型", "名称", "执行者", "耗时", "状态"],
      spans.map((s) => [s.sequence, `<span class="kind ${s.kind}">${s.kind}</span>`, s.label || s.name, s.actor, durationOf(s.duration_ms), s.status]));
  }

  function stop() {
    state.playing = false;
    if (state.timer) window.clearTimeout(state.timer);
    state.timer = null;
    el("btn-play").textContent = "播放";
    el("btn-play").setAttribute("aria-pressed", "false");
  }

  function next() {
    if (state.index >= state.bundle.steps.length - 1) { stop(); return false; }
    state.index += 1;
    renderStep();
    return true;
  }

  function tick() {
    if (!state.playing) return;
    const advanced = next();
    if (advanced) state.timer = window.setTimeout(tick, BASE_STEP_MS / state.speed);
    else stop();
  }

  function play() {
    if (state.index >= state.bundle.steps.length - 1) state.index = 0;
    state.playing = true;
    el("btn-play").textContent = "暂停";
    el("btn-play").setAttribute("aria-pressed", "true");
    renderStep();
    state.timer = window.setTimeout(tick, BASE_STEP_MS / state.speed);
  }

  function toast(message) {
    el("step-card").innerHTML = `<div class="step-top"><span class="step-kicker">LOADING</span></div><h2>${message}</h2>`;
  }

  async function load(caseId) {
    stop();
    toast("正在加载运行记录…");
    const response = await fetch(`data/${caseId}.json`, { cache: "no-store" });
    if (!response.ok) throw new Error(`数据加载失败：${response.status}`);
    state.bundle = await response.json();
    state.index = 0;
    document.querySelectorAll(".case-tab").forEach((tab) => {
      tab.setAttribute("aria-selected", String(tab.dataset.case === caseId));
    });
    text(el("release-label"), state.bundle.release || "v0.6.0");
    document.title = `RevGuard 运行回放 — ${state.bundle.case.case_id} ${state.bundle.case.status}`;
    facts();
    renderStep();
    renderAudit();
    renderTrace();
  }

  function bind() {
    document.querySelectorAll(".case-tab").forEach((tab) => {
      tab.addEventListener("click", () => {
        load(tab.dataset.case).catch((error) => toast(String(error.message || error)));
      });
    });
    el("btn-play").addEventListener("click", () => (state.playing ? stop() : play()));
    el("btn-next").addEventListener("click", () => { stop(); next(); });
    el("btn-prev").addEventListener("click", () => { stop(); if (state.index > 0) { state.index -= 1; renderStep(); } });
    el("btn-reset").addEventListener("click", () => { stop(); state.index = 0; renderStep(); });
    el("speed").addEventListener("change", (event) => {
      state.speed = Number(event.target.value) || 1;
      if (state.playing) { stop(); play(); }
    });
    document.addEventListener("keydown", (event) => {
      if (event.target.tagName === "SELECT") return;
      if (event.key === " ") { event.preventDefault(); state.playing ? stop() : play(); }
      if (event.key === "ArrowRight") { stop(); next(); }
      if (event.key === "ArrowLeft") { stop(); if (state.index > 0) { state.index -= 1; renderStep(); } }
    });
  }

  bind();
  load(DEFAULT_CASE).catch((error) => toast(String(error.message || error)));
})();
