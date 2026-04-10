/* ============================================================
   AI Customer Simulator — Frontend Logic (Phase 2 & 3)
   ============================================================ */

const API_BASE = "http://localhost:8000";

// Chart instances
let pieChart = null;
let barChart = null;

// Cached results for A/B comparison
let originalResult = null;

// Custom personas state
let customPersonas = [];

// ---------------------------------------------------------------------------
// Modifier label
// ---------------------------------------------------------------------------

function updateModifierLabel() {
  const val = parseFloat(document.getElementById("price-slider").value);
  const pct = Math.round((val - 1) * 100);
  let label;
  if (pct === 0)       label = `1.00×`;
  else if (pct > 0)    label = `${val.toFixed(2)}× (+${pct}%)`;
  else                 label = `${val.toFixed(2)}× (${pct}%)`;
  document.getElementById("modifier-display").textContent = label;
}

// ---------------------------------------------------------------------------
// State helpers
// ---------------------------------------------------------------------------

function showLoading(msg = "Running simulation...") {
  hide("dashboard");
  hide("error-banner");
  hide("ab-section");
  hide("live-feed");
  document.getElementById("loading-text").textContent = msg;
  show("loading");
  setButtonsDisabled(true);
}

function hideLoading() {
  hide("loading");
  setButtonsDisabled(false);
}

function setButtonsDisabled(disabled) {
  document.getElementById("run-btn").disabled = disabled;
  document.getElementById("ab-btn").disabled = disabled;
  document.getElementById("persona-btn").disabled = disabled;
}

function showError(msg) {
  const banner = document.getElementById("error-banner");
  banner.textContent = `Error: ${msg}`;
  show("error-banner");
  hide("dashboard");
  hide("live-feed");
}

function show(id) { document.getElementById(id).classList.remove("hidden"); }
function hide(id) { document.getElementById(id).classList.add("hidden"); }

// ---------------------------------------------------------------------------
// Persona Builder
// ---------------------------------------------------------------------------

function togglePersonaPanel() {
  const panel = document.getElementById("persona-panel");
  panel.classList.toggle("hidden");
}

function closePersonaPanel() {
  document.getElementById("persona-panel").classList.add("hidden");
}

function addPersona() {
  const id = document.getElementById("p-id").value.trim();
  const type = document.getElementById("p-type").value;
  const budget = parseFloat(document.getElementById("p-budget").value);
  const impulsiveness = parseFloat(document.getElementById("p-impulsiveness").value);
  const goal = document.getElementById("p-goal").value.trim();

  if (!id) { alert("Please enter an Agent ID."); return; }
  if (!budget || budget <= 0) { alert("Please enter a valid budget."); return; }
  if (customPersonas.find(p => p.id === id)) {
    alert(`A persona with ID "${id}" already exists.`);
    return;
  }

  customPersonas.push({ id, type, budget, impulsiveness, goal });
  renderPersonaList();
  // Clear form
  document.getElementById("p-id").value = "";
  document.getElementById("p-budget").value = "";
  document.getElementById("p-goal").value = "";
}

function removePersona(id) {
  customPersonas = customPersonas.filter(p => p.id !== id);
  renderPersonaList();
}

function clearPersonas() {
  customPersonas = [];
  renderPersonaList();
}

function renderPersonaList() {
  const list = document.getElementById("persona-list");
  const badge = document.getElementById("persona-count-badge");
  const hint = document.getElementById("persona-using-default");

  list.innerHTML = "";

  if (customPersonas.length === 0) {
    badge.classList.add("hidden");
    hint.textContent = "Using default 9 personas";
    return;
  }

  badge.textContent = customPersonas.length;
  badge.classList.remove("hidden");
  hint.textContent = `Running ${customPersonas.length} custom persona${customPersonas.length !== 1 ? "s" : ""}`;

  const personaColors = { budget: "#3b82f6", luxury: "#a855f7", impulsive: "#f97316", custom: "#14b8a6" };

  for (const p of customPersonas) {
    const card = document.createElement("div");
    card.className = "persona-card";
    card.innerHTML = `
      <div class="persona-card-header">
        <span class="persona-card-id" style="color:${personaColors[p.type] || '#e2e8f0'}">${escHtml(p.id)}</span>
        <span class="persona-card-type">${p.type} · $${p.budget} budget · imp ${p.impulsiveness.toFixed(2)}</span>
        <button class="persona-card-remove" onclick="removePersona('${escHtml(p.id)}')">&#x2715;</button>
      </div>
      ${p.goal ? `<div class="persona-card-goal">"${escHtml(p.goal)}"</div>` : ""}
    `;
    list.appendChild(card);
  }
}

// ---------------------------------------------------------------------------
// SSE Streaming run
// ---------------------------------------------------------------------------

// Live feed agent tracking: agent_id → DOM element
const liveAgentEls = {};

function setLiveStatus(msg) {
  document.getElementById("live-status-text").textContent = msg;
}

function ensureLiveFeedVisible() {
  hide("loading");
  hide("dashboard");
  hide("error-banner");
  show("live-feed");
}

