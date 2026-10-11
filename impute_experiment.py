# -*- coding: utf-8 -*-
"""
缺失值插補方法 - 遮罩評估實驗（masked evaluation prototype）

專題新方向的第一個雛形實驗：證明「用什麼方法補缺值」這件事本身值得研究，
不是順手解決的小問題。

做法：
  用西嶼(C0W120) 2022～2025 風速資料，在「真實有觀測值」的時段人工挖空
  不同長度的區段（模擬真實斷檔的長度分布），比較幾種插補方法還原的誤差，
  以及對 7 級風門檻（13.9 m/s）判斷的影響（召回率：真實達標時，插補值
  是否也判斷達標）。

目前已知的簡化（下一步要修正，才能當正式方法）：
  1. climatology（月×時平均）與 regression（澎湖站迴歸）都是用「全部」資料
     配適，包含被挖空評估的那些時段，有資料洩漏，誤差可能被低估。正式做法
     要切訓練期／測試期（例如訓練 2016～2023、測試 2024～2025）。
  2. 挖空的起點是隨機選的，不是直接重現資料庫裡實際發生過的斷檔位置／季節。
  3. 門檻召回率只看平均風速是否「同時」達標，沒有看連續事件的誤差（例如
     事件開始/結束時間抓得準不準）。

使用方式：
  python impute_experiment.py
"""
import csv
import os
import random
import sqlite3
import statistics as st
from collections import defaultdict
from datetime import datetime, timedelta, timezone

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
HISTORY_DB_PATH = os.path.join(BASE_DIR, "penghu_history.db")
OUTPUT_DIR = os.path.join(BASE_DIR, "analysis_output")

random.seed(42)
HOUR = timedelta(hours=1)
TZ8 = timezone(timedelta(hours=8))
T_STAGE1_STRICT = 13.9  # 7 級風門檻（m/s）

EVAL_START, EVAL_END = datetime(2022, 1, 1, 0, tzinfo=TZ8), datetime(2025, 12, 31, 23, tzinfo=TZ8)
GAP_LENGTHS = [1, 2, 3, 4, 6, 8, 12, 18, 24, 36, 48, 72, 96, 120, 168]  # 小時，模擬真實斷檔長度分布
N_TRIALS = 400
BUCKETS = ["短 1-6h", "中 7-24h", "長 25-168h"]


def bucket(h):
    if h <= 6:
        return "短 1-6h"
    if h <= 24:
        return "中 7-24h"
    return "長 25-168h"


def load_history(conn):
    series = defaultdict(dict)
    for sid, t, ws in conn.execute(
            "SELECT station_id, obs_time, wind_speed FROM history_hourly "
            "WHERE station_id IN ('C0W120','467350')"):
        series[sid][datetime.fromisoformat(t)] = ws
    return series["C0W120"], series["467350"]


def build_climatology(X):
    buckets_ = defaultdict(list)
    for t, v in X.items():
        if v is not None:
            buckets_[(t.month, t.hour)].append(v)
    return {k: st.mean(v) for k, v in buckets_.items() if v}


def build_regression(X, P):
    pairs = [(P[t], X[t]) for t in X if t in P and P[t] is not None and X[t] is not None]
    return st.linear_regression([p for p, _ in pairs], [x for _, x in pairs])


def make_methods(X, P, clim, reg):
    def persistence(window_start, window_len):
        last = X.get(window_start - HOUR)
        return [last] * window_len

    def interpolate(window_start, window_len):
        a, b = X.get(window_start - HOUR), X.get(window_start + window_len * HOUR)
        if a is None or b is None:
            return [None] * window_len
        return [a + (b - a) * (i + 1) / (window_len + 1) for i in range(window_len)]

    def climatology(window_start, window_len):
        return [clim.get(((window_start + i * HOUR).month, (window_start + i * HOUR).hour))
                for i in range(window_len)]

    def regression(window_start, window_len):
        out = []
        for i in range(window_len):
            t = window_start + i * HOUR
            p = P.get(t)
            out.append(reg.intercept + reg.slope * p if p is not None
                       else clim.get((t.month, t.hour)))
        return out

    def hybrid(window_start, window_len):
        # 由下方實測結果調整：內插在 24 小時內都優於迴歸，超過才切換成迴歸
        if window_len <= 24:
            vals = interpolate(window_start, window_len)
            if all(v is not None for v in vals):
                return vals
        return regression(window_start, window_len)

    return {
        "persistence（延用前值）": persistence,
        "linear interpolation（線性內插）": interpolate,
        "climatology（月×時平均）": climatology,
        "regression（澎湖站迴歸）": regression,
        "hybrid（≤24h內插/>24h迴歸）": hybrid,
    }


