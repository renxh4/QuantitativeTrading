const el = (id) => document.getElementById(id);

const wsStatus = el("wsStatus");
const activeSymbol = el("activeSymbol");
const msgCountEl = el("msgCount");
const lastUpdateEl = el("lastUpdate");
const jsStatusEl = el("jsStatus");
const debugLineEl = el("debugLine");
const debugLine2El = el("debugLine2");
const pollCountEl = el("pollCount");
const logBoxEl = el("logBox");
const btnClearLog = el("btnClearLog");

const symEl = el("sym");
const priceEl = el("price");
const signalEl = el("signal");
const reasonEl = el("reason");
const maSEl = el("maS");
const maLEl = el("maL");
const rsiEl = el("rsi");

const cashEl = el("cash");
const equityEl = el("equity");
const lastOrderEl = el("lastOrder");

const posTableBody = document.querySelector("#posTable tbody");

const fmt = (x, digits = 4) => (x === null || x === undefined ? "-" : Number(x).toFixed(digits));
const fmtMoney = (x) => (x === null || x === undefined ? "-" : Number(x).toFixed(2));

function tsLocal() {
  const d = new Date();
  return d.toTimeString().slice(0, 8);
}

const LOG_MAX = 300;
let logLines = [];
function logLine(level, msg, obj) {
  const levelCN = level === "ERROR" ? "错误" : level === "WARN" ? "警告" : "信息";
  const base = `[${tsLocal()}] ${levelCN} ${msg}`;
  let line = base;
  if (obj !== undefined) {
    try {
      line += " " + JSON.stringify(obj);
    } catch (e) {
      line += " " + String(obj);
    }
  }
  logLines.push(line);
  if (logLines.length > LOG_MAX) logLines = logLines.slice(logLines.length - LOG_MAX);
  if (logBoxEl) {
    logBoxEl.textContent = logLines.join("\n");
    logBoxEl.scrollTop = logBoxEl.scrollHeight;
  }
  // also mirror to console
  if (level === "ERROR") console.error(msg, obj);
  else if (level === "WARN") console.warn(msg, obj);
  else console.log(msg, obj);
}

if (btnClearLog) {
  btnClearLog.addEventListener("click", () => {
    logLines = [];
    if (logBoxEl) logBoxEl.textContent = "";
  });
}

jsStatusEl.textContent = "JS:OK";
jsStatusEl.textContent = "脚本:正常";
logLine("INFO", "前端脚本已加载", { ua: navigator.userAgent });

let chart;
const chartData = {
  labels: [],
  datasets: [
    {
      label: "Price",
      data: [],
      borderColor: "rgba(99, 163, 255, 0.95)",
      backgroundColor: "rgba(99, 163, 255, 0.12)",
      tension: 0.2,
      fill: true,
      pointRadius: 0,
    },
  ],
};

function initChart() {
  // Chart.js is loaded from CDN. If user has no internet, degrade gracefully:
  if (!window.Chart) {
    console.warn("Chart.js not loaded; running without chart.");
    logLine("WARN", "图表库 Chart.js 未加载（可能网络无法访问 CDN），将不显示曲线");
    chart = null;
    return;
  }

  const canvas = document.getElementById("priceChart");
  const ctx = canvas.getContext("2d");
  chart = new window.Chart(ctx, {
    type: "line",
    data: chartData,
    options: {
      responsive: true,
      animation: false,
      scales: {
        x: { display: false },
        y: { ticks: { color: "rgba(232,240,255,0.8)" }, grid: { color: "rgba(255,255,255,0.06)" } },
      },
      plugins: {
        legend: { labels: { color: "rgba(232,240,255,0.85)" } },
      },
    },
  });
}

function setWS(ok) {
  wsStatus.textContent = ok ? "已连接" : "未连接";
  wsStatus.className = ok ? "pill pill-good" : "pill pill-warn";
}

function renderPositions(positions) {
  posTableBody.innerHTML = "";
  (positions || []).forEach((p) => {
    const tr = document.createElement("tr");
    tr.innerHTML = `<td>${p.symbol}</td><td class="mono">${p.qty}</td><td class="mono">${fmt(p.avg_price, 4)}</td>`;
    posTableBody.appendChild(tr);
  });
}

