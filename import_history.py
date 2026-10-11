# -*- coding: utf-8 -*-
"""
澎湖強風風險預警系統 - 歷史逐時資料匯入工具

資料來源：
  中央氣象署 CODiS（氣候觀測資料查詢服務）的逐時觀測資料，
  透過 Raingel/historical_weather（中興大學維護的 CODiS 整理版，GitHub）取得。
    https://github.com/Raingel/historical_weather
  每個測站每年一個 CSV，網址格式：
    https://raw.githubusercontent.com/Raingel/historical_weather/main/data/{測站代碼}/{測站代碼}_{年}.csv

  【論文引用提醒】資料原始來源是 CODiS，此 repo 只是取得管道，且 README 未標示授權條款。
  引用時請註明「資料來源：中央氣象署 CODiS」，並用 CODiS 官方網站抽樣對照幾天確認一致。

做了什麼：
  1. 逐站逐年下載 CSV（存在 history_raw/ 資料夾，重跑不會重複下載；當年度檔案每次都會重抓，
     因為該 repo 每天更新）
  2. 清理缺值代碼（-9999.7、-99.7、-9997、-99.8 等負值一律視為缺值；風速不可能為負）
  3. 時間統一成與即時資料庫相同的格式（2024-01-01T01:00:00+08:00）
  4. 寫入獨立的資料庫 penghu_history.db（不動即時資料庫 penghu_weather.db，避免與排程互相干擾）

使用方式：
  python import_history.py                       # 預設：西嶼 + 澎湖，2016 年至今
  python import_history.py --all                 # 澎湖縣全部 9 站
  python import_history.py --stations C0W120 467350 --start-year 2010
  python import_history.py --offline             # 不連網，只用 history_raw/ 裡已有的檔案
  python import_history.py --force               # 忽略快取，全部重新下載

注意：
  西嶼站（C0W120）的資料從 2016-06-30 才開始，之前的年份沒有檔案，屬正常。
"""

import argparse
import csv
import hashlib
import io
import os
import sqlite3
import time
from datetime import datetime

import requests

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
HISTORY_DB_PATH = os.path.join(BASE_DIR, "penghu_history.db")
RAW_DIR = os.path.join(BASE_DIR, "history_raw")

RAW_URL = ("https://raw.githubusercontent.com/Raingel/historical_weather/"
           "main/data/{sid}/{sid}_{year}.csv")

# 澎湖縣測站（代碼與即時資料庫 penghu_weather.db 的 station_id 相同）
STATIONS = {
    "C0W120": "西嶼",      # 離跨海大橋最近，橋區參考站
    "467350": "澎湖",      # 馬公
    "C2W230": "畜試所澎湖",
    "C0W180": "七美",
    "C0W190": "望安",
    "C0W200": "湖西",
    "C0W130": "花嶼",
    "467300": "東吉島",
    "A0W080": "吉貝",
}
DEFAULT_STATIONS = ["C0W120", "467350"]
DEFAULT_START_YEAR = 2016

TZ_SUFFIX = "+08:00"   # CODiS 資料為臺灣時間


# ------------------------------------------------------------
# 欄位清理
# ------------------------------------------------------------
def to_float(text):
    if text is None:
        return None
    text = text.strip()
    if text == "":
        return None
    try:
        value = float(text)
    except ValueError:
        return None
    if value != value:  # NaN
        return None
    return value


def clean_speed(text):
    """風速（m/s）：空白、非數字、負值（缺值代碼如 -9999.7、-99.7）一律視為缺值"""
    value = to_float(text)
    return None if value is None or value < 0 else value


def clean_direction(text):
    """風向（度）：只接受 0～360"""
    value = to_float(text)
    return None if value is None or value < 0 or value > 360 else value


def clean_temperature(text):
    """氣溫（°C）：-90 以下視為缺值代碼"""
    value = to_float(text)
    return None if value is None or value <= -90 else value


def normalize_time(text):
    """'2024-01-01 01:00:00' -> '2024-01-01T01:00:00+08:00'；無法解析回傳 None"""
    text = (text or "").strip()
    if not text:
        return None
    try:
        dt = datetime.fromisoformat(text.replace(" ", "T"))
    except ValueError:
        return None
    return dt.strftime("%Y-%m-%dT%H:%M:%S") + TZ_SUFFIX