def run_trials(X, timeline, methods):
    errors = defaultdict(lambda: defaultdict(list))
    hits = defaultdict(lambda: defaultdict(lambda: [0, 0]))
    used, tries = 0, 0
    while used < N_TRIALS and tries < N_TRIALS * 50:
        tries += 1
        gap_len = random.choice(GAP_LENGTHS)
        i0 = random.randrange(0, len(timeline) - gap_len - 2)
        window_start = timeline[i0 + 1]
        window_times = [window_start + k * HOUR for k in range(gap_len)]
        truths = [X.get(wt) for wt in window_times]
        if any(v is None for v in truths):
            continue
        used += 1
        b = bucket(gap_len)
        for name, fn in methods.items():
            preds = fn(window_start, gap_len)
            for truth, pred in zip(truths, preds):
                if pred is None:
                    continue
                errors[name][b].append(abs(pred - truth))
                if truth >= T_STAGE1_STRICT:
                    hits[name][b][0] += 1 if pred >= T_STAGE1_STRICT else 0
                    hits[name][b][1] += 1
    return errors, hits, used, tries


def print_table(title, methods, rows_fn):
    print(f"\n{title}")
    print(f"{'方法':<30}" + "".join(f"{b:>16}" for b in BUCKETS))
    print("-" * (30 + 16 * len(BUCKETS)))
    for name in methods:
        print(f"{name:<30}" + "".join(f"{rows_fn(name, b):>16}" for b in BUCKETS))


def write_csv(path, methods, errors, hits):
    with open(path, "w", encoding="utf-8-sig", newline="") as f:
        w = csv.writer(f)
        w.writerow(["方法", "長度分組", "樣本數", "平均絕對誤差(m/s)", "7級門檻召回率"])
        for name in methods:
            for b in BUCKETS:
                es = errors[name][b]
                num, den = hits[name][b]
                w.writerow([name, b, len(es),
                            f"{st.mean(es):.3f}" if es else "",
                            f"{num / den:.3f}" if den else ""])


def main():
    if not os.path.exists(HISTORY_DB_PATH):
        print(f"找不到 {HISTORY_DB_PATH}，請先執行 import_history.py")
        return
    conn = sqlite3.connect(HISTORY_DB_PATH)
    X, P = load_history(conn)
    conn.close()

    timeline = []
    t = EVAL_START
    while t <= EVAL_END:
        timeline.append(t)
        t += HOUR

    clim = build_climatology(X)
    reg = build_regression(X, P)
    methods = make_methods(X, P, clim, reg)
    errors, hits, used, tries = run_trials(X, timeline, methods)

    print(f"有效試驗 {used} 次（嘗試 {tries} 次），評估範圍 {EVAL_START.date()} ~ {EVAL_END.date()}")
    print("（climatology／regression 以全部資料配適，含評估點，屬雛形簡化，正式版需切訓練/測試期）")

    print_table("平均絕對誤差 MAE（m/s，越低越好）", methods,
                lambda name, b: (f"{st.mean(errors[name][b]):.2f} (n={len(errors[name][b])})"
                                  if errors[name][b] else "-"))
    print_table("7級門檻(13.9 m/s)召回率（真實達標時，插補值是否也達標）", methods,
                lambda name, b: (f"{hits[name][b][0] / hits[name][b][1]:.0%} (n={hits[name][b][1]})"
                                  if hits[name][b][1] else "-"))

    os.makedirs(OUTPUT_DIR, exist_ok=True)
    out_path = os.path.join(OUTPUT_DIR, "插補方法比較_西嶼.csv")
    write_csv(out_path, methods, errors, hits)
    print(f"\n已輸出：{out_path}")


if __name__ == "__main__":
    main()
