"""页面渲染：刀架图、领还扫码台、保养排程、异常隔离、复检、追溯。

配色：不锈钢白 / 炭灰 为主，安全黄仅用于异常与提醒。
刀型一律「轮廓 + 文字」双重表示。
"""
from __future__ import annotations

import html
import json
from typing import Any

from .service import (EDGE_LABELS, EVENT_LABELS, KNIFE_TYPES, REASON_LABELS,
                      STATUS_LABELS)

# ---------- 刀型轮廓（0 0 64 64，刀尖朝左/刃口朝下）----------

BLADE_PATHS = {
    "chef":    "M6 38 L40 9 C49 11 54 19 52 29 L12 43 Z",
    "slicer":  "M6 32 L48 17 C55 19 56 26 50 30 L10 40 Z",
    "cleaver": "M8 14 L52 14 L52 42 L8 42 Z",
    "santoku": "M8 40 L42 12 L53 13 C57 22 53 31 45 35 L12 44 Z",
    "paring":  "M12 38 L38 19 C45 21 46 28 40 32 L15 42 Z",
    "boning":  "M7 36 C22 30 36 18 48 14 C50 22 40 33 26 39 L14 43 Z",
}
HANDLE_PATH = "M6 38 L-3 44 L1 51 L14 44 Z"

STATUS_STYLE = {
    "in_rack":     {"blade": "#c7ccd2", "edge": "#3e4248", "badge": "ok"},
    "issued":      {"blade": "#8d949c", "edge": "#2e3136", "badge": "char"},
    "sharpening":  {"blade": "#e9d28a", "edge": "#8a6d1f", "badge": "yellow"},
    "quarantine":  {"blade": "#f2c42f", "edge": "#5c4a00", "badge": "yellow"},
    "repair":      {"blade": "#d98a82", "edge": "#7a2a22", "badge": "red"},
    "lost":        {"blade": "none",    "edge": "#c8463a", "badge": "red"},
}

BADGE_CLASS = {
    "ok": "badge ok", "char": "badge char",
    "yellow": "badge yellow", "red": "badge red",
}


def esc(v: Any) -> str:
    return html.escape("" if v is None else str(v))


def knife_svg(ktype: str, status: str = "in_rack", size: int = 46) -> str:
    st = STATUS_STYLE.get(status, STATUS_STYLE["in_rack"])
    blade = BLADE_PATHS.get(ktype, BLADE_PATHS["chef"])
    fill = f'fill="{st["blade"]}"' if st["blade"] != "none" \
        else 'fill="none" stroke-dasharray="4 3"'
    return f'''<svg class="knife-svg" width="{size}" height="{size}" viewBox="-8 4 68 52"
 aria-label="{esc(KNIFE_TYPES.get(ktype, ktype))}轮廓" role="img">
<path d="{HANDLE_PATH}" fill="#3e4248"/>
<path d="{blade}" {fill} stroke="{st["edge"]}" stroke-width="1.6" stroke-linejoin="round"/>
<line x1="10" y1="42" x2="50" y2="30" stroke="{st["edge"]}" stroke-width="1.1"/>
</svg>'''


def badge(status: str) -> str:
    cls = STATUS_STYLE.get(status, STATUS_STYLE["in_rack"])["badge"]
    return f'<span class="{BADGE_CLASS[cls]}">{esc(STATUS_LABELS.get(status, status))}</span>'


