/* 后厨刀具流转 —— 前端逻辑（无框架，原生 JS） */
"use strict";

const $ = (s, r = document) => r.querySelector(s);
const $$ = (s, r = document) => [...r.querySelectorAll(s)];
const api = async (path, body) => {
  const res = await fetch(path, {
    method: "POST", headers: { "Content-Type": "application/json" },
    body: JSON.stringify(body || {}),
  });
  const data = await res.json().catch(() => ({ ok: false, error: "响应解析失败" }));
  if (!res.ok || data.ok === false) throw new Error(data.error || `请求失败 ${res.status}`);
  return data;
};
const esc = (s) => (s == null ? "" : String(s).replace(/[&<>"']/g,
  (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c])));
const fmtTs = (s) => s ? s.replace("T", " ").slice(0, 16) : "—";

/* ---------- 刀型轮廓（轮廓 + 文字共同表示刀型） ---------- */
const PROFILES = {
  chef:    { blade: "M2 17 C18 6 55 4 86 9 L86 14 C55 17 25 20 2 20 Z", hx: 86, w: 12 },
  santoku: { blade: "M3 17 C25 8 60 6 84 10 L82 19 L3 19 Z", hx: 84, w: 12 },
  slicer:  { blade: "M2 13.5 L80 12 L80 18 L2 19 Z", hx: 80, w: 17 },
  cleaver: { blade: "M3 5 L58 5 L58 25 L3 25 Z", hx: 58, w: 39 },
  paring:  { blade: "M2 16 C14 10 36 9 52 12 L52 18 C33 19.5 14 20 2 19 Z", hx: 52, w: 45 },
  boning:  { blade: "M2 16 C22 9 60 10 80 13.5 L80 16.5 C58 19 22 20 2 17 Z", hx: 80, w: 17 },
  utility: { blade: "M2 16 C18 9.5 55 8.5 78 12.5 L78 17.5 C50 19.5 20 20 2 19 Z", hx: 78, w: 19 },
  bread:   { blade: "M2 13 L82 11.5 L82 15.5 l-4.5 3.5 l-4.5 -3.5 l-4.5 3.5 l-4.5 -3.5 l-4.5 3.5 l-4.5 -3.5 l-4.5 3.5 l-4.5 -3.5 l-4.5 3.5 l-4.5 -3.5 l-4.5 3.5 l-4.5 -3.5 l-4.5 3.5 l-4.5 -3.5 l-4.5 3.5 l-4.5 -3.5 L2 18 Z", hx: 82, w: 15 },
  peeler:  { blade: "M4 11 C4 6 20 6 20 12 C20 18 10 20 6 24", hx: 24, w: 73 },
};

function knifeSvg(profile, cls = "") {
  const p = PROFILES[profile] || PROFILES.utility;
  return `<svg class="knife-svg ${cls}" viewBox="0 0 100 30" preserveAspectRatio="xMidYMid meet">
    <path d="${p.blade}" fill="#c8ced3" stroke="#8d979e" stroke-width="1" stroke-linejoin="round"/>
    <rect x="${p.hx}" y="10.5" width="${p.w}" height="9.5" rx="2.5" fill="#2e3338"/>
    <line x1="${p.hx + 3}" y1="15.2" x2="${p.hx + p.w - 3}" y2="15.2" stroke="#f5c400" stroke-width="1"/>
  </svg>`;
}

/* ---------- 全局状态 ---------- */
let STATE = null;
const EXTRA_STAFF = ["值班主管", "老李", "王强"];
const staffList = () => {
  const set = new Set(EXTRA_STAFF);
  STATE.knives.forEach((k) => { set.add(k.holder); if (k.current_user) set.add(k.current_user); });
  STATE.jobs.forEach((j) => { [j.requested_by, j.sharpened_by, j.checked_by].forEach((n) => n && set.add(n)); });
  return [...set].sort();
};
const knifeById = (id) => STATE.knives.find((k) => k.id === id);
const stationById = (id) => STATE.stations.find((s) => s.id === id);

/* ============================================================
   渲染：刀架图
   ============================================================ */
