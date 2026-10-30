"""One self-contained HTML page: a chart per stock, grouped by sector.

Each card shows the daily closing line. Clicking a card opens a large weekly OHLC chart built from
the same daily data (no extra downloads). Dragging across that chart zooms into daily candles for the
selected dates; buttons go back to the weekly view or to the line card.

``build_payload`` turns a long price table and the universe into plain JSON-able data;
``render_html`` embeds it in a page that draws the charts itself (inline SVG, no external scripts).
Charts are drawn when they scroll into view, so 500+ stocks stay responsive.
"""

from __future__ import annotations

import html
import json
import math
from datetime import date
from typing import Dict, List, Sequence

import numpy as np
import pandas as pd

OHLC = ["open", "high", "low", "close"]
ETF_GROUP = "ETF"  # sector ETFs are a group of their own, shown first


def quarter_labels(dates: Sequence, max_labels: int = 5) -> List[list]:
    """[index, "Oct 24"] at each quarter start, thinned to about ``max_labels`` labels."""
    seen, marks = set(), []
    for i, d in enumerate(dates):
        quarter = (d.year, (d.month - 1) // 3)
        if quarter not in seen:
            seen.add(quarter)
            marks.append((i, f"{d.strftime('%b')} {d.year % 100:02d}"))
    step = max(1, math.ceil(len(marks) / max_labels))
    return [[i, label] for k, (i, label) in enumerate(marks) if k % step == 0]


def weekly_calendar(idx: pd.DatetimeIndex):
    """Week (ending Friday) of each session, and the ordered weeks with their first and last session."""
    week_key = pd.Series(idx.to_period("W-FRI"), index=idx)
    spans = (pd.DataFrame({"first": idx, "last": idx}, index=idx)
             .groupby(week_key.values).agg({"first": "min", "last": "max"}))
    weeks = [[a.date().isoformat(), b.date().isoformat()] for a, b in zip(spans["first"], spans["last"])]
    return week_key, list(spans.index), weeks


def weekly_bars(sub: np.ndarray, idx: pd.DatetimeIndex, week_key: pd.Series, week_order: list) -> list:
    """Weekly [open, high, low, close] from daily rows: first open, highest high, lowest low, last close.

    A week with no complete daily bar is null. A partial current week is included as it stands.
    """
    frame = pd.DataFrame(sub, index=idx, columns=OHLC).dropna()
    if frame.empty:
        return [None] * len(week_order)
    frame["wk"] = week_key.reindex(frame.index).values
    agg = (frame.groupby("wk")
           .agg(open=("open", "first"), high=("high", "max"), low=("low", "min"), close=("close", "last"))
           .reindex(week_order))
    values = agg.to_numpy(dtype=float)
    return [None if np.isnan(row).any() else [round(float(v), 2) for v in row] for row in values]


def _cap_order(stock: dict):
    """Largest market cap first (the cap shown on the card); no cap last, then by snapshot rank, then ticker."""
    cap = stock["cap"]
    return (cap is None, -(cap or 0.0), stock["r"] is None, stock["r"] or 0, stock["s"])


def build_payload(prices: pd.DataFrame, universe: pd.DataFrame) -> dict:
    """Aligned data for the page.

    prices    long table: symbol, date, open, high, low, close (one row per stock per session)
    universe  symbol, name, sector, market_cap_rank, optional market_cap and price (snapshot)

    ``o`` per stock: one [open, high, low, close] per session in ``dates`` (null where the stock has
    no complete bar that day). ``w``: the same per week in ``wk`` (first session, last session).
    Stocks with no prices are listed in ``missing``. Sectors are sorted by name; within a sector,
    largest market cap first.
    """
    prices = prices.copy()
    prices["date"] = pd.to_datetime(prices["date"]).dt.date
    dates = sorted(prices["date"].unique())
    idx = pd.to_datetime(dates)
    week_key, week_order, weeks = weekly_calendar(idx)
    wide = prices.set_index(["symbol", "date"]).sort_index()
    have = set(prices["symbol"].unique())

    uni = universe.copy()
    uni["sector"] = uni["sector"].fillna("Unknown").astype(str)
    uni["rank_sort"] = pd.to_numeric(uni["market_cap_rank"], errors="coerce").fillna(10**9)
    uni = uni.sort_values(["sector", "rank_sort", "symbol"])

    groups: Dict[str, list] = {}
    missing: List[str] = []
    for row in uni.itertuples(index=False):
        if row.symbol not in have:
            missing.append(row.symbol)
            continue
        sub = wide.loc[row.symbol].reindex(dates)[OHLC].to_numpy(dtype=float)
        complete = ~np.isnan(sub).any(axis=1)
        closes = sub[:, 3][~np.isnan(sub[:, 3])]
        if closes.size == 0:
            missing.append(row.symbol)
            continue
        ohlc = [[round(float(v), 2) for v in sub[i]] if complete[i] else None for i in range(len(dates))]
        first, last = float(closes[0]), float(closes[-1])
        rank = None if row.rank_sort >= 10**9 else int(row.rank_sort)
        # Market cap at the latest close: the snapshot's cap scaled by the price move since the snapshot.
        # Scaling (rather than shares x price) also works for companies with several share classes.
        cap_b = None
        snap_cap, snap_price = getattr(row, "market_cap", None), getattr(row, "price", None)
        if snap_cap is not None and snap_price and not pd.isna(snap_cap) and not pd.isna(snap_price):
            cap_b = round(float(snap_cap) * last / float(snap_price) / 1e9, 1)
        groups.setdefault(row.sector, []).append({
            "s": row.symbol,
            "n": "" if pd.isna(row.name) else str(row.name).strip(),
            "r": rank,
            "o": ohlc,
            "w": weekly_bars(sub, idx, week_key, week_order),
            "last": round(last, 2),
            "chg": round(100 * (last / first - 1), 1) if first else 0.0,
            "cap": cap_b,
        })

    return {
        "dates": [d.isoformat() for d in dates],
        "xl": quarter_labels(dates),
        "wk": weeks,
        "wxl": quarter_labels([date.fromisoformat(start) for start, _ in weeks]),
        "groups": [{"sector": name, "stocks": sorted(stocks, key=_cap_order)}
                   for name, stocks in sorted(groups.items(), key=lambda kv: (kv[0] != ETF_GROUP, kv[0]))],
        "missing": missing,
    }


_TEMPLATE = r"""<!doctype html>
<html lang="en">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<title>__TITLE__</title>
<style>
:root{color-scheme:light;--bg:#fcfcfb;--panel:#ffffff;--ink:#0b0b0b;--ink2:#52514e;--grid:#e6e5e0;
  --line:#2a78d6;--up:#1baf7a;--down:#e34948}
@media (prefers-color-scheme:dark){:root:not([data-theme="light"]){color-scheme:dark;--bg:#1a1a19;--panel:#222221;
  --ink:#ffffff;--ink2:#c3c2b7;--grid:#33332f;--line:#3987e5;--up:#2fc08f;--down:#e66767}}
:root[data-theme="dark"]{color-scheme:dark;--bg:#1a1a19;--panel:#222221;--ink:#ffffff;--ink2:#c3c2b7;
  --grid:#33332f;--line:#3987e5;--up:#2fc08f;--down:#e66767}
*{box-sizing:border-box}
body{margin:0;background:var(--bg);color:var(--ink);font:14px/1.45 system-ui,"Segoe UI",Roboto,sans-serif}
.top{padding:18px 20px 6px}
h1{font-size:22px;margin:0 0 4px}
.sub{color:var(--ink2);margin:0 0 12px}
.tools{display:flex;gap:12px;align-items:center;flex-wrap:wrap}
#q{font:inherit;padding:6px 10px;border:1px solid var(--grid);border-radius:8px;background:var(--panel);color:var(--ink);min-width:240px}

/* Sticky sector bar: stays at the top while scrolling. */
.jump{position:sticky;top:0;z-index:20;display:flex;flex-wrap:wrap;gap:6px;padding:8px 20px;
  background:var(--bg);border-bottom:1px solid var(--grid)}
.chip{font:inherit;font-size:12px;padding:4px 10px;border:1px solid var(--grid);border-radius:999px;
  background:var(--panel);color:var(--ink);cursor:pointer;white-space:nowrap}
.chip:hover,.chip:focus-visible{border-color:var(--line);outline:none}
.chip .n{color:var(--ink2);margin-left:4px;font-variant-numeric:tabular-nums}
.chip.off{opacity:.4}

main{padding:0 20px 40px}
.sector{margin-top:22px;scroll-margin-top:64px}
.sector h2{font-size:17px;margin:0 0 10px;display:flex;gap:8px;align-items:baseline}
.count{color:var(--ink2);font-weight:400;font-size:13px}
.grid{display:grid;grid-template-columns:repeat(4,minmax(0,1fr));gap:12px}
.card{background:var(--panel);border:1px solid var(--grid);border-radius:10px;padding:10px 12px 8px;min-width:0}
.card[hidden],.sector[hidden]{display:none}
.card header{display:flex;gap:6px;align-items:baseline;min-width:0}
.tk{font-weight:700}
.nm{color:var(--ink2);font-size:12px;flex:1;min-width:0;overflow:hidden;text-overflow:ellipsis;white-space:nowrap}
.cap{color:var(--ink2);font-size:12px;white-space:nowrap;font-variant-numeric:tabular-nums}
.chg{font-variant-numeric:tabular-nums;font-size:13px}
.pos{color:var(--up)} .neg{color:var(--down)}
.plot{position:relative;cursor:zoom-in;border-radius:6px;outline:none;margin-top:4px}
.plot:focus-visible{box-shadow:0 0 0 2px var(--line)}
.plot svg{width:100%;height:auto;display:block}
/* Stroke widths stay in screen pixels, so zooming never makes lines thick. */
svg path,svg line{vector-effect:non-scaling-stroke}
.gl{stroke:var(--grid);stroke-width:1}
.axis{fill:var(--ink2);font-size:10.5px}
.line{fill:none;stroke:var(--line);stroke-width:1.6;stroke-linejoin:round}
.up{fill:none;stroke:var(--up);stroke-width:1.2}
.down{fill:none;stroke:var(--down);stroke-width:1.2}
.xh{stroke:var(--ink2);stroke-width:1;stroke-dasharray:3 3}
.sel{fill:var(--line);fill-opacity:.16;stroke:var(--line);stroke-width:1;pointer-events:none}
.ro{display:flex;gap:8px;justify-content:space-between;align-items:baseline;color:var(--ink2);font-size:12px;
  min-height:18px;margin-top:2px;font-variant-numeric:tabular-nums;white-space:nowrap}
.ro-text{overflow:hidden;text-overflow:ellipsis;min-width:0}
.mode{flex:none;font-size:11px;color:var(--ink2)}
.foot{color:var(--ink2);font-size:12px;padding:0 20px 40px;margin-top:26px}

/* Zoom overlay: the weekly OHLC chart at about three times the card size. */
.zoom-back{position:fixed;inset:0;z-index:50;display:flex;align-items:center;justify-content:center;
  background:rgba(0,0,0,.55);opacity:0;transition:opacity .18s ease}
.zoom-back.open{opacity:1}
.zoom-back[hidden]{display:none}
.zoom-panel{width:min(94vw,1260px,calc((100vh - 170px) * 1.826));background:var(--panel);border:1px solid var(--grid);
  border-radius:12px;padding:14px 18px 10px;transform:scale(.96);transition:transform .18s ease}
.zoom-back.open .zoom-panel{transform:scale(1)}
.zoom-head{display:flex;flex-wrap:wrap;gap:10px;align-items:baseline;margin-bottom:6px;min-width:0}
.zoom-head .tk{font-size:18px}
.zoom-head .nm{font-size:14px;flex:0 1 auto}
.zoom-head .chg{font-size:15px}
.zbtns{display:flex;gap:6px;margin-left:auto}
.btn{font:inherit;font-size:13px;padding:4px 10px;border:1px solid var(--grid);border-radius:8px;
  background:var(--panel);color:var(--ink);cursor:pointer;white-space:nowrap}
.btn:hover,.btn:focus-visible{border-color:var(--line);outline:none}
.btn[hidden]{display:none}
.zoom-plot{cursor:crosshair;touch-action:none;user-select:none}
.zoom-plot .axis{font-size:4.4px}
.zoom-plot .up,.zoom-plot .down{stroke-width:2px}
.zoom-ro{font-size:14px}
.zoom-ro .mode{font-size:12px}

@media (max-width:1100px){.grid{grid-template-columns:repeat(3,minmax(0,1fr))}}
@media (max-width:800px){.grid{grid-template-columns:repeat(2,minmax(0,1fr))}}
@media (max-width:520px){
  .grid{grid-template-columns:1fr}
  .top,main,.foot{padding-left:16px;padding-right:16px}
  .jump{flex-wrap:nowrap;overflow-x:auto;padding-left:16px;padding-right:16px}
}
</style>
</head>
<body>
<header class="top">
  <h1>__TITLE__</h1>
  <p class="sub">__SUB__</p>
  <div class="tools">
    <input id="q" type="search" placeholder="Filter by ticker or name" aria-label="Filter stocks">
    <span class="sub" style="margin:0">Click a chart for its weekly OHLC view. Drag across that view to see daily candles for those dates. Hover for values.</span>
  </div>
</header>
<nav class="jump" id="jump" aria-label="Sectors"></nav>
<main id="groups"></main>
<div class="foot" id="foot"></div>

<div class="zoom-back" id="zoom" hidden>
  <div class="zoom-panel" role="dialog" aria-modal="true" aria-labelledby="zt">
    <div class="zoom-head">
      <span class="tk" id="zt"></span><span class="nm" id="zn"></span><span class="cap" id="zcap"></span><span class="chg" id="zc"></span>
      <div class="zbtns">
        <button class="btn" id="zreset" type="button" hidden>Reset to weekly</button>
        <button class="btn" id="zback" type="button">&larr; Line chart</button>
      </div>
    </div>
    <div class="plot zoom-plot" id="zoom-plot" tabindex="0" role="img" aria-label="OHLC chart: drag across it to zoom into daily candles">
      <svg viewBox="0 0 420 230" role="img"></svg>
    </div>
    <div class="ro zoom-ro"><span class="ro-text" id="zro">Hover for values</span><span class="mode" id="zhint"></span></div>
  </div>
</div>

<script>
const D = __DATA__;
const NS = "http://www.w3.org/2000/svg";
const W = 420, H = 230, L = 48, R = 10, T = 10, B = 24;
const PW = W - L - R, PH = H - T - B;
const N = D.dates.length;
const NW = D.wk.length;
const nf = (v, dp) => v.toLocaleString("en-US", {minimumFractionDigits: dp, maximumFractionDigits: dp});

function niceStep(span, ticks) {
  const raw = span / ticks, p = Math.pow(10, Math.floor(Math.log10(raw))), f = raw / p;
  return (f < 1.5 ? 1 : f < 3 ? 2 : f < 7 ? 5 : 10) * p;
}

// Price scale for one set of bars: lows and highs (closes lie within them).
function rangeOf(data) {
  let lo = Infinity, hi = -Infinity;
  for (const b of data) if (b) { if (b[2] < lo) lo = b[2]; if (b[1] > hi) hi = b[1]; }
  if (!isFinite(lo)) return [0, 1];
  const pad = (hi - lo) * 0.04 || hi * 0.01 || 1;
  return [lo - pad, hi + pad];
}

// SVG elements need the SVG namespace; HTML elements (spans, headings) must not get it.
function svgEl(name, attrs, parent, text) {
  const e = document.createElementNS(NS, name);
  for (const k in attrs) e.setAttribute(k, attrs[k]);
  if (text !== undefined) e.textContent = text;
  if (parent) parent.appendChild(e);
  return e;
}

function htmlEl(tag, className, text) {
  const e = document.createElement(tag);
  if (className) e.className = className;
  if (text !== undefined) e.textContent = text;
  return e;
}

// ---- Views: a set of bars with its own x labels and readout text -----------
function dailyView(st) {
  return {n: N, dates: D.dates, starts: D.dates, xl: D.xl, data: st.o, weekly: false};
}
function weeklyView(st) {
  return {n: NW, dates: D.wk.map(w => w[1]), starts: D.wk.map(w => w[0]), xl: D.wxl, data: st.w, weekly: true};
}
function labelsFor(dates) {
  const marks = []; let prev = "";
  dates.forEach((d, i) => {
    const k = d.slice(0, 7);
    if (k !== prev) { prev = k; marks.push([i, monthLabel(d)]); }
  });
  const step = Math.max(1, Math.ceil(marks.length / 5));
  return marks.filter((_, k) => k % step === 0);
}
function monthLabel(iso) {
  const [y, m] = iso.split("-");
  return new Date(+y, +m - 1, 1).toLocaleString("en-US", {month: "short"}) + " " + y.slice(2);
}
function sliceView(st, i0, i1) {
  const dates = D.dates.slice(i0, i1 + 1);
  return {n: dates.length, dates, starts: dates, xl: labelsFor(dates), data: st.o.slice(i0, i1 + 1), weekly: false};
}
function viewText(v, i) {
  if (v.weekly && v.starts[i] !== v.dates[i]) return "week " + v.starts[i] + " to " + v.dates[i];
  return v.dates[i];
}

// Draws one chart into an svg. mode "line" = closes, "ohlc" = bars with open and close ticks.
function paint(svg, v, mode, selectable) {
  const [lo, hi] = rangeOf(v.data);
  const xOf = i => L + (v.n > 1 ? i * PW / (v.n - 1) : 0);
  const yOf = val => T + PH * (hi - val) / (hi - lo);
  svg.textContent = "";
  svg._n = v.n;
  svg._xOf = xOf;

  const step = niceStep(hi - lo, 4), dp = step >= 1 ? 0 : 2;
  for (let p = Math.ceil(lo / step) * step; p <= hi; p += step) {
    const y = yOf(p).toFixed(1);
    svgEl("line", {x1: L, x2: W - R, y1: y, y2: y, class: "gl"}, svg);
    svgEl("text", {x: L - 6, y: (+y + 3.5).toFixed(1), class: "axis", "text-anchor": "end"}, svg, nf(p, dp));
  }
  for (const [i, label] of v.xl)
    svgEl("text", {x: xOf(i).toFixed(1), y: H - 6, class: "axis", "text-anchor": i === 0 ? "start" : "middle"}, svg, label);

  if (mode === "line") {
    let d = "", pen = false;
    v.data.forEach((b, i) => {
      if (!b) { pen = false; return; }
      d += (pen ? "L" : "M") + xOf(i).toFixed(1) + " " + yOf(b[3]).toFixed(1);
      pen = true;
    });
    svgEl("path", {d: d, class: "line"}, svg);
  } else {
    const bw = Math.max(1.5, Math.min(6, PW / v.n * 0.35));
    let up = "", down = "";
    v.data.forEach((b, i) => {
      if (!b) return;
      const [o, h, l, c] = b, x = xOf(i);
      const seg = `M${x.toFixed(1)} ${yOf(h).toFixed(1)}V${yOf(l).toFixed(1)}` +
        `M${(x - bw).toFixed(1)} ${yOf(o).toFixed(1)}h${bw.toFixed(1)}` +
        `M${x.toFixed(1)} ${yOf(c).toFixed(1)}h${bw.toFixed(1)}`;
      if (c >= o) up += seg; else down += seg;
    });
    svgEl("path", {d: up, class: "up"}, svg);
    svgEl("path", {d: down, class: "down"}, svg);
  }
  svgEl("rect", {x: L, y: T, width: PW, height: PH, fill: "transparent"}, svg);
  svg._xh = svgEl("line", {x1: 0, x2: 0, y1: T, y2: T + PH, class: "xh", style: "display:none"}, svg);
  if (selectable) svg._sel = svgEl("rect", {x: L, y: T, width: 0, height: PH, class: "sel", style: "display:none"}, svg);
}

function readoutFor(v, i, mode) {
  const b = v.data[i], label = viewText(v, i);
  if (!b) return label + " · no data";
  if (mode === "line") return label + " · close " + nf(b[3], 2);
  return label + " · O " + nf(b[0], 2) + "  H " + nf(b[1], 2) + "  L " + nf(b[2], 2) + "  C " + nf(b[3], 2);
}

function indexAt(evt, svg) {
  const r = svg.getBoundingClientRect(), x = (evt.clientX - r.left) * W / r.width;
  return Math.max(0, Math.min(svg._n - 1, Math.round((x - L) / PW * (svg._n - 1))));
}

// Hover readout and crosshair. Each plot carries its state: _state {v, mode}, _ro (text element).
function onMove(evt) {
  const plot = evt.currentTarget, svg = plot.querySelector("svg"), s = plot._state;
  if (!s || !svg._xh) return;
  const i = indexAt(evt, svg), px = svg._xOf(i).toFixed(1);
  svg._xh.setAttribute("x1", px); svg._xh.setAttribute("x2", px); svg._xh.style.display = "";
  plot._ro.textContent = readoutFor(s.v, i, s.mode);
}

function onLeave(evt) {
  const plot = evt.currentTarget, svg = plot.querySelector("svg");
  if (svg._xh) svg._xh.style.display = "none";
  if (plot._ro) plot._ro.textContent = "Hover for values";
}

// ---- Cards -----------------------------------------------------------------
function capText(st) {
  return st.cap == null ? "" : "$" + Math.round(st.cap).toLocaleString("en-US") + "B";
}

function drawSmall(card) {
  const plot = card.querySelector(".plot");
  paint(plot.querySelector("svg"), plot._state.v, "line", false);
}

function makeCard(st) {
  const card = htmlEl("article", "card");
  card._st = st;
  card.dataset.search = (st.s + " " + st.n).toLowerCase();

  const head = htmlEl("header");
  const change = (st.chg >= 0 ? "+" : "") + st.chg.toFixed(1) + "%";
  head.append(htmlEl("span", "tk", st.s), htmlEl("span", "nm", st.n), htmlEl("span", "cap", capText(st)),
              htmlEl("span", "chg " + (st.chg >= 0 ? "pos" : "neg"), change));

  const plot = htmlEl("div", "plot");
  plot.tabIndex = 0;
  plot.setAttribute("role", "button");
  plot.setAttribute("aria-label", st.s + " price chart: click to open the weekly OHLC view");
  plot.innerHTML = '<svg viewBox="0 0 ' + W + ' ' + H + '" role="img"></svg>';

  const roText = htmlEl("span", "ro-text", "Hover for values");
  const ro = htmlEl("footer", "ro");
  ro.append(roText, htmlEl("span", "mode", "Click for weekly OHLC"));
  card.append(head, plot, ro);

  plot._state = {v: dailyView(st), mode: "line"};
  plot._ro = roText;
  plot.addEventListener("click", () => openZoom(card));
  plot.addEventListener("keydown", e => {
    if (e.key === "Enter" || e.key === " ") { e.preventDefault(); openZoom(card); }
  });
  plot.addEventListener("pointermove", onMove);
  plot.addEventListener("pointerleave", onLeave);
  return card;
}

// ---- Zoom overlay ----------------------------------------------------------
const zoomBack = document.getElementById("zoom");
const zoomPlot = document.getElementById("zoom-plot");
const zoomRo = document.getElementById("zro");
const zoomHint = document.getElementById("zhint");
const resetBtn = document.getElementById("zreset");
let zoomOrigin = null, zoomSt = null, zoomDrag = null;
zoomPlot._ro = zoomRo;

function setZoomView(v) {
  zoomPlot._state = {v, mode: "ohlc"};
  paint(zoomPlot.querySelector("svg"), v, "ohlc", true);
  zoomRo.textContent = "Hover for values";
  resetBtn.hidden = !!v.weekly;
  zoomHint.textContent = v.weekly ? "Drag across the chart to see daily candles" : "Drag again to narrow down";
}

function openZoom(card) {
  zoomOrigin = card.querySelector(".plot");
  zoomSt = card._st;
  document.getElementById("zt").textContent = zoomSt.s;
  document.getElementById("zn").textContent = zoomSt.n;
  document.getElementById("zcap").textContent = capText(zoomSt);
  const zc = document.getElementById("zc");
  zc.textContent = (zoomSt.chg >= 0 ? "+" : "") + zoomSt.chg.toFixed(1) + "%";
  zc.className = "chg " + (zoomSt.chg >= 0 ? "pos" : "neg");

  setZoomView(weeklyView(zoomSt));
  zoomBack.hidden = false;
  document.body.style.overflow = "hidden";
  requestAnimationFrame(() => {
    zoomBack.classList.add("open");
    zoomPlot.focus({preventScroll: true});
  });
}

function closeZoom() {
  if (zoomBack.hidden) return;
  zoomBack.classList.remove("open");
  document.body.style.overflow = "";
  setTimeout(() => { if (!zoomBack.classList.contains("open")) zoomBack.hidden = true; }, 180);
  if (zoomOrigin) zoomOrigin.focus({preventScroll: true});
}

// Dragged range of bars in view v (indices a..c) -> the daily candles for those dates.
function selectionView(v, a, c) {
  const first = v.starts[a], last = v.dates[c];
  let i0 = D.dates.findIndex(d => d >= first), i1 = -1;
  D.dates.forEach((d, i) => { if (d <= last) i1 = i; });
  if (i0 < 0 || i1 - i0 < 1) return null;
  return sliceView(zoomSt, i0, i1);
}

function setupZoomDrag() {
  zoomPlot.addEventListener("pointerdown", e => {
    if (e.button !== 0 || !zoomPlot._state) return;
    const svg = zoomPlot.querySelector("svg");
    zoomDrag = {a: indexAt(e, svg), x0: e.clientX};
    zoomPlot.setPointerCapture(e.pointerId);
    e.preventDefault();
  });
  zoomPlot.addEventListener("pointermove", e => {
    onMove(e);
    if (!zoomDrag) return;
    const svg = zoomPlot.querySelector("svg"), b = indexAt(e, svg);
    const lo = Math.min(zoomDrag.a, b), hi = Math.max(zoomDrag.a, b);
    if (svg._sel) {
      svg._sel.setAttribute("x", svg._xOf(lo).toFixed(1));
      svg._sel.setAttribute("width", (svg._xOf(hi) - svg._xOf(lo)).toFixed(1));
      svg._sel.style.display = "";
    }
  });
  const endDrag = (e, commit) => {
    if (!zoomDrag) return;
    const svg = zoomPlot.querySelector("svg"), start = zoomDrag, b = indexAt(e, svg);
    zoomDrag = null;
    if (svg._sel) svg._sel.style.display = "none";
    if (!commit || Math.abs(e.clientX - start.x0) < 6) return;
    const next = selectionView(zoomPlot._state.v, Math.min(start.a, b), Math.max(start.a, b));
    if (next) setZoomView(next);
  };
  zoomPlot.addEventListener("pointerup", e => endDrag(e, true));
  zoomPlot.addEventListener("pointercancel", e => endDrag(e, false));
  zoomPlot.addEventListener("pointerleave", onLeave);
}

// ---- Build -----------------------------------------------------------------
function build() {
  const root = document.getElementById("groups");
  const jump = document.getElementById("jump");
  // The first charts are drawn straight away; the rest are drawn when they scroll near the screen.
  const EAGER = 12;
  const lazy = "IntersectionObserver" in window;
  const io = lazy ? new IntersectionObserver(entries => {
    for (const e of entries) if (e.isIntersecting) { drawSmall(e.target); io.unobserve(e.target); }
  }, {rootMargin: "300px"}) : null;
  let made = 0;

  D.groups.forEach((g, gi) => {
    const sec = htmlEl("section", "sector");
    sec.id = "sec-" + gi;
    const h = htmlEl("h2");
    h.append(htmlEl("span", "", g.sector), htmlEl("span", "count", String(g.stocks.length)));
    const grid = htmlEl("div", "grid");
    for (const st of g.stocks) {
      const card = makeCard(st);
      grid.append(card);
      if (!lazy || made < EAGER) drawSmall(card);
      else io.observe(card);
      made++;
    }
    sec.append(h, grid);
    root.append(sec);

    const chip = htmlEl("button", "chip");
    chip.type = "button";
    chip.append(htmlEl("span", "", g.sector), htmlEl("span", "n", String(g.stocks.length)));
    chip.addEventListener("click", () => sec.scrollIntoView({behavior: "smooth", block: "start"}));
    jump.append(chip);
  });

  setupZoomDrag();
  document.getElementById("zback").addEventListener("click", closeZoom);
  resetBtn.addEventListener("click", () => { if (zoomSt) setZoomView(weeklyView(zoomSt)); });
  zoomBack.addEventListener("click", e => { if (e.target === zoomBack) closeZoom(); });
  document.addEventListener("keydown", e => { if (e.key === "Escape") closeZoom(); });

  document.getElementById("q").addEventListener("input", e => {
    const q = e.target.value.trim().toLowerCase();
    const chips = jump.querySelectorAll(".chip");
    document.querySelectorAll(".sector").forEach((sec, si) => {
      let shown = 0;
      for (const card of sec.querySelectorAll(".card")) {
        const ok = !q || card.dataset.search.includes(q);
        card.hidden = !ok;
        if (ok) shown++;
      }
      sec.hidden = shown === 0;
      sec.querySelector(".count").textContent = shown;
      chips[si].querySelector(".n").textContent = shown;
      chips[si].classList.toggle("off", shown === 0);
    });
  });

  const total = D.groups.reduce((n, g) => n + g.stocks.length, 0);
  document.getElementById("foot").textContent =
    total + " stocks with prices." +
    (D.missing.length ? " No price data for: " + D.missing.join(", ") + "." : "") +
    " Prices are split- and dividend-adjusted. Weekly bars are built from the daily data (first open, highest high, lowest low, last close).";
}

build();
</script>
</body>
</html>
"""


def render_html(payload: dict, title: str, subtitle: str) -> str:
    """The page with ``payload`` embedded. ``</`` is escaped so names cannot close the script tag."""
    data = json.dumps(payload, separators=(",", ":"), ensure_ascii=False).replace("</", "<\\/")
    return (_TEMPLATE.replace("__TITLE__", html.escape(title))
            .replace("__SUB__", html.escape(subtitle))
            .replace("__DATA__", data))