# ------------------------------------------------------------
# CSV 解析
# ------------------------------------------------------------
REQUIRED_COLUMNS = ["WS"]
OPTIONAL_COLUMNS = ["WD", "WSGust", "WDGust", "Tx"]


def cell(row, index):
    if index is None or index >= len(row):
        return None
    return row[index]


def parse_csv_text(text):
    """
    解析一個「測站-年」的逐時 CSV。
    回傳 (records, stats)：
      records: {obs_time: (wind_speed, wind_direction, peak_gust_speed, peak_gust_direction, air_temperature)}
      stats:   各種計數，供匯入後檢查
    第一欄（時間）的欄位名稱是空字串，所以直接以第 0 欄當作時間。
    """
    reader = csv.reader(io.StringIO(text))
    try:
        header = next(reader)
    except StopIteration:
        raise ValueError("檔案是空的")

    index = {name.strip(): i for i, name in enumerate(header) if name.strip()}
    missing = [c for c in REQUIRED_COLUMNS if c not in index]
    if missing:
        raise ValueError(f"缺少必要欄位：{missing}；實際欄位：{header[:12]}...")

    records = {}
    stats = {"rows": 0, "bad_time": 0, "duplicate_time": 0, "wind_missing": 0, "gust_missing": 0}

    for row in reader:
        if not row or not row[0].strip():
            continue
        obs_time = normalize_time(row[0])
        if obs_time is None:
            stats["bad_time"] += 1
            continue

        wind = clean_speed(cell(row, index.get("WS")))
        wind_dir = clean_direction(cell(row, index.get("WD")))
        gust = clean_speed(cell(row, index.get("WSGust")))
        gust_dir = clean_direction(cell(row, index.get("WDGust")))
        temp = clean_temperature(cell(row, index.get("Tx")))

        if obs_time in records:
            stats["duplicate_time"] += 1   # 同一時間出現多次，保留最後一筆
        records[obs_time] = (wind, wind_dir, gust, gust_dir, temp)

    stats["rows"] = len(records)
    stats["wind_missing"] = sum(1 for r in records.values() if r[0] is None)
    stats["gust_missing"] = sum(1 for r in records.values() if r[2] is None)
    return records, stats


# ------------------------------------------------------------
# 下載與快取
# ------------------------------------------------------------
def download_csv(session, station_id, year, retries=3):
    """下載一個測站-年的 CSV。檔案不存在（404）回傳 None；其他錯誤重試後拋出。"""
    url = RAW_URL.format(sid=station_id, year=year)
    for attempt in range(1, retries + 1):
        try:
            resp = session.get(url, timeout=60)
            if resp.status_code == 404:
                return None
            resp.raise_for_status()
            return resp.content
        except requests.RequestException:
            if attempt == retries:
                raise
            time.sleep(2 * attempt)
    return None


def cache_path(station_id, year):
    return os.path.join(RAW_DIR, station_id, f"{station_id}_{year}.csv")


def get_csv_bytes(session, station_id, year, current_year, force, offline):
    """回傳 (內容 bytes 或 None, 來源說明)。當年度檔案每次重抓，過去年度用快取。"""
    path = cache_path(station_id, year)
    use_cache = os.path.exists(path) and not force and year < current_year
    if offline or use_cache:
        if os.path.exists(path):
            with open(path, "rb") as f:
                return f.read(), "cache"
        return None, "offline-missing"

    data = download_csv(session, station_id, year)
    if data is None:
        return None, "not-found"
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "wb") as f:
        f.write(data)
    return data, "download"


# ------------------------------------------------------------
# 資料庫
# ------------------------------------------------------------
def init_history_db(conn):
    conn.execute("""
        CREATE TABLE IF NOT EXISTS history_hourly (
            station_id TEXT NOT NULL,
            obs_time TEXT NOT NULL,
            wind_speed REAL,
            wind_direction REAL,
            peak_gust_speed REAL,
            peak_gust_direction REAL,
            air_temperature REAL,
            PRIMARY KEY (station_id, obs_time)
        )
    """)
    # 記錄每個檔案的來源與內容雜湊，方便論文說明資料版本、重現分析結果
    conn.execute("""
        CREATE TABLE IF NOT EXISTS history_files (
            station_id TEXT NOT NULL,
            year INTEGER NOT NULL,
            url TEXT,
            sha256 TEXT,
            source TEXT,
            processed_at TEXT,
            n_rows INTEGER,
            n_wind_missing INTEGER,
            n_gust_missing INTEGER,
            PRIMARY KEY (station_id, year)
        )
    """)
    conn.commit()