function renderRacks() {
  $("#racks").innerHTML = STATE.stations.map((st) => {
    const slots = [];
    for (let r = 0; r < st.rack_rows; r++) {
      for (let c = 0; c < st.rack_cols; c++) {
        const slotId = `${"ABCDEFG"[r]}-${String(c + 1).padStart(2, "0")}`;
        slots.push(renderSlot(st, slotId));
      }
    }
    const missing = STATE.knives.filter((k) =>
      k.home_station_id === st.id && k.status === "在用").length;
    return `<div class="rack">
      <div class="rack-title"><span>${esc(st.name)} · ${esc(st.id)}</span>
      <span class="tag">${missing ? `缺件 ${missing} 把` : "槽位齐全"}</span></div>
      <div class="rack-body" style="grid-template-columns:repeat(${st.rack_cols},1fr)">${slots.join("")}</div>
    </div>`;
  }).join("");
}

function knifeCard(k, extraClass = "") {
  const stateBadge = {
    "在用": `<span class="state-badge badge-out">${esc(k.current_user)} 领用中</span>`,
    "待修": `<span class="state-badge badge-q">待修·${esc(k.issue_type || "")}</span>`,
    "遗失": `<span class="state-badge badge-lost">遗失冻结</span>`,
    "磨刀中": `<span class="state-badge badge-care">${k.flags.some((f) => f.kind === "未复检") ? "待复检" : "磨刀中"}</span>`,
    "在架": k.issue_type === "错放"
      ? `<span class="state-badge badge-care">错放·发现于 ${esc(k.found_station_id || "他架")}</span>`
      : `<span class="state-badge badge-out">在架</span>`,
  }[k.status] || "";
  const flagDots = k.flags.filter((f) => f.kind !== "缺口" && f.kind !== "松柄" &&
    f.kind !== "遗失" && f.kind !== "未复检").map((f) => {
    const cls = f.kind === "超期" ? "dot-overdue" : "dot-misplace";
    return `<span class="flag-dot ${cls}" title="${esc(f.label)}"></span>`;
  }).join("");
  return `<div class="slot-inner">
    <div class="slot-id">${esc(k.slot)} ${flagDots}</div>
    ${knifeSvg(k.profile)}
    <div class="knife-meta">
      <span class="knife-name">${esc(k.name)} <span class="knife-code">${esc(k.id)}</span></span>
      ${stateBadge}
    </div>
  </div>`;
}

function renderSlot(st, slotId) {
  const home = STATE.knives.find((k) => k.home_station_id === st.id && k.slot === slotId);
  const misplaced = STATE.knives.find((k) =>
    k.issue_type === "错放" && k.found_station_id === st.id && (k.found_slot || "") === slotId);
  const click = (id) => `onclick="openDrawer('${id}')"`;

  if (home) {
    if (home.status === "在用") {
      return `<div class="slot empty">
        <div class="slot-id">${esc(slotId)} · ${esc(home.id)} 的槽位</div>
        <span class="missing-tag">缺件</span>
        <div style="font-size:12px;color:#3d434a">${esc(home.name)} · ${esc(home.current_user)} 领用未还</div>
      </div>`;
    }
    const cls = home.status === "待修" ? "quarantined"
      : home.status === "遗失" ? "lost"
      : home.status === "磨刀中" ? "incare"
      : home.issue_type === "错放" ? "incare" : "";
    return `<div class="slot ${cls}" ${click(home.id)}>${knifeCard(home)}</div>`;
  }
  if (misplaced) {
    return `<div class="slot incare" ${click(misplaced.id)}>
      <div class="slot-inner">
        <div class="slot-id">${esc(slotId)} · 非本架刀具</div>
        ${knifeSvg(misplaced.profile)}
        <div class="knife-meta">
          <span class="knife-name">${esc(misplaced.name)} <span class="knife-code">${esc(misplaced.id)}</span></span>
          <span class="state-badge badge-care">错放·应在 ${esc(misplaced.slot)}</span>
        </div>
      </div>
    </div>`;
  }
  return `<div class="slot empty"><div class="slot-id">${esc(slotId)}</div>
    <div style="font-size:11px;color:#9aa4ac">空槽（未登记刀具）</div></div>`;
}

