# -*- coding: utf-8 -*-
"""
澎湖強風風險預警系統 - 舊資料風險等級回填工具（一次性使用）

背景：
  fetch_weather.py 從某個時間點開始，才加入風險等級計算與儲存邏輯。
  在那之前就已經存在資料庫裡的資料，risk_level / risk_label 會是 NULL。
  這些舊資料本身已經有 wind_speed，不需要重新呼叫API，
  直接用現有的 wind_speed 回頭算一次風險等級、UPDATE 回資料庫即可。

  這支腳本只需要執行一次；之後 fetch_weather.py 存進去的新資料
  本來就會直接帶有風險等級，不需要再回填。

使用方式：
  python3 backfill_risk.py
"""

import sqlite3

import risk_assessment

DB_PATH = "penghu_weather.db"


def main():
    conn = sqlite3.connect(DB_PATH)

    cur = conn.execute("""
        SELECT id, wind_speed FROM penghu_weather
        WHERE risk_level IS NULL
    """)
    rows = cur.fetchall()

    if not rows:
        print("沒有需要回填的資料，所有紀錄都已經有風險等級了。")
        conn.close()
        return

    print(f"找到 {len(rows)} 筆缺少風險等級的舊資料，開始回填...")

    updated = 0
    for row_id, wind_speed in rows:
        result = risk_assessment.assess_risk(wind_speed)
        conn.execute("""
            UPDATE penghu_weather
            SET risk_level = ?, risk_label = ?
            WHERE id = ?
        """, (result.level.value, result.label, row_id))
        updated += 1

    conn.commit()
    conn.close()
    print(f"回填完成，共更新 {updated} 筆。")


if __name__ == "__main__":
    main()