function getOrCreateLiveAgent(agentId, personaType, budget) {
  if (liveAgentEls[agentId]) return liveAgentEls[agentId];

  const agentEl = document.createElement("div");
  agentEl.className = "live-agent";
  agentEl.id = `live-agent-${agentId}`;

  const personaColors = { budget: "#3b82f6", luxury: "#a855f7", impulsive: "#f97316", custom: "#14b8a6" };
  const color = personaColors[personaType] || "#e2e8f0";

  agentEl.innerHTML = `
    <div class="live-agent-header">
      <span class="live-agent-id" style="color:${color}">${agentId.replace("_", " ")}</span>
      <span class="live-agent-meta">${personaType} · $${budget}</span>
      <span class="live-agent-status live-status-running">running...</span>
      <div class="live-spinner"></div>
    </div>
    <div class="live-agent-chips" id="live-chips-${agentId}"></div>
  `;

  document.getElementById("live-agents").appendChild(agentEl);
  liveAgentEls[agentId] = agentEl;
  return agentEl;
}

function appendLiveStep(agentId, step) {
  const chipsEl = document.getElementById(`live-chips-${agentId}`);
  if (!chipsEl) return;

  const action = step.action || step.data?.action || "unknown";
  const reason = step.reason || step.data?.reason || "";
  const target = step.target || step.data?.target || "";

  const chip = document.createElement("span");
  chip.className = `chip ${action}`;
  chip.textContent = action.replace(/_/g, " ");
  chip.title = `${target}\n"${reason}"`;
  chipsEl.appendChild(chip);
}

function finalizeLiveAgent(agentId, result) {
  const agentEl = liveAgentEls[agentId];
  if (!agentEl) return;

  const statusEl = agentEl.querySelector(".live-agent-status");
  const spinnerEl = agentEl.querySelector(".live-spinner");

  if (statusEl) {
    statusEl.textContent = result === "purchased" ? "✓ Bought" : "✗ Left";
    statusEl.className = `live-agent-status ${result === "purchased" ? "live-status-purchased" : "live-status-left"}`;
  }
  if (spinnerEl) spinnerEl.remove();
  agentEl.classList.add("live-agent-done");
}

async function runStream(url, priceModifier = 1.0, personas = null, enableVision = false) {
  // Clear live agent tracking
  Object.keys(liveAgentEls).forEach(k => delete liveAgentEls[k]);
  document.getElementById("live-agents").innerHTML = "";

  hide("ab-section");
  ensureLiveFeedVisible();
  setButtonsDisabled(true);
  setLiveStatus("Connecting...");

  const body = {
    url,
    price_modifier: priceModifier,
    enable_vision: enableVision,
  };
  if (personas && personas.length > 0) body.custom_personas = personas;

  let collectedData = {
    site_info: null,
    analytics: null,
    report: null,
    logs: [],
    visual_analysis: null,
  };

  try {
    const res = await fetch(`${API_BASE}/run-stream`, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(body),
    });

    if (!res.ok) {
      const err = await res.json().catch(() => ({ detail: res.statusText }));
      throw new Error(err.detail || `HTTP ${res.status}`);
    }

    const reader = res.body.getReader();
    const decoder = new TextDecoder();
    let buffer = "";

    while (true) {
      const { done, value } = await reader.read();
      if (done) break;

      buffer += decoder.decode(value, { stream: true });
      const lines = buffer.split("\n");
      buffer = lines.pop(); // keep incomplete line

      for (const line of lines) {
        if (!line.startsWith("data: ")) continue;
        let event;
        try {
          event = JSON.parse(line.slice(6));
        } catch {
          continue;
        }

        switch (event.type) {
          case "status":
            setLiveStatus(event.message || "");
            break;

          case "site_info":
            collectedData.site_info = event;
            setLiveStatus("Site parsed — agents running...");
            break;

          case "agent_start":
            getOrCreateLiveAgent(event.agent_id, event.persona_type, event.budget);
            break;

          case "step": {
            const step = event.data || event;
            const agId = event.agent_id;
            // Ensure agent row exists (may arrive before agent_start in edge cases)
            getOrCreateLiveAgent(agId, step.persona_type || "", step.budget || 0);
            appendLiveStep(agId, step);
            break;
          }

          case "agent_done":
            finalizeLiveAgent(event.agent_id, event.result);
            setLiveStatus(`Agent ${event.agent_id} done (${event.result})`);
            break;

          case "analytics":
            collectedData.analytics = event;
            setLiveStatus("Analytics computed — generating report...");
            break;

          case "report":
            collectedData.report = event;
            setLiveStatus("Report ready — wrapping up...");
            break;

          case "visual_analysis":
            collectedData.visual_analysis = event;
            break;

          case "done":
            collectedData.logs = event.logs || [];
            setLiveStatus("Done!");
            break;

          case "error":
            throw new Error(event.message || "Unknown stream error");
        }
      }
    }

    // Hide live feed, show full dashboard
    hide("live-feed");
    setButtonsDisabled(false);

    if (!collectedData.analytics || !collectedData.site_info) {
      showError("Incomplete response from server.");
      return null;
    }

    return collectedData;

  } catch (e) {
    hide("live-feed");
    setButtonsDisabled(false);
    showError(e.message);
    return null;
  }
}

// ---------------------------------------------------------------------------
// Blocking API call (used for A/B second leg)
// ---------------------------------------------------------------------------

