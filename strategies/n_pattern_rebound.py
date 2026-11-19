# -*- coding: utf-8 -*-
"""N字反包·影线（观察池）

涨停次日 T1 放量+上下影线+收盘站上 T0 涨停价（影线微超），列观察池。
用户盯后续缩量洗盘 → T+3 缩量低点买 → 反包冲高走人。

形态（用户案例：林州重机 11-10、维通利 11-05）：
  T0 涨停 → T1 放量+上下影+收盘>T0最高价(≈涨停价，影线微超) → T2/T3 缩量洗盘 → 反包大阳

信号日 = T1（涨停次日）。

选股条件（信号日 = T1 = 当期）：
  COND_ZT    T0=前日涨停（c.shift(1) ≥ zt_price）
  COND_VOL   T1 放量（量 > MA5×1.0 且 量比 < 3.0）
  COND_WICK  T1 上下影都要有（上影>0.5%×c 且 下影>0.5%×c）
  COND_OVER  T1 收盘 > T0 最高价（≈涨停价，强势确认/影线微超）

买点（实盘，非 select 参数）：
  T1 后等 T2/T3 缩量回调到 MA5，T+3 缩量低点买（贴合均线）。
  回测（A2 影线版，T0涨停+T1放量+影线+收盘超涨停价→T2/T3缩量→T+3低点买，主板）：
    反包率 69.7%  止损率 23.9%  保守均+3.99%  胜率75.3%  止损均幅-3.75%
    买入价相对涨停价折价中位 -3.1%
  止损 = T0最低价-3%（用户口径），反包冲高走人（上影也认）。

ST/板别过滤在 datasource.filter_pool。互斥于 n_pattern_rebound_strong（强势版，不带影线）。
见 memory n-pattern-rebound-buy-point / n-pattern-rebound-exit。
"""
import numpy as np
import pandas as pd

import tdx

NAME = "N字反包·影线"
DESC = "涨停次日放量(<3倍)+上下影+收盘超涨停价=观察池|买T+3缩量低点|止损T0最低-3%反包走"
AUTHOR = "用户"
OBSERVATION_POOL = True     # 观察池：看板不计回测equity（T+1收盘复利口径不适用，实盘T+3缩量低点买+反包走人）
PARAMS = {
    "vol_ratio_t1_low": 1.0,          # T1 放量下限（量/MA5）
    "vol_ratio_t1_high": 3.0,         # T1 放量上限
    "wick_min": 0.005,                # T1 上下影最小长度（相对收盘价，0.5%）
}


def select(g):
    """g: 单只股票日线，按 date 升序，含 symbol 列。返回 bool Series（信号日=T1）。"""
    c = g["close"].astype(float)
    h = g["high"].astype(float)
    l = g["low"].astype(float)
    o = g["open"].astype(float)
    v = g["volume"].astype(float)
    sym = str(g["symbol"].iloc[0])

    prev_c = c.shift(1)
    zt = tdx.zt_price(prev_c, sym)
    ma_v5 = tdx.MA(v, 5)

    cond_zt = c.shift(1) >= zt.shift(1) - 1e-9     # T0 前日涨停
    cond_vol = (v > ma_v5 * PARAMS["vol_ratio_t1_low"]) & \
               (v < ma_v5 * PARAMS["vol_ratio_t1_high"])
    body_high = c.clip(lower=o)
    body_low = c.clip(upper=o)
    upper_wick = h - body_high
    lower_wick = body_low - l
    wick_thr = PARAMS["wick_min"] * c
    cond_wick = (upper_wick > wick_thr) & (lower_wick > wick_thr)   # 上下影都要有
    cond_over = c > h.shift(1)              # T1 收盘 > T0 最高价（≈涨停价）

    sig = cond_zt & cond_vol & cond_wick & cond_over
    return sig.fillna(False).astype(bool)
