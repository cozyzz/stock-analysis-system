# -*- coding: utf-8 -*-
"""严格趋势（移植自 Big-A-chovy a_share_daily_screen.py:1417 strict_trend）

均线多排 + MA20 上升 + 距 60 日高点≤10% + 涨幅 3-6.5% + 换手 2-8% + 量≤2 倍 5
日均量 + 5 日涨幅≤20% + 流通市值 30-250 亿 + 价格 8-45 元。趋势顺势口径，T+1/T+3。

Big-A-chovy 趋势确认池的正式定义，是该系统里唯一零盘中依赖（无资金/板块/VWAP）
的策略，参数已在《选股框架.md》固化。与本项目现有「反转低吸（超卖反转）」「隔日
超短（动能追涨）」互补，覆盖「顺势」风格。

原码字段口径（a_share_daily_screen.py:1333-1355）：
  change    = 当日涨幅 %（百分点）   five_ret   = c/REF(c,5)-1（小数）
  dist60    = HHV(h,60)/c-1（小数）  vol_vs_avg5= v/REF(MA(v,5),1)（倍，不含今日）
  float_mv  = float_shares*c（元）    turnover   = v*100/float_shares*100（%）
"""
import pandas as pd

import tdx

NAME = "严格趋势"
DESC = "均线多排|MA20上升|距60日高≤10%|涨幅3-6.5%|换手2-8%|量≤2倍|5日≤20%|30-250亿"
AUTHOR = "user（移植 Big-A-chovy strict_trend）"
PARAMS = {
    "ma": (5, 10, 20),
    "price": (8, 45),
    "float_mv_yi": (30, 250),
    "dist60_max": 0.10,
    "chg_pct": (3, 6.5),
    "turnover_pct": (2, 8),
    "vol_vs_avg5_max": 2,
    "five_ret_max": 0.20,
}


def select(g):
    """g: 单只股票日线（date 升序，含 symbol/total_shares/float_shares）。返回 bool Series。"""
    c = g["close"].astype(float)
    h = g["high"].astype(float)
    v = g["volume"].astype(float)
    fs = g.get("float_shares")          # 引擎注入：流通股本（股）
    if fs is None:
        return pd.Series(False, index=g.index)
    fs = fs.astype(float)

    ma5 = tdx.MA(c, 5)
    ma10 = tdx.MA(c, 10)
    ma20 = tdx.MA(c, 20)
    prev_ma20 = tdx.REF(ma20, 1)
    prev_c = tdx.REF(c, 1)

    chg = (c / prev_c - 1) * 100                  # 当日涨幅 %（百分点）
    five_ret = c / tdx.REF(c, 5) - 1              # 5 日累计涨幅（小数）
    dist60 = tdx.HHV(h, 60) / c - 1               # 距 60 日高点（小数）
    turnover = v * 100.0 / fs * 100.0              # 换手 %（百分点）
    vol_vs_avg5 = v / tdx.REF(tdx.MA(v, 5), 1)    # 量 / 昨日 5 日均量（倍，不含今日）
    float_mv = fs * c                              # 流通市值（元）

    cond = (
        (c >= 8) & (c <= 45)
        & (float_mv >= 3e9) & (float_mv <= 25e9)
        & (c > ma5) & (c > ma10) & (c > ma20)
        & (ma20 > prev_ma20)
        & (dist60 <= 0.10)
        & (turnover >= 2) & (turnover <= 8)
        & (vol_vs_avg5 <= 2)
        & (chg >= 3) & (chg <= 6.5)
        & (five_ret <= 0.20)
    )
    return cond.fillna(False).astype(bool)
