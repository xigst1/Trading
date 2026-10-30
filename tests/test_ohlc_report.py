import datetime as dt
import json

import pandas as pd

from common.ohlc_report import build_payload, quarter_labels, render_html


def prices(rows):
    return pd.DataFrame(rows, columns=["symbol", "date", "open", "high", "low", "close"])


def universe(rows):
    return pd.DataFrame(rows, columns=["symbol", "name", "sector", "market_cap_rank"])


D1, D2, D3 = dt.date(2026, 1, 2), dt.date(2026, 1, 5), dt.date(2026, 1, 6)


def test_payload_aligns_dates_and_leaves_null_where_a_bar_is_missing():
    data = prices([
        ("AAA", D1, 10, 11, 9, 10.5), ("AAA", D3, 12, 13, 11, 12.5),         # no D2 bar
        ("BBB", D1, 20, 21, 19, 20.5), ("BBB", D2, 20, 22, 19, 21.5), ("BBB", D3, 21, 23, 20, 22.5),
    ])
    uni = universe([("AAA", "A Corp", "Tech", 1), ("BBB", "B Inc", "Tech", 2)])
    payload = build_payload(data, uni)
    assert payload["dates"] == ["2026-01-02", "2026-01-05", "2026-01-06"]
    aaa = payload["groups"][0]["stocks"][0]
    assert aaa["s"] == "AAA" and aaa["o"] == [[10, 11, 9, 10.5], None, [12, 13, 11, 12.5]]
    assert aaa["last"] == 12.5 and aaa["chg"] == 19.0   # first close 10.5 -> last close 12.5 is +19.0%


def test_sectors_sorted_by_name_and_stocks_by_market_cap_rank_with_unranked_last():
    data = prices([(s, D1, 1, 1, 1, 1) for s in ["ZZZ", "AAA", "MMM", "QQQ"]])
    uni = universe([
        ("ZZZ", "Z", "Utilities", 4),
        ("AAA", "A", "Energy", None),
        ("MMM", "M", "Energy", 1),
        ("QQQ", "Q", "Energy", 3),
    ])
    payload = build_payload(data, uni)
    assert [g["sector"] for g in payload["groups"]] == ["Energy", "Utilities"]
    energy = payload["groups"][0]["stocks"]
    assert [s["s"] for s in energy] == ["MMM", "QQQ", "AAA"]
    assert energy[-1]["r"] is None and energy[0]["r"] == 1


def test_sector_is_ordered_by_the_market_cap_shown_on_the_card():
    data = prices([(s, D1, 10, 10, 10, 10) for s in ["SMALL", "BIG", "NOCAP"]])
    uni = universe([("SMALL", "S", "Tech", 1), ("BIG", "B", "Tech", 2), ("NOCAP", "N", "Tech", None)])
    uni["market_cap"] = [100e9, 300e9, None]   # snapshot rank says SMALL first; live cap says BIG first
    uni["price"] = [10.0, 10.0, None]
    stocks = build_payload(data, uni)["groups"][0]["stocks"]
    assert [s["s"] for s in stocks] == ["BIG", "SMALL", "NOCAP"]


def test_etf_group_comes_first_and_is_not_mixed_into_sectors():
    data = prices([("XLK", D1, 1, 1, 1, 1), ("AAA", D1, 1, 1, 1, 1)])
    uni = universe([("XLK", "Select Sector SPDR: Information Technology", "ETF", None),
                    ("AAA", "A", "Energy", 1)])
    names = [g["sector"] for g in build_payload(data, uni)["groups"]]
    assert names == ["ETF", "Energy"]


def test_stocks_without_prices_are_listed_as_missing_not_charted():
    data = prices([("AAA", D1, 1, 1, 1, 1)])
    payload = build_payload(data, universe([("AAA", "A", "Tech", 1), ("GONE", "Gone Co", "Tech", 2)]))
    assert payload["missing"] == ["GONE"]
    assert [s["s"] for s in payload["groups"][0]["stocks"]] == ["AAA"]


def test_unknown_sector_and_missing_name_do_not_crash():
    data = prices([("AAA", D1, 1, 1, 1, 1)])
    payload = build_payload(data, universe([("AAA", None, None, None)]))
    stock = payload["groups"][0]
    assert stock["sector"] == "Unknown" and payload["groups"][0]["stocks"][0]["n"] == ""


def test_quarter_labels_mark_quarter_starts_and_thin_out():
    dates = [dt.date(2024, 1, 2) + dt.timedelta(days=7 * k) for k in range(120)]  # about two years, weekly
    labels = quarter_labels(dates, max_labels=5)
    assert len(labels) <= 5 and labels[0][0] == 0
    assert all(isinstance(i, int) and isinstance(text, str) for i, text in labels)
    assert quarter_labels([dt.date(2026, 1, 2), dt.date(2026, 2, 2)]) == [[0, "Jan 26"]]


def test_render_embeds_data_and_cannot_break_out_of_the_script_tag():
    data = prices([("AAA", D1, 1, 2, 0.5, 1.5)])
    evil = universe([("AAA", "</script><script>alert(1)</script>", "Tech", 1)])
    page = render_html(build_payload(data, evil), "Title & <more>", "2 stocks")
    assert "Title &amp; &lt;more&gt;" in page
    assert "</script><script>alert(1)" not in page
    start = page.index("const D = ") + len("const D = ")
    end = page.index(";\nconst NS")
    embedded = json.loads(page[start:end].replace("<\\/", "</"))
    assert embedded["groups"][0]["stocks"][0]["s"] == "AAA"