CSS = """
:root{--steel:#f4f5f6;--steel2:#e7eaec;--panel:#fff;--line:#d4d8dc;--char:#2e3136;
--muted:#6b7077;--yellow:#f2c42f;--yellow-bg:#fff6d6;--red:#c8463a;--red-bg:#fdeceb;
--green:#3d7a5a;--blue:#33587a;}
*{box-sizing:border-box}
body{margin:0;font-family:"PingFang SC","Microsoft YaHei",system-ui,sans-serif;
background:var(--steel);color:var(--char);font-size:14px}
header{background:linear-gradient(180deg,#fbfcfd,#e9ecef);border-bottom:2px solid var(--char);
padding:10px 22px;display:flex;align-items:center;gap:22px;flex-wrap:wrap}
header h1{font-size:17px;margin:0;letter-spacing:2px}
nav a{color:var(--char);text-decoration:none;margin-right:16px;padding:5px 10px;border-radius:4px}
nav a:hover{background:#fff;box-shadow:0 0 0 1px var(--line)}
nav a.active{background:var(--char);color:#fff}
.wrap{padding:18px 22px;max-width:1280px;margin:0 auto}
.panel{background:var(--panel);border:1px solid var(--line);border-radius:8px;padding:14px 16px;margin-bottom:16px}
.panel h2{font-size:15px;margin:0 0 10px;border-left:4px solid var(--char);padding-left:8px}
.grid{display:grid;gap:14px}
.rack{background:repeating-linear-gradient(90deg,#dfe3e6 0 2px,transparent 2px 86px),
repeating-linear-gradient(0deg,#d7dbde 0 2px,transparent 2px 92px),#eef0f2;
border:2px solid #9aa1a8;border-radius:10px;padding:12px}
.rack-grid{display:grid;grid-template-columns:repeat(4,1fr);gap:8px}
.slot{background:#fbfcfd;border:1px solid #c4c9ce;border-radius:6px;padding:7px 8px;min-height:104px;
display:flex;flex-direction:column;gap:3px;position:relative}
.slot .slotid{font-size:11px;color:var(--muted);letter-spacing:1px}
.slot .krow{display:flex;align-items:center;gap:6px}
.slot .kname{font-weight:600;font-size:13px}
.slot .ktype{font-size:11px;color:var(--muted)}
.slot.empty{border-style:dashed;background:#f3f5f6;color:#a7adb3;justify-content:center;align-items:center}
.slot.missing{border-color:var(--red);background:var(--red-bg)}
.slot.warn{border-color:#c9a526;background:var(--yellow-bg)}
.slot.out{border-color:#7e878f;background:#eef0f2}
.badge{display:inline-block;padding:1px 7px;border-radius:10px;font-size:11px;font-weight:600}
.badge.ok{background:#e3ede7;color:var(--green)}
.badge.char{background:var(--char);color:#fff}
.badge.yellow{background:var(--yellow);color:#4a3d00}
.badge.red{background:var(--red);color:#fff}
table{border-collapse:collapse;width:100%;font-size:13px}
th,td{border-bottom:1px solid var(--line);padding:7px 8px;text-align:left;vertical-align:middle}
th{background:var(--steel2);font-weight:600}
tr.danger td{background:var(--red-bg)}
tr.warn td{background:var(--yellow-bg)}
.btn{display:inline-block;border:1px solid var(--char);background:var(--char);color:#fff;
padding:5px 12px;border-radius:5px;cursor:pointer;font-size:13px;text-decoration:none}
.btn.sec{background:#fff;color:var(--char)}
.btn.y{background:var(--yellow);border-color:#c9a526;color:#3d3300}
.btn.r{background:var(--red);border-color:var(--red)}
.btn:disabled{opacity:.45;cursor:not-allowed}
.btn.mini{padding:2px 8px;font-size:12px}
.reminders{border:2px solid var(--yellow);background:var(--yellow-bg);border-radius:8px;padding:10px 14px;margin-bottom:16px}
.reminders .r{padding:3px 0}
.reminders .danger{color:var(--red);font-weight:600}
.reminders h3{margin:0 0 6px;font-size:13px;color:#5c4a00}
.flash{padding:9px 13px;border-radius:6px;margin-bottom:12px}
.flash.ok{background:#e3ede7;border:1px solid var(--green);color:var(--green)}
.flash.err{background:var(--red-bg);border:1px solid var(--red);color:#8c2b22}
.flash.info{background:#e7edf2;border:1px solid var(--blue)}
.scanstage{display:grid;grid-template-columns:1fr 320px;gap:14px}
.field{margin:6px 0}
.field label{display:inline-block;width:86px;color:var(--muted)}
select,input[type=text]{padding:5px 8px;border:1px solid #b6bcc2;border-radius:4px;min-width:150px}
.codepill{font-family:ui-monospace,Menlo,monospace;background:var(--char);color:var(--yellow);
padding:2px 9px;border-radius:4px;font-size:13px}
.scanlist button{margin:2px 3px}
.miss-strip{display:flex;gap:10px;flex-wrap:wrap;margin-bottom:10px}
.miss-strip .chip{border-radius:16px;padding:4px 12px;font-size:12px;font-weight:600}
.chip.red{background:var(--red);color:#fff}
.chip.y{background:var(--yellow);color:#4a3d00}
.chip.gray{background:#d7dbde;color:var(--char)}
.timeline{border-left:3px solid var(--line);margin-left:8px;padding-left:16px}
.timeline .ev{position:relative;padding:6px 0}
.timeline .ev::before{content:"";position:absolute;left:-23px;top:11px;width:9px;height:9px;
border-radius:50%;background:var(--char)}
.timeline .ev.bad::before{background:var(--red)}
.timeline .ev.good::before{background:var(--green)}
.ts{color:var(--muted);font-size:11px;margin-right:8px}
.photo{border:1px solid var(--line);border-radius:6px;padding:6px;background:#fff;display:inline-block}
.photo img{display:block;width:230px;image-rendering:pixelated;border:1px solid var(--line)}
.photo .cap{font-size:11px;color:var(--muted);margin-top:4px}
.legend{display:flex;gap:14px;flex-wrap:wrap;font-size:12px;color:var(--muted);margin-top:8px}
.legend i{display:inline-block;width:12px;height:12px;border-radius:2px;margin-right:4px;vertical-align:-1px}
h3.sub{font-size:13px;color:var(--muted);margin:14px 0 6px}
.hold{font-size:12px;color:var(--muted)}
"""


