# -*- coding: utf-8 -*-
"""探针：反转低吸筛选日次日的盘中高低分布 + 挂单成交模拟。

反转低吸当前口径 T+1 +0.70%/胜率49%/累计+34371%（次日收盘卖）。
看次日最高价中位，判断挂限价卖单能否比 +0.70% 更好。
绕开 engine，直接逐票跑 select + 取次日 OHLC，约 10s。
"""
import sys, os, time
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import numpy as np
import pandas as pd
import datasource as ds
import strategies

t0 = time.time()
ohlcv = ds.load_ohlcv(verbose=False)
uni = ds.load_universe(verbose=False)
allowed, _ = ds.filter_pool(uni)
shares = ds.build_shares_map(uni)
pool_set = set(allowed)
ohlcv_pool = ohlcv[ohlcv["symbol"].isin(pool_set)].copy()
print(f"数据加载完成（{time.time()-t0:.0f}s），扫描 {len(pool_set)} 只...")

TARGETS = [
    ("reversal_low_absorb", "反转低吸(尾盘买c[i])"),
]

def scan(name):
    st = strategies.get_strategy(name)
    rows = []
    n_err = 0
    for sym, g in ohlcv_pool.groupby("symbol"):
        g = g.sort_values("date")
        _ts, _fs = shares.get(sym, (float("nan"), float("nan")))
        g["total_shares"] = float(_ts)
        g["float_shares"] = float(_fs)
        try:
            mask = st.select(g)
        except Exception:
            n_err += 1
            continue
        idx = np.where(mask.fillna(False).values)[0]
        if len(idx) == 0:
            continue
        c = g["close"].astype(float).values
        h = g["high"].astype(float).values
        o = g["open"].astype(float).values
        l = g["low"].astype(float).values
        n = len(c)
        for i in idx:
            if i + 1 >= n:
                continue
            buy = c[i]
            if not (np.isfinite(buy) and buy > 0):
                continue
            rows.append({
                "ret_high":  h[i+1] / buy - 1.0,
                "ret_open":  o[i+1] / buy - 1.0,
                "ret_close": c[i+1] / buy - 1.0,
                "ret_low":   l[i+1] / buy - 1.0,
            })
    return pd.DataFrame(rows), n_err

def stat(df, col, name):
    s = df[col].dropna()
    return (f"{name:8s}: 均值{s.mean()*100:+.2f}%  中位{s.median()*100:+.2f}%  "
            f"胜率{(s>0).mean()*100:.0f}%  "
            f"P10 {s.quantile(0.10)*100:+.2f}%  P25 {s.quantile(0.25)*100:+.2f}%  "
            f"P75 {s.quantile(0.75)*100:+.2f}%  P90 {s.quantile(0.90)*100:+.2f}%")

for key, label in TARGETS:
    df, n_err = scan(key)
    print("=" * 70)
    print(f"  {label}  select={key}")
    print("=" * 70)
    print(f"  命中 {len(df)} 个买入日（{n_err} 只 select 报错），耗时 {time.time()-t0:.0f}s")
    for col, nm in [("ret_high","次日最高"),("ret_open","次日开盘"),
                    ("ret_close","次日收盘"),("ret_low","次日最低")]:
        print("  " + stat(df, col, nm))
    print("-" * 70)
    print(f"  对照（现状·次日收盘卖 c[i+1]/c[i]-1）: T+1均值 {df['ret_close'].mean()*100:+.2f}% "
          f"胜率{(df['ret_close']>0).mean()*100:.0f}% 中位{df['ret_close'].median()*100:+.2f}%")
    print("-" * 70)
    print("  挂单成交模拟：买入价 c，次日挂限价 c*(1+x)，最高价触及即成交吃 x%，否则收盘卖")
    print(f"    {'挂单价':>8s} {'成交率':>7s} {'混合T+1均值':>12s} {'混合胜率':>9s} {'混合中位':>9s} {'vs收盘均值':>11s}")
    base_mean = df["ret_close"].mean()
    for x in [0.005, 0.01, 0.015, 0.02, 0.025, 0.03, 0.04, 0.05, 0.06, 0.08]:
        hit = (df["ret_high"] >= x)
        ret_mixed = np.where(hit.values, x, df["ret_close"].values)
        print(f"    +{x*100:5.1f}%  {hit.mean()*100:6.0f}%   {np.mean(ret_mixed)*100:+.2f}%"
              f"     {(ret_mixed>0).mean()*100:6.0f}%   {np.median(ret_mixed)*100:+.2f}%"
              f"   {(np.mean(ret_mixed)-base_mean)*100:+.2f}%")
    print()

print(f"总耗时 {time.time()-t0:.0f}s")
