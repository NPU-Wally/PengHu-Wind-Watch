# -*- coding: utf-8 -*-
"""
澎湖強風風險預警系統 - 資料庫統計查詢工具

功能：
  1. 資料涵蓋範圍、總筆數、實際觀測頻率、資料新鮮度
  2. 斷檔偵測：找出觀測時間出現空洞的區段
     （排程中斷 / 電腦休眠 / CWA 未更新），並估算缺了幾個時點
  3. 各測站風速、陣風缺值率與風速摘要
  4. 逐站風險等級分布（不再把全測站合併：實測各站風速差異可達 2 倍以上）
  5. 橋區參考站（預設西嶼）最近幾筆觀測
  6. 陣風缺值檢查：缺值是否與風速高低有關（判斷是否為「非隨機缺值」）

使用方式：
  python stats.py
"""

import os
import sqlite3
import statistics
import unicodedata
from collections import Counter, defaultdict
from datetime import datetime, timedelta, timezone

import risk_assessment
from risk_assessment import RiskLevel

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
DB_PATH = os.path.join(BASE_DIR, "penghu_weather.db")

REFERENCE_STATION = "西嶼"   # 離跨海大橋最近的測站，作為橋區風況的主要參考
STALE_HOURS = 2              # 最新觀測距今超過這麼多小時，就提示排程可能中斷
TAIPEI_TZ = timezone(timedelta(hours=8))


# ------------------------------------------------------------
# 小工具
# ------------------------------------------------------------
def display_width(text):
    """中文字佔 2 格寬，用來讓表格對齊"""
    return sum(2 if unicodedata.east_asian_width(ch) in ("W", "F") else 1 for ch in str(text))


def pad(text, width, align="left"):
    gap = max(width - display_width(text), 0)
    return (" " * gap + str(text)) if align == "right" else (str(text) + " " * gap)


def parse_time(text):
    dt = datetime.fromisoformat(text)
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=TAIPEI_TZ)
    return dt


def fmt(value, digits=1):
    return "-" if value is None else f"{value:.{digits}f}"


def print_section(title):
    print(f"\n=== {title} ===")


# ------------------------------------------------------------
# 資料讀取
# ------------------------------------------------------------
def fetch_all_records(conn):
    conn.row_factory = sqlite3.Row
    cur = conn.execute("""
        SELECT station_name, obs_time, wind_speed, wind_direction, peak_gust_speed
        FROM penghu_weather
        ORDER BY obs_time
    """)
    return cur.fetchall()


# ------------------------------------------------------------
# 1. 時間範圍、觀測頻率、資料新鮮度
# ------------------------------------------------------------
def distinct_times(rows):
    return sorted({parse_time(r["obs_time"]) for r in rows})


def typical_step(times):
    """觀測時間的典型間隔（取中位數）；資料太少時回傳 None"""
    if len(times) < 2:
        return None
    intervals = [(b - a).total_seconds() for a, b in zip(times, times[1:])]
    return timedelta(seconds=statistics.median(intervals))


def print_time_range(rows, times, step):
    print_section("資料涵蓋範圍")
    print(f"時間範圍：{rows[0]['obs_time']} ～ {rows[-1]['obs_time']}")
    print(f"總筆數：{len(rows)} 筆，觀測時點 {len(times)} 個，測站 "
          f"{len({r['station_name'] for r in rows})} 個")
    if step:
        print(f"實際觀測間隔（中位數）：{step.total_seconds() / 60:.0f} 分鐘")

    age_hours = (datetime.now(timezone.utc) - times[-1]).total_seconds() / 3600
    print(f"最新一筆觀測距今：約 {age_hours:.1f} 小時")
    if age_hours > STALE_HOURS:
        print(f"[警告] 已超過 {STALE_HOURS} 小時沒有新資料，請確認 scheduler.py 是否仍在執行"
              "（電腦休眠、視窗被關閉都會造成中斷）。")


# ------------------------------------------------------------
# 2. 斷檔偵測
# ------------------------------------------------------------
def print_gaps(times, step):
    print_section("斷檔偵測")
    if not step:
        print("資料點太少，無法判斷。")
        return

    expected = round((times[-1] - times[0]) / step) + 1
    coverage = len(times) / expected * 100
    print(f"預期時點 {expected} 個，實際 {len(times)} 個，涵蓋率 {coverage:.1f}%")

    gaps = []
    for a, b in zip(times, times[1:]):
        if (b - a) > step * 1.5:
            missing = round((b - a) / step) - 1
            gaps.append((a, b, missing))

    if not gaps:
        print("沒有發現斷檔。")
        return

    print(f"發現 {len(gaps)} 段斷檔：")
    for a, b, missing in gaps:
        hours = (b - a).total_seconds() / 3600
        print(f"  {a:%m/%d %H:%M} → {b:%m/%d %H:%M}（間隔 {hours:.1f} 小時，缺 {missing} 個時點）")
    print("提醒：CWA 這支 API 只回傳最新一筆，斷檔期間的資料無法從它補回，"
          "需改用 CODiS 歷史資料下載。")


# ------------------------------------------------------------
# 3. 各測站缺值率與風速摘要
# ------------------------------------------------------------
def group_by_station(rows):
    by_station = defaultdict(list)
    for r in rows:
        by_station[r["station_name"]].append(r)
    return by_station


