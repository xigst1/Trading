---
name: stock-news-report
description: Research recent news for one or more stock tickers and publish it as a styled HTML report with price context, per-ticker direct and indirect news, an up/down driver table, upcoming catalysts and cited sources. Use this whenever the user asks what is happening with a stock, what news could move a ticker, for a news roundup, catalyst check, pre-market or overnight brief, or a "what should I watch tomorrow" style question about specific tickers — even when they do not say the word "report". Also use it when they name several tickers and want them compared, or when they ask why a stock moved recently.
---

# Stock news report

Turn a short request that names one or more tickers into a researched, sourced HTML
report saved under `reports/` in the current working directory.

The reader is an active trader who will decide something from this. That shapes
everything below: date every claim, separate what is about the company from what is
about its industry or the macro backdrop, and be honest about what is unknown. A
confident report that turns out to be wrong is worse than a hedged one that is right.

## Workflow

1. **Parse the request.** Pull out the tickers, the lookback window (default: last 5
   days), and any extra requirement the user attached ("only regulatory news", "focus on
   the AI angle", "compare them", "I hold it long"). Honour that requirement visibly —
   it is usually the reason they asked.
2. **Get price context** (a script, no research needed):
   ```bash
   python ~/.claude/skills/stock-news-report/scripts/price_context.py AAPL MSFT --out /tmp/price.json
   ```
   It prints a readable summary and writes JSON. Run it early: knowing the stock is up
   10% in a month or sitting at a 52-week high changes which news actually matters, and
   lets you check the news against what the price did.
3. **Research with WebSearch.** Budget 4-8 searches per ticker, run in parallel where
   they are independent. Cover these angles, because the market-moving item is often not
   the obvious one:
   - the company itself: products, guidance, management, legal, buybacks
   - analyst actions in the window: upgrades, downgrades, target changes, and the
     *reasoning*, which is what other desks react to
   - suppliers, customers and close competitors (an input cost or a rival's results
     move the stock too)
   - regulation, litigation, tariffs, export controls
   - the macro and sector backdrop, plus the data calendar for the next session
   - scheduled catalysts: earnings date, product events, index changes, lockups
   Use `WebFetch` on the two or three most important articles to confirm dates, numbers
   and ratings rather than trusting a search snippet. Snippets routinely blur dates and
   mix up old and new stories, and a wrong date destroys the report's value.
4. **Write `report.json`** (schema below) into a temp path.
5. **Build the HTML:**
   ```bash
   python ~/.claude/skills/stock-news-report/scripts/build_report.py /tmp/report.json --out reports/
   ```
   It prints the path it wrote. Filenames look like `2026-09-27_AAPL-MSFT.html`.
6. **Reply in chat** with the two or three things that matter most and the file path.
   Do not paste the whole report back; they can open it.

## Report structure

The builder renders these sections in this order. Keep them — the shape is the point,
because it separates company news from context and ends with something actionable.

| Section | What goes in it |
|---|---|
| Header | Title, tickers, the window, when it was generated, the original request |
| Bottom line | 2-4 sentences: the single most likely driver and the balance of risk |
| Price context | From the script: last close, changes, 52-week position, ATR, volume |
| Direct news | Dated items about the company itself, most market-relevant first |
| Indirect news | Suppliers, sector, memory/commodity costs, macro, regulation |
| What could move it | Two columns: reasons it goes up, reasons it goes down |
| Calendar | Dated upcoming catalysts, nearest first |
| Takeaway | One paragraph per ticker: what you would watch and why |
| Caveats | What is uncertain, what could not be verified, that this is not advice |
| Sources | Every cited URL with its date |

With several tickers, each gets its own price, direct, indirect, up/down, calendar and
takeaway block. Put anything that hits all of them (a Fed meeting, a sector selloff)
in `shared_drivers` once, rather than repeating it per ticker.

## report.json schema

Every field is optional except `tickers` and `sections`; the builder skips what is
missing rather than printing empty boxes.

```json
{
  "title": "AAPL — what could move the stock",
  "request": "the user's original words, verbatim",
  "window": "22-26 September 2026",
  "next_session": "Monday 28 September 2026",
  "generated_at": "2026-09-27 21:30 PT",
  "tickers": ["AAPL"],
  "bottom_line": "2-4 sentences.",
  "shared_drivers": [
    {"heading": "Macro", "body": "Core PCE and payrolls land this week...", "sources": [10]}
  ],
  "sections": [
    {
      "ticker": "AAPL",
      "name": "Apple Inc.",
      "price": { "...": "paste the object for this ticker from price_context.py" },
      "direct": [
        {"date": "2026-09-25", "headline": "Bernstein flags December-quarter margin risk",
         "detail": "Keeps Outperform and a $370 target but sees iPhone gross margin at 40.4%, about 390bp below consensus, on memory costs.",
         "impact": "bearish", "sources": [2, 3]}
      ],
      "indirect": [
        {"date": "2026-09-25", "headline": "Mobile DRAM up >50% q/q", "detail": "...",
         "impact": "bearish", "sources": [7]}
      ],
      "up": ["Reasons it could rise, each one sentence"],
      "down": ["Reasons it could fall"],
      "calendar": [{"date": "2026-10-29", "what": "Q4 FY2026 earnings"}],
      "takeaway": "One paragraph."
    }
  ],
  "caveats": ["..."],
  "sources": [{"id": 1, "title": "UBS on iPhone demand", "url": "https://...", "date": "2026-09-23"}]
}
```

Notes on the fields:

- `impact` is `bullish`, `bearish` or `neutral` and colours the item's left border. Use
  `neutral` when it genuinely cuts both ways; a report where everything is bullish reads
  as marketing.
- `sources` on an item is a list of `id`s from the top-level `sources` array, rendered
  as small numbered links. Every factual item should carry at least one, so the reader
  can check you.
- `detail` and `body` accept `**bold**`, `*italic*` and `[text](url)`. Everything is
  escaped, so plain prose is safe.
- Order `direct` items by how much they could move the stock, not chronologically. The
  reader stops after the first few.

## Judgment calls that make these reports good

**Distinguish fresh from stale.** Only include items inside the window unless older
context is needed to make a new item legible, and then date it clearly. Search engines
happily return year-old articles.

**Say when nothing happened.** A quiet week is a real finding. Write "no company-specific
news in the window; the stock is trading with the sector" rather than padding with
filler. Do not manufacture drama.

**Quantify where you can.** "Target cut to $296 from $315, implying 13% downside" beats
"analyst turned cautious". Numbers are what the reader acts on.

**Name the disagreement.** When two desks contradict each other, show both with their
targets. That disagreement is often the most useful content in the report.

**Check the news against the price.** If the news is grim but the stock rose, say so —
either the news is priced in or the market disagrees, and both are worth flagging.

**Stay out of advice.** Describe drivers, risks and what to watch. Do not tell them to
buy, sell or size a position, and keep a caveat that this is research, not a
recommendation, single-day moves are mostly noise, and sources may be wrong.

## Multi-ticker requests

Report each ticker fully, then let `shared_drivers` carry the common thread. If the
tickers are related (competitors, supplier and customer, same index), say explicitly
how a piece of news hits them differently — that comparison is usually why the user
grouped them.

## When research fails

If a ticker returns nothing useful, still produce the report with its price context and
an honest note. If `price_context.py` fails (no `yfinance`, no network, bad symbol), the
report is still worth building from news alone — mention the gap in `caveats` rather
than silently dropping it.