async function fetchRun(url, priceModifier = 1.0, personas = null) {
  const body = { url, price_modifier: priceModifier };
  if (personas && personas.length > 0) body.custom_personas = personas;

  const res = await fetch(`${API_BASE}/run`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(body),
  });
  if (!res.ok) {
    const err = await res.json().catch(() => ({ detail: res.statusText }));
    throw new Error(err.detail || `HTTP ${res.status}`);
  }
  return res.json();
}

// ---------------------------------------------------------------------------
// Main handlers
// ---------------------------------------------------------------------------

async function handleRun() {
  const url = document.getElementById("url-input").value.trim();
  if (!url) { showError("Please enter a URL."); return; }

  const enableVision = document.getElementById("vision-toggle").checked;
  const personas = customPersonas.length > 0 ? customPersonas : null;

  const streamData = await runStream(url, 1.0, personas, enableVision);
  if (!streamData) return;

  // Merge streaming data into a unified result shape
  const data = mergeStreamData(streamData);
  originalResult = data;
  renderAll(data);
}

async function handleAB() {
  const url = document.getElementById("url-input").value.trim();
  if (!url) { showError("Please enter a URL first."); return; }

  const modifier = parseFloat(document.getElementById("price-slider").value);
  if (modifier === 1.0) {
    showError("Set the price slider to something other than 1.0× to run an A/B test.");
    return;
  }

  const personas = customPersonas.length > 0 ? customPersonas : null;

  // Run original via streaming
  if (!originalResult) {
    const streamData = await runStream(url, 1.0, personas, false);
    if (!streamData) return;
    originalResult = mergeStreamData(streamData);
    renderAll(originalResult);
  }

  // Run modified (blocking — we need the full result to compare)
  showLoading(`Running A/B test with ${Math.round((modifier - 1) * 100)}% price change...`);
  try {
    const modifiedResult = await fetchRun(url, modifier, personas);
    hideLoading();
    renderAll(originalResult);
    renderAB(originalResult, modifiedResult, modifier);
  } catch (e) {
    hideLoading();
    showError(e.message);
  }
}

// ---------------------------------------------------------------------------
// Merge streaming data into full result shape
// ---------------------------------------------------------------------------

function mergeStreamData(sd) {
  return {
    site_info: {
      headline: sd.site_info?.headline || "",
      cta_text: sd.site_info?.cta_text || "",
      products: sd.site_info?.products || [],
      ux_breakdown: sd.site_info?.ux_breakdown || {},
    },
    analytics: sd.analytics || {},
    ux_report: sd.report || {},
    logs: sd.logs || [],
    visual_analysis: sd.visual_analysis || null,
  };
}

// ---------------------------------------------------------------------------
// Render everything
// ---------------------------------------------------------------------------

function renderAll(data) {
  renderSiteStrip(data.site_info);
  renderKPIs(data.analytics);
  renderPieChart(data.analytics);
  renderBarChart(data.analytics);
  renderUXBreakdown(data.analytics, data.site_info);
  renderTimeline(data.logs);
  renderUXReport(data.ux_report);
  renderVisualAnalysis(data.visual_analysis);
  renderComplaints(data.analytics);
  renderThemeBars(data.analytics);
  renderDropoffInsights(data.analytics);
  renderPurchaseInsights(data.analytics);
  renderProducts(data.site_info.products);
  show("dashboard");
}

// ---------------------------------------------------------------------------
// Site strip
// ---------------------------------------------------------------------------

function renderSiteStrip(siteInfo) {
  document.getElementById("site-headline").textContent = siteInfo.headline || "No headline detected";
  const ctaEl = document.getElementById("site-cta");
  if (siteInfo.cta_text) {
    ctaEl.textContent = `CTA: ${siteInfo.cta_text}`;
    ctaEl.classList.remove("hidden");
  } else {
    ctaEl.classList.add("hidden");
  }
}

// ---------------------------------------------------------------------------
// KPI cards
// ---------------------------------------------------------------------------

function renderKPIs(analytics) {
  const convPct = Math.round(analytics.conversion_rate * 100);
  const dropPct = Math.round(analytics.dropoff_rate * 100);
  const uxScore = analytics.ux_score;

  const convEl = document.getElementById("val-conversion");
  convEl.textContent = `${convPct}%`;
  convEl.className = "kpi-value " + (convPct >= 50 ? "green" : convPct >= 25 ? "yellow" : "red");

  const dropEl = document.getElementById("val-dropoff");
  dropEl.textContent = `${dropPct}%`;
  dropEl.className = "kpi-value " + (dropPct >= 75 ? "red" : dropPct >= 50 ? "yellow" : "green");

  document.getElementById("val-steps").textContent = analytics.avg_steps.toFixed(1);

  const uxEl = document.getElementById("val-ux");
  uxEl.textContent = uxScore;
  uxEl.className = "kpi-value " + (uxScore >= 70 ? "green" : uxScore >= 40 ? "yellow" : "red");
}

// ---------------------------------------------------------------------------
// Pie chart
// ---------------------------------------------------------------------------