/* ============================================================
   渲染：领还扫码侧栏（突出缺件 / 未复检 / 超期）
   ============================================================ */
function alertItem(k, detail) {
  return `<div class="alert-item" onclick="openDrawer('${k.id}')">
    <span><span class="code">${esc(k.id)}</span> ${esc(k.name)} · ${esc(detail)}</span>
    <span>›</span></div>`;
}
function renderScanSide() {
  const missing = STATE.knives.filter((k) => k.status === "在用");
  const uncheckedJobs = STATE.jobs.filter((j) => j.status === "待复检");
  const overdue = STATE.knives.filter((k) =>
    !["遗失", "磨刀中"].includes(k.status) && k.flags.some((f) => f.kind === "超期"));

  $("#missingList").innerHTML = missing.length
    ? missing.map((k) => alertItem(k, `${k.current_user} 领用中`)).join("")
    : `<div class="alert-empty">无缺件，刀架齐全</div>`;
  $("#uncheckedList").innerHTML = uncheckedJobs.length
    ? uncheckedJobs.map((j) => alertItem(knifeById(j.knife_id) || { id: j.knife_id },
        `磨刀人 ${esc(j.sharpened_by)}，待他人复检`)).join("")
    : `<div class="alert-empty">没有待复检刀具</div>`;
  $("#overdueList").innerHTML = overdue.length
    ? overdue.map((k) => {
        const f = k.flags.find((x) => x.kind === "超期");
        return alertItem(k, f.label);
      }).join("")
    : `<div class="alert-empty">磨刀排程均在周期内</div>`;
}

/* ============================================================
   渲染：保养排程
   ============================================================ */
function statusText(k) {
  if (k.status === "待修") return `<span class="pill pill-reject">待修·${esc(k.issue_type || "")}</span>`;
  if (k.status === "遗失") return `<span class="pill pill-reject">遗失冻结</span>`;
  if (k.status === "磨刀中") return k.flags.some((f) => f.kind === "未复检")
    ? `<span class="pill pill-check">待复检（不得发放）</span>`
    : `<span class="pill pill-grind">磨刀中</span>`;
  if (k.status === "在用") return `<span class="pill pill-grind">${esc(k.current_user)} 在用</span>`;
  const od = k.flags.find((f) => f.kind === "超期");
  return od ? `<span class="overdue-cell">⚠ ${esc(od.label)}</span>`
    : `<span style="color:#2f8f5b">● 周期内</span>`;
}

function renderCare() {
  $("#cycleTable").querySelector("tbody").innerHTML = STATE.knives.map((k) => {
    const canSend = ["在架"].includes(k.status) && !k.quarantined;
    return `<tr>
      <td><span class="code-link" onclick="openDrawer('${k.id}')">${esc(k.id)}</span></td>
      <td>${esc(k.name)}</td>
      <td>${esc(k.home_station_id)} / ${esc(k.slot)}</td>
      <td>${k.last_sharpen_ts ? fmtTs(k.last_sharpen_ts) : '<span class="overdue-cell">从未磨刀</span>'}</td>
      <td>${k.sharpen_cycle_d} 天</td>
      <td>${statusText(k)}</td>
      <td>${canSend ? `<button class="btn btn-dark btn-mini" onclick="askSendSharpen('${k.id}')">送磨</button>` : ""}</td>
    </tr>`;
  }).join("");

  const jobCls = { "待磨刀": "wait-grind", "待复检": "wait-check", "通过": "pass", "驳回": "reject" };
  const jobPill = { "待磨刀": "pill-grind", "待复检": "pill-check", "通过": "pill-pass", "驳回": "pill-reject" };
  $("#jobList").innerHTML = STATE.jobs.length ? STATE.jobs.map((j) => {
    const k = knifeById(j.knife_id);
    let actions = "";
    if (j.status === "待磨刀") {
      actions = `<button class="btn btn-dark btn-mini" onclick="askCompleteSharpen(${j.id})">登记磨刀完成</button>
        <span class="hint">完成后须由另一人复检</span>`;
    } else if (j.status === "待复检") {
      actions = `<button class="btn btn-yellow btn-mini" onclick="askCheckSharpen(${j.id})">复检锋利度/刀身</button>
        <span class="hint">复检人不可为磨刀人「${esc(j.sharpened_by)}」</span>`;
    }
    const photo = j.photo_path ? `<div class="job-photo"><img src="/api/photos/${esc(j.photo_path)}" alt="刃口照片"></div>` : "";
    return `<div class="job-card ${jobCls[j.status]}">
      <div class="job-head">
        <strong>#${j.id} ${esc(j.knife_id)} ${esc(j.knife_name || "")}</strong>
        <span class="pill ${jobPill[j.status]}">${esc(j.status)}</span>
      </div>
      <div class="job-meta">
        <span>送磨：${esc(j.requested_by)} · ${fmtTs(j.request_ts)}</span>
        <span>磨刀：${j.sharpened_by ? esc(j.sharpened_by) + " · " + fmtTs(j.sharpened_ts) : "—"}</span>
        <span>刃口：${esc(j.edge_state || "—")}</span>
        <span>复检：${j.checked_by ? esc(j.checked_by) + " · " + esc(j.check_result) : "—"}</span>
      </div>
      ${photo}
      ${j.check_note ? `<p class="hint" style="margin-top:6px">复检备注：${esc(j.check_note)}</p>` : ""}
      <div class="job-actions">${actions}</div>
    </div>`;
  }).join("") : `<p class="alert-empty">暂无磨刀工单</p>`;
}

