# -*- coding: utf-8 -*-
"""
澎湖強風風險預警系統 - 歷史逐時資料分析

前置：先執行 python import_history.py，把歷史資料匯入 penghu_history.db。

回答的問題：
  1. 資料品質：各測站各年的涵蓋率、風速與陣風缺值率
  2. 橋區參考站（預設西嶼）的 7 級／8 級／10 級風實際出現頻率：逐年、逐月（季節性）
  3. 強風「事件」：連續達到門檻的時段有幾次、各持續多久（管制是以事件為單位，比單看小時數更貼近實況）
  4. 測站比較：用其他測站（如澎湖）當橋區代表，會漏掉多少 8 級以上的時數？
  5. 陣風係數：陣風與平均風速的關係，以及「平均風速未達管制、但陣風已達管制風速」的時數
  6. 與即時資料庫（penghu_weather.db）重疊時段的時間對齊檢查（確認歷史檔與 API 的時間定義一致）

輸出：
  終端機報告，以及 analysis_output/ 資料夾下的 CSV（可直接用 Excel 開啟畫圖）

使用方式：
  python analyze_history.py
  python analyze_history.py --reference C0W120
"""

import argparse
import csv
import os
import sqlite3
import statistics
from collections import defaultdict
from datetime import datetime, timedelta

import risk_assessment
from import_history import HISTORY_DB_PATH, STATIONS
from stats import pad

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
LIVE_DB_PATH = os.path.join(BASE_DIR, "penghu_weather.db")
OUTPUT_DIR = os.path.join(BASE_DIR, "analysis_output")

REFERENCE_STATION = "C0W120"  # 西嶼

T = risk_assessment.THRESHOLDS
LEVELS = [                       # (欄位名稱, 門檻 m/s)
    ("7級以上", T["stage1_strict_min"]),
    ("8級以上", T["stage1_min"]),
    ("10級以上", T["stage2_min"]),
]
ONE_HOUR = timedelta(hours=1)


def station_label(sid):
    return f"{STATIONS.get(sid, sid)}({sid})"


def section(title):
    print(f"\n=== {title} ===")


def pct(part, whole):
    return f"{part / whole * 100:.1f}%" if whole else "-"


def fmt(value, digits=1):
    return "-" if value is None else f"{value:.{digits}f}"


# ------------------------------------------------------------
# 資料載入
# ------------------------------------------------------------
def load_all(conn):
    """回傳 {station_id: {datetime: (風速, 風向, 陣風)}}"""
    data = defaultdict(dict)
    cur = conn.execute("""
        SELECT station_id, obs_time, wind_speed, wind_direction, peak_gust_speed
        FROM history_hourly ORDER BY station_id, obs_time
    """)
    for sid, t, ws, wd, gust in cur:
        data[sid][datetime.fromisoformat(t)] = (ws, wd, gust)
    return data


# ------------------------------------------------------------
# 1. 資料品質
# ------------------------------------------------------------
def print_availability(data):
    section("資料品質：各測站各年涵蓋率與缺值率")
    print(pad("測站", 18) + pad("年", 6) + pad("筆數", 7, "right") + pad("涵蓋率", 8, "right")
          + pad("風速缺值", 10, "right") + pad("陣風缺值", 10, "right"))
    print("-" * 59)
    for sid in sorted(data):
        by_year = defaultdict(list)
        for t in data[sid]:
            by_year[t.year].append(t)
        for year in sorted(by_year):
            times = by_year[year]
            expected = round((max(times) - min(times)) / ONE_HOUR) + 1
            ws_missing = sum(1 for t in times if data[sid][t][0] is None)
            gust_missing = sum(1 for t in times if data[sid][t][2] is None)
            n = len(times)
            print(pad(station_label(sid), 18) + pad(year, 6) + pad(n, 7, "right")
                  + pad(pct(n, expected), 8, "right")
                  + pad(pct(ws_missing, n), 10, "right") + pad(pct(gust_missing, n), 10, "right"))
    print("（涵蓋率 = 實際筆數 ÷ 該年第一筆到最後一筆之間應有的整點數）")