function renderPieChart(analytics) {
  const ctx = document.getElementById("pie-chart").getContext("2d");
  if (pieChart) pieChart.destroy();

  pieChart = new Chart(ctx, {
    type: "doughnut",
    data: {
      labels: ["Purchased", "Left"],
      datasets: [{
        data: [analytics.purchased_count, analytics.left_count],
        backgroundColor: ["rgba(34,197,94,0.85)", "rgba(239,68,68,0.85)"],
        borderColor: ["#166534", "#7f1d1d"],
        borderWidth: 2,
        hoverOffset: 6,
      }],
    },
    options: {
      plugins: {
        legend: {
          labels: { color: "#0f172a", font: { size: 12, weight: "600" }, padding: 16 },
        },
        tooltip: {
          callbacks: {
            label: (ctx) => ` ${ctx.label}: ${ctx.raw} agent${ctx.raw !== 1 ? "s" : ""}`,
          },
        },
      },
      cutout: "60%",
    },
  });
}

// ---------------------------------------------------------------------------
// Bar chart
// ---------------------------------------------------------------------------

function renderBarChart(analytics) {
  const ctx = document.getElementById("bar-chart").getContext("2d");
  if (barChart) barChart.destroy();

  const bd = analytics.agent_breakdown || {};

  // Build labels + data dynamically (supports custom persona types)
  const keys = Object.keys(bd);
  const colors = ["rgba(59,130,246,0.85)", "rgba(168,85,247,0.85)", "rgba(249,115,22,0.85)",
                  "rgba(20,184,166,0.85)", "rgba(234,179,8,0.85)", "rgba(239,68,68,0.85)"];
  const borders = ["#2563eb", "#7c3aed", "#c2410c", "#0d9488", "#b45309", "#b91c1c"];

  barChart = new Chart(ctx, {
    type: "bar",
    data: {
      labels: keys.map(k => k.charAt(0).toUpperCase() + k.slice(1)),
      datasets: [{
        label: "Conversion Rate (%)",
        data: keys.map(k => Math.round((bd[k]?.conversion_rate || 0) * 100)),
        backgroundColor: keys.map((_, i) => colors[i % colors.length]),
        borderColor: keys.map((_, i) => borders[i % borders.length]),
        borderWidth: 2,
        borderRadius: 6,
      }],
    },
    options: {
      plugins: {
        legend: { display: false },
        tooltip: {
          callbacks: {
            label: (ctx) => {
              const k = keys[ctx.dataIndex];
              return ` ${ctx.raw}% conversion (${bd[k]?.avg_steps?.toFixed(1) ?? "?"} avg steps)`;
            },
          },
        },
      },
      scales: {
        y: {
          min: 0,
          max: 100,
          grid: { color: "#f1f5f9" },
          ticks: { color: "#64748b", callback: (v) => v + "%" },
        },
        x: {
          grid: { display: false },
          ticks: { color: "#0f172a", font: { weight: "600" } },
        },
      },
    },
  });
}

// ---------------------------------------------------------------------------
// UX Breakdown
// ---------------------------------------------------------------------------

const UX_LABELS = {
  headline: "Clear Headline",
  cta: "CTA Button",
  price_clarity: "Price Clarity",
  product_count: "3+ Products",
  image_alts: "Image Alt Text",
};

function renderUXBreakdown(analytics, siteInfo) {
  const container = document.getElementById("ux-pills");
  container.innerHTML = "";
  const breakdown = siteInfo.ux_breakdown || {};
  for (const [key, label] of Object.entries(UX_LABELS)) {
    const pass = breakdown[key];
    const pill = document.createElement("span");
    pill.className = `pill ${pass ? "pass" : "fail"}`;
    pill.textContent = `${pass ? "✓" : "✗"} ${label}`;
    container.appendChild(pill);
  }
}

// ---------------------------------------------------------------------------
// Agent Timeline (full, after run)
// ---------------------------------------------------------------------------

const PERSONA_COLORS = {
  budget: "#3b82f6",
  luxury: "#a855f7",
  impulsive: "#f97316",
  custom: "#14b8a6",
};

