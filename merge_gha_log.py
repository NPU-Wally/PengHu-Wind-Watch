# -*- coding: utf-8 -*-
"""
把 GitHub Actions 收集到的 gha_log/weather_log.csv 併入本機的 penghu_weather.db。

使用方式：
  1. 本機已經 clone 這個 repo，log 檔就在旁邊：
       python merge_gha_log.py
  2. 不想先 clone，直接從 GitHub 上的公開 raw 檔案網址抓最新的 log 來併入
     （這也是之後可以請 Claude 在對話中直接執行、不需要連你電腦的方式）：
       python merge_gha_log.py --url https://raw.githubusercontent.com/<帳號>/<repo>/main/gha_log/weather_log.csv

重複的 (station_id, obs_time) 會被資料庫的 UNIQUE INDEX 自動跳過，
所以重複執行、或本機排程跟 GitHub Actions 同時在跑，都不會把資料弄亂或算兩次。
"""
import argparse
import csv
import io
import os
import sqlite3

import requests

import fetch_weather as fw

LOCAL_LOG_PATH = os.path.join(os.path.dirname(os.path.abspath(__file__)), "gha_log", "weather_log.csv")


def load_rows(path=None, url=None):
    if url:
        resp = requests.get(url, timeout=30)
        resp.raise_for_status()
        text = resp.content.decode("utf-8-sig")
    else:
        with open(path, encoding="utf-8-sig") as f:
            text = f.read()

    records = []
    for row in csv.DictReader(io.StringIO(text)):
        records.append({
            "station_id": row["station_id"],
            "station_name": row["station_name"],
            "town_name": row["town_name"],
            "obs_time": row["obs_time"],
            "fetched_at": row["fetched_at"],
            "wind_speed": fw.safe_float(row["wind_speed"]),
            "wind_direction": fw.safe_float(row["wind_direction"]),
            "peak_gust_speed": fw.safe_float(row["peak_gust_speed"]),
            "air_temperature": fw.safe_float(row["air_temperature"]),
            "weather": row["weather"] or None,
        })
    return records


def main():
    parser = argparse.ArgumentParser(description="把 GitHub Actions 收集的 log 併入本機資料庫")
    parser.add_argument("--url", help="repo 中 gha_log/weather_log.csv 的 raw 網址（不想先 clone 時用）")
    parser.add_argument("--path", default=LOCAL_LOG_PATH,
                         help="本機 log 檔路徑（預設同資料夾下的 gha_log/weather_log.csv）")
    args = parser.parse_args()

    records = load_rows(path=None if args.url else args.path, url=args.url)
    print(f"讀到 {len(records)} 筆")

    conn = sqlite3.connect(fw.DB_PATH)
    fw.init_db(conn)
    inserted = fw.save_records(conn, records)
    conn.close()
    print(f"新寫入 {inserted} 筆（重複的已自動略過）")


if __name__ == "__main__":
    main()