def shell(title: str, active: str, body: str) -> str:
    nav = [("rack", "刀架图", "/"), ("scan", "领还扫码", "/scan"),
           ("maint", "保养排程", "/maintenance"), ("quar", "异常隔离", "/quarantine")]
    links = "".join(
        f'<a href="{url}" class="{"active" if key == active else ""}">{label}</a>'
        for key, label, url in nav)
    return f"""<!doctype html><html lang="zh"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<title>{esc(title)} · 后厨刀具管控</title><style>{CSS}</style></head>
<body><header><h1>🔪 后厨刀具管控台</h1><nav>{links}</nav></header>
<main class="wrap">{body}</main></body></html>"""


def reminders_bar(items: list[dict]) -> str:
    if not items:
        return ""
    rows = "".join(
        f'<div class="r {esc(it["level"])}">● {esc(it["msg"])}</div>' for it in items[:20])
    more = f'<div class="r">…另有 {len(items) - 20} 条</div>' if len(items) > 20 else ""
    return f'<div class="reminders"><h3>⚠ 进程内提醒（{len(items)}）</h3>{rows}{more}</div>'


def flash(kind: str, msg: str) -> str:
    return f'<div class="flash {kind}">{esc(msg)}</div>'


# ---------- 刀架图 ----------

def rack_page(rack: list[dict], reminders: list[dict]) -> str:
    station_html = []
    for block in rack:
        st = block["station"]
        cells = []
        for item in block["slots"]:
            slot = item["slot"]
            k = item["knife"]
            head = f'<span class="slotid">{esc(slot["id"])} · {esc(slot["rack"])}-{slot["slot_row"]}排{slot["slot_col"]}列</span>'
            if not k:
                cells.append(f'<div class="slot empty">{head}<div>空槽</div></div>')
                continue
            status = k["status"]
            cls = "slot"
            tag = ""
            if status == "lost":
                cls += " missing"
                tag = '<span class="badge red">缺件·遗失</span>'
            elif status == "issued":
                cls += " out"
                tag = f'<span class="badge char">领用中 {esc(k["current_holder_id"] or "")}</span>'
            elif status == "quarantine":
                cls += " warn"
                reason = REASON_LABELS.get(k["quarantine_reason"] or "", "隔离")
                tag = f'<span class="badge yellow">{esc(reason)}</span>'
            elif status == "repair":
                cls += " missing"
                tag = '<span class="badge red">待修离架</span>'
            elif status == "sharpening":
                cls += " warn"
                tag = '<span class="badge yellow">磨刀/待复检</span>'
            else:
                tag = badge(status)
            cells.append(
                f'<div class="{cls}">{head}'
                f'<div class="krow">{knife_svg(k["type"], status)}'
                f'<div><div class="kname">{esc(k["id"])}</div>'
                f'<div class="ktype">{esc(KNIFE_TYPES.get(k["type"], k["type"]))} · {esc(EDGE_LABELS.get(k["edge_state"], k["edge_state"]))}</div></div></div>'
                f'<div>{tag} <a class="btn mini sec" href="/trace?id={esc(k["id"])}">追溯</a></div></div>')
        station_html.append(
            f'<div class="panel"><h2>{esc(st["name"])}（{esc(st["zone"])}）· 刀架 R1</h2>'
            f'<div class="rack"><div class="rack-grid">{"".join(cells)}</div></div></div>')
    legend = ('<div class="legend"><span><i style="background:#c7ccd2"></i>在架</span>'
              '<span><i style="background:#8d949c"></i>领用中（槽位空）</span>'
              '<span><i style="background:#f2c42f"></i>磨刀/隔离</span>'
              '<span><i style="background:#c8463a"></i>遗失/待修缺件</span>'
              '<span><i style="background:none;border:1px dashed #a7adb3"></i>空槽</span></div>')
    body = reminders_bar(reminders) + "".join(station_html) + legend
    return shell("刀架图", "rack", body)