function renderTimeline(logs) {
  const container = document.getElementById("timeline");
  container.innerHTML = "";

  for (const agent of logs) {
    const wrapper = document.createElement("div");
    wrapper.className = "agent-row-wrapper";

    const header = document.createElement("div");
    header.className = "agent-row";
    header.style.cursor = "pointer";
    header.setAttribute("aria-expanded", "false");

    const label = document.createElement("div");
    label.className = "agent-label";
    label.innerHTML = `
      <div style="color:${PERSONA_COLORS[agent.persona_type] || "#e2e8f0"}">
        ${agent.agent_id.replace(/_/g, " ")}
      </div>
      <div class="agent-type">$${agent.budget}</div>
    `;
    header.appendChild(label);

    const chips = document.createElement("div");
    chips.className = "step-chips";
    for (const step of agent.steps) {
      const chip = document.createElement("span");
      chip.className = `chip ${step.action}`;
      chip.textContent = step.action.replace(/_/g, " ");
      const urlNote = step.product_url ? `\nFetched: ${step.product_url}` : "";
      chip.title = `Step ${step.step}: ${step.target}${urlNote}\n"${step.reason}"`;
      chips.appendChild(chip);
    }
    header.appendChild(chips);

    const right = document.createElement("div");
    right.style.cssText = "display:flex;align-items:center;gap:8px;margin-left:auto;flex-shrink:0";
    const badge = document.createElement("span");
    badge.className = `result-badge ${agent.result}`;
    badge.textContent = agent.result === "purchased" ? "✓ Bought" : "✗ Left";
    const caret = document.createElement("span");
    caret.className = "caret";
    caret.textContent = "▸";
    right.appendChild(badge);
    right.appendChild(caret);
    header.appendChild(right);

    const detail = document.createElement("div");
    detail.className = "agent-detail hidden";

    for (const step of agent.steps) {
      const stepRow = document.createElement("div");
      stepRow.className = `step-detail-row ${step.action}`;

      const actionLabel = document.createElement("span");
      actionLabel.className = `chip ${step.action} chip-sm`;
      actionLabel.textContent = `${step.step}. ${step.action.replace(/_/g, " ")}`;

      const targetSpan = document.createElement("span");
      targetSpan.className = "step-target";
      targetSpan.textContent = step.target ? `→ ${step.target}` : "";

      const reasonSpan = document.createElement("span");
      reasonSpan.className = "step-reason";
      reasonSpan.textContent = `"${step.reason}"`;

      stepRow.appendChild(actionLabel);
      stepRow.appendChild(targetSpan);
      stepRow.appendChild(reasonSpan);

      if (step.product_url) {
        const urlSpan = document.createElement("a");
        urlSpan.className = "step-url";
        urlSpan.textContent = "Fetched product page";
        urlSpan.href = step.product_url;
        urlSpan.target = "_blank";
        stepRow.appendChild(urlSpan);
      }

      detail.appendChild(stepRow);
    }

    header.addEventListener("click", () => {
      const expanded = header.getAttribute("aria-expanded") === "true";
      header.setAttribute("aria-expanded", String(!expanded));
      caret.textContent = expanded ? "▸" : "▾";
      detail.classList.toggle("hidden", expanded);
    });

    wrapper.appendChild(header);
    wrapper.appendChild(detail);
    container.appendChild(wrapper);
  }
}

// ---------------------------------------------------------------------------
// UX Consultant Report
// ---------------------------------------------------------------------------

function renderUXReport(report) {
  if (!report || !report.executive_summary) return;

  const score = report.overall_score || 0;
  const scoreEl = document.getElementById("report-score");
  scoreEl.textContent = score + "/10";
  scoreEl.className = "report-score " + (score >= 7 ? "green" : score >= 4 ? "yellow" : "red");
  document.getElementById("report-score-rationale").textContent = report.score_rationale || "";
  document.getElementById("report-summary").textContent = report.executive_summary || "";

  const issuesList = document.getElementById("report-issues");
  issuesList.innerHTML = "";
  (report.critical_issues || []).forEach(issue => {
    const li = document.createElement("li");
    li.textContent = issue;
    issuesList.appendChild(li);
  });

  const winsList = document.getElementById("report-wins");
  winsList.innerHTML = "";
  (report.quick_wins || []).forEach(win => {
    const li = document.createElement("li");
    li.textContent = win;
    winsList.appendChild(li);
  });

  const personasEl = document.getElementById("report-personas");
  personasEl.innerHTML = "";
  const personaColors = { budget: "#3b82f6", luxury: "#a855f7", impulsive: "#f97316" };
  for (const [type, insight] of Object.entries(report.persona_insights || {})) {
    if (!insight) continue;
    const card = document.createElement("div");
    card.className = "report-persona-card";
    card.innerHTML = `
      <div class="report-persona-label" style="color:${personaColors[type] || '#e2e8f0'}">${type}</div>
      <div class="report-persona-text">${escHtml(insight)}</div>
    `;
    personasEl.appendChild(card);
  }

  document.getElementById("report-tools").textContent = report.tools_used_insights || "";

  const prioritiesList = document.getElementById("report-priorities");
  prioritiesList.innerHTML = "";
  (report.redesign_priorities || []).forEach(p => {
    const li = document.createElement("li");
    li.textContent = p;
    prioritiesList.appendChild(li);
  });
}

// ---------------------------------------------------------------------------
// Visual Analysis (Phase 3)
// ---------------------------------------------------------------------------

function renderVisualAnalysis(visual) {
  const section = document.getElementById("visual-section");
  if (!visual || !visual.visual_score) {
    section.classList.add("hidden");
    return;
  }
  section.classList.remove("hidden");

  const score = visual.visual_score || 0;
  const scoreEl = document.getElementById("visual-score");
  scoreEl.textContent = score + "/10";
  scoreEl.className = "report-score " + (score >= 7 ? "green" : score >= 4 ? "yellow" : "red");

  document.getElementById("visual-verdict").textContent = visual.one_line_verdict || "";
  document.getElementById("visual-impression").textContent = visual.visual_first_impression || "";
  document.getElementById("visual-layout").textContent = visual.layout_clarity || "";
  document.getElementById("visual-cta").textContent = visual.cta_visibility || "";
  document.getElementById("visual-trust").textContent = visual.trust_signals || "";
  document.getElementById("visual-mobile").textContent = visual.mobile_readiness_guess || "";

  const frictionEl = document.getElementById("visual-friction");
  frictionEl.innerHTML = "";
  const friction = visual.friction_points || [];
  if (friction.length === 0) {
    frictionEl.innerHTML = `<span class="pill neutral">None detected</span>`;
  } else {
    for (const f of friction) {
      const pill = document.createElement("span");
      pill.className = "pill fail";
      pill.textContent = f;
      frictionEl.appendChild(pill);
    }
  }
}

