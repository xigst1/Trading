"""Render report.json as a standalone HTML report (stock-news-report skill).

    python build_report.py /tmp/report.json --out reports/

Writes reports/<date>_<TICKERS>.html and prints the path. No dependencies beyond the
standard library, and no network calls: the HTML is self-contained so it still opens
years from now. See SKILL.md for the JSON schema.
"""

from __future__ import annotations

import argparse
import datetime as dt
import html
import json
import re
import sys
from pathlib import Path

IMPACT_LABEL = {"bullish": "bullish", "bearish": "bearish", "neutral": "mixed"}

CSS = """
:root {
  color-scheme: light;
  --bg: #f7f7f5; --surface: #ffffff; --text: #16161a; --muted: #5f5f66;
  --line: #e3e3dd; --accent: #2a78d6; --up: #0f8a4d; --down: #c8384a; --flat: #8a8a90;
  --chip: #f0f0ec;
}
@media (prefers-color-scheme: dark) {
  :root:not([data-theme="light"]) {
    color-scheme: dark;
    --bg: #131315; --surface: #1c1c1f; --text: #f2f2f0; --muted: #a9a9b2;
    --line: #2e2e33; --accent: #6aa9f0; --up: #46c07f; --down: #f2707f; --flat: #9a9aa2;
    --chip: #26262b;
  }
}
* { box-sizing: border-box; }
body {
  margin: 0; padding: 32px 16px 64px; background: var(--bg); color: var(--text);
  font: 16px/1.6 system-ui, -apple-system, "Segoe UI", sans-serif;
}
.wrap { max-width: 900px; margin: 0 auto; }
header.top { border-bottom: 2px solid var(--line); padding-bottom: 16px; margin-bottom: 28px; }
h1 { font-size: 1.7rem; line-height: 1.25; margin: 0 0 8px; }
h2 { font-size: 1.25rem; margin: 36px 0 12px; padding-top: 12px; border-top: 1px solid var(--line); }
h3 { font-size: 1.02rem; margin: 24px 0 8px; color: var(--muted); text-transform: uppercase;
     letter-spacing: .06em; font-weight: 600; }
p { margin: 0 0 12px; }
a { color: var(--accent); }
.meta { color: var(--muted); font-size: .88rem; }
.meta span + span::before { content: "·"; margin: 0 8px; }
.chips { margin: 10px 0 0; }
.chip { display: inline-block; background: var(--chip); border: 1px solid var(--line);
        border-radius: 999px; padding: 2px 11px; margin: 0 6px 6px 0; font-size: .85rem;
        font-weight: 600; }
.card { background: var(--surface); border: 1px solid var(--line); border-radius: 10px;
        padding: 16px 18px; margin: 0 0 16px; }
.request { font-style: italic; color: var(--muted); }
.bottom-line { font-size: 1.05rem; border-left: 4px solid var(--accent); }
table { width: 100%; border-collapse: collapse; margin: 6px 0 18px; font-size: .93rem; }
th, td { text-align: left; padding: 7px 10px; border-bottom: 1px solid var(--line); }
th { color: var(--muted); font-weight: 600; font-size: .82rem; text-transform: uppercase;
     letter-spacing: .04em; }
td.num { text-align: right; font-variant-numeric: tabular-nums; }
.stats { display: grid; grid-template-columns: repeat(auto-fit, minmax(128px, 1fr)); gap: 1px;
         background: var(--line); border: 1px solid var(--line); border-radius: 10px;
         overflow: hidden; margin-bottom: 18px; }
.stat { background: var(--surface); padding: 11px 13px; }
.stat .k { color: var(--muted); font-size: .75rem; text-transform: uppercase; letter-spacing: .05em; }
.stat .v { font-size: 1.15rem; font-weight: 600; font-variant-numeric: tabular-nums; margin-top: 2px; }
.up { color: var(--up); } .down { color: var(--down); } .flat { color: var(--flat); }
.item { border-left: 3px solid var(--flat); padding: 2px 0 2px 14px; margin: 0 0 16px; }
.item.bullish { border-left-color: var(--up); }
.item.bearish { border-left-color: var(--down); }
.item .head { font-weight: 600; }
.item .date { color: var(--muted); font-size: .84rem; font-variant-numeric: tabular-nums;
              margin-right: 8px; }
.tag { font-size: .72rem; text-transform: uppercase; letter-spacing: .05em; border-radius: 4px;
       padding: 1px 6px; margin-left: 8px; border: 1px solid currentColor; vertical-align: 1px; }
.cols { display: grid; grid-template-columns: 1fr 1fr; gap: 16px; }
@media (max-width: 640px) { .cols { grid-template-columns: 1fr; } body { padding: 20px 12px 48px; } }
ul { margin: 0; padding-left: 20px; } li { margin-bottom: 7px; }
.src { font-size: .78rem; margin-left: 6px; }
.src a { text-decoration: none; background: var(--chip); border: 1px solid var(--line);
         border-radius: 4px; padding: 0 5px; margin-right: 3px; }
ol.sources { font-size: .9rem; } ol.sources li { margin-bottom: 9px; }
ol.sources .when { color: var(--muted); }
footer { margin-top: 40px; padding-top: 14px; border-top: 1px solid var(--line);
         color: var(--muted); font-size: .84rem; }
@media print {
  body { background: #fff; padding: 0; } .card, .stat { break-inside: avoid; }
  h2 { break-after: avoid; } a { color: inherit; }
}
"""