# ------------------------------------------------------------
# 2. 各風級出現頻率
# ------------------------------------------------------------
def count_hours(series_items):
    valid = 0
    hits = [0] * len(LEVELS)
    for ws, _wd, _gust in series_items:
        if ws is None:
            continue
        valid += 1
        for i, (_name, threshold) in enumerate(LEVELS):
            if ws >= threshold:
                hits[i] += 1
    return valid, hits


def print_level_frequency(series, sid, rows_out):
    section(f"風級出現頻率：{station_label(sid)}（平均風速，單位：小時）")
    header = pad("年", 8) + pad("有效小時", 10, "right")
    for name, _ in LEVELS:
        header += pad(name, 14, "right")
    print(header)
    print("-" * (18 + 14 * len(LEVELS)))

    by_year = defaultdict(list)
    for t, v in series.items():
        by_year[t.year].append(v)
    yearly_csv = []
    for year in sorted(by_year):
        valid, hits = count_hours(by_year[year])
        line = pad(year, 8) + pad(valid, 10, "right")
        for h in hits:
            line += pad(f"{h} ({pct(h, valid)})", 14, "right")
        print(line)
        yearly_csv.append([year, valid, *hits])
    rows_out["yearly"] = (["年", "有效小時"] + [n for n, _ in LEVELS], yearly_csv)

    print("\n逐月（各年合計，看季節性）：")
    print(header.replace("年", "月", 1))
    print("-" * (18 + 14 * len(LEVELS)))
    by_month = defaultdict(list)
    for t, v in series.items():
        by_month[t.month].append(v)
    monthly_csv = []
    for month in range(1, 13):
        if month not in by_month:
            continue
        valid, hits = count_hours(by_month[month])
        line = pad(f"{month}月", 8) + pad(valid, 10, "right")
        for h in hits:
            line += pad(f"{h} ({pct(h, valid)})", 14, "right")
        print(line)
        monthly_csv.append([month, valid, *hits])
    rows_out["monthly"] = (["月", "有效小時"] + [n for n, _ in LEVELS], monthly_csv)
    print("（7級 = 加嚴門檻研擬中；8級 = 第一階段管制；10級 = 全橋封閉）")


# ------------------------------------------------------------
# 3. 強風事件
# ------------------------------------------------------------
def find_episodes(series, threshold):
    """連續（整點相連）平均風速 >= 門檻的時段。資料缺值或時間不連續會切斷事件。"""
    episodes = []
    current = None
    prev_t = None
    for t in sorted(series):
        ws, _wd, gust = series[t]
        hit = ws is not None and ws >= threshold
        contiguous = prev_t is not None and (t - prev_t) == ONE_HOUR
        if hit and current is not None and contiguous:
            current["end"] = t
            current["hours"] += 1
            current["max_ws"] = max(current["max_ws"], ws)
            if gust is not None:
                current["max_gust"] = gust if current["max_gust"] is None else max(current["max_gust"], gust)
        else:
            if current is not None:
                episodes.append(current)
                current = None
            if hit:
                current = {"start": t, "end": t, "hours": 1, "max_ws": ws, "max_gust": gust}
        prev_t = t
    if current is not None:
        episodes.append(current)
    return episodes


def print_episodes(series, sid, rows_out):
    section(f"強風事件（連續達門檻的時段）：{station_label(sid)}")
    print(pad("門檻", 12) + pad("事件數", 8, "right") + pad("平均持續(小時)", 16, "right")
          + pad("最長(小時)", 12, "right") + pad("有事件的日數", 14, "right"))
    print("-" * 62)
    for name, threshold in LEVELS:
        eps = find_episodes(series, threshold)
        if not eps:
            print(pad(f"{name}", 12) + pad(0, 8, "right"))
            continue
        durations = [e["hours"] for e in eps]
        days = {d.date() for e in eps for d in (e["start"] + i * ONE_HOUR for i in range(e["hours"]))}
        print(pad(f"{name}", 12) + pad(len(eps), 8, "right")
              + pad(f"{statistics.mean(durations):.1f}", 16, "right")
              + pad(max(durations), 12, "right") + pad(len(days), 14, "right"))

        if threshold == T["stage1_min"]:   # 8 級事件逐筆輸出，方便對照公路局管制公告
            rows_out["episodes_8"] = (
                ["開始時間", "結束時間(最後一個達標整點)", "持續小時", "最大平均風速", "最大陣風"],
                [[e["start"].strftime("%Y-%m-%d %H:%M"), e["end"].strftime("%Y-%m-%d %H:%M"),
                  e["hours"], e["max_ws"], "" if e["max_gust"] is None else e["max_gust"]]
                 for e in eps])
    print("（事件清單已輸出成 CSV，可拿去對照公路局實際發布管制的日期，作為模型驗證依據）")


