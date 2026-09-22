# -*- coding: utf-8 -*-
"""低吸趋势（移植自 Big-A-chovy a_share_daily_screen.py:1581 low_trend_class A 类，改造版）

均线多排 + MA5 上升 + MA10 平/升 + 涨幅 2.5-5.2% + 换手 2-7.5% + 收盘未贴最高点
（high_pull>0.3%）+ 距 MA20≤15% + 5 日涨幅≤18% + 成交额>3 亿。比 strict_trend 更低位
低吸（涨幅 2.5-5.2% vs 3-6.5%、ma20_dist≤15% vs dist60≤10%）。

改造：原 A 类还要求「板块共振 has_resonance」和排除「尾盘追高风险」盘中标签——前者
select 单股扫描无法算板块，后者依赖 after_1420 盘中时序，均去掉。核心均线/量能/位置
条件全保留。hard_c 否决项（chg>6/turnover>9/ma20_dist>0.15/five_ret>0.18）已并入正向条件。

原码字段口径：
  high_pull = (high-prev_close)/prev_close*100 - chg = (high-close)/prev_close*100（百分点）
  ma20_dist = c/ma20-1（小数）
"""
import pandas as pd

import tdx

NAME = "低吸趋势"
DESC = "均线多排+MA5升|涨幅2.5-5.2%|换手2-7.5%|未贴高点|距MA20≤15%|5日≤18%|额>3亿"
AUTHOR = "user（移植 Big-A-chovy low_trend_class 改造）"
PARAMS = {
    "ma": (5, 10, 20),
    "chg_pct": (2.5, 5.2),
    "turnover_pct": (2, 7.5),
    "high_pull_min": 0.3,      # 百分点
    "ma20_dist_max": 0.15,
    "five_ret_max": 0.18,
    "amount_min_yi": 3,
}


def select(g):
    c = g["close"].astype(float)
    h = g["high"].astype(float)
    v = g["volume"].astype(float)
    fs = g.get("float_shares")
    if fs is None:
        return pd.Series(False, index=g.index)
    fs = fs.astype(float)

    ma5 = tdx.MA(c, 5)
    ma10 = tdx.MA(c, 10)
    ma20 = tdx.MA(c, 20)
    prev_ma5 = tdx.REF(ma5, 1)
    prev_ma10 = tdx.REF(ma10, 1)
    prev_c = tdx.REF(c, 1)

    chg = (c / prev_c - 1) * 100                  # 当日涨幅 %（百分点）
    five_ret = c / tdx.REF(c, 5) - 1              # 5 日累计涨幅（小数）
    ma20_dist = c / ma20 - 1                      # 距 MA20（小数）
    turnover = v * 100.0 / fs * 100.0             # 换手 %（百分点）
    high_pull = (h - c) / prev_c * 100.0          # 日内高点回落（百分点）
    amount = c * v * 100                           # 成交额（元）

    base = (
        (c > ma5) & (c > ma10) & (c > ma20)
        & (ma5 > prev_ma5) & (ma10 >= prev_ma10)
        & (amount > 3e8)
    )
    cond = (
        base
        & (chg >= 2.5) & (chg <= 5.2)
        & (turnover >= 2) & (turnover <= 7.5)
        & (high_pull > 0.3)
        & (ma20_dist <= 0.15)
        & (five_ret <= 0.18)
    )
    return cond.fillna(False).astype(bool)
