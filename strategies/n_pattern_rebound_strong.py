# -*- coding: utf-8 -*-
"""N字反包·强势（观察池）

涨停次日 T1 放量+收盘站上 T0 涨停价（大阳续涨，不带影线），列观察池。
T1 强势确认（大阳/连板），后续动量延续/反包概率高。

形态：
  T0 涨停 → T1 放量+收盘>T0最高价（大阳/连板，无明显上下影）→ T2/T3 缩量回调 → 反包/续涨

信号日 = T1（涨停次日）。

选股条件（信号日 = T1 = 当期）：
  COND_ZT    T0=前日涨停（c.shift(1) ≥ zt_price）
  COND_VOL   T1 放量（量 > MA5×1.0 且 量比 < 3.0）
  COND_OVER  T1 收盘 > T0 最高价（≈涨停价，强势确认）
  不带影线（~COND_WICK，排除影线版，互斥）

买点（实盘，非 select 参数）：
  T1 强势后等 T2/T3 缩量回调，T+3 缩量低点买（若回调；续涨直接走则不回踩）。
  回测（B 强势版，T0涨停+T1放量+收盘超涨停价(不带影线)→T2/T3缩量→T+3低点买，主板）：
    反包率 72.1%  止损率 22.7%  保守均+6.10%  胜率77.6%  止损均幅-3.21%
    买入价相对涨停价折价中位 -2.1%
  止损 = T0最低价-3%（用户口径），反包冲高走人（上影也认）。

ST/板别过滤在 datasource.filter_pool。互斥于 n_pattern_rebound（影线版）。
见 memory n-pattern-rebound-buy-point / n-pattern-rebound-exit。
"""
import numpy as np
import pandas as pd

import tdx

NAME = "N字反包·强势"
DESC = "涨停次日放量(<3倍)+收盘超涨停价(不带影线)=观察池|买T+3缩量低点|止损T0最低-3%反包走"
AUTHOR = "用户"
PARAMS = {
    "vol_ratio_t1_low": 1.0,
    "vol_ratio_t1_high": 3.0,
    "wick_min": 0.005,
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
    cond_over = c > h.shift(1)              # T1 收盘 > T0 最高价

    sig = cond_zt & cond_vol & cond_over & ~cond_wick   # 强势版：不带影线（互斥影线版）
    return sig.fillna(False).astype(bool)