def esc(text) -> str:
    return html.escape(str(text if text is not None else ""))


def rich(text) -> str:
    """Escape, then allow **bold**, *italic* and [text](url) so prose can carry emphasis."""
    out = esc(text)
    out = re.sub(r"\[([^\]]+)\]\((https?://[^\s)]+)\)", r'<a href="\2" target="_blank" rel="noopener">\1</a>', out)
    out = re.sub(r"\*\*([^*]+)\*\*", r"<strong>\1</strong>", out)
    out = re.sub(r"(?<!\*)\*([^*]+)\*(?!\*)", r"<em>\1</em>", out)
    return out


def signed(value, suffix="%"):
    if value is None:
        return '<span class="flat">n/a</span>'
    cls = "up" if value > 0 else "down" if value < 0 else "flat"
    return f'<span class="{cls}">{value:+.2f}{suffix}</span>'


def source_links(ids, sources):
    if not ids:
        return ""
    by_id = {str(s.get("id")): s for s in sources}
    links = []
    for sid in ids:
        source = by_id.get(str(sid))
        if not source:
            continue
        title = esc(source.get("title", f"source {sid}"))
        url = esc(source.get("url", ""))
        links.append(f'<a href="{url}" target="_blank" rel="noopener" title="{title}">{esc(sid)}</a>')
    return f'<span class="src">{"".join(links)}</span>' if links else ""


def render_items(items, sources, empty_note):
    if not items:
        return f'<p class="meta">{esc(empty_note)}</p>'
    out = []
    for item in items:
        impact = (item.get("impact") or "neutral").lower()
        tag = (f'<span class="tag {impact}">{IMPACT_LABEL.get(impact, impact)}</span>'
               if impact in IMPACT_LABEL else "")
        date = f'<span class="date">{esc(item.get("date", ""))}</span>' if item.get("date") else ""
        out.append(
            f'<div class="item {impact}">'
            f'<div class="head">{date}{rich(item.get("headline", ""))}{tag}</div>'
            f'<div>{rich(item.get("detail", ""))}{source_links(item.get("sources"), sources)}</div>'
            f"</div>"
        )
    return "".join(out)


def render_price(price):
    if not price:
        return ""
    if price.get("error"):
        return f'<p class="meta">Price context unavailable: {esc(price["error"])}</p>'

    def stat(label, value):
        return f'<div class="stat"><div class="k">{esc(label)}</div><div class="v">{value}</div></div>'

    cells = [stat("Close", f'{price.get("close", "n/a")}')]
    for label, key in (("1 day", "change_1d_pct"), ("5 days", "change_5d_pct"),
                       ("1 month", "change_1m_pct"), ("1 year", "change_1y_pct")):
        if price.get(key) is not None:
            cells.append(stat(label, signed(price[key])))
    if price.get("pct_from_52w_high") is not None:
        cells.append(stat("vs 52w high", signed(price["pct_from_52w_high"])))
    if price.get("atr14") is not None:
        atr_pct = price.get("atr14_pct_of_close")
        cells.append(stat("ATR(14)", f'{price["atr14"]}' + (f' <span class="meta">({atr_pct}%)</span>' if atr_pct else "")))
    if price.get("last_volume_vs_20d_avg") is not None:
        cells.append(stat("Vol vs 20d", f'{price["last_volume_vs_20d_avg"]}x'))

    notes = [f'as of {esc(price.get("as_of", ""))}']
    if price.get("close_source") and price["close_source"] != "daily bar":
        notes.append(esc(price["close_source"]))
    if price.get("next_earnings"):
        notes.append(f'next earnings {esc(price["next_earnings"])}')
    return f'<div class="stats">{"".join(cells)}</div><p class="meta">{" · ".join(notes)}</p>'


