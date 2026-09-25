# -*- coding: utf-8 -*-
"""缠论底分型（用户公式）

缠论标准底分型（3 根 K 线：中间 K-1 的低点与高点均为三根最低）｜10 日内曾涨停
｜总市值≥50 亿｜收盘>MA3｜量比>1.2

通达信原式逐字翻译。ST / 板别（300/301/688/920）过滤在 datasource.filter_pool
已剔除，不进 select。

选股条件：
  COND_FX        缠论底分型：REF(L,1)<REF(L,2) 且 REF(L,1)<L 且 REF(H,1)<REF(H,2) 且 REF(H,1)<H
  COND_HAVE_ZT  10 日内曾涨停（主板 10%，zt_price 按板别取涨停价）
  COND_MKTCAP   总市值 ≥ 50 亿（总股本 × 收盘价 ≥ 5e9 元）
  COND_MA3      收盘 > MA3（MA(C,3)）
  COND_VR       量比 > 1.2（DYNAINFO(17) 为盘中实时量比，日线用 v/MA(v,5) 近似，
                与 ultra_strict / next_day_short 同口径）

口径：信号日 = 底分型确认日（K0 当日），默认 SELL_BASIS=close（次日收盘卖）。
"""
import numpy as np
import pandas as pd

import tdx

NAME = "缠论底分型"
DESC = "缠论底分型|10日内涨停|总市值≥50亿|收盘>MA3|量比>1.2"
AUTHOR = "用户"
PARAMS = {
    "zt_window": 10,         # 10 日内曾涨停
    "mktcap_min_yi": 50,     # 总市值下限（亿）
    "ma3": 3,                # 收盘 > MA3
    "vol_ratio_min": 1.2,    # 量比下限
}


def select(g):
    """g: 单只股票日线，按 date 升序，含 symbol 列 + 引擎注入 total_shares。返回 bool Series。"""
    c = g["close"].astype(float)
    h = g["high"].astype(float)
    l = g["low"].astype(float)
    v = g["volume"].astype(float)
    ts = g.get("total_shares")          # 引擎注入：总股本(股)
    if ts is None:
        return pd.Series(False, index=g.index)
    ts = ts.astype(float)
    sym = str(g["symbol"].iloc[0])

    # --- 1. 缠论底分型（3 根：K-2 / K-1 / K0，中间 K-1 低点&高点均最低）---
    #   K2=REF(.,2)  K1=REF(.,1)  K0=当期
    ref_l1, ref_l2 = tdx.REF(l, 1), tdx.REF(l, 2)
    ref_h1, ref_h2 = tdx.REF(h, 1), tdx.REF(h, 2)
    cond_fx = (ref_l1 < ref_l2) & (ref_l1 < l) & (ref_h1 < ref_h2) & (ref_h1 < h)

    # --- 2. 10 日内曾涨停（zt_price 按板别：主板 10%，已被 filter_pool 剔除创业板/科创板）---
    prev_c = c.shift(1)
    zt = tdx.zt_price(prev_c, sym)
    have_zt = tdx.EXIST(c >= zt - 1e-9, 10)

    # --- 3. 总市值 ≥ 50 亿（总股本 × 收盘价；FINANCE(39) 同口径）---
    mktcap = ts * c
    cond_mktcap = mktcap >= 5e9      # 50 亿 = 5e9 元

    # --- 4. 收盘 > MA3 ---
    ma3 = tdx.MA(c, 3)
    cond_ma3 = c > ma3

    # --- 5. 量比 > 1.2（DYNAINFO(17) 盘中实时，日线用 v/MA(v,5) 近似）---
    vol_ratio = v / tdx.MA(v, 5)
    cond_vr = vol_ratio > 1.2

    xg = cond_fx & have_zt & cond_mktcap & cond_ma3 & cond_vr
    return xg.fillna(False).astype(bool)
