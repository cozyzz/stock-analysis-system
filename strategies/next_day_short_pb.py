# -*- coding: utf-8 -*-
"""隔日超短·回踩版（next_day_short_pb）

隔日超短的"贴线买入"口径：筛选日沿用隔日超短（当日涨 2-5% 放量 + 涨停基因 + 大盘），
但不追涨——在信号后 5 日内首次回踩 MA5（close ≤ MA5×1.02）那天收盘买入，次日开盘卖。
对应"贴合均线低吸 + 隔日冲高兑现"的实盘节奏，对应你说的真实买点。

select 返回**买入日 j**（不是筛选日 i）：对每个筛选日 i 在 [i+1, i+5] 找首次 close≤MA5×1.02
的 j 标 True。引擎现成 ret1os（o[j+1]/c[j]-1）即"买入日次日开盘卖"收益，配 SELL_BASIS=
"open_next" 自动启用。金竖线标在买入日 j（实际入场点）。

与 next_day_short 的区别：后者尾盘追涨买（c[i]，已涨 2-5% 高位）；本策略等回踩 MA5 买
（c[j]，贴线低成本）。买点更接近实盘"贴合均线低吸"。
"""
import numpy as np
import pandas as pd

import tdx

NAME = "隔日超短(回踩版)"
DESC = "隔日超短筛选|信号后5日内首回踩MA5买|次日开盘卖|贴线低吸"
AUTHOR = "user"
# 买入日次日开盘卖（竞价挂跌停成交开盘价附近），和 next_day_short 同口径
SELL_BASIS = "open_next"
PARAMS = {
    "horizon": 1,
    # 筛选条件（同隔日超短）
    "chg_pct": [2, 5],
    "vol_ratio": [1, 5],
    "turnover_pct": [5, 10],
    "have_zt_days": 10,
    "mktcap_min_yi": 100,
    # 回踩买入
    "pullback_window": 5,       # 信号后 K 日内
    "pullback_ma": 5,           # 回踩 MA5
    "pullback_tol": 0.02,       # close ≤ MA5*(1+2%)
    "buy_price": "买入日收盘",
    "sell": "次日开盘",
}


def select(g):
    """返回买入日 j 的 bool Series（不是筛选日 i）。"""
    c = g["close"].astype(float)
    h = g["high"].astype(float)
    l = g["low"].astype(float)
    v = g["volume"].astype(float)
    ts = g.get("total_shares")    # 引擎注入：总股本(股)
    fs = g.get("float_shares")    # 引擎注入：流通股本(股)
    if ts is None or fs is None:
        return pd.Series(False, index=g.index)
    ts = ts.astype(float)
    fs = fs.astype(float)
    prev = c.shift(1)
    chg = c / prev - 1.0                              # 涨幅(分数)
    vr = v / tdx.MA(v, 5)                            # 量比
    turnover = (v * 100.0) / fs * 100.0              # 换手 %
    mktcap = ts * c                                  # 总市值(元)
    sym = str(g["symbol"].iloc[0]) if "symbol" in g.columns else ""
    zt = tdx.zt_price(prev, sym)
    have_zt = tdx.EXIST(c >= zt - 1e-9, 10)          # 近 10 日涨停

    # ① 筛选日 i：隔日超短条件（当日强势放量启动的大盘股）
    cand = (
        (chg >= 0.02) & (chg <= 0.05)
        & (vr > 1) & (vr < 5)
        & (turnover > 5) & (turnover < 10)
        & have_zt
        & (mktcap > 1e10)        # 100 亿 = 1e10 元
    ).fillna(False)

    # ② 买入日 j：每个筛选日 i 之后 [i+1, i+5] 首次 close ≤ MA5*(1+2%) 那天
    ma5 = tdx.MA(c, 5)
    pullback = (c <= ma5 * 1.02).fillna(False)
    buy = pd.Series(False, index=g.index)
    cand_idx = np.where(cand.values)[0]
    n = len(c)
    for i in cand_idx:
        for j in range(i + 1, min(i + 6, n)):
            if bool(pullback.iloc[j]):
                buy.iloc[j] = True
                break        # 首次回踩即买，不重复
    return buy.fillna(False).astype(bool)
