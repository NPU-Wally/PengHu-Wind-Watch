# -*- coding: utf-8 -*-
"""
澎湖跨海大橋強風風險判斷模組

判斷依據：公路局澎湖跨海大橋風力管制標準（平均風速為主要判斷變數）
  - 第一階段管制（8級風，約 17.2 m/s 以上）：
        封閉機車專用道、人行道、自行車道；快車道降速至 40 或 30 km/h
  - 第二階段管制（10級風，約 24.5 m/s 以上）：
        全橋預警性封閉，禁止所有車輛通行
  - 公路局近期研擬將機車/人行/自行車道門檻加嚴至 7 級風（約 13.9 m/s 以上），
        這裡先做成可調整參數，方便之後依最新公告調整，不用改程式邏輯本身

注意：
  官方標準用的是「平均風速」，不是陣風（最大瞬間風速）。
  這代表我們先前遇到的陣風大量缺值問題，不影響核心風險判斷邏輯，
  陣風只作為輔助參考，不是必要欄位。
"""

from dataclasses import dataclass
from enum import Enum


class RiskLevel(Enum):
    NORMAL = "normal"                # 正常通行
    STAGE1_STRICT = "stage1_strict"  # 加嚴門檻（7級風，研擬中）
    STAGE1 = "stage1"                # 第一階段管制（8級風）
    STAGE2 = "stage2"                # 第二階段全橋封閉（10級風）
    UNKNOWN = "unknown"              # 資料缺值，無法判斷


# ------------------------------------------------------------
# 風速門檻設定（單位：m/s）
# 數值依蒲福風級與平均風速對照表換算，之後若公路局標準更新，
# 只需要調整這裡的數字，不用改下面的判斷邏輯。
# ------------------------------------------------------------
THRESHOLDS = {
    "stage1_strict_min": 13.9,  # 7級風下限（機車/人行/自行車道加嚴門檻，研擬中）
    "stage1_min": 17.2,          # 8級風下限（第一階段管制：封閉機車道等）
    "stage2_min": 24.5,          # 10級風下限（第二階段：全橋封閉）
}


@dataclass
class RiskAssessment:
    level: RiskLevel
    wind_speed: float | None
    label: str          # 給使用者看的中文說明
    advice: str          # 給機車族的具體建議
    restricted: bool     # 機車是否受管制影響


def assess_risk(wind_speed: float | None) -> RiskAssessment:
    """
    輸入平均風速（m/s），回傳風險評估結果。
    wind_speed 為 None 時代表資料缺值，回傳 UNKNOWN，
    前端應提示使用者「資料暫時無法取得」，而不是顯示錯誤的正常/安全狀態。
    """
    if wind_speed is None:
        return RiskAssessment(
            level=RiskLevel.UNKNOWN,
            wind_speed=None,
            label="資料暫時無法取得",
            advice="目前測站資料缺值，請改參考中央氣象署官方預報或跨海大橋現場標示。",
            restricted=False,
        )

    if wind_speed >= THRESHOLDS["stage2_min"]:
        return RiskAssessment(
            level=RiskLevel.STAGE2,
            wind_speed=wind_speed,
            label="全橋封閉（第二階段管制）",
            advice="跨海大橋已達全橋封閉風速門檻，禁止所有車輛通行，請勿前往橋區。",
            restricted=True,
        )

    if wind_speed >= THRESHOLDS["stage1_min"]:
        return RiskAssessment(
            level=RiskLevel.STAGE1,
            wind_speed=wind_speed,
            label="第一階段管制（機車/人行/自行車道封閉）",
            advice="機車、人行道、自行車道已封閉，快車道降速通行，機車請改道或等待風勢減弱。",
            restricted=True,
        )

    if wind_speed >= THRESHOLDS["stage1_strict_min"]:
        return RiskAssessment(
            level=RiskLevel.STAGE1_STRICT,
            wind_speed=wind_speed,
            label="接近管制門檻（7級風，加嚴標準研擬中）",
            advice="風速已接近第一階段管制門檻，機車通行需特別注意側風，建議降低車速。",
            restricted=False,
        )

    return RiskAssessment(
        level=RiskLevel.NORMAL,
        wind_speed=wind_speed,
        label="正常通行",
        advice="目前風速在安全範圍內，正常通行。",
        restricted=False,
    )


if __name__ == "__main__":
    # 快速自我測試：用幾個代表性風速值檢查判斷邏輯是否正確
    test_cases = [3.3, 11.0, 13.9, 15.0, 17.2, 20.0, 24.5, 30.0, None]
    for ws in test_cases:
        result = assess_risk(ws)
        ws_display = f"{ws} m/s" if ws is not None else "缺值"
        print(f"風速 {ws_display:>10} -> {result.level.value:15s} | {result.label}")
