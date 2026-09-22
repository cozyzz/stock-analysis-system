# -*- coding: utf-8 -*-
"""趋势观察（移植自 Big-A-chovy a_share_daily_screen.py:1457 trend_observation）

strict_trend 的宽松超集：容许「红转绿修复日」+ 价略破 MA5（×0.985）+ MA5 略破 MA10
（×0.985）+ 涨幅 -1~4.5% + 换手 1.5-10% + 量≤2.5 倍 + 距 60 日高≤15%。原码若已满足
strict_trend 则直接入选，故本策略 = strict ∪ 宽松，捕捉趋势中回踩低吸点。

去掉了原系统的板块共振（select 是单股扫描，无法算板块），其余全日线可复现。

原码字段口径同 trend_strict；amount = close*volume*100（元）。
"""
import pandas as pd

import tdx

NAME = "趋势观察"
DESC = "strict_trend宽松版|容许回踩MA5×0.985|涨幅-1~4.5%|距60日高≤15%|回踩低吸"
AUTHOR = "user（移植 Big-A-chovy trend_observation）"
PARAMS = {
    "ma": (5, 10, 20),
    "price": (8, 45),
    "float_mv_yi": (30, 250),
    "amount_min_yi": 3,
    "ma5_tol": 0.985,
    "chg_pct": (-1, 4.5),
    "turnover_pct": (1.5, 10),
    "vol_vs_avg5_max": 2.5,
    "five_ret_max": 0.20,
    "dist60_max": 0.15,
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
    prev_ma20 = tdx.REF(ma20, 1)
    prev_c = tdx.REF(c, 1)

    chg = (c / prev_c - 1) * 100
    five_ret = c / tdx.REF(c, 5) - 1
    dist60 = tdx.HHV(h, 60) / c - 1
    turnover = v * 100.0 / fs * 100.0
    vol_vs_avg5 = v / tdx.REF(tdx.MA(v, 5), 1)
    float_mv = fs * c
    amount = c * v * 100                       # 成交额（元）

    # strict 子集（趋势确认直接入选）
    strict = (
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
    # 宽松：红转绿修复 + MA5/10 容忍 + 涨幅/换手/量/距离全面放宽
    loose = (
        (c >= 8) & (c <= 45)
        & (float_mv >= 3e9) & (float_mv <= 25e9)
        & (amount > 3e8)
        & (c >= ma20)
        & (ma20 > prev_ma20)
        & (c >= ma5 * 0.985)
        & (ma5 >= ma10 * 0.985)
        & (chg >= -1) & (chg <= 4.5)
        & (turnover >= 1.5) & (turnover <= 10)
        & (vol_vs_avg5 <= 2.5)
        & (five_ret <= 0.20)
        & (dist60 <= 0.15)
    )
    return (strict | loose).fillna(False).astype(bool)