/* ============================================================
   渲染：异常隔离
   ============================================================ */
function renderQuarantine() {
  const bad = STATE.knives.filter((k) => k.quarantined || k.issue_type);
  $("#issueList").innerHTML = bad.length ? bad.map((k) => {
    const cls = k.status === "遗失" ? "lost"
      : k.issue_type === "错放" ? "misplace"
      : ["缺口", "松柄", "复检驳回"].includes(k.issue_type) ? (k.issue_type === "松柄" ? "loose" : "chip") : "";
    const action = {
      "遗失": "遗失找回登记",
      "待修": k.issue_type === "复检驳回" ? "返修后归架" : "修毕归架",
      "在架": "错放归位",
    }[k.status] || "解除隔离";
    return `<div class="issue-card ${cls}">
      <div class="issue-head">
        <span class="code">${esc(k.id)}</span>
        <span class="pill pill-reject">${esc(k.status)}${k.issue_type ? "·" + esc(k.issue_type) : ""}</span>
      </div>
      <div style="display:flex;gap:10px;align-items:center">
        <div style="width:110px;flex:none">${knifeSvg(k.profile)}</div>
        <div>
          <div><strong>${esc(k.name)}</strong> · ${esc(k.home_station_id)} ${esc(k.slot)}</div>
          <div class="issue-note">${esc(k.issue_note || "（无备注）")}</div>
        </div>
      </div>
      <div class="issue-foot">
        ${k.found_station_id ? `实际发现：${esc(k.found_station_id)} ${esc(k.found_slot || "")} · ` : ""}
        最后接触人：<strong>${esc(k.last_handler || "—")}</strong>
        ${k.issue_ts ? ` · 登记于 ${fmtTs(k.issue_ts)}` : ""}
      </div>
      <div class="issue-actions">
        <button class="btn btn-yellow btn-mini" onclick="askRestore('${k.id}','${esc(action)}')">${esc(action)}</button>
        <button class="btn btn-ghost btn-mini" onclick="openDrawer('${k.id}')">流转记录</button>
      </div>
    </div>`;
  }).join("") : `<p class="alert-empty">当前没有隔离 / 冻结刀具</p>`;
}

/* ============================================================
   渲染：台账
   ============================================================ */
