# -*- coding: utf-8 -*-
"""
澎湖強風風險預警系統 - 排程執行器
功能：
  每隔固定間隔時間，自動呼叫 fetch_weather.py 裡的抓取邏輯一次，
  持續把資料寫入 SQLite 資料庫，不需要手動重複執行。

使用方式：
  python scheduler.py

  執行後這個視窗會持續佔用（顯示每次抓取的結果），
  要停止就按 Ctrl+C。

  如果想讓它背景執行、不佔用視窗，可以之後再串接
  Windows 工作排程器（工作排程器 App）指向這支腳本，
  或串 nssm 之類的工具做成背景服務 —— 這是進階選項，先不急。

關於電腦休眠：
  程式執行期間會要求 Windows「不要因閒置而自動休眠」。
  但下列情況仍會讓電腦休眠、排程中斷，需另外處理：
    - 筆電闔上螢幕（到「電源選項」把「闔上蓋子時」設為「不採取任何動作」）
    - 手動按下睡眠 / 關機
  排程中斷期間 CWA 的資料無法補抓（API 只回傳最新一筆）。
"""

import sqlite3
import sys
import time
import traceback
from datetime import datetime, timedelta, timezone

import fetch_weather  # 直接重用我們已經寫好、測試過的抓取邏輯

# ------------------------------------------------------------
# 設定區
# ------------------------------------------------------------
INTERVAL_MINUTES = 10  # 每隔幾分鐘抓一次
# 註：實測 obs_time 全部落在整點，代表 CWA 這個資料集「每小時」才產生一筆觀測。
# 但新資料是在整點過後約 10～35 分鐘才會出現在 API 上，時間並不固定。
# 若間隔設 30 分鐘，資料延遲最長可到 30 分鐘；改成 10 分鐘可把延遲壓在約 10 分鐘內。
# 重複抓到的資料會被 UNIQUE INDEX 自動略過，不會弄髒資料庫，只是多幾次 API 請求。

STALE_HOURS = 2  # 啟動時若資料庫最後一筆觀測距今超過這麼多小時，就提示有斷檔

TAIPEI_TZ = timezone(timedelta(hours=8))


# ------------------------------------------------------------
# 防止 Windows 因閒置而休眠
# ------------------------------------------------------------
_ES_CONTINUOUS = 0x80000000
_ES_SYSTEM_REQUIRED = 0x00000001


def prevent_sleep():
    """要求系統在本程式執行期間不要因閒置而休眠。成功回傳 True。非 Windows 或失敗時回傳 False。"""
    if sys.platform != "win32":
        return False
    try:
        import ctypes
        ctypes.windll.kernel32.SetThreadExecutionState(_ES_CONTINUOUS | _ES_SYSTEM_REQUIRED)
        return True
    except Exception:
        return False


def allow_sleep():
    """程式結束前，把系統的休眠設定還原"""
    if sys.platform != "win32":
        return
    try:
        import ctypes
        ctypes.windll.kernel32.SetThreadExecutionState(_ES_CONTINUOUS)
    except Exception:
        pass


# ------------------------------------------------------------
# 啟動時檢查資料庫最後一筆觀測
# ------------------------------------------------------------
def report_last_observation():
    """讓你一啟動就知道：上次資料收到什麼時候、中間有沒有斷檔"""
    try:
        conn = sqlite3.connect(fetch_weather.DB_PATH)
        row = conn.execute("SELECT MAX(obs_time) FROM penghu_weather").fetchone()
        conn.close()
    except sqlite3.Error:
        return  # 資料庫還不存在或還沒建表（第一次執行），不需提示

    if not row or not row[0]:
        return

    last = datetime.fromisoformat(row[0])
    if last.tzinfo is None:
        last = last.replace(tzinfo=TAIPEI_TZ)
    age_hours = (datetime.now(timezone.utc) - last).total_seconds() / 3600
    print(f"資料庫最後一筆觀測：{row[0]}（距今約 {age_hours:.1f} 小時）")
    if age_hours > STALE_HOURS:
        print(f"[警告] 距離上次收到資料已超過 {STALE_HOURS} 小時，中間的觀測時點已經無法補抓。"
              "可執行 stats.py 查看斷檔範圍。")


# ------------------------------------------------------------
def run_once():
    """執行一次完整的抓取流程，任何錯誤都攔截，不讓排程整個中斷"""
    try:
        fetch_weather.main()
    except Exception:
        print(f"[{datetime.now()}] 執行時發生未預期錯誤，略過這一輪，下次排程繼續：")
        traceback.print_exc()


def run_forever():
    print(f"排程啟動，每 {INTERVAL_MINUTES} 分鐘自動抓取一次。按 Ctrl+C 可停止。")
    if prevent_sleep():
        print("已啟用「執行期間防止系統閒置休眠」。")
    else:
        print("[提示] 未能啟用防休眠（非 Windows 或呼叫失敗），請自行確認電腦不會休眠。")
    report_last_observation()
    print()

    last_start = None
    try:
        while True:
            now = datetime.now()
            # 兩輪之間的實際間隔遠超過設定值，代表這段時間電腦大概睡著了
            if last_start and (now - last_start) > timedelta(minutes=INTERVAL_MINUTES * 2):
                gap_min = (now - last_start).total_seconds() / 60
                print(f"[警告] 距離上一輪已過 {gap_min:.0f} 分鐘（設定為 {INTERVAL_MINUTES} 分鐘），"
                      "這段期間可能曾休眠，資料可能有斷檔。")
            last_start = now

            run_once()

            next_run = datetime.now() + timedelta(minutes=INTERVAL_MINUTES)
            print(f"--- 休息 {INTERVAL_MINUTES} 分鐘，下次抓取時間約 {next_run:%H:%M:%S} ---\n")
            time.sleep(INTERVAL_MINUTES * 60)
    except KeyboardInterrupt:
        print("\n收到停止指令，排程已安全結束。")
    finally:
        allow_sleep()


if __name__ == "__main__":
    run_forever()
