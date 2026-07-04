const state = {
  scan: null,
  plan: null,
  sessionId: getSessionId(),
};

const $ = (id) => document.getElementById(id);

function getSessionId() {
  const key = "portfolio_guard_session";
  const existing = window.localStorage.getItem(key);
  if (existing) return existing;
  const next =
    window.crypto && window.crypto.randomUUID
      ? window.crypto.randomUUID()
      : `session-${Date.now()}-${Math.random().toString(16).slice(2)}`;
  window.localStorage.setItem(key, next);
  return next;
}

function apiHeaders(extra = {}) {
  return {
    "X-Guard-Session": state.sessionId,
    ...extra,
  };
}

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

function setUploadStatus(text, tone = "idle") {
  const node = $("uploadStatus");
  node.textContent = text;
  node.dataset.tone = tone;
}

function intentLabel(intent) {
  if (intent === "protect_profit") return "利润保护";
  if (intent === "control_loss") return "回撤控制";
  if (intent === "unknown") return "待确认";
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

function renderTrace(trace) {
  const items = trace || [];
  $("traceList").innerHTML = items.length
    ? items
        .map(
          (item) =>
            `<span class="trace-chip"><strong>${item.tool}</strong>${item.status}: ${item.detail}</span>`
        )
        .join("")
    : `<span class="trace-chip"><strong>Agent</strong>ready</span>`;
}

function renderPlan(plan) {
  state.plan = plan;
  $("detectedSymbol").textContent = plan.symbol;
  $("intentLabel").textContent = intentLabel(plan.intent);
  $("planHeadline").textContent = plan.headline;
  renderTarget(plan.target);
  renderTrace(plan.trace);
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
  const response = await fetch("/api/scan", { headers: apiHeaders() });
  renderScan(await response.json());
}

async function sendPlan(query) {
  const response = await fetch("/api/plan", {
    method: "POST",
    headers: apiHeaders({ "Content-Type": "application/json" }),
    body: JSON.stringify({ query }),
  });
  renderPlan(await response.json());
}

function readFileAsDataUrl(file) {
  return new Promise((resolve, reject) => {
    const reader = new FileReader();
    reader.onload = () => resolve(String(reader.result || ""));
    reader.onerror = () => reject(reader.error || new Error("read failed"));
    reader.readAsDataURL(file);
  });
}

async function uploadPortfolioImages(files) {
  const selected = Array.from(files || []);
  if (!selected.length) return;
  if (selected.length > 6) {
    setUploadStatus("一次最多上传 6 张截图", "error");
    return;
  }
  if (selected.some((file) => file.size > 6_500_000)) {
    setUploadStatus("单张图片过大，请换截图", "error");
    return;
  }
  const totalSize = selected.reduce((sum, file) => sum + file.size, 0);
  if (totalSize > 18_000_000) {
    setUploadStatus("图片总大小过大，请分批上传", "error");
    return;
  }
  const button = $("uploadPortfolioBtn");
  button.disabled = true;
  button.textContent = "识别中";
  setUploadStatus(`正在读取 ${selected.length} 张截图`, "loading");
  try {
    const images = await Promise.all(selected.map((file) => readFileAsDataUrl(file)));
    setUploadStatus(`正在识别 ${selected.length} 张截图`, "loading");
    const response = await fetch("/api/portfolio/upload", {
      method: "POST",
      headers: apiHeaders({ "Content-Type": "application/json" }),
      body: JSON.stringify({ images }),
    });
    const payload = await response.json();
    if (!response.ok || !payload.ok) {
      const detail = (payload.trace || []).map((item) => item.detail).join("；") || payload.error;
      throw new Error(detail || "识别失败");
    }
    renderScan(payload.scan);
    setUploadStatus(`已从 ${payload.image_count || selected.length} 张图识别 ${payload.positions.length} 个持仓`, "ok");
    await sendPlan($("queryInput").value.trim() || "我想买特斯拉");
  } catch (error) {
    console.error(error);
    setUploadStatus(`识别失败：${error.message}`, "error");
  } finally {
    button.disabled = false;
    button.textContent = "更新持仓";
    $("portfolioImageInput").value = "";
  }
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
  $("uploadPortfolioBtn").addEventListener("click", () => {
    $("portfolioImageInput").click();
  });
  $("portfolioImageInput").addEventListener("change", (event) => {
    uploadPortfolioImages(event.target.files);
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