# ------------------------------------------------------------
# 4. 測站比較
# ------------------------------------------------------------
def print_station_comparison(data, ref_sid):
    section(f"測站比較：以 {station_label(ref_sid)} 為基準")
    ref = data[ref_sid]
    others = [s for s in sorted(data) if s != ref_sid]
    if not others:
        print("資料庫中只有參考站，無法比較。請用 --all 匯入其他測站。")
        return

    print(pad("對照測站", 18) + pad("配對筆數", 10, "right") + pad("相關係數", 10, "right")
          + pad("斜率", 8, "right") + pad("參考站8級時數", 14, "right")
          + pad("該站也≥8級", 12, "right") + pad("漏掉比例", 10, "right"))
    print("-" * 82)
    for sid in others:
        xs, ys = [], []
        ref_stage1 = 0
        other_also = 0
        for t, (ws_ref, _a, _b) in ref.items():
            other = data[sid].get(t)
            if other is None or other[0] is None or ws_ref is None:
                continue
            xs.append(other[0])
            ys.append(ws_ref)
            if ws_ref >= T["stage1_min"]:
                ref_stage1 += 1
                if other[0] >= T["stage1_min"]:
                    other_also += 1
        if len(xs) < 3:
            print(pad(station_label(sid), 18) + pad(len(xs), 10, "right") + "  配對太少")
            continue
        try:
            r = statistics.correlation(xs, ys)
            slope = statistics.linear_regression(xs, ys).slope
            r_text, slope_text = f"{r:.2f}", f"{slope:.2f}"
        except statistics.StatisticsError:
            r_text, slope_text = "-", "-"
        missed = pct(ref_stage1 - other_also, ref_stage1)
        print(pad(station_label(sid), 18) + pad(len(xs), 10, "right") + pad(r_text, 10, "right")
              + pad(slope_text, 8, "right") + pad(ref_stage1, 14, "right")
              + pad(other_also, 12, "right") + pad(missed, 10, "right"))
    print("（斜率 = 以對照站風速預測參考站風速的迴歸斜率；漏掉比例 = 參考站達 8 級時，")
    print("  該對照站卻未達 8 級的比例，越高代表越不適合用它代替橋區。）")


# ------------------------------------------------------------
# 5. 陣風係數
# ------------------------------------------------------------
def print_gust_analysis(series, sid):
    section(f"陣風分析：{station_label(sid)}")
    pairs = [(ws, gust) for ws, _wd, gust in series.values()
             if ws is not None and gust is not None and ws > 0]
    total_valid = sum(1 for v in series.values() if v[0] is not None)
    print(f"同時有平均風速與陣風的筆數：{len(pairs)}／{total_valid} 筆有效風速 ({pct(len(pairs), total_valid)})")
    if not pairs:
        return

    def gust_factor_summary(label, items):
        if len(items) < 5:
            print(f"  {label}：筆數不足")
            return
        factors = sorted(g / w for w, g in items)
        n = len(factors)
        print(f"  {label}：{n} 筆，陣風係數（陣風÷平均風速）中位數 {factors[n // 2]:.2f}，"
              f"第 10～90 百分位 {factors[int(n * 0.1)]:.2f}～{factors[int(n * 0.9)]:.2f}")

    gust_factor_summary("全部", pairs)
    gust_factor_summary(f"平均風速 ≥ {T['stage1_strict_min']} m/s", [p for p in pairs if p[0] >= T["stage1_strict_min"]])

    below = [p for p in pairs if p[0] < T["stage1_min"] and p[1] >= T["stage1_min"]]
    print(f"平均風速未達 8 級、但陣風已達 8 級風速的時數：{len(below)} 小時"
          f"（占有陣風紀錄的 {pct(len(below), len(pairs))}）")
    print("  → 公路局管制以平均風速為準，這些時段依規定不會管制，但實際體感風險較高。")


