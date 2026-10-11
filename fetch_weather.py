# -*- coding: utf-8 -*-
"""
澎湖強風風險預警系統 - 資料抓取模組
功能：
  1. 呼叫 CWA 開放資料平台 O-A0001-001（現在天氣觀測報告）API
  2. 篩選出澎湖縣所轄測站
  3. 過濾掉 CWA 的缺值代碼（-99, -9991 等）
  4. 寫入 SQLite 資料庫，並保留歷史紀錄（每次呼叫都新增一筆，不覆蓋）

使用方式：
  1. 提供你的 CWA Authorization 授權碼（二擇一，環境變數優先）：
       - 環境變數 CWA_API_KEY
       - 與本檔同資料夾的 api_key.txt（內容只放授權碼一行，此檔已列入 .gitignore）
  2. 直接執行：python fetch_weather.py
  3. 之後可以用 scheduler.py 排程（CWA 這個資料集每小時才更新一筆）
"""

import os
import requests
import sqlite3
import json
import ssl
from datetime import datetime
from requests.adapters import HTTPAdapter

import risk_assessment


class RelaxedStrictSSLAdapter(HTTPAdapter):
    """
    部分台灣政府網站（含 opendata.cwa.gov.tw）的憑證缺少完整的
    Subject/Authority Key Identifier 擴充欄位。
    Python 3.13 起 SSL 模組預設會強制做更嚴格的憑證鏈檢查（VERIFY_X509_STRICT），
    導致這類原本合法、瀏覽器打得開的網站被誤判為驗證失敗。

    這裡只關閉「嚴格模式」這一項過嚴的檢查，其餘憑證驗證
    （憑證是否過期、是否為信任CA簽發、主機名稱是否相符等）完全照常執行，
    比直接 verify=False（整個關閉驗證）安全很多。
    """
    def init_poolmanager(self, *args, **kwargs):
        ctx = ssl.create_default_context()
        if hasattr(ssl, "VERIFY_X509_STRICT"):
            ctx.verify_flags &= ~ssl.VERIFY_X509_STRICT
        kwargs["ssl_context"] = ctx
        return super().init_poolmanager(*args, **kwargs)


def build_session():
    session = requests.Session()
    session.mount("https://", RelaxedStrictSSLAdapter())
    return session

# ------------------------------------------------------------
# 設定區
# ------------------------------------------------------------
BASE_DIR = os.path.dirname(os.path.abspath(__file__))

DATASET_ID = "O-A0001-001"
API_URL = f"https://opendata.cwa.gov.tw/api/v1/rest/datastore/{DATASET_ID}"

# 資料庫固定放在程式所在資料夾，不受「從哪個資料夾執行」影響
# （例如改用 Windows 工作排程器時，工作目錄常常不是專案資料夾，
#   相對路徑會悄悄地在別處建立一個全新的空資料庫）
DB_PATH = os.path.join(BASE_DIR, "penghu_weather.db")
TARGET_COUNTY = "澎湖縣"

API_KEY_ENV_VAR = "CWA_API_KEY"
API_KEY_FILE = os.path.join(BASE_DIR, "api_key.txt")


def load_api_key():
    """
    取得 CWA 授權碼：優先讀環境變數，其次讀 api_key.txt。
    授權碼不再直接寫在程式碼裡，避免上傳 GitHub 或分享程式時外洩。
    """
    key = os.environ.get(API_KEY_ENV_VAR, "").strip()
    if key:
        return key
    if os.path.exists(API_KEY_FILE):
        with open(API_KEY_FILE, encoding="utf-8") as f:
            key = f.read().strip()
        if key:
            return key
    raise RuntimeError(
        f"找不到 CWA 授權碼。請設定環境變數 {API_KEY_ENV_VAR}，"
        f"或在 {API_KEY_FILE} 放一行授權碼。"
    )

# CWA 常見的缺值代碼，凡是遇到這些值一律視為「無資料」
MISSING_VALUES = {"-99", "-9991", "-9997", "-9998", "-9999", "X", ""}


def is_missing(value):
    """判斷某個觀測值是否為缺值代碼"""
    if value is None:
        return True
    return str(value).strip() in MISSING_VALUES


def safe_float(value):
    """把字串轉成 float，缺值或無法轉換時回傳 None"""
    if is_missing(value):
        return None
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def fetch_raw_data():
    """呼叫 CWA API，回傳解析後的 JSON"""
    params = {"Authorization": load_api_key()}
    session = build_session()
    resp = session.get(API_URL, params=params, timeout=15)
    resp.raise_for_status()
    return resp.json()


def get_station_list(raw_json):
    """
    找出 Station 陣列。
    CWA 網站「手動下載」的檔案格式，跟直接呼叫 REST API 拿到的格式，
    最外層包裝不太一樣，這裡把兩種已知格式都納入判斷：
      - 手動下載檔案：raw_json["cwaopendata"]["dataset"]["Station"]
      - 直接呼叫 API：  raw_json["records"]["Station"]
    """
    if "cwaopendata" in raw_json:
        return raw_json["cwaopendata"]["dataset"]["Station"]
    if "records" in raw_json:
        return raw_json["records"]["Station"]
    raise KeyError(
        "在回傳的 JSON 裡找不到 Station 陣列，"
        "請檢查 API 回傳內容的最外層 key 是否又換了一種格式：\n"
        f"目前拿到的最外層 key 有：{list(raw_json.keys())}"
    )