# ---------- 领还扫码台 ----------

def scan_page(ctx: dict) -> str:
    role_names = {"chef": "厨师", "sharpener": "磨刀岗", "inspector": "复检岗", "manager": "经理"}
    staff_opts_parts = []
    for s in ctx["staff"]:
        sel = "selected" if s["id"] == ctx["actor_id"] else ""
        staff_opts_parts.append(
            f'<option value="{esc(s["id"])}" {sel}>{esc(s["id"])} {esc(s["name"])}'
            f'（{esc(role_names[s["role"]])}）</option>')
    staff_opts = "".join(staff_opts_parts)
    shift_opts_parts = []
    for sh in ctx["shifts"]:
        sel = "selected" if sh["id"] == ctx["shift_id"] else ""
        state = "已收班" if sh["ended_at"] else "进行中"
        when = sh["started_at"][:16].replace("T", " ")
        shift_opts_parts.append(
            f'<option value="{sh["id"]}" {sel}>#{sh["id"]} {esc(sh["name"])}'
            f'（{esc(when)} {state}）</option>')
    shift_opts = "".join(shift_opts_parts)

    missing = [k for k in ctx["knives"] if k["status"] == "lost"]
    pending = [k for k in ctx["knives"] if k["pending_reinspect"]
               or k["quarantine_reason"] in ("found_pending", "repair_pending")]
    chips = "".join(f'<span class="chip red">缺件 {esc(k["id"])}</span>' for k in missing)
    chips += "".join(
        f'<span class="chip y">未复检 {esc(k["id"])}</span>' for k in pending)
    issued = [k for k in ctx["knives"] if k["status"] == "issued"]
    chips += "".join(
        f'<span class="chip gray">领用 {esc(k["id"])}→{esc(k["current_holder_id"])}</span>'
        for k in issued)
    strip = f'<div class="miss-strip">{chips or "<span class=hold>当前无缺件/未复检项</span>"}</div>'

    rows = "".join(
        f'<tr class="{"danger" if k["status"]=="lost" else "warn" if k["status"] in ("quarantine","sharpening","repair") else ""}">'
        f'<td class="codepill">{esc(k["id"])}</td>'
        f'<td>{knife_svg(k["type"], k["status"], 30)} {esc(KNIFE_TYPES.get(k["type"], k["type"]))}</td>'
        f'<td>{badge(k["status"])}</td><td>{esc(EDGE_LABELS.get(k["edge_state"], k["edge_state"]))}</td>'
        f'<td>{esc(k["current_holder_id"] or "—")}</td>'
        f'<td>{esc(k["home_slot_id"])}</td><td><a class="btn mini sec" href="/trace?id={esc(k["id"])}">追溯</a></td></tr>'
        for k in ctx["knives"])

    scan_btns = []
    for s in ctx["staff"]:
        scan_btns.append(f'<button class="btn mini sec" onclick="emit(\'{s["id"]}\')">工牌 {esc(s["id"])}</button>')
    for k in ctx["knives"]:
        scan_btns.append(f'<button class="btn mini {"r" if k["status"]=="lost" else "sec"}" onclick="emit(\'{k["id"]}\')">刀 {esc(k["id"])}</button>')
    for sl in ctx["slots"]:
        scan_btns.append(f'<button class="btn mini sec" onclick="emit(\'{sl["id"]}\')">槽 {esc(sl["id"])}</button>')

    body = f"""
{reminders_bar(ctx["reminders"])}
{ctx.get("flash", "")}
<div class="panel">
  <h2>领还扫码台</h2>
  {strip}
  <div class="scanstage">
    <div>
      <form method="post" action="/scan/action">
        <div class="field"><label>当前班次</label><select name="shift_id">{shift_opts}</select></div>
        <div class="field"><label>操作人工牌</label><select name="actor_id">{staff_opts}</select>
          <span class="hold">（扫码枪扫工牌会自动选中）</span></div>
        <div class="field"><label>刀具条码</label><input type="text" id="knife_code" name="knife_id" placeholder="K-001" required></div>
        <div class="field"><label>槽位条码</label><input type="text" id="slot_code" name="slot_id" placeholder="归还时扫刀架槽位"></div>
        <h3 class="sub">班次开始 → 领刀</h3>
        <button class="btn" name="op" value="issue">领刀（独占登记）</button>
        <h3 class="sub">班次结束 → 逐件清洗、核对归还</h3>
        <button class="btn y" name="op" value="return_clean">清洗完好 · 扫槽归位</button>
        <button class="btn sec" name="op" value="return_dull">刃钝送磨</button>
        <button class="btn y" name="op" value="return_loose">刀柄松动 · 隔离</button>
        <button class="btn r" name="op" value="return_chipped">刃口缺口 · 隔离</button>
        <button class="btn sec" name="op" value="report_lost">上报遗失（冻结）</button>
        <h3 class="sub">班次与跨班交接</h3>
        <button class="btn sec" name="op" value="close_shift">收班核对（有未还刀将拒绝）</button>
        <input type="text" name="to_chef" placeholder="交接给厨师工号 E02" style="min-width:120px">
        <input type="text" name="to_shift" placeholder="下一班次#" style="min-width:90px">
        <button class="btn sec" name="op" value="handover">跨班交接</button>
      </form>
      <form method="post" action="/shift/start" style="margin-top:10px">
        <input type="text" name="name" placeholder="新班次名，如 晚班" required>
        <input type="text" name="leader_id" placeholder="值班经理 M01" value="M01">
        <button class="btn y">开新班</button>
      </form>
    </div>
    <div>
      <h3 class="sub">模拟扫码枪</h3>
      <p class="hold">点击条码即模拟一次扫描，事件经适配器队列推送，扫码台自动填入。</p>
      <div class="scanlist">{"".join(scan_btns)}</div>
      <p class="hold" id="scanst">适配器待取事件：0</p>
    </div>
  </div>
</div>
<div class="panel"><h2>逐刀状态</h2>
<table><thead><tr><th>刀号</th><th>刀型</th><th>状态</th><th>刃口</th><th>持有人</th><th>归属槽位</th><th></th></tr></thead>
<tbody>{rows}</tbody></table></div>
<script>
async function emit(code){{
  await fetch('/api/scan/emit', {{method:'POST',headers:{{'Content-Type':'application/json'}},
    body:JSON.stringify({{code}})}});
}}
setInterval(async ()=>{{
  const r = await fetch('/api/scan/drain'); const d = await r.json();
  document.getElementById('scanst').textContent = '适配器待取事件：' + d.pending;
  for (const e of d.events) {{
    if (e.kind === 'knife') {{document.getElementById('knife_code').value = e.code;}}
    else if (e.kind === 'slot') {{document.getElementById('slot_code').value = e.code;}}
    else if (e.kind === 'staff') {{
      const sel = document.querySelector('[name=actor_id]');
      [...sel.options].some(o=>{{if(o.value===e.code){{sel.value=e.code;return true;}}}});
    }}
  }}
}}, 1200);
</script>"""
    return shell("领还扫码", "scan", body)


