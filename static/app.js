const state = {
  scan: null,
  plan: null,
};

const $ = (id) => document.getElementById(id);

function fmtMoney(value) {
  return new Intl.NumberFormat("en-US", {
    style: "currency",
    currency: "USD",
    maximumFractionDigits: 0,
  }).format(value || 0);
}

function tagClass(status) {
  if (status === "高风险") return "tag high";
  if (status === "偏高") return "tag warn";
  if (status === "正常" || status === "可控") return "tag ok";
  if (status === "loss") return "tag loss";
  if (status === "profit") return "tag profit";
  return "tag warn";
}

function setScore(score) {
  $("riskScore").textContent = score;
  const deg = Math.round((score / 100) * 360);
  let color = "var(--green)";
  if (score >= 75) color = "var(--amber)";
  if (score >= 88) color = "var(--red)";
  $("scoreRing").style.background = `conic-gradient(${color} 0deg, ${color} ${deg}deg, #e7ebe7 ${deg}deg 360deg)`;
}

function renderPositions(rows) {
  const maxWeight = Math.max(...rows.map((row) => row.weight_pct), 1);
  $("positions").innerHTML = rows
    .map((row) => {
      const width = Math.max(4, (row.weight_pct / maxWeight) * 100);
      const ret = Number(row.return_5d || 0);
      const retClass = ret >= 0 ? "var(--green)" : "var(--red)";
      return `
        <div class="position-row">
          <div>
            <div class="symbol">${row.symbol}</div>
            <div class="position-meta" style="color:${retClass}">${ret >= 0 ? "+" : ""}${ret.toFixed(1)}%</div>
          </div>
          <div class="bar"><span style="width:${width}%"></span></div>
          <div class="weight">${row.weight_pct.toFixed(1)}%</div>
        </div>
      `;
    })
    .join("");
}

function renderAlerts(scan) {
  const alerts = [...scan.profit_alerts, ...scan.loss_alerts];
  if (!alerts.length) {
    $("alerts").innerHTML = `<div class="alert-item"><p>当前没有触发利润保护或回撤控制提醒。</p></div>`;
    return;
  }
  $("alerts").innerHTML = alerts
    .map(
      (alert) => `
        <div class="alert-item">
          <div class="module-topline">
            <h3>${alert.title}</h3>
            <span class="${tagClass(alert.severity)}">${alert.severity === "profit" ? "浮盈保护" : "回撤控制"}</span>
          </div>
          <p>${alert.evidence}</p>
          <p>${alert.meaning}</p>
          <ul>${alert.actions.map((item) => `<li>${item}</li>`).join("")}</ul>
        </div>
      `
    )
    .join("");
}

function renderModules(modules) {
  $("modules").innerHTML = modules
    .map(
      (mod) => `
        <div class="module-item">
          <div class="module-topline">
            <h3>${mod.title}</h3>
            <span class="${tagClass(mod.status)}">${mod.status}</span>
          </div>
          <p><strong>证据：</strong>${mod.evidence}</p>
          <p><strong>影响：</strong>${mod.impact}</p>
          <p><strong>变化：</strong>${mod.change}</p>
        </div>
      `
    )
    .join("");
}

function renderScan(scan) {
  state.scan = scan;
  $("asOf").textContent = `数据更新时间：${scan.as_of}`;
  if (scan.data_mode) {
    $("portfolioMode").textContent = scan.data_mode.portfolio || "Portfolio Input";
    $("marketMode").textContent = scan.data_mode.market || "Market Data";
  }
  $("riskLevel").textContent = scan.risk_level;
  $("headline").textContent = scan.headline;
  $("totalValue").textContent = fmtMoney(scan.total_value);
  $("trendDay").textContent = scan.risk_trend.day;
  $("trendWeek").textContent = `${scan.risk_trend.week} (${scan.risk_trend.week_delta >= 0 ? "+" : ""}${scan.risk_trend.week_delta})`;
  $("trendMonth").textContent = `${scan.risk_trend.month} (${scan.risk_trend.month_delta >= 0 ? "+" : ""}${scan.risk_trend.month_delta})`;
  setScore(scan.risk_score);
  renderPositions(scan.top_positions);
  renderAlerts(scan);
  renderModules(scan.modules);
}

function intentLabel(intent) {
  if (intent === "protect_profit") return "利润保护";
  if (intent === "control_loss") return "回撤控制";
  return "买入评估";
}

function fmtMaybe(value, suffix = "") {
  if (value === null || value === undefined || value === "" || Number.isNaN(Number(value))) return "--";
  return `${Number(value).toFixed(2)}${suffix}`;
}

function renderTarget(target) {
  if (!target) return;
  $("targetSymbol").textContent = target.symbol || "--";
  $("targetPosition").textContent = `${target.quantity || 0} 股 / ${fmtMaybe(target.weight_pct, "%")}`;
  $("targetPrice").textContent = fmtMaybe(target.price);
  $("targetReturn5d").textContent = fmtMaybe(target.return_5d, "%");
  $("targetReturn20d").textContent = fmtMaybe(target.return_20d, "%");
  $("targetSupport").textContent = `${target.support_near || "--"} / ${target.support_major || "--"}`;
  $("targetResistance").textContent = `${target.resistance || "--"} / ${target.next_resistance || "--"}`;
}

function renderPlan(plan) {
  state.plan = plan;
  $("detectedSymbol").textContent = plan.symbol;
  $("intentLabel").textContent = intentLabel(plan.intent);
  $("planHeadline").textContent = plan.headline;
  renderTarget(plan.target);
  $("planSections").innerHTML = plan.sections
    .map(
      (section) => `
        <div class="plan-section">
          <h3>${section.title}</h3>
          <ul>${section.bullets.map((item) => `<li>${item}</li>`).join("")}</ul>
        </div>
      `
    )
    .join("");
  $("recommended").innerHTML = plan.recommended.map((item) => `<li>${item}</li>`).join("");
  $("avoid").innerHTML = plan.avoid.map((item) => `<li>${item}</li>`).join("");
}

async function loadScan() {
  const response = await fetch("/api/scan");
  renderScan(await response.json());
}

async function sendPlan(query) {
  const response = await fetch("/api/plan", {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ query }),
  });
  renderPlan(await response.json());
}

function bindEvents() {
  $("sendBtn").addEventListener("click", () => {
    sendPlan($("queryInput").value.trim() || "我想买特斯拉");
  });
  $("queryInput").addEventListener("keydown", (event) => {
    if (event.key === "Enter") {
      sendPlan($("queryInput").value.trim() || "我想买特斯拉");
    }
  });
  document.querySelectorAll("[data-query]").forEach((button) => {
    button.addEventListener("click", () => {
      const query = button.getAttribute("data-query");
      $("queryInput").value = query;
      sendPlan(query);
    });
  });
}

async function init() {
  bindEvents();
  await loadScan();
  await sendPlan($("queryInput").value);
}

init().catch((error) => {
  console.error(error);
  $("headline").textContent = "应用启动失败，请检查本地服务。";
});