def save_year(conn, station_id, year, records, stats, data, source):
    conn.executemany(
        """INSERT OR REPLACE INTO history_hourly
           (station_id, obs_time, wind_speed, wind_direction, peak_gust_speed,
            peak_gust_direction, air_temperature)
           VALUES (?, ?, ?, ?, ?, ?, ?)""",
        [(station_id, t, *values) for t, values in records.items()],
    )
    conn.execute(
        """INSERT OR REPLACE INTO history_files
           (station_id, year, url, sha256, source, processed_at,
            n_rows, n_wind_missing, n_gust_missing)
           VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)""",
        (station_id, year, RAW_URL.format(sid=station_id, year=year),
         hashlib.sha256(data).hexdigest(), source,
         datetime.now().isoformat(timespec="seconds"),
         stats["rows"], stats["wind_missing"], stats["gust_missing"]),
    )
    conn.commit()


# ------------------------------------------------------------
def parse_args():
    p = argparse.ArgumentParser(description="匯入 CODiS 歷史逐時風速資料")
    p.add_argument("--stations", nargs="+", default=None, help="測站代碼（預設：西嶼與澎湖）")
    p.add_argument("--all", action="store_true", help="匯入澎湖縣全部 9 站")
    p.add_argument("--start-year", type=int, default=DEFAULT_START_YEAR)
    p.add_argument("--end-year", type=int, default=datetime.now().year)
    p.add_argument("--force", action="store_true", help="忽略快取，全部重新下載")
    p.add_argument("--offline", action="store_true", help="不連網，只用 history_raw/ 內已有的檔案")
    return p.parse_args()


def main():
    args = parse_args()
    if args.all:
        station_ids = list(STATIONS)
    else:
        station_ids = args.stations or DEFAULT_STATIONS

    for sid in station_ids:
        if sid not in STATIONS:
            print(f"[提示] 測站代碼 {sid} 不在內建澎湖測站清單中，仍會嘗試下載。")

    current_year = datetime.now().year
    session = requests.Session()
    conn = sqlite3.connect(HISTORY_DB_PATH)
    init_history_db(conn)

    print(f"匯入 {len(station_ids)} 站，{args.start_year}～{args.end_year} 年 -> {HISTORY_DB_PATH}\n")
    print(f"{'測站':<10}{'年':<6}{'來源':<10}{'筆數':>7}{'風速缺值':>9}{'陣風缺值':>9}{'重複時間':>9}{'壞列':>6}")
    print("-" * 68)

    totals = {"files": 0, "rows": 0, "not_found": 0, "errors": 0}
    for sid in station_ids:
        name = STATIONS.get(sid, sid)
        for year in range(args.start_year, args.end_year + 1):
            try:
                data, source = get_csv_bytes(session, sid, year, current_year,
                                             args.force, args.offline)
            except requests.RequestException as e:
                print(f"{name:<10}{year:<6}{'下載失敗':<10}  {e}")
                totals["errors"] += 1
                continue

            if data is None:
                totals["not_found"] += 1
                note = "無此檔案" if source == "not-found" else "快取中沒有（離線模式）"
                print(f"{name:<10}{year:<6}{note}")
                continue

            try:
                records, stats = parse_csv_text(data.decode("utf-8-sig", errors="replace"))
            except ValueError as e:
                print(f"{name:<10}{year:<6}{'解析失敗':<10}  {e}")
                totals["errors"] += 1
                continue

            save_year(conn, sid, year, records, stats, data, source)
            totals["files"] += 1
            totals["rows"] += stats["rows"]
            print(f"{name:<10}{year:<6}{source:<10}{stats['rows']:>7}{stats['wind_missing']:>9}"
                  f"{stats['gust_missing']:>9}{stats['duplicate_time']:>9}{stats['bad_time']:>6}")

    conn.close()
    print(f"\n完成：成功 {totals['files']} 個檔案、共 {totals['rows']} 筆；"
          f"無此檔案 {totals['not_found']}；失敗 {totals['errors']}。")
    if totals["errors"]:
        print("有檔案失敗，請檢查網路後重新執行（已完成的年份會直接用快取，不會重抓）。")
    print("下一步：python analyze_history.py")


if __name__ == "__main__":
    main()