// ---------------------------------------------------------------------------
// Drop-off theme bars
// ---------------------------------------------------------------------------

const THEME_META = {
  price:     { label: "Price too high",         color: "#ef4444" },
  selection: { label: "Limited selection",       color: "#f97316" },
  ux:        { label: "Confusing / poor UX",     color: "#a855f7" },
  relevance: { label: "Product not relevant",    color: "#3b82f6" },
  other:     { label: "Other / unclear",         color: "#64748b" },
};

function renderThemeBars(analytics) {
  const container = document.getElementById("theme-bars");
  container.innerHTML = "";
  const themes = analytics.drop_themes || {};
  const total = Object.values(themes).reduce((s, n) => s + n, 0);

  if (total === 0) {
    container.innerHTML = `<span class="pill neutral">No drop-offs to analyze</span>`;
    return;
  }

  for (const [key, meta] of Object.entries(THEME_META)) {
    const count = themes[key] || 0;
    if (count === 0) continue;
    const pct = Math.round((count / total) * 100);

    const row = document.createElement("div");
    row.className = "theme-bar-row";
    row.innerHTML = `
      <div class="theme-bar-label">${meta.label}</div>
      <div class="theme-bar-track">
        <div class="theme-bar-fill" style="width:${pct}%;background:${meta.color}"></div>
      </div>
      <div class="theme-bar-count">${count} agent${count !== 1 ? "s" : ""} (${pct}%)</div>
    `;
    container.appendChild(row);
  }
}

// ---------------------------------------------------------------------------
// Per-agent insight cards
// ---------------------------------------------------------------------------

function renderDropoffInsights(analytics) {
  const container = document.getElementById("dropoff-insights");
  container.innerHTML = "";
  const reasons = analytics.dropoff_reasons || [];

  if (reasons.length === 0) {
    container.innerHTML = `<span class="pill pass">All agents purchased!</span>`;
    return;
  }

  for (const agent of reasons) {
    const theme = agent.theme || "other";
    const themeMeta = THEME_META[theme] || THEME_META.other;
    const color = PERSONA_COLORS[agent.persona_type] || "#e2e8f0";

    const card = document.createElement("div");
    card.className = "insight-card";
    card.innerHTML = `
      <div class="insight-card-header">
        <span class="insight-agent-id" style="color:${color}">${agent.agent_id.replace(/_/g, " ")}</span>
        <span class="insight-persona">${agent.persona_type} · $${agent.budget} budget</span>
        <span class="insight-theme-badge" style="border-color:${themeMeta.color};color:${themeMeta.color}">${themeMeta.label}</span>
      </div>
      <div class="insight-steps-taken">${agent.step_count} step${agent.step_count !== 1 ? "s" : ""} before leaving</div>
      <div class="insight-reason">"${escHtml(agent.exit_reason)}"</div>
      <div class="insight-journey">${_journeyText(agent.journey)}</div>
    `;
    container.appendChild(card);
  }
}

function renderPurchaseInsights(analytics) {
  const container = document.getElementById("purchase-insights");
  container.innerHTML = "";
  const section = document.getElementById("purchase-section");
  const reasons = analytics.purchase_reasons || [];

  if (reasons.length === 0) {
    section.classList.add("hidden");
    return;
  }
  section.classList.remove("hidden");

  for (const agent of reasons) {
    const color = PERSONA_COLORS[agent.persona_type] || "#e2e8f0";

    const card = document.createElement("div");
    card.className = "insight-card insight-card-purchase";
    card.innerHTML = `
      <div class="insight-card-header">
        <span class="insight-agent-id" style="color:${color}">${agent.agent_id.replace(/_/g, " ")}</span>
        <span class="insight-persona">${agent.persona_type} · $${agent.budget} budget</span>
        ${agent.purchased_product ? `<span class="insight-product-badge">Bought: ${escHtml(agent.purchased_product)}</span>` : ""}
      </div>
      <div class="insight-steps-taken">${agent.step_count} step${agent.step_count !== 1 ? "s" : ""} to purchase</div>
      <div class="insight-reason purchase">"${escHtml(agent.exit_reason)}"</div>
      <div class="insight-journey">${_journeyText(agent.journey)}</div>
    `;
    container.appendChild(card);
  }
}

function _journeyText(journey) {
  if (!journey || journey.length <= 1) return "";
  const steps = journey.slice(0, -1);
  if (steps.length === 0) return "";
  return `<div class="journey-label">Path taken:</div>` +
    steps.map(s =>
      `<div class="journey-step">
        <span class="chip ${s.action} chip-sm">${s.action.replace(/_/g, " ")}</span>
        <span class="journey-reason">${escHtml(s.reason)}</span>
      </div>`
    ).join("");
}

// ---------------------------------------------------------------------------
// Top Complaints
// ---------------------------------------------------------------------------