def extract_penghu_stations(raw_json):
    """
    從完整的 JSON 中篩出澎湖縣的測站，並整理成乾淨的 dict list。
    """
    stations = get_station_list(raw_json)
    results = []

    for s in stations:
        geo = s.get("GeoInfo", {})
        if geo.get("CountyName") != TARGET_COUNTY:
            continue

        we = s.get("WeatherElement", {})
        gust_info = we.get("GustInfo", {})

        record = {
            "station_id": s.get("StationId"),
            "station_name": s.get("StationName"),
            "town_name": geo.get("TownName"),
            "obs_time": s.get("ObsTime", {}).get("DateTime"),
            "fetched_at": datetime.now().isoformat(timespec="seconds"),
            "wind_speed": safe_float(we.get("WindSpeed")),
            "wind_direction": safe_float(we.get("WindDirection")),
            "peak_gust_speed": safe_float(gust_info.get("PeakGustSpeed")),
            "air_temperature": safe_float(we.get("AirTemperature")),
            "weather": None if is_missing(we.get("Weather")) else we.get("Weather"),
        }
        results.append(record)

    return results


def init_db(conn):
    conn.execute("""
        CREATE TABLE IF NOT EXISTS penghu_weather (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            station_id TEXT,
            station_name TEXT,
            town_name TEXT,
            obs_time TEXT,
            fetched_at TEXT,
            wind_speed REAL,
            wind_direction REAL,
            peak_gust_speed REAL,
            air_temperature REAL,
            weather TEXT,
            risk_level TEXT,
            risk_label TEXT
        )
    """)
    # 避免同一測站同一觀測時間重複寫入（例如排程重跑時 CWA 還沒更新資料）
    conn.execute("""
        CREATE UNIQUE INDEX IF NOT EXISTS idx_station_obstime
        ON penghu_weather (station_id, obs_time)
    """)

    # 相容舊資料庫：如果是先前版本建立的 db（沒有 risk_level / risk_label 欄位），
    # 用 ALTER TABLE 補上去，既有資料不會被清空，只是這兩欄新資料會是 NULL。
    # sqlite 對「欄位已存在」會丟 OperationalError，這裡直接忽略即可。
    for column_def in ("risk_level TEXT", "risk_label TEXT"):
        try:
            conn.execute(f"ALTER TABLE penghu_weather ADD COLUMN {column_def}")
        except sqlite3.OperationalError:
            pass  # 欄位已存在，代表是新版本建立的 db，不用理會

    conn.commit()


def save_records(conn, records):
    inserted = 0
    for r in records:
        # 依公路局風力管制標準（平均風速）換算風險等級，隨資料一起存入，
        # 之後查詢/畫圖不用每次重新計算。
        risk = risk_assessment.assess_risk(r["wind_speed"])
        r = {**r, "risk_level": risk.level.value, "risk_label": risk.label}

        try:
            conn.execute("""
                INSERT INTO penghu_weather
                (station_id, station_name, town_name, obs_time, fetched_at,
                 wind_speed, wind_direction, peak_gust_speed, air_temperature, weather,
                 risk_level, risk_label)
                VALUES (:station_id, :station_name, :town_name, :obs_time, :fetched_at,
                        :wind_speed, :wind_direction, :peak_gust_speed, :air_temperature, :weather,
                        :risk_level, :risk_label)
            """, r)
            inserted += 1
        except sqlite3.IntegrityError:
            # 同一測站同一觀測時間已經存過了，跳過即可（代表 CWA 資料尚未更新）
            pass
    conn.commit()
    return inserted


def main():
    print(f"[{datetime.now()}] 開始抓取 {DATASET_ID} ...")

    try:
        raw = fetch_raw_data()
    except requests.exceptions.RequestException as e:
        print(f"[錯誤] API 呼叫失敗：{e}")
        return

    records = extract_penghu_stations(raw)
    print(f"篩出澎湖縣測站：{len(records)} 筆")

    conn = sqlite3.connect(DB_PATH)
    init_db(conn)
    inserted = save_records(conn, records)
    conn.close()

    print(f"新寫入 {inserted} 筆（重複的已自動略過）")

    # 印出這次抓到的資料摘要，方便你肉眼檢查
    for r in records:
        gust = r["peak_gust_speed"]
        gust_str = f"{gust} m/s" if gust is not None else "缺值"
        risk = risk_assessment.assess_risk(r["wind_speed"])
        print(f"  {r['station_name']:6s} 風速={r['wind_speed']} m/s  陣風={gust_str}  風險={risk.label}")


if __name__ == "__main__":
    main()