function updateUI(msg) {
  if (msg.type !== "tick") return;
  msgCountEl.textContent = String(Number(msgCountEl.textContent || "0") + 1);
  lastUpdateEl.textContent = (msg.ts || "").slice(11, 19) || "--:--:--";

  const symbol = msg.symbol;
  activeSymbol.textContent = symbol;

  symEl.textContent = symbol;
  priceEl.textContent = fmt(msg.price, 4);
  signalEl.textContent = msg.signal || "-";
  reasonEl.textContent = (msg.signal_meta && msg.signal_meta.reason) || "-";

  const ind = msg.indicators || {};
  maSEl.textContent = ind.ma_short == null ? "-" : fmt(ind.ma_short, 4);
  maLEl.textContent = ind.ma_long == null ? "-" : fmt(ind.ma_long, 4);
  rsiEl.textContent = ind.rsi == null ? "-" : fmt(ind.rsi, 2);

  const broker = msg.broker || {};
  cashEl.textContent = fmtMoney(broker.cash);
  equityEl.textContent = fmtMoney(broker.equity);
  lastOrderEl.textContent = broker.last_order ? JSON.stringify(broker.last_order) : "-";
  renderPositions(broker.positions);

  // chart
  if (chart) {
    const ts = (msg.ts || "").slice(11, 19); // HH:MM:SS
    chartData.labels.push(ts);
    chartData.datasets[0].data.push(msg.price);
    if (chartData.labels.length > 240) {
      chartData.labels.shift();
      chartData.datasets[0].data.shift();
    }
    chart.update();
  }
}

let wsRetryMs = 500;
let wsEverConnected = false;
let wsCurrent = null;
let wsKeepaliveTimer = null;
let wsReconnectTimer = null;

function wsStateName(ws) {
  // 0 CONNECTING, 1 OPEN, 2 CLOSING, 3 CLOSED
  if (!ws) return "no_ws";
  const s = ws.readyState;
  if (s === 0) return "CONNECTING";
  if (s === 1) return "OPEN";
  if (s === 2) return "CLOSING";
  if (s === 3) return "CLOSED";
  return String(s);
}