# ------------------------------------------------------------
# 6. 與即時資料庫的時間對齊檢查
# ------------------------------------------------------------
def print_alignment_check(data):
    section("時間對齊檢查：歷史資料 vs 即時資料庫（penghu_weather.db）")
    if not os.path.exists(LIVE_DB_PATH):
        print("找不到即時資料庫，略過。")
        return
    live = defaultdict(dict)
    conn = sqlite3.connect(LIVE_DB_PATH)
    try:
        for sid, t, ws in conn.execute(
                "SELECT station_id, obs_time, wind_speed FROM penghu_weather WHERE wind_speed IS NOT NULL"):
            live[sid][datetime.fromisoformat(t)] = ws
    except sqlite3.Error:
        print("即時資料庫讀取失敗，略過。")
        return
    finally:
        conn.close()

    results = []
    for offset in (-1, 0, 1):
        diffs = []
        for sid in live:
            if sid not in data:
                continue
            for t, ws_live in live[sid].items():
                hist = data[sid].get(t + offset * ONE_HOUR)
                if hist and hist[0] is not None:
                    diffs.append(abs(hist[0] - ws_live))
        results.append((offset, diffs))

    if not any(d for _o, d in results):
        print("兩邊沒有重疊的時段（歷史 repo 通常晚一天更新，請過一兩天、重新執行 import_history.py 後再試）。")
        return

    print("把歷史資料的時間平移後，與即時資料的風速逐筆比對：")
    print(pad("平移", 8) + pad("配對筆數", 10, "right") + pad("平均絕對差", 12, "right")
          + pad("幾乎相同(<0.05)", 16, "right"))
    for offset, diffs in results:
        if not diffs:
            continue
        exact = sum(1 for d in diffs if d < 0.05)
        print(pad(f"{offset:+d} 小時", 8) + pad(len(diffs), 10, "right")
              + pad(f"{statistics.mean(diffs):.2f}", 12, "right")
              + pad(f"{exact} ({pct(exact, len(diffs))})", 16, "right"))

    best = min((r for r in results if r[1]), key=lambda r: statistics.mean(r[1]))
    if best[0] == 0:
        print("結論：平移 0 小時最吻合，兩份資料的時間定義一致。")
    else:
        print(f"[警告] 平移 {best[0]:+d} 小時最吻合，代表歷史檔與 API 的時間定義可能相差 {abs(best[0])} 小時，"
              "合併分析前請先確認並修正。")


# ------------------------------------------------------------
def write_csvs(rows_out, sid):
    os.makedirs(OUTPUT_DIR, exist_ok=True)
    names = {"yearly": "逐年風級時數", "monthly": "逐月風級時數", "episodes_8": "8級事件清單"}
    written = []
    for key, (header, rows) in rows_out.items():
        path = os.path.join(OUTPUT_DIR, f"{names[key]}_{sid}.csv")
        with open(path, "w", encoding="utf-8-sig", newline="") as f:
            w = csv.writer(f)
            w.writerow(header)
            w.writerows(rows)
        written.append(path)
    if written:
        print("\n已輸出 CSV：")
        for p in written:
            print(f"  {p}")


def main():
    parser = argparse.ArgumentParser(description="澎湖歷史逐時風速分析")
    parser.add_argument("--reference", default=REFERENCE_STATION, help="橋區參考站代碼（預設西嶼 C0W120）")
    args = parser.parse_args()

    if not os.path.exists(HISTORY_DB_PATH):
        print(f"找不到 {HISTORY_DB_PATH}，請先執行 python import_history.py")
        return
    conn = sqlite3.connect(HISTORY_DB_PATH)
    data = load_all(conn)
    conn.close()

    if not data:
        print("歷史資料表是空的，請先執行 python import_history.py")
        return
    ref = args.reference
    if ref not in data:
        print(f"歷史資料庫中沒有參考站 {ref}。已匯入的測站：{[station_label(s) for s in data]}")
        return

    rows_out = {}
    print_availability(data)
    print_level_frequency(data[ref], ref, rows_out)
    print_episodes(data[ref], ref, rows_out)
    print_station_comparison(data, ref)
    print_gust_analysis(data[ref], ref)
    print_alignment_check(data)
    write_csvs(rows_out, ref)


if __name__ == "__main__":
    main()
