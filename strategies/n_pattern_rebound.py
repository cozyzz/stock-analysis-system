# -*- coding: utf-8 -*-
"""N字反包（观察池）

盯紧涨停后第二天（T1）有上下影线 + 量能符合的票，列为观察池，用户后续自行观察
是否缩量洗盘 → 反包 → 妖股。

核心逻辑：赚"1进2"那帮人的钱——
  - 涨停日 T0 首板，T1（次日）放量但不过度(量比<2.5)、带上下影线（试盘/洗盘），
    理想在 T1 下影线区域买 → 成本接近 T0 吃涨停的人，远低于 1进2 追涨成本；
  - 列出 T1 符合条件的票作为观察池，用户盯后续缩量/反包/妖股（不等到反包确认）。

信号日 = T1（涨停次日放量影线日），比"反包确认"更早，便于用户提前埋伏观察。
用户实盘在 T1 下影线买（成本更低），回测用 T1 收盘买为保守下限。

ST/板别（300/301/688/920）过滤在 datasource.filter_pool 已剔除，不进 select。

选股条件（XG，信号日 = T1 = 当期）：
  COND_ZT     T0 = T1-1（前日）：涨停（c ≥ zt_price）
  COND_VOL    T1（当期）：放量（量 > MA5×1.0）且量比 < 2.5（量 < MA5×2.5，"不能太大"）
  COND_WICK   T1 上下影都要有（上影 > wick_min×c 且 下影 > wick_min×c）

口径：信号日 = T1，SELL_BASIS=close（次日收盘卖）。

买点（实盘动作，非 select 参数；select 只负责列 T1 信号）：
  信号日 T1 后，等 T2/T3 缩量回调到 MA5（均价线）区域，在 T+3 低点买入（贴合均线）。
  回测（同子集 2278 笔：T1冲高回落+T2/T3缩量回调，主板10%板）：
    缩量低点买(min T+2/T+3低)  保守均+4.61%  止损样本均+1.18%（止损无痛）
    T1收盘买                    保守均-2.74%  止损-7.14%
    T1下影买                    保守均+0.53%  止损-4.35%
  缩量低点买入比 T1 收盘低中位 5.5%。
  关键：优势在【成本】不在【概率】——反包率没提高（43%≈45%），但买得低→反包赚多、
  止损不疼（买贴失效价 T0最低，破位几乎不亏）。大阳(收盘>5%)首现中位 T1后第3天=涨停后
  第4天(T+4)，买在 T+3 埋伏等 T+4 大阳反包冲高走人。
  见 memory n-pattern-rebound-buy-point / n-pattern-rebound-exit。
"""
import numpy as np
import pandas as pd

import tdx

NAME = "N字反包"
DESC = "涨停次日放量(<3倍)+上下影=观察池|买点T+3缩量贴MA5低点买|反包冲高走人破失效价走"
AUTHOR = "用户"
PARAMS = {
    "vol_ratio_t1_low": 1.0,          # T1 放量下限（量/MA5，"放大"）
    "vol_ratio_t1_high": 3.0,         # T1 放量上限（金健米业案例2.66，放宽到3.0）
    "wick_min": 0.005,                # T1 上下影最小长度（相对收盘价，0.5%）
}


def select(g):
    """g: 单只股票日线，按 date 升序，含 symbol 列。返回 bool Series（信号日=T1 涨停次日放量影线日）。"""
    c = g["close"].astype(float)
    h = g["high"].astype(float)
    l = g["low"].astype(float)
    o = g["open"].astype(float)
    v = g["volume"].astype(float)
    sym = str(g["symbol"].iloc[0])

    prev_c = c.shift(1)
    zt = tdx.zt_price(prev_c, sym)        # 每日涨停价（基于前日收盘×板别）
    ma_v5 = tdx.MA(v, 5)

    # --- T0 = 前日（涨停日）---
    cond_zt = c.shift(1) >= zt.shift(1) - 1e-9     # 前日收盘 ≥ 前日涨停价

    # --- T1 = 当期：放量 + 上下影 ---
    cond_vol = (v > ma_v5 * PARAMS["vol_ratio_t1_low"]) & \
               (v < ma_v5 * PARAMS["vol_ratio_t1_high"])
    body_high = c.clip(lower=o)                   # max(close, open)
    body_low = c.clip(upper=o)                    # min(close, open)
    upper_wick = h - body_high
    lower_wick = body_low - l
    wick_thr = PARAMS["wick_min"] * c
    cond_wick = (upper_wick > wick_thr) & (lower_wick > wick_thr)   # 上下影都要有

    sig = cond_zt & cond_vol & cond_wick
    return sig.fillna(False).astype(bool)