function renderLedger() {
  const f = $("#ledgerFilter").value.trim().toUpperCase();
  const rows = STATE.recent.filter((m) => !f || (m.knife_id || "").toUpperCase().includes(f));
  $("#ledgerTable").querySelector("tbody").innerHTML = rows.map((m) => {
    const bad = /缺口|松柄|遗失|驳回/.test(m.action);
    const good = /通过|归架|找回|归位/.test(m.action);
    return `<tr style="${bad ? "background:#fbe6e4" : good ? "background:#e3f3ea" : ""}">
      <td class="tl-ts">${fmtTs(m.ts)}</td>
      <td>${m.knife_id ? `<span class="code-link" onclick="openDrawer('${esc(m.knife_id)}')">${esc(m.knife_id)}</span>` : "—"}</td>
      <td><strong>${esc(m.action)}</strong></td>
      <td>${esc(m.actor)}</td>
      <td>${m.to_station ? `${esc(m.to_station)} ${esc(m.to_slot || "")}` : "—"}</td>
      <td>${esc(m.note || "")}</td>
    </tr>`;
  }).join("");
}

/* ============================================================
   刀具详情抽屉
   ============================================================ */
async function openDrawer(id) {
  if (!id) return;
  const d = await (await fetch(`/api/knife/${encodeURIComponent(id)}`)).json();
  const flags = (d.flags || []).map((f) =>
    `<span class="pill pill-reject" style="margin-right:4px">${esc(f.label)}</span>`).join("") || "无异常标记";
  const photos = (d.jobs || []).filter((j) => j.photo_path)
    .map((j) => `<div class="job-photo"><img src="/api/photos/${esc(j.photo_path)}">
      <div class="hint">磨刀单 #${j.id} · ${fmtTs(j.sharpened_ts)} · ${esc(j.sharpened_by)}</div></div>`).join("");
  const tlCls = (a) => /缺口|松柄|遗失|驳回|错放/.test(a) ? "bad"
    : /通过|归架|找回|归位|关班/.test(a) ? "good"
    : /磨刀|送磨/.test(a) ? "warn" : "";
  $("#drawerBody").innerHTML = `
    <h3>${esc(d.id)} · ${esc(d.name)}</h3>
    <div style="width:70%;margin:8px 0">${knifeSvg(d.profile)}</div>
    <div>${flags}</div>
    <dl class="kv">
      <dt>归属工位</dt><dd>${esc(stationById(d.home_station_id)?.name || d.home_station_id)} · 槽位 ${esc(d.slot)}</dd>
      <dt>责任人</dt><dd>${esc(d.holder)}</dd>
      <dt>当前状态</dt><dd>${esc(d.status)}${d.current_user ? " · " + esc(d.current_user) + " 领用中" : ""}</dd>
      <dt>磨刀周期</dt><dd>${d.sharpen_cycle_d} 天（上次：${d.last_sharpen_ts ? fmtTs(d.last_sharpen_ts) : "从未"}）</dd>
      <dt>最后接触人</dt><dd><strong>${esc(d.last_handler || "—")}</strong></dd>
      ${d.issue_note ? `<dt>异常备注</dt><dd>${esc(d.issue_note)}</dd>` : ""}
    </dl>
    ${photos ? `<h3>刃口照片（本地存储）</h3>${photos}` : ""}
    <h3 style="margin-top:14px">逐刀流转链</h3>
    <ul class="timeline">
      ${d.timeline.map((m) => `<li class="${tlCls(m.action)}">
        <div class="tl-ts">${fmtTs(m.ts)}${m.shift_name ? " · " + esc(m.shift_name) : ""}</div>
        <div class="tl-action">${esc(m.action)} · ${esc(m.actor)}</div>
        ${m.note ? `<div class="tl-note">${esc(m.note)}</div>` : ""}
      </li>`).join("")}
    </ul>`;
  $("#drawer").classList.remove("hidden");
  $("#drawerMask").classList.remove("hidden");
}
function closeDrawer() {
  $("#drawer").classList.add("hidden");
  $("#drawerMask").classList.add("hidden");
}

/* ============================================================
   通用弹窗
   ============================================================ */
function modal(title, bodyHtml, onOk) {
  $("#modalTitle").textContent = title;
  $("#modalBody").innerHTML = bodyHtml;
  $("#modalMask").classList.remove("hidden");
  const ok = $("#modalOk");
  const cancel = $("#modalCancel");
  const close = () => $("#modalMask").classList.add("hidden");
  ok.onclick = async () => {
    try {
      await onOk();
      close();
    } catch (e) { toast(e.message, "err"); }
  };
  cancel.onclick = close;
}
const staffOptions = (sel) => staffList().map((n) =>
  `<option value="${esc(n)}" ${n === sel ? "selected" : ""}>${esc(n)}</option>`).join("");