# ---------- 保养排程 ----------

def maintenance_page(ctx: dict) -> str:
    def row(k):
        due = k["next_sharpen_due"] or "—"
        cls = ""
        if k["status"] == "sharpening":
            cls = "warn"
        elif k["next_sharpen_due"] and k["next_sharpen_due"] < ctx["now_iso"]:
            cls = "danger"
        action = ""
        if k["status"] == "in_rack":
            action = (f'<form method="post" action="/sharpen/send" style="display:inline">'
                      f'<input type="hidden" name="knife_id" value="{esc(k["id"])}">'
                      f'<input type="hidden" name="actor_id" value="{esc(ctx["actor_id"])}">'
                      f'<button class="btn mini y">送磨</button></form>')
        elif k["status"] == "sharpening" and not k["pending_reinspect"]:
            action = (f'<form method="post" action="/sharpen/done" style="display:inline">'
                      f'<input type="hidden" name="knife_id" value="{esc(k["id"])}">'
                      f'<input type="hidden" name="sharpener_id" value="{esc(ctx["actor_id"])}">'
                      f'<button class="btn mini">磨刀完成</button></form>')
        elif k["pending_reinspect"]:
            action = (f'<span class="badge yellow">待复检（磨刀人 {esc(k["sharpened_by"])}）</span> '
                      f'<a class="btn mini" href="/reinspect?id={esc(k["id"])}">去复检</a>')
        else:
            action = "—"
        return (f'<tr class="{cls}"><td class="codepill">{esc(k["id"])}</td>'
                f'<td>{knife_svg(k["type"], k["status"], 30)} {esc(KNIFE_TYPES.get(k["type"], k["type"]))}</td>'
                f'<td>{badge(k["status"])}</td>'
                f'<td>{esc((k["last_sharpen_at"] or "—")[:10])}</td>'
                f'<td>{esc(due[:10] if due != "—" else due)}</td>'
                f'<td>{k["sharpen_interval_days"]} 天</td><td>{action}</td></tr>')

    body = f"""
{reminders_bar(ctx["reminders"])}
{ctx.get("flash", "")}
<div class="panel"><h2>保养排程（磨刀周期与复检）</h2>
<p class="hold">超期刀具在领刀台会被直接拦下；磨刀完成后必须由<b>磨刀者之外</b>的复检岗检查锋利度与刀身完整性。</p>
<table><thead><tr><th>刀号</th><th>刀型</th><th>状态</th><th>上次磨刀</th><th>下次到期</th><th>周期</th><th>操作</th></tr></thead>
<tbody>{"".join(row(k) for k in ctx["knives"])}</tbody></table></div>"""
    return shell("保养排程", "maint", body)