def print_station_summary(by_station):
    print_section("各測站缺值率與風速摘要（* 為橋區參考站）")
    print(pad("測站", 12) + pad("筆數", 6, "right") + pad("風速缺值", 10, "right")
          + pad("陣風缺值", 10, "right") + pad("平均風速", 10, "right") + pad("最大風速", 10, "right"))
    print("-" * 58)
    for name in sorted(by_station):
        rs = by_station[name]
        total = len(rs)
        winds = [r["wind_speed"] for r in rs if r["wind_speed"] is not None]
        wind_missing = total - len(winds)
        gust_missing = sum(1 for r in rs if r["peak_gust_speed"] is None)
        label = f"{name}*" if name == REFERENCE_STATION else name
        print(pad(label, 12) + pad(total, 6, "right")
              + pad(f"{wind_missing / total * 100:.1f}%", 10, "right")
              + pad(f"{gust_missing / total * 100:.1f}%", 10, "right")
              + pad(fmt(statistics.mean(winds)) if winds else "-", 10, "right")
              + pad(fmt(max(winds)) if winds else "-", 10, "right"))
    print("（風速單位 m/s）")


# ------------------------------------------------------------
# 4. 逐站風險等級分布
# ------------------------------------------------------------
LEVEL_COLUMNS = [
    (RiskLevel.NORMAL, "正常"),
    (RiskLevel.STAGE1_STRICT, "7級"),
    (RiskLevel.STAGE1, "8級"),
    (RiskLevel.STAGE2, "10級"),
    (RiskLevel.UNKNOWN, "缺值"),
]


def print_risk_by_station(by_station):
    print_section("逐站風險等級分布（依平均風速換算，單位：筆）")
    print(pad("測站", 12) + "".join(pad(label, 8, "right") for _lv, label in LEVEL_COLUMNS))
    print("-" * (12 + 8 * len(LEVEL_COLUMNS)))
    for name in sorted(by_station):
        counter = Counter(
            risk_assessment.assess_risk(r["wind_speed"]).level for r in by_station[name]
        )
        label = f"{name}*" if name == REFERENCE_STATION else name
        print(pad(label, 12) + "".join(pad(counter.get(lv, 0), 8, "right") for lv, _ in LEVEL_COLUMNS))
    print("（7級=接近管制、8級=第一階段管制、10級=全橋封閉）")


# ------------------------------------------------------------
# 5. 橋區參考站
# ------------------------------------------------------------
def print_reference_station(by_station, latest_n=6):
    print_section(f"橋區參考站：{REFERENCE_STATION}")
    rs = by_station.get(REFERENCE_STATION)
    if not rs:
        print(f"資料庫中找不到「{REFERENCE_STATION}」測站。")
        return
    print(f"最近 {latest_n} 筆觀測：")
    for r in rs[-latest_n:]:
        risk = risk_assessment.assess_risk(r["wind_speed"])
        print(f"  {r['obs_time']}  風速 {fmt(r['wind_speed'])} m/s  風向 {fmt(r['wind_direction'], 0)}°  "
              f"陣風 {fmt(r['peak_gust_speed'])} m/s  {risk.label}")


# ------------------------------------------------------------
# 6. 陣風缺值檢查
# ------------------------------------------------------------
def print_gust_missing_check(by_station):
    print_section("陣風缺值檢查（缺值是否與風速高低有關）")
    print(pad("測站", 12) + pad("有陣風", 8, "right") + pad("最小陣風", 10, "right")
          + pad("有陣風時均風速", 16, "right") + pad("缺陣風時均風速", 16, "right"))
    print("-" * 62)
    for name in sorted(by_station):
        rs = [r for r in by_station[name] if r["wind_speed"] is not None]
        with_gust = [r for r in rs if r["peak_gust_speed"] is not None]
        without_gust = [r for r in rs if r["peak_gust_speed"] is None]
        if not rs:
            continue
        min_gust = min((r["peak_gust_speed"] for r in with_gust), default=None)
        mean_with = statistics.mean(r["wind_speed"] for r in with_gust) if with_gust else None
        mean_without = statistics.mean(r["wind_speed"] for r in without_gust) if without_gust else None
        print(pad(name, 12) + pad(len(with_gust), 8, "right") + pad(fmt(min_gust), 10, "right")
              + pad(fmt(mean_with), 16, "right") + pad(fmt(mean_without), 16, "right"))
    print("判讀：若各站「最小陣風」都落在相近的下限，且「缺陣風時均風速」普遍較低，")
    print("      代表陣風可能只在超過某個門檻時才有紀錄（非隨機缺值）。")
    print("      這會影響缺失值插補方法的選擇；資料量少時請勿過度解讀。")


# ------------------------------------------------------------
def main():
    if not os.path.exists(DB_PATH):
        print(f"找不到資料庫：{DB_PATH}")
        return

    conn = sqlite3.connect(DB_PATH)
    rows = fetch_all_records(conn)
    conn.close()

    if not rows:
        print("資料庫目前沒有任何紀錄。")
        return

    times = distinct_times(rows)
    step = typical_step(times)
    by_station = group_by_station(rows)

    print_time_range(rows, times, step)
    print_gaps(times, step)
    print_station_summary(by_station)
    print_risk_by_station(by_station)
    print_reference_station(by_station)
    print_gust_missing_check(by_station)


if __name__ == "__main__":
    main()