function renderComplaints(analytics) {
  const container = document.getElementById("complaints");
  container.innerHTML = "";
  const complaints = analytics.top_complaints || [];
  if (complaints.length === 0) {
    container.innerHTML = `<span class="pill neutral">No complaints recorded</span>`;
    return;
  }
  for (const word of complaints) {
    const pill = document.createElement("span");
    pill.className = "pill fail";
    pill.textContent = word;
    container.appendChild(pill);
  }
}

// ---------------------------------------------------------------------------
// Products
// ---------------------------------------------------------------------------

function renderProducts(products) {
  const grid = document.getElementById("products-grid");
  grid.innerHTML = "";
  for (const p of products) {
    const card = document.createElement("div");
    card.className = "product-card";
    card.innerHTML = `
      <div class="product-name">${escHtml(p.name)}</div>
      <div class="product-price">${escHtml(p.price)}</div>
      <div class="product-desc">${escHtml(p.description)}</div>
    `;
    grid.appendChild(card);
  }
}

// ---------------------------------------------------------------------------
// A/B Comparison
// ---------------------------------------------------------------------------

function renderAB(original, modified, modifier) {
  const pct = Math.round((modifier - 1) * 100);
  const label = pct >= 0 ? `+${pct}%` : `${pct}%`;
  document.getElementById("ab-subtitle").textContent =
    `Comparing original prices vs ${label} price change`;
  document.getElementById("ab-modified-label").textContent = `Modified (${label})`;

  renderABKPIs("ab-original", original.analytics, null);
  renderABKPIs("ab-modified", modified.analytics, original.analytics);

  show("ab-section");
  document.getElementById("ab-section").scrollIntoView({ behavior: "smooth" });
}

function renderABKPIs(containerId, analytics, baseline) {
  const container = document.getElementById(containerId);
  container.innerHTML = "";

  const metrics = [
    {
      label: "Conversion",
      value: Math.round(analytics.conversion_rate * 100) + "%",
      raw: analytics.conversion_rate,
      baseRaw: baseline?.conversion_rate ?? null,
      higherIsBetter: true,
    },
    {
      label: "Drop-off",
      value: Math.round(analytics.dropoff_rate * 100) + "%",
      raw: analytics.dropoff_rate,
      baseRaw: baseline?.dropoff_rate ?? null,
      higherIsBetter: false,
    },
    {
      label: "Avg Steps",
      value: analytics.avg_steps.toFixed(1),
      raw: analytics.avg_steps,
      baseRaw: baseline?.avg_steps ?? null,
      higherIsBetter: null,
    },
    {
      label: "Purchased",
      value: `${analytics.purchased_count} / ${analytics.total_agents}`,
      raw: analytics.purchased_count,
      baseRaw: baseline?.purchased_count ?? null,
      higherIsBetter: true,
    },
  ];

  for (const m of metrics) {
    const card = document.createElement("div");
    card.className = "ab-kpi";

    let deltaHtml = "";
    if (baseline !== null && m.baseRaw !== null) {
      const diff = m.raw - m.baseRaw;
      if (Math.abs(diff) < 0.001) {
        deltaHtml = `<div class="ab-kpi-delta delta-same">= no change</div>`;
      } else {
        const better =
          m.higherIsBetter === null ? null : (diff > 0) === m.higherIsBetter;
        const cls = better === null ? "delta-same" : better ? "delta-up" : "delta-down";
        const sign = diff > 0 ? "+" : "";
        const displayDiff =
          typeof m.baseRaw === "number" && m.baseRaw <= 1 && m.baseRaw >= 0
            ? `${sign}${Math.round(diff * 100)}pp`
            : `${sign}${diff.toFixed(1)}`;
        deltaHtml = `<div class="ab-kpi-delta ${cls}">${displayDiff}</div>`;
      }
    }

    card.innerHTML = `
      <div class="ab-kpi-label">${m.label}</div>
      <div class="ab-kpi-value">${m.value}</div>
      ${deltaHtml}
    `;
    container.appendChild(card);
  }
}

// ---------------------------------------------------------------------------
// Utilities
// ---------------------------------------------------------------------------

