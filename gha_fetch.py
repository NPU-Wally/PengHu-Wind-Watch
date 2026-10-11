# -*- coding: utf-8 -*-
"""
GitHub Actions 用的輕量抓取腳本。

背景：scheduler.py 必須在本機終端機視窗保持開啟才能運作，闔蓋、關機、
出國不帶電腦都會讓它停止，防休眠機制無法解決「人不在電腦前」這個根本限制。
這支腳本在 GitHub 的雲端伺服器上執行，跟使用者的電腦完全無關。

做法：
  重用 fetch_weather.py 已經寫好、測試過的 API 呼叫與解析邏輯，
  把抓到的資料「附加」到 gha_log/weather_log.csv，而不是直接寫 SQLite，
  因為 SQLite 二進位檔案不適合用 git 頻繁提交（diff 很大、容易衝突）。
  之後用 merge_gha_log.py 把這份 log 併入本機的 penghu_weather.db
  （靠 obs_time + station_id 的 UNIQUE INDEX 自動跳過重複，不會把資料弄亂）。

環境需求：
  環境變數 CWA_API_KEY（在 GitHub repo 的 Settings → Secrets 設定，
  絕對不要寫進程式碼或提交到 repo —— .gitignore 已經擋掉 api_key.txt 本身，
  但環境變數不經過檔案，是更適合 CI 環境的做法）。

使用方式（本機測試用，正式執行由 .github/workflows/collect.yml 排程）：
  python gha_fetch.py
"""
import csv
import os

import fetch_weather as fw

LOG_PATH = os.path.join(os.path.dirname(os.path.abspath(__file__)), "gha_log", "weather_log.csv")
FIELDS = ["station_id", "station_name", "town_name", "obs_time", "fetched_at",
          "wind_speed", "wind_direction", "peak_gust_speed", "air_temperature", "weather"]


def main():
    raw = fw.fetch_raw_data()  # 失敗就讓它丟例外，GitHub Actions 會記錄這次失敗，方便事後追查
    records = fw.extract_penghu_stations(raw)
    print(f"抓到 {len(records)} 筆")

    os.makedirs(os.path.dirname(LOG_PATH), exist_ok=True)
    write_header = not os.path.exists(LOG_PATH)
    with open(LOG_PATH, "a", encoding="utf-8", newline="") as f:
        w = csv.DictWriter(f, fieldnames=FIELDS)
        if write_header:
            w.writeheader()
        for r in records:
            w.writerow({k: r.get(k, "") for k in FIELDS})

    print(f"已附加到 {LOG_PATH}")


if __name__ == "__main__":
    main()
