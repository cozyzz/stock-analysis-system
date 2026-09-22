# -*- coding: utf-8 -*-
"""严格超短（移植自 Big-A-chovy a_share_daily_screen.py:1408 strict_ultra）

小盘动能口径：价格 5-30 元 + 流通市值<200 亿 + 涨幅 2-5.5% + 5 日涨幅≤18% + 收盘
距当日高点≤3% + 换手>3% + 量比>1.2 + 成交额>3 亿 + 收盘>MA5。T+1 超短。

与本项目现有「隔日超短」（涨幅 2-5% + 量比 1-5 + 换手 5-10% + 近 10 日涨停 + 总市值
>100 亿）形成大小盘互补：本策略做流通市值<200 亿的小盘，隔日超短做总市值>100 亿的大盘。

原码 volume_ratio 来自新浪实时「量比」字段（日内折算），日线无法精确复现，这里用
v/MA(v,5)（含今日）近似——日线口径的标准量比定义。cur_to_high = high/close-1（小数）。
"""
import pandas as pd

import tdx

NAME = "严格超短"
DESC = "小盘<200亿|涨幅2-5.5%|5日≤18%|收盘距高点≤3%|量比>1.2|换手>3%|额>3亿"
AUTHOR = "user（移植 Big-A-chovy strict_ultra）"
PARAMS = {
    "price": (5, 30),
    "float_mv_max_yi": 200,
    "chg_pct": (2, 5.5),
    "five_ret_max": 0.18,
    "cur_to_high_max": 0.03,
    "turnover_min": 3,
    "vol_ratio_min": 1.2,
    "amount_min_yi": 3,
    "ma": 5,
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
    prev_c = tdx.REF(c, 1)

    chg = (c / prev_c - 1) * 100                  # 当日涨幅 %（百分点）
    five_ret = c / tdx.REF(c, 5) - 1              # 5 日累计涨幅（小数）
    cur_to_high = h / c - 1                       # 距当日高点（小数）
    turnover = v * 100.0 / fs * 100.0              # 换手 %（百分点）
    vol_ratio = v / tdx.MA(v, 5)                   # 量比近似（含今日）
    float_mv = fs * c                              # 流通市值（元）
    amount = c * v * 100                           # 成交额（元）

    cond = (
        (c >= 5) & (c <= 30)
        & (float_mv < 20e9)
        & (turnover > 3)
        & (vol_ratio > 1.2)
        & (amount > 3e8)
        & (chg >= 2) & (chg <= 5.5)
        & (c > ma5)
        & (five_ret <= 0.18)
        & (cur_to_high <= 0.03)
    )
    return cond.fillna(False).astype(bool)