def reinspect_page(ctx: dict) -> str:
    k = ctx["knife"]
    body = f"""
{ctx.get("flash", "")}
<div class="panel"><h2>复检：{esc(k["id"])} {knife_svg(k["type"], k["status"], 40)}
{esc(KNIFE_TYPES.get(k["type"], k["type"]))}</h2>
<p>当前状态：{badge(k["status"])}
刃口：{esc(EDGE_LABELS.get(k["edge_state"], k["edge_state"]))}
磨刀/维修人：<b>{esc(k["sharpened_by"] or ctx.get("done_by") or "—")}</b></p>
<p class="hold">制度：磨刀者不能批准自己的复检。请用另一个复检岗工号登录操作。</p>
<form method="post" action="/reinspect" class="field">
<input type="hidden" name="knife_id" value="{esc(k["id"])}">
<input type="hidden" name="kind" value="{esc(ctx["kind"])}">
<label>复检人工号</label>
<select name="inspector_id">
{ "".join(f'<option value="{esc(s["id"])}">{esc(s["id"])} {esc(s["name"])}（{esc(s["role"])}）</option>' for s in ctx["inspectors"])}
</select>
<p>检查项：① 刃口锋利度（试切）　② 刀身完整性（裂纹/崩口/刀柄）</p>
<button class="btn" name="passed" value="1">两项合格 · 批准回架</button>
<button class="btn r" name="passed" value="0">不合格 · 退回隔离/维修</button>
</form></div>
<a class="btn sec" href="/maintenance">返回保养排程</a>"""
    return shell("复检", "maint", body)