function startWS() {
  // Ensure single active connection attempt
  if (wsCurrent && (wsCurrent.readyState === WebSocket.CONNECTING || wsCurrent.readyState === WebSocket.OPEN)) {
    return;
  }
  if (wsReconnectTimer) {
    clearTimeout(wsReconnectTimer);
    wsReconnectTimer = null;
  }

  const url = (location.protocol === "https:" ? "wss://" : "ws://") + location.host + "/ws";
  logLine("INFO", "正在连接 WebSocket", { url });
  debugLineEl.textContent = `debug: ws=${url} mode=ws`;
  const ws = new WebSocket(url);
  wsCurrent = ws;
  debugLine2El.textContent = `debug2: ws_state=${wsStateName(ws)}`;

  const wsStateTimer = setInterval(() => {
    debugLine2El.textContent = `debug2: ws_state=${wsStateName(ws)} retry_ms=${wsRetryMs}`;
    if (ws.readyState === WebSocket.CLOSED) clearInterval(wsStateTimer);
  }, 500);

  ws.onopen = () => {
    setWS(true);
    wsEverConnected = true;
    wsRetryMs = 500;
    debugLineEl.textContent = `debug: ws=${url} mode=ws connected`;
    logLine("INFO", "WebSocket 已连接");

    // keepalive (per-connection)
    if (wsKeepaliveTimer) clearInterval(wsKeepaliveTimer);
    wsKeepaliveTimer = setInterval(() => {
      if (ws.readyState === WebSocket.OPEN) ws.send("ping");
    }, 15000);

    // help some servers/proxies establish flow
    try {
      ws.send("hello");
    } catch (e) {
      // ignore
    }
  };
  ws.onclose = (evt) => {
    setWS(false);
    reasonEl.textContent = `连接关闭 code=${evt.code}`;
    logLine("WARN", "WebSocket 连接关闭", { code: evt.code, reason: evt.reason });
    debugLineEl.textContent = `debug: ws=${url} mode=ws close_code=${evt.code} retry_ms=${wsRetryMs}`;

    if (wsKeepaliveTimer) {
      clearInterval(wsKeepaliveTimer);
      wsKeepaliveTimer = null;
    }
    if (wsCurrent === ws) wsCurrent = null;

    if (evt.code === 1012) {
      logLine("WARN", "服务端重启导致断开(1012)。如果你用的是 uvicorn --reload，建议改用无 reload 的稳定模式。");
    }

    // auto-retry
    wsReconnectTimer = setTimeout(() => startWS(), wsRetryMs);
    wsRetryMs = Math.min(8000, wsRetryMs * 2);
  };
  ws.onerror = (evt) => {
    setWS(false);
    reasonEl.textContent = "连接错误";
    logLine("ERROR", "WebSocket 连接错误", { event: String(evt && evt.type ? evt.type : evt) });
    debugLineEl.textContent = `debug: ws=${url} mode=ws error retry_ms=${wsRetryMs}`;
  };

  ws.onmessage = (evt) => {
    try {
      const msg = JSON.parse(evt.data);
      if (msg.type === "snapshot") {
        // ignore; UI will update on first tick
        logLine("INFO", "收到快照", { symbols: (msg.data && msg.data.symbols) || [] });
        return;
      }
      if (msg.type === "error") {
        msgCountEl.textContent = String(Number(msgCountEl.textContent || "0") + 1);
        lastUpdateEl.textContent = (msg.ts || "").slice(11, 19) || "--:--:--";
        activeSymbol.textContent = msg.symbol || "-";
        reasonEl.textContent = msg.error || "provider_error";
        signalEl.textContent = "错误";
        logLine("ERROR", "数据源错误", { symbol: msg.symbol, error: msg.error });
        return;
      }
      // tick
      if ((msgCountEl.textContent || "0") === "1") {
        logLine("INFO", "收到第一条行情", { symbol: msg.symbol });
      }
      updateUI(msg);
    } catch (e) {
      logLine("WARN", "WebSocket 消息解析失败", { err: String(e) });
    }
  };

  // keepalive timer is managed in ws.onopen and cleared on close
}

async function pollSnapshotOnce() {
  try {
    pollCountEl.textContent = `轮询:${Number(String(pollCountEl.textContent || "轮询:0").split(":")[1] || "0") + 1}`;
    const r = await fetch("/api/snapshot", { cache: "no-store" });
    if (!r.ok) {
      debugLineEl.textContent = `debug: polling /api/snapshot http=${r.status}`;
      logLine("WARN", "轮询 /api/snapshot 失败", { status: r.status });
      return;
    }
    const snap = await r.json();
    // If we have no tick yet, at least show account + last tick if present
    if (snap && snap.last) {
      cashEl.textContent = fmtMoney(snap.cash);
      equityEl.textContent = fmtMoney(snap.equity);
      renderPositions(snap.positions || []);

      const sym = (snap.symbols && snap.symbols[0]) || null;
      if (sym && snap.last[sym] && snap.last[sym].tick) {
        const t = snap.last[sym].tick;
        activeSymbol.textContent = sym;
        symEl.textContent = sym;
        priceEl.textContent = fmt(t.price, 4);
        lastUpdateEl.textContent = (snap.ts || "").slice(11, 19) || "--:--:--";
      }
    }

    // If WS never connects, explicitly say we're in polling mode.
    if (!wsEverConnected) {
      const ts = (snap.ts || "").slice(11, 19) || "--:--:--";
      debugLineEl.textContent = `debug: ws=blocked? mode=polling snapshot_ts=${ts}`;
      if ((pollCountEl.textContent || "轮询:0") === "轮询:1") {
        logLine("WARN", "WebSocket 未连接，已自动使用轮询兜底");
      }
    }
  } catch (e) {
    debugLineEl.textContent = `debug: polling error=${String(e)}`;
    logLine("ERROR", "轮询 /api/snapshot 异常", { err: String(e) });
  }
}

initChart();
startWS();
// Fallback if WS is blocked: still show changing snapshot data
setInterval(pollSnapshotOnce, 2000);
pollSnapshotOnce();

