# -*- coding: utf-8 -*-
"""反转低吸（用户自创）

超卖反转低吸｜A组均线 MA7/MA28/MA56 至少 2 根站上｜20日窗口过滤涨幅>40%高位标的｜10日内存在涨停

通达信原式逐字翻译。ST / 板别（300/688/920）过滤在 datasource 层做，不进 select。

选股条件（XG）：
  COND_HAVE_LOW   20 周期内 J 值超卖（J<20）
  COND_BREAK      上穿 3 日均线
  COND_VOL        量 > 5 日均量
  COND_HIGHFILTER 20 日涨幅 < 40%
  COND_HAVE_ZT    10 日内曾涨停
  COND_MA_OK      MA7/MA28/MA56 至少 2 根价在上方
"""
import numpy as np
import pandas as pd

import tdx

NAME = "反转低吸"
DESC = "超卖反转低吸｜MA7/28/56至少2根站上｜20日涨幅<40%｜10日内涨停"
AUTHOR = "用户"
PARAMS = {
    "N_rsi": 14,
    "kdj": (9, 3, 3),
    "ma_group": (7, 28, 56),
    "ma_stand_min": 2,
    "oversold_j": 20,
    "oversold_window": 20,
    "high_filter_days": 20,
    "high_filter_pct": 40,
    "zt_window": 10,
}


def select(g):
    """g: 单只股票日线，按 date 升序，含 symbol 列。返回 bool Series。"""
    c = g["close"].astype(float)
    h = g["high"].astype(float)
    l = g["low"].astype(float)
    v = g["volume"].astype(float)
    sym = str(g["symbol"].iloc[0])

    # --- RSI 块（公式有，XG 不直接用，保忠实计算）---
    chg = c.diff()                                   # CLOSE - REF(CLOSE,1)
    up = tdx.sma_wilder(chg.clip(lower=0), 14)        # SMA(MAX(chg,0),14,1)
    dn = tdx.sma_wilder(chg.abs(), 14)                # SMA(ABS(chg),14,1)  ← 用户原文用 ABS
    # RS = up / dn ; RS_0_100 = 100 - 100/(1+RS)  （RSI，未进 XG）

    # --- A 组均线 ---
    ma3 = tdx.MA(c, 3)
    ma7 = tdx.MA(c, 7)
    ma28 = tdx.MA(c, 28)
    ma56 = tdx.MA(c, 56)
    cnt_ma = (c > ma7).astype(int) + (c > ma28).astype(int) + (c > ma56).astype(int)
    cond_ma_ok = cnt_ma >= 2

    # --- KDJ(9,3,3) ---
    llv9 = tdx.LLV(l, 9)
    hhv9 = tdx.HHV(h, 9)
    rsv = (c - llv9) / (hhv9 - llv9).replace(0, np.nan) * 100.0
    k = tdx.sma_wilder(rsv, 3)                        # SMA(RSV,3,1)
    d = tdx.sma_wilder(k, 3)                          # SMA(K,3,1)
    j = 3 * k - 2 * d
    cond_have_low = tdx.EXIST(j < 20, 20)             # 20 周期内 J<20

    # --- 突破 / 量 / 高位过滤 ---
    cond_break = tdx.CROSS(c, ma3)                    # 上穿 MA3
    cond_vol = v > tdx.MA(v, 5)                       # 量 > 5 日均量
    cond_highfilter = (c / c.shift(20) - 1) * 100 < 40  # 20 日涨幅 < 40%

    # --- 涨停基因 ---
    prev_c = c.shift(1)
    zt = tdx.zt_price(prev_c, sym)                   # 涨停价（按板别 10%/20%）
    cond_have_zt = tdx.EXIST(c >= zt - 1e-9, 10)      # 10 日内曾涨停

    xg = (cond_have_low & cond_break & cond_vol &
          cond_highfilter & cond_have_zt & cond_ma_ok)
    return xg
