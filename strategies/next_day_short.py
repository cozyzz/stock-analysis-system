# -*- coding: utf-8 -*-
"""隔日超短 · 涨幅2-5% + 量比1-5 + 换手5-10% + 近10日涨停 + 总市值>100亿

隔日超短策略：当日收盘买、次日收盘卖（T+1）。板别/ST 在 datasource.filter_pool
已剔除（非科创/创业板/ST）。换手与总市值用 universe 快照股本估算（look-ahead
近似），引擎会注入 total_shares/float_shares 两列（股）：
  换手% = volume(手)*100 / 流通股本 * 100
  总市值 = 总股本 * close
"""
import numpy as np
import pandas as pd

import tdx

NAME = "隔日超短"
DESC = "涨幅2-5%|量比1-5|换手5-10%|近10日涨停|总市值>100亿|尾盘买→次日开盘卖"
AUTHOR = "user"
# 真实交易节奏：信号日尾盘买 → 次日 10 点前卖（竞价挂跌停卖、成交开盘价附近）。
# 引擎据此用 o[i+1]/c[i]-1（收盘买→次日开盘卖）覆盖 ret1c，净值/统计自动按此口径。
# 其他策略默认 SELL_BASIS="close"（次日收盘卖），不受影响。
SELL_BASIS = "open_next"
PARAMS = {
    "horizon": 1,
    "chg_pct": [2, 5],
    "vol_ratio": [1, 5],
    "turnover_pct": [5, 10],
    "have_zt_days": 10,
    "mktcap_min_yi": 100,   # 总市值下限（亿）
}


def select(g):
    c = g["close"].astype(float)
    v = g["volume"].astype(float)
    ts = g.get("total_shares")    # 引擎注入：总股本(股)
    fs = g.get("float_shares")    # 引擎注入：流通股本(股)
    if ts is None or fs is None:
        return pd.Series(False, index=g.index)
    ts = ts.astype(float)
    fs = fs.astype(float)
    prev = c.shift(1)
    chg = c / prev - 1.0                                     # 涨幅(分数)
    vr = v / tdx.MA(v, 5)                                    # 量比
    turnover = (v * 100.0) / fs.replace(0, np.nan) * 100.0   # vol手*100=股 /流通股 *100=%
    mktcap = ts * c                                           # 总市值(元)
    sym = str(g["symbol"].iloc[0]) if "symbol" in g.columns else ""
    zt = tdx.zt_price(prev, sym)
    have_zt = tdx.EXIST(c >= zt - 1e-9, 10)
    cond = (
        (chg >= 0.02) & (chg <= 0.05)
        & (vr > 1) & (vr < 5)
        & (turnover > 5) & (turnover < 10)
        & have_zt
        & (mktcap > 1e10)        # 100亿 = 1e10 元
    )
    return cond.fillna(False).astype(bool)