const knifeOptions = () => STATE.knives.map((k) =>
  `<option value="${esc(k.id)}">${esc(k.id)} ${esc(k.name)}（${esc(k.status)}）</option>`).join("");
const stationOptions = () => STATE.stations.map((s) =>
  `<option value="${esc(s.id)}">${esc(s.id)} ${esc(s.name)}</option>`).join("");

/* ---------- 班次 ---------- */
$("#btnStartShift").onclick = () => modal("开始新班次",
  `<label>班次名称</label><input id="mShiftName" value="班次日 ${new Date().toLocaleString("zh-CN").slice(5, 16)}">`,
  async () => { await api("/api/shift/start", { name: $("#mShiftName").value }); });

$("#btnHandover").onclick = () => modal("班次跨日交接",
  `<label>新班次名称</label><input id="mNewShift" value="次日早市班">
   <label>交接值班人</label><select id="mActor">${staffOptions("值班主管")}</select>
   <label>备注</label><textarea id="mNote">跨日 23:50 → 次日 00:10，未归还刀随班移交</textarea>
   <p class="hint" style="margin-top:8px">未归还的刀会逐刀记录交接与接收，领用人不变。</p>`,
  async () => {
    const r = await api("/api/shift/handover", {
      name: $("#mNewShift").value, actor: $("#mActor").value, note: $("#mNote").value,
    });
    toast(`交接完成，${r.transferred.length} 把未还刀已随班移交`, "warn");
  });

$("#btnCloseShift").onclick = () => modal("关班核对",
  `<label>值班人</label><select id="mActor">${staffOptions("值班主管")}</select>
   <p class="hint" style="margin-top:8px">逐件清洗、核对归还完成后才能关班；仍有刀未归还将被拒绝。</p>`,
  async () => { await api("/api/shift/close", { actor: $("#mActor").value }); });

/* ---------- 扫码 ---------- */
async function doScan() {
  const code = $("#scanCode").value.trim().toUpperCase();
  const user = $("#scanUser").value;
  const box = $("#scanResult");
  if (!code) return;
  try {
    const mode = $("input[name=scanMode]:checked").value;
    if (mode === "auto") {
      await api("/api/scan", { code, actor: user });
      box.className = "scan-result ok";
      box.textContent = `已扫描 ${code}，系统按当前状态自动判定领用 / 归还（结果见实时反馈）`;
    } else if (mode === "checkout") {
      await api("/api/checkout", { knife_id: code, user });
      box.className = "scan-result ok";
      box.textContent = `${code} 已发放给 ${user}`;
    } else {
      await api("/api/checkin", { knife_id: code, user, washed: $("#scanWashed").checked });
      box.className = "scan-result ok";
      box.textContent = `${code} 已清洗核对归还`;
    }
    $("#scanCode").value = "";
    $("#scanCode").focus();
  } catch (e) {
    box.className = "scan-result err";
    box.textContent = "✕ " + e.message;
  }
}
$("#scanGo").onclick = doScan;
$("#scanCode").addEventListener("keydown", (e) => { if (e.key === "Enter") doScan(); });

$("#simPlay").onclick = async () => {
  const codes = $("#simCodes").value.split(/[,，\s]+/).filter(Boolean);
  if (!codes.length) return;
  await api("/api/scanner/script", { codes, actor: $("#simActor").value || "扫码枪", interval: 1.2 });
  simLog(`▶ 扫码序列开始（${codes.length} 件，操作人 ${$("#simActor").value}）`, "");
};
$("#simStop").onclick = async () => { await api("/api/scanner/script", { stop: true }); };
function simLog(msg, cls) {
  const box = $("#simLog");
  const line = document.createElement("div");
  line.className = `line ${cls}`;
  line.textContent = `[${new Date().toLocaleTimeString("zh-CN")}] ${msg}`;
  box.appendChild(line);
  box.scrollTop = box.scrollHeight;
}