function escHtml(str) {
  if (!str) return "";
  return String(str)
    .replace(/&/g, "&amp;")
    .replace(/</g, "&lt;")
    .replace(/>/g, "&gt;")
    .replace(/"/g, "&quot;");
}

// Initialize modifier label on page load
updateModifierLabel();

// ---------------------------------------------------------------------------
// Tab Switching
// ---------------------------------------------------------------------------

let impactChartsRendered = false;

function switchTab(tabName) {
  // Toggle tab content
  document.getElementById("tab-simulator").classList.toggle("hidden", tabName !== "simulator");
  document.getElementById("tab-impact").classList.toggle("hidden", tabName !== "impact");

  // Update tab button states
  document.querySelectorAll(".tab-btn").forEach(btn => btn.classList.remove("active"));
  const activeBtn = document.querySelector(`.tab-btn[onclick*="${tabName}"]`);
  if (activeBtn) activeBtn.classList.add("active");

  // Render impact charts and animate counters on first view
  if (tabName === "impact" && !impactChartsRendered) {
    impactChartsRendered = true;
    setTimeout(() => {
      animateCounters();
      renderImpactCharts();
    }, 100);
  }
}

// ---------------------------------------------------------------------------
// Counter Animations
// ---------------------------------------------------------------------------

function animateCounters() {
  const counters = document.querySelectorAll(".impact-counter-num[data-target]");
  counters.forEach(el => {
    const target = parseInt(el.dataset.target, 10);
    const suffix = el.dataset.suffix || "";
    const prefix = el.textContent.startsWith("$") ? "$" : "";
    const duration = 1400;
    const steps = 60;
    const increment = target / steps;
    let current = 0;
    let step = 0;

    const timer = setInterval(() => {
      step++;
      // Ease out: slow down near end
      const progress = step / steps;
      const eased = 1 - Math.pow(1 - progress, 3);
      current = Math.round(eased * target);

      el.textContent = prefix + current + suffix;

      if (step >= steps) {
        clearInterval(timer);
        el.textContent = prefix + target + suffix;
      }
    }, duration / steps);
  });
}

// ---------------------------------------------------------------------------
// Impact Charts
// ---------------------------------------------------------------------------

let impactMarketChart = null;
let impactCostChart = null;

function renderImpactCharts() {
  renderMarketGrowthChart();
  renderCostComparisonChart();
}

function renderMarketGrowthChart() {
  const ctx = document.getElementById("impact-market-chart");
  if (!ctx) return;
  if (impactMarketChart) impactMarketChart.destroy();

  const years = ["2020","2021","2022","2023","2024","2025","2026","2027","2028","2029","2030"];
  const data  = [8.1, 9.5, 10.8, 12.0, 13.5, 15.2, 17.1, 19.3, 21.8, 24.7, 27.9];
  const splitAt = 5; // first 5 are historical, rest projected

  impactMarketChart = new Chart(ctx, {
    type: "line",
    data: {
      labels: years,
      datasets: [
        {
          label: "Historical",
          data: data.map((v, i) => i <= splitAt ? v : null),
          borderColor: "#16a34a",
          backgroundColor: "rgba(22,163,74,0.1)",
          borderWidth: 3,
          pointBackgroundColor: "#16a34a",
          pointRadius: 5,
          fill: true,
          tension: 0.4,
        },
        {
          label: "Projected",
          data: data.map((v, i) => i >= splitAt ? v : null),
          borderColor: "#4ade80",
          backgroundColor: "rgba(74,222,128,0.07)",
          borderWidth: 2,
          borderDash: [6, 3],
          pointBackgroundColor: "#4ade80",
          pointRadius: 4,
          fill: true,
          tension: 0.4,
        },
      ],
    },
    options: {
      animation: { duration: 1200, easing: "easeOutQuart" },
      plugins: {
        legend: {
          labels: { color: "#64748b", font: { size: 11, weight: "600" } },
        },
        tooltip: {
          callbacks: {
            label: (ctx) => ` $${ctx.raw}B`,
          },
        },
      },
      scales: {
        y: {
          min: 0,
          grid: { color: "#f1f5f9" },
          ticks: {
            color: "#64748b",
            callback: (v) => "$" + v + "B",
            font: { size: 11 },
          },
        },
        x: {
          grid: { display: false },
          ticks: { color: "#64748b", font: { size: 11 } },
        },
      },
    },
  });
}

function renderCostComparisonChart() {
  const ctx = document.getElementById("impact-cost-chart");
  if (!ctx) return;
  if (impactCostChart) impactCostChart.destroy();

  impactCostChart = new Chart(ctx, {
    type: "bar",
    data: {
      labels: ["Focus Group", "User Testing", "Survey Panel", "AI Simulator"],
      datasets: [{
        label: "Cost per Study ($)",
        data: [15000, 8000, 3000, 0.50],
        backgroundColor: [
          "rgba(220,38,38,0.15)",
          "rgba(220,38,38,0.1)",
          "rgba(234,179,8,0.15)",
          "rgba(22,163,74,0.18)",
        ],
        borderColor: [
          "#dc2626",
          "#ef4444",
          "#d97706",
          "#16a34a",
        ],
        borderWidth: 2,
        borderRadius: 6,
      }],
    },
    options: {
      animation: { duration: 1000, easing: "easeOutQuart" },
      plugins: {
        legend: { display: false },
        tooltip: {
          callbacks: {
            label: (ctx) => {
              const v = ctx.raw;
              return v < 1 ? ` $${v.toFixed(2)}` : ` $${v.toLocaleString()}`;
            },
          },
        },
      },
      scales: {
        y: {
          type: "logarithmic",
          grid: { color: "#f1f5f9" },
          ticks: {
            color: "#64748b",
            font: { size: 11 },
            callback: (v) => {
              if (v === 0.5) return "$0.50";
              if (v >= 1000) return "$" + (v/1000) + "K";
              return "$" + v;
            },
          },
        },
        x: {
          grid: { display: false },
          ticks: { color: "#64748b", font: { size: 11 } },
        },
      },
    },
  });
}

// ---------------------------------------------------------------------------
// Chart.js theme: update grid/label colors for light theme
// ---------------------------------------------------------------------------

// Override defaults once so all charts get the light-mode palette
Chart.defaults.color = "#64748b";
Chart.defaults.borderColor = "#f1f5f9";