# ---------- 异常隔离 ----------

def quarantine_page(ctx: dict) -> str:
    actor = ctx["actor_id"]

    def qrow(k):
        reason = REASON_LABELS.get(k["quarantine_reason"] or "", k["quarantine_reason"] or "—")
        actions = []
        r = k["quarantine_reason"]
        if r in ("chipped", "loose_handle", "rejected"):
            actions.append(
                f'<form method="post" action="/repair/send" style="display:inline">'
                f'<input type="hidden" name="knife_id" value="{esc(k["id"])}">'
                f'<input type="hidden" name="actor_id" value="{actor}">'
                f'<button class="btn mini r">送修</button></form>')
        if r == "misplaced":
            actions.append(
                f'<form method="post" action="/relocate" style="display:inline">'
                f'<input type="hidden" name="knife_id" value="{esc(k["id"])}">'
                f'<input type="hidden" name="actor_id" value="{actor}">'
                f'<input type="hidden" name="slot_id" value="{esc(k["home_slot_id"])}">'
                f'<button class="btn mini y">核对归位 {esc(k["home_slot_id"])}</button></form>')
        if r == "repair_pending":
            actions.append(f'<a class="btn mini" href="/reinspect?id={esc(k["id"])}&kind=repair">维修复检</a>')
        if r == "found_pending":
            actions.append(f'<a class="btn mini" href="/reinspect?id={esc(k["id"])}&kind=found">找回复检</a>')
        return (f'<tr class="warn"><td class="codepill">{esc(k["id"])}</td>'
                f'<td>{knife_svg(k["type"], k["status"], 30)} {esc(KNIFE_TYPES.get(k["type"], k["type"]))}</td>'
                f'<td>{esc(reason)}</td><td>{esc(EDGE_LABELS.get(k["edge_state"], k["edge_state"]))}</td>'
                f'<td>{"".join(actions) or "—"}</td></tr>')

    def rrow(k):
        return (f'<tr class="danger"><td class="codepill">{esc(k["id"])}</td>'
                f'<td>{knife_svg(k["type"], k["status"], 30)} {esc(KNIFE_TYPES.get(k["type"], k["type"]))}</td>'
                f'<td>待修（修复前冻结发放）</td><td>{esc(EDGE_LABELS.get(k["edge_state"], k["edge_state"]))}</td>'
                f'<td><form method="post" action="/repair/done" style="display:inline">'
                f'<input type="hidden" name="knife_id" value="{esc(k["id"])}">'
                f'<input type="hidden" name="repairer_id" value="{actor}">'
                f'<button class="btn mini y">维修完成（转待检）</button></form></td></tr>')

    def lrow(k):
        return (f'<tr class="danger"><td class="codepill">{esc(k["id"])}</td>'
                f'<td>{knife_svg(k["type"], k["status"], 30)} {esc(KNIFE_TYPES.get(k["type"], k["type"]))}</td>'
                f'<td>遗失冻结（最后接触：{esc(k["last_touched_by"])} {esc((k["last_touched_at"] or "")[:16].replace("T"," "))}）</td>'
                f'<td>{esc(EDGE_LABELS.get(k["edge_state"], k["edge_state"]))}</td>'
                f'<td><form method="post" action="/found" style="display:inline">'
                f'<input type="hidden" name="knife_id" value="{esc(k["id"])}">'
                f'<input type="hidden" name="finder_id" value="{actor}">'
                f'<button class="btn mini y">找回登记（转待检）</button></form></td></tr>')

    body = f"""
{reminders_bar(ctx["reminders"])}
{ctx.get("flash", "")}
<div class="panel"><h2>异常隔离区</h2>
<table><thead><tr><th>刀号</th><th>刀型</th><th>隔离原因</th><th>刃口</th><th>处理</th></tr></thead>
<tbody>{"".join(qrow(k) for k in ctx["quarantine"]) or '<tr><td colspan=5 class=hold>无隔离刀具</td></tr>'}</tbody></table></div>
<div class="panel"><h2>待修</h2>
<table><tbody>{"".join(rrow(k) for k in ctx["repair"]) or '<tr><td colspan=5 class=hold>无待修刀具</td></tr>'}</tbody></table></div>
<div class="panel"><h2>遗失冻结</h2>
<table><tbody>{"".join(lrow(k) for k in ctx["lost"]) or '<tr><td colspan=5 class=hold>无遗失刀具</td></tr>'}</tbody></table></div>"""
    return shell("异常隔离", "quar", body)