/* ---------- 异常 ---------- */
$("#btnReportIssue").onclick = () => modal("登记刀具异常",
  `<label>刀具</label><select id="mKnife">${knifeOptions()}</select>
   <label>异常类型</label><select id="mIssue">
     <option>缺口</option><option>松柄</option><option>错放</option><option>遗失</option>
   </select>
   <label>实际发现工位（错放时填写）</label><select id="mFound" disabled>${stationOptions()}</select>
   <label>登记人</label><select id="mActor">${staffOptions("值班主管")}</select>
   <label>情况说明</label><textarea id="mNote" placeholder="如：刃口近刀尖 4mm 缺口"></textarea>`,
  async () => {
    const type = $("#mIssue").value;
    await api("/api/issue", {
      knife_id: $("#mKnife").value, issue_type: type, actor: $("#mActor").value,
      note: $("#mNote").value, found_station: type === "错放" ? $("#mFound").value : null,
    });
  });
// 弹窗内容是动态注入的，事件在打开时绑定
document.addEventListener("change", (e) => {
  if (e.target.id === "mIssue") $("#mFound").disabled = e.target.value !== "错放";
});

function askRestore(id, action) {
  modal(`${action} · ${id}`,
    `<label>处理人</label><select id="mActor">${staffOptions("值班主管")}</select>
     <label>备注</label><textarea id="mNote" placeholder="如：缺口已打磨焊补，试切合格"></textarea>`,
    async () => { await api("/api/restore", {
      knife_id: id, actor: $("#mActor").value, note: $("#mNote").value,
    }); });
}

/* ---------- 磨刀 / 复检 ---------- */
function askSendSharpen(id) {
  modal(`送磨 · ${id}`,
    `<label>送磨人</label><select id="mActor">${staffOptions("值班主管")}</select>
     <label>备注</label><textarea id="mNote" placeholder="保养排程到期 / 厨师反映偏钝"></textarea>`,
    async () => { await api("/api/sharpen/request", {
      knife_id: id, actor: $("#mActor").value, note: $("#mNote").value,
    }); });
}

function askCompleteSharpen(jobId) {
  modal(`磨刀完成 · 工单 #${jobId}`,
    `<label>磨刀人</label><select id="mActor">${staffOptions("老李")}</select>
     <label>刃口状态</label><select id="mEdge">
       <option>锋利</option><option>合格</option><option>偏钝</option></select>
     <label>刃口照片（本地存储，可选）</label><input type="file" id="mPhoto" accept="image/*">
     <label>备注</label><textarea id="mNote"></textarea>
     <p class="hint" style="margin-top:8px">提交后刀具进入「待复检」，<strong>磨刀人本人不能复检</strong>。</p>`,
    async () => {
      const f = $("#mPhoto").files[0];
      const photo = f ? await fileToDataUrl(f) : null;
      await api("/api/sharpen/complete", {
        job_id: jobId, knife_id: STATE.jobs.find((j) => j.id === jobId).knife_id,
        actor: $("#mActor").value, edge_state: $("#mEdge").value, photo, note: $("#mNote").value,
      });
    });
}

function askCheckSharpen(jobId) {
  const job = STATE.jobs.find((j) => j.id === jobId);
  const others = staffList().filter((n) => n !== job.sharpened_by);
  modal(`复检 · 工单 #${jobId}`,
    `<p class="hint">磨刀人：<strong>${esc(job.sharpened_by)}</strong>。
       若选择与磨刀人相同的名字，系统将拒绝提交。</p>
     <label>复检人（须为另一人）</label><select id="mInspector">${staffOptions(others[0] || "周敏")}</select>
     <label>复检结果（锋利度 + 刀身完整性）</label>
     <select id="mResult"><option value="通过">通过，归架并重置磨刀周期</option>
       <option value="驳回">驳回，隔离待修</option></select>
     <label>复检备注</label><textarea id="mNote" placeholder="如：试切番茄顺滑，刀身无裂纹"></textarea>`,
    async () => {
      const inspector = $("#mInspector").value;
      if (inspector === job.sharpened_by) throw new Error("磨刀者不能复检自己的工作");
      await api("/api/sharpen/check", {
        job_id: jobId, inspector, result: $("#mResult").value, note: $("#mNote").value,
      });
    });
}