def test_page_has_the_zoom_sector_bar_and_grid_hooks():
    page = render_html(build_payload(prices([("AAA", D1, 1, 2, 0.5, 1.5)]), universe([("AAA", "A", "Tech", 1)])),
                       "t", "s")
    assert "grid-template-columns:repeat(4" in page.replace(" ", "")
    assert 'plot.addEventListener("click", () => openZoom(card))' in page  # small chart opens the weekly OHLC
    assert 'id="zback"' in page and 'id="zreset"' in page                  # buttons: back to line, reset zoom
    assert 'document.getElementById("zback").addEventListener("click", closeZoom)' in page
    assert 'if (e.key === "Escape") closeZoom()' in page
    assert "selectionView(" in page and 'pointerup' in page               # drag selects daily candles
    assert '<nav class="jump" id="jump"' in page                          # sticky sector bar
    assert "position:sticky;top:0" in page
    assert 'sec.id = "sec-" + gi' in page and 'scrollIntoView({behavior: "smooth", block: "start"})' in page
    assert 'setAttribute("role", "button")' in page


def test_market_cap_in_billions_scales_with_the_latest_close():
    data = prices([("AAA", D1, 10, 10, 10, 10), ("AAA", D2, 20, 20, 20, 20)])   # latest close 20
    uni = universe([("AAA", "A", "Tech", 1)])
    uni["market_cap"] = [50e9]        # snapshot: $50B at price 10 -> $100B at 20
    uni["price"] = [10.0]
    stock = build_payload(data, uni)["groups"][0]["stocks"][0]
    assert stock["cap"] == 100.0
    assert "capText(st)" in render_html(build_payload(data, uni), "t", "s")


def test_missing_market_cap_is_null_not_an_error():
    data = prices([("AAA", D1, 10, 10, 10, 10)])
    stock = build_payload(data, universe([("AAA", "A", "Tech", 1)]))["groups"][0]["stocks"][0]
    assert stock["cap"] is None


def test_weekly_bars_are_first_open_highest_high_lowest_low_last_close():
    # Week of Mon 5 Jan to Fri 9 Jan 2026, then a partial week (Mon 12 to Wed 14 Jan).
    days = [dt.date(2026, 1, 5), dt.date(2026, 1, 6), dt.date(2026, 1, 7), dt.date(2026, 1, 8), dt.date(2026, 1, 9),
            dt.date(2026, 1, 12), dt.date(2026, 1, 13), dt.date(2026, 1, 14)]
    rows = [("AAA", d, o, h, l, c) for d, (o, h, l, c) in zip(days, [
        (10, 12, 9, 11), (11, 15, 10, 14), (14, 14, 8, 9), (9, 11, 8.5, 10), (10, 13, 9.5, 12),
        (20, 21, 19, 20.5), (20.5, 22, 20, 21.5), (21.5, 22.5, 21, 22)])]
    payload = build_payload(prices(rows), universe([("AAA", "A", "Tech", 1)]))
    assert payload["wk"] == [["2026-01-05", "2026-01-09"], ["2026-01-12", "2026-01-14"]]
    weeks = payload["groups"][0]["stocks"][0]["w"]
    assert weeks[0] == [10, 15, 8, 12]           # open of Monday, highest high, lowest low, Friday's close
    assert weeks[1] == [20, 22.5, 19, 22]        # partial week kept as it stands
    assert payload["wxl"][0][0] == 0


def test_weekly_week_with_no_complete_bar_is_null():
    rows = [("AAA", dt.date(2026, 1, 5), 10, 11, 9, 10), ("AAA", dt.date(2026, 1, 12), None, None, None, None)]
    rows = [r for r in rows if r[2] is not None or r[1] == dt.date(2026, 1, 5)]
    data = pd.DataFrame(rows, columns=["symbol", "date", "open", "high", "low", "close"])
    data = pd.concat([data, prices([("BBB", dt.date(2026, 1, 12), 5, 6, 4, 5)])])
    payload = build_payload(data, universe([("AAA", "A", "Tech", 1), ("BBB", "B", "Tech", 2)]))
    stocks = {s["s"]: s for g in payload["groups"] for s in g["stocks"]}
    assert stocks["AAA"]["w"] == [[10, 11, 9, 10], None] and stocks["BBB"]["w"] == [None, [5, 6, 4, 5]]


def test_sector_bar_lists_every_sector_with_its_count():
    data = prices([("AAA", D1, 1, 1, 1, 1), ("BBB", D1, 1, 1, 1, 1), ("CCC", D1, 1, 1, 1, 1)])
    uni = universe([("AAA", "A", "Tech", 1), ("BBB", "B", "Tech", 2), ("CCC", "C", "Energy", 3)])
    payload = build_payload(data, uni)
    assert [(g["sector"], len(g["stocks"])) for g in payload["groups"]] == [("Energy", 1), ("Tech", 2)]


def test_zero_first_close_gives_zero_change_instead_of_dividing_by_zero():
    data = prices([("AAA", D1, 0, 0, 0, 0), ("AAA", D2, 0, 0, 0, 0)])
    payload = build_payload(data, universe([("AAA", "A", "Tech", 1)]))
    assert payload["groups"][0]["stocks"][0]["chg"] == 0.0