# ---------- 追溯 ----------

def trace_page(detail: dict, contact: dict | None, flash_html: str = "") -> str:
    k = detail
    bad_types = {"report_chipped", "report_loose", "return_wrong_slot", "reinspect_fail",
                 "repair_inspect_fail", "found_inspect_fail", "report_lost"}
    good_types = {"return_ok", "reinspect_pass", "repair_inspect_pass", "found_inspect_pass",
                  "relocate"}
    evs = []
    for e in k["events"]:
        d = json.loads(e["detail"] or "{}")
        extra = ""
        if e["type"] == "return_wrong_slot":
            extra = f'扫入 {esc(d.get("scanned_slot"))}，应为 {esc(d.get("home_slot"))}'
        elif e["type"] == "handover":
            extra = f'{esc(d.get("from_chef"))} → {esc(d.get("to_chef"))}' + ("（跨班）" if d.get("cross_day") else "")
        elif e["type"] == "report_lost" and d.get("note"):
            extra = esc(d["note"])
        cls = "ev bad" if e["type"] in bad_types else "ev good" if e["type"] in good_types else "ev"
        evs.append(
            f'<div class="{cls}"><span class="ts">{esc(e["ts"][:19].replace("T"," "))}</span>'
            f'<b>{esc(EVENT_LABELS.get(e["type"], e["type"]))}</b> '
            f'<span class="hold">{esc(e["actor_name"] or e["actor_id"] or "系统")} · 班次#{esc(e["shift_id"])}</span>'
            f'{" — " + extra if extra else ""}</div>')
    photos_html = "".join(
        f'<div class="photo"><img src="/{esc(p["path"])}" alt="刃口照片">'
        f'<div class="cap">{esc(p["label"])} · {esc(p["taken_by"])} · {esc(p["taken_at"][:10])}</div></div>'
        for p in k["photos"])
    body = f"""
{flash_html}
<div class="panel"><h2>{esc(k["id"])} 追溯
{knife_svg(k["type"], k["status"], 44)} {esc(KNIFE_TYPES.get(k["type"], k["type"]))}</h2>
<p>{badge(k["status"])} 刃口：{esc(EDGE_LABELS.get(k["edge_state"], k["edge_state"]))}
归属槽位：{esc(k["home_slot_id"])} 持有人：{esc(k["current_holder_id"] or "—")}</p>
<p>最后接触人：<b>{esc(contact["name"]) if contact else "—"}</b>
<span class="hold">{esc(contact["at"][:19].replace("T"," ") if contact else "")}</span></p></div>
<div class="panel"><h2>刃口照片</h2><div class="miss-strip">{photos_html or '<span class=hold>暂无照片</span>'}</div></div>
<div class="panel"><h2>逐刀流转记录</h2><div class="timeline">{"".join(evs) or "<p class=hold>无记录</p>"}</div></div>
<a class="btn sec" href="/">返回刀架图</a>"""
    return shell("追溯", "rack", body)