def render_section(section, sources):
    ticker = esc(section.get("ticker", ""))
    name = esc(section.get("name") or (section.get("price") or {}).get("name") or "")
    heading = f"{ticker}" + (f" — {name}" if name else "")
    parts = [f"<h2>{heading}</h2>", render_price(section.get("price"))]

    parts.append("<h3>Direct news</h3>")
    parts.append(render_items(section.get("direct"), sources,
                              "No company-specific news found in the window."))
    parts.append("<h3>Indirect and context</h3>")
    parts.append(render_items(section.get("indirect"), sources,
                              "Nothing notable in the sector or macro backdrop."))

    up, down = section.get("up") or [], section.get("down") or []
    if up or down:
        def column(title, items, cls):
            body = "".join(f"<li>{rich(i)}</li>" for i in items) or '<li class="meta">nothing notable</li>'
            return f'<div class="card"><h3 class="{cls}">{title}</h3><ul>{body}</ul></div>'
        parts.append("<h3>What could move it</h3>")
        parts.append(f'<div class="cols">{column("Higher", up, "up")}{column("Lower", down, "down")}</div>')

    calendar = section.get("calendar") or []
    if calendar:
        rows = "".join(f'<tr><td class="num">{esc(c.get("date",""))}</td><td>{rich(c.get("what",""))}</td></tr>'
                       for c in calendar)
        parts.append('<h3>Calendar</h3><table><thead><tr><th>Date</th><th>Event</th></tr></thead>'
                     f"<tbody>{rows}</tbody></table>")

    if section.get("takeaway"):
        parts.append(f'<h3>Takeaway</h3><div class="card">{rich(section["takeaway"])}</div>')
    return "".join(parts)


def build_html(report):
    sources = report.get("sources") or []
    tickers = report.get("tickers") or [s.get("ticker") for s in report.get("sections", [])]
    title = report.get("title") or f"{', '.join(tickers)} news report"

    meta = [f'<span>{esc(report.get("generated_at", dt.datetime.now().strftime("%Y-%m-%d %H:%M")))}</span>']
    if report.get("window"):
        meta.append(f'<span>window: {esc(report["window"])}</span>')
    if report.get("next_session"):
        meta.append(f'<span>next session: {esc(report["next_session"])}</span>')

    head = [f"<h1>{esc(title)}</h1>", f'<div class="meta">{"".join(meta)}</div>',
            '<div class="chips">' + "".join(f'<span class="chip">{esc(t)}</span>' for t in tickers) + "</div>"]
    body = [f'<header class="top">{"".join(head)}</header>']

    if report.get("request"):
        body.append(f'<p class="request">Request: “{esc(report["request"])}”</p>')
    if report.get("bottom_line"):
        body.append(f'<div class="card bottom-line">{rich(report["bottom_line"])}</div>')

    for section in report.get("sections", []):
        body.append(render_section(section, sources))

    shared = report.get("shared_drivers") or []
    if shared:
        body.append("<h2>Shared drivers</h2>")
        for driver in shared:
            body.append(f'<div class="card"><strong>{esc(driver.get("heading",""))}</strong><br>'
                        f'{rich(driver.get("body",""))}{source_links(driver.get("sources"), sources)}</div>')

    caveats = report.get("caveats") or []
    if caveats:
        body.append("<h2>Caveats</h2><div class=\"card\"><ul>"
                    + "".join(f"<li>{rich(c)}</li>" for c in caveats) + "</ul></div>")

    if sources:
        items = []
        for source in sources:
            when = f' <span class="when">({esc(source["date"])})</span>' if source.get("date") else ""
            url = esc(source.get("url", ""))
            items.append(f'<li value="{esc(source.get("id",""))}">'
                         f'<a href="{url}" target="_blank" rel="noopener">{esc(source.get("title", url))}</a>{when}</li>')
        body.append(f'<h2>Sources</h2><ol class="sources">{"".join(items)}</ol>')

    body.append('<footer>Research summary, not investment advice. Figures come from public '
                'sources and may be wrong or revised; verify anything you act on.</footer>')

    return ("<!doctype html>\n<html lang=\"en\"><head><meta charset=\"utf-8\">\n"
            '<meta name="viewport" content="width=device-width, initial-scale=1">\n'
            f"<title>{esc(title)}</title>\n<style>{CSS}</style>\n</head><body>\n"
            f'<div class="wrap">{"".join(body)}</div>\n</body></html>\n')


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("report_json")
    parser.add_argument("--out", default="reports", help="Output directory (default: reports/)")
    parser.add_argument("--filename", help="Override the generated file name")
    args = parser.parse_args(argv)

    report = json.loads(Path(args.report_json).read_text())
    tickers = report.get("tickers") or [s.get("ticker") for s in report.get("sections", [])]
    name = args.filename or f"{dt.date.today().isoformat()}_{'-'.join(t.upper() for t in tickers) or 'report'}.html"

    out_dir = Path(args.out)
    out_dir.mkdir(parents=True, exist_ok=True)
    path = out_dir / name
    path.write_text(build_html(report))
    print(path.resolve())
    return 0


if __name__ == "__main__":
    sys.exit(main())
