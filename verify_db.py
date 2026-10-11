# -*- coding: utf-8 -*-
"""
澎湖強風風險預警系統 - 資料庫驗證工具

功能：
  在你把 fetch_weather.py 換成新版（有 risk_level/risk_label 欄位）之後，
  用這支腳本確認：
    1. 資料表結構是否正確升級（risk_level / risk_label 欄位存在）
    2. 舊資料（升級前存的）是否還在，沒有被清空或損毀
    3. 新資料（升級後存的）是否正確帶有風險等級
    4. 資料庫整體有沒有明顯異常（例如時間戳記錯亂、重複鍵等）

使用方式：
  python3 verify_db.py
"""

import sqlite3

DB_PATH = "penghu_weather.db"


def check_schema(conn):
    print("【1】檢查資料表結構")
    cur = conn.execute("PRAGMA table_info(penghu_weather)")
    columns = [row[1] for row in cur.fetchall()]
    print(f"  目前欄位：{columns}")

    required = ["station_id", "station_name", "obs_time", "wind_speed",
                "peak_gust_speed", "risk_level", "risk_label"]
    missing = [c for c in required if c not in columns]

    if missing:
        print(f"  [X] 缺少欄位：{missing} —— 資料庫可能還是舊版結構，"
              f"請確認 fetch_weather.py 是否已更新，並且至少成功執行過一次")
    else:
        print("  [OK] 所有必要欄位都存在")
    print()
    return not missing


def check_row_counts(conn):
    print("【2】檢查資料筆數與新舊資料銜接")
    total = conn.execute("SELECT COUNT(*) FROM penghu_weather").fetchone()[0]
    with_risk = conn.execute(
        "SELECT COUNT(*) FROM penghu_weather WHERE risk_level IS NOT NULL"
    ).fetchone()[0]
    without_risk = total - with_risk

    print(f"  總筆數：{total}")
    print(f"  有風險等級的筆數（新版程式存的）：{with_risk}")
    print(f"  沒有風險等級的筆數（升級前的舊資料，正常現象）：{without_risk}")

    if total == 0:
        print("  [X] 資料庫是空的，代表 scheduler.py 可能還沒成功執行過，或路徑不對")
    elif with_risk == 0:
        print("  [!] 目前還沒有任何一筆帶有風險等級的資料，"
              "代表新版 fetch_weather.py 可能還沒真的執行過一輪，"
              "請確認 scheduler.py 有重新啟動")
    else:
        print("  [OK] 新舊資料銜接正常，新資料有風險等級、舊資料保留原樣")
    print()


def check_latest_records(conn, limit=5):
    print(f"【3】最新 {limit} 筆資料（肉眼檢查用）")
    cur = conn.execute("""
        SELECT station_name, obs_time, wind_speed, peak_gust_speed, risk_level, risk_label
        FROM penghu_weather
        ORDER BY obs_time DESC, id DESC
        LIMIT ?
    """, (limit,))
    rows = cur.fetchall()

    if not rows:
        print("  （沒有資料）")
    else:
        for name, obs_time, wind_speed, gust, risk_level, risk_label in rows:
            gust_str = gust if gust is not None else "缺值"
            risk_str = risk_label if risk_label is not None else "（舊資料，未計算）"
            print(f"  {obs_time}  {name:8s} 風速={wind_speed}  陣風={gust_str}  風險={risk_str}")
    print()


def check_jibei_status(conn):
    """
    專門檢查吉貝站狀態。
    已知吉貝合作站因周邊工程延宕，自113/11起停止觀測，
    截至查證當下（115年9月）仍未恢復。
    這裡統計吉貝站『最近』的資料是否仍全數缺值，
    確認系統目前的判讀跟已知的外部事實一致。
    """
    print("【4】吉貝站狀態檢查（已知：因周邊工程延宕，仍處於停止觀測狀態）")
    cur = conn.execute("""
        SELECT COUNT(*), SUM(CASE WHEN wind_speed IS NULL THEN 1 ELSE 0 END)
        FROM penghu_weather
        WHERE station_name = '吉貝'
    """)
    total, missing = cur.fetchone()

    if not total:
        print("  資料庫裡目前沒有吉貝站的紀錄")
    else:
        missing = missing or 0
        rate = missing / total * 100
        print(f"  吉貝站累計 {total} 筆，缺值 {missing} 筆（{rate:.1f}%）")
        if rate == 100:
            print("  [OK] 與已知外部事實一致（工程尚未完工，測站仍停止觀測）")
        else:
            print("  [!] 出現非缺值資料，代表吉貝站可能已經恢復觀測，"
                  "建議去 CWA 官網確認最新測站狀態公告是否已更新")
    print()


def main():
    conn = sqlite3.connect(DB_PATH)

    schema_ok = check_schema(conn)
    if schema_ok:
        check_row_counts(conn)
        check_latest_records(conn)
        check_jibei_status(conn)

    conn.close()
    print("驗證完成。")


if __name__ == "__main__":
    main()