function fileToDataUrl(file) {
  return new Promise((res, rej) => {
    const r = new FileReader();
    r.onload = () => res(r.result);
    r.onerror = rej;
    r.readAsDataURL(file);
  });
}

/* ============================================================
   Toast / 顶栏 / SSE
   ============================================================ */
function toast(msg, kind = "") {
  const t = document.createElement("div");
  t.className = `toast ${kind}`;
  t.textContent = msg;
  $("#toasts").appendChild(t);
  setTimeout(() => t.remove(), 3600);
}

function renderChrome() {
  const sh = STATE.shift;
  const b = $("#shiftBadge");
  if (sh) {
    b.className = "shift-badge live";
    b.textContent = `● ${sh.name} · ${fmtTs(sh.start_ts)} 起`;
  } else {
    b.className = "shift-badge";
    b.textContent = "无进行中班次";
  }
  $("#simMode").textContent = STATE.scanner_mode === "auto" ? "自动播放中" : "手动";
  $("#simMode").className = "sim-mode" + (STATE.scanner_mode === "auto" ? " auto" : "");
  if (!$("#scanUser").dataset.filled) {
    $("#scanUser").innerHTML = staffList().map((n) => `<option>${esc(n)}</option>`).join("");
  }
}

async function refresh() {
  const keepUser = $("#scanUser").value;
  STATE = await (await fetch("/api/state")).json();
  renderChrome();
  if (keepUser) { $("#scanUser").value = keepUser; $("#scanUser").dataset.filled = "1"; }
  renderRacks();
  renderScanSide();
  renderCare();
  renderQuarantine();
  renderLedger();
}

function connectSSE() {
  const es = new EventSource("/api/events");
  const rerender = () => refresh();
  ["issue", "restore", "sharpen", "shift"].forEach((ev) =>
    es.addEventListener(ev, (e) => {
      const p = JSON.parse(e.data).payload || {};
      if (p.msg) toast(p.msg, ev === "issue" ? "err" : ev === "sharpen" ? "warn" : "ok");
      rerender();
    }));
  es.addEventListener("scan", (e) => {
    const p = JSON.parse(e.data).payload;
    simLog(`扫描 ${p.code}（${p.actor}）`, "");
  });
  es.addEventListener("checkout", (e) => {
    const p = JSON.parse(e.data).payload;
    simLog(`→ ${p.msg}`, "ok");
    toast(p.msg, "ok");
    rerender();
  });
  es.addEventListener("checkin", (e) => {
    const p = JSON.parse(e.data).payload;
    simLog(`← ${p.msg}`, "ok");
    toast(p.msg, "ok");
    rerender();
  });
  es.addEventListener("scan_rejected", (e) => {
    const p = JSON.parse(e.data).payload;
    simLog(`✕ ${p.code} 被拦截：${p.msg}`, "err");
    toast(p.msg, "err");
  });
  es.addEventListener("reminder", (e) => {
    const p = JSON.parse(e.data).payload;
    const parts = [];
    if (p.overdue.length) parts.push(`${p.overdue.length} 把磨刀超期`);
    if (p.unchecked.length) parts.push(`${p.unchecked.length} 把待复检`);
    if (parts.length) toast("⏳ 保养提醒：" + parts.join("，"), "warn");
  });
  es.onerror = () => { /* 浏览器会自动重连 */ };
}

/* ---------- 页签 / 抽屉 / 筛选 ---------- */
$$(".tab").forEach((t) => t.onclick = () => {
  $$(".tab").forEach((x) => x.classList.remove("active"));
  $$(".panel").forEach((x) => x.classList.remove("active"));
  t.classList.add("active");
  $(`#tab-${t.dataset.tab}`).classList.add("active");
});
$("#drawerClose").onclick = closeDrawer;
$("#drawerMask").onclick = closeDrawer;
$("#modalMask").addEventListener("click", (e) => { if (e.target.id === "modalMask") e.target.classList.add("hidden"); });
$("#ledgerFilter").addEventListener("input", renderLedger);

refresh().then(connectSSE);
