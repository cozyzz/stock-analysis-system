# -*- coding: utf-8 -*-
"""探针：缠论底分型·次日盘中分布 + 挂单成交模拟 + 三口径净值。

A 收盘卖(现状)   : ret = c[i+1]/c[i]-1
B 挂5%全卖(脉冲)  : 次日最高触及+5%则成交吃+5%，否则收盘卖
C 5%走一半剩收盘  : 成交则一半吃5%一半收盘，否则收盘卖
净值模型同 engine._equity_curve：满仓均分当日信号、逐信号日复利、cost=0.003/轮。
"""
import sys, os, time
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import numpy as np
import pandas as pd
import config as C
import datasource as ds
import strategies
import tdx

t0 = time.time()
ohlcv = ds.load_ohlcv(verbose=False)
uni = ds.load_universe(verbose=False)
allowed, _ = ds.filter_pool(uni)
shares = ds.build_shares_map(uni)
pool_set = set(allowed)
ohlcv_pool = ohlcv[ohlcv["symbol"].isin(pool_set)].copy()
print(f"数据加载完成（{time.time()-t0:.0f}s），扫描缠论底分型信号...")

st = strategies.get_strategy("chanlun_bottom_fx")

rows = []
n_err = 0
for sym, g in ohlcv_pool.groupby("symbol"):
    g = g.sort_values("date").reset_index(drop=True)
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
    dates = g["date"].values
    n = len(c)
    prev = pd.Series(c).shift(1).values
    zt = tdx.zt_price(pd.Series(prev), str(sym)).values
    for i in idx:
        if i + 1 >= n:
            continue
        buy = c[i]
        if not (np.isfinite(buy) and buy > 0):
            continue
        nc, nh, no, nl = c[i+1], h[i+1], o[i+1], l[i+1]
        rows.append({
            "date": pd.Timestamp(dates[i]),
            "ret_high":  nh / buy - 1.0,
            "ret_open":  no / buy - 1.0,
            "ret_close": nc / buy - 1.0,
            "ret_low":   nl / buy - 1.0,
            "is_zt": bool(np.isfinite(zt[i+1]) and nc >= zt[i+1] - 1e-9),
        })

df = pd.DataFrame(rows)
df = df[df["ret_close"].notna()].copy()
print(f"信号数 {len(df)}（{n_err} 只 select 报错），封涨停次日 {df['is_zt'].sum()}，耗时 {time.time()-t0:.0f}s")
print()


def stat(col, nm):
    s = df[col].dropna()
    return (f"{nm:8s}: 均值{s.mean()*100:+.2f}%  中位{s.median()*100:+.2f}%  "
            f"胜率{(s>0).mean()*100:.0f}%  "
            f"P10 {s.quantile(0.10)*100:+.2f}%  P25 {s.quantile(0.25)*100:+.2f}%  "
            f"P75 {s.quantile(0.75)*100:+.2f}%  P90 {s.quantile(0.90)*100:+.2f}%")


print("=" * 78)
print("  ① 次日盘中分布（相对买入价 c[i]）")
print("=" * 78)
for col, nm in [("ret_high", "次日最高"), ("ret_open", "次日开盘"),
                ("ret_close", "次日收盘"), ("ret_low", "次日最低")]:
    print("  " + stat(col, nm))

sh = df["ret_high"].dropna()
print("-" * 78)
print("  ② 次日最高价分位（盘中冲高空间）")
print(f"  P10 {sh.quantile(0.10)*100:+.2f}%  P25 {sh.quantile(0.25)*100:+.2f}%  "
      f"中位 {sh.median()*100:+.2f}%  均值 {sh.mean()*100:+.2f}%  "
      f"P75 {sh.quantile(0.75)*100:+.2f}%  P90 {sh.quantile(0.90)*100:+.2f}%")
print(f"  冲过正(high>0): {(sh>0).mean()*100:.0f}%  冲过+3%: {(sh>=0.03).mean()*100:.0f}%  "
      f"冲过+5%: {(sh>=0.05).mean()*100:.0f}%")

print("-" * 78)
print("  ③ 挂单成交模拟：买入价 c，次日挂限价 c*(1+x)，最高价触及即成交吃 x%，否则收盘卖")
print(f"    {'挂单价':>8s} {'成交率':>7s} {'混合T+1均值':>12s} {'混合胜率':>9s} {'混合中位':>9s} {'vs收盘均值':>11s}")
base_mean = df["ret_close"].mean()
for x in [0.005, 0.01, 0.015, 0.02, 0.025, 0.03, 0.04, 0.05, 0.06, 0.08]:
    hit = (df["ret_high"] >= x)
    ret_mixed = np.where(hit.values, x, df["ret_close"].values)
    print(f"    +{x*100:5.1f}%  {hit.mean()*100:6.0f}%   {np.mean(ret_mixed)*100:+.2f}%"
          f"     {(ret_mixed>0).mean()*100:6.0f}%   {np.median(ret_mixed)*100:+.2f}%"
          f"   {(np.mean(ret_mixed)-base_mean)*100:+.2f}%")

hit5 = (df["ret_high"] >= 0.05)
df["ret_l5"]   = np.where(hit5, 0.05, df["ret_close"])
df["ret_half"] = np.where(hit5, 0.5 * 0.05 + 0.5 * df["ret_close"], df["ret_close"])


def equity(col):
    daily = df.groupby("date")[col].mean().sort_index()
    e = 1.0; peak = 1.0; maxdd = 0.0
    for r in daily:
        if pd.isna(r):
            continue
        e *= (1.0 + r) * (1.0 - C.EQUITY_TX_COST)
        if e > peak: peak = e
        dd = (peak - e) / peak if peak > 0 else 0
        if dd > maxdd: maxdd = dd
    years = (daily.index.max() - daily.index.min()).days / 365.25
    ann = (e ** (1.0 / years) - 1.0) if years > 0 and e > 0 else 0.0
    return {"mean": df[col].mean(), "median": df[col].median(), "win": (df[col] > 0).mean(),
            "total": e - 1.0, "ann": ann, "maxdd": maxdd, "n_days": len(daily), "years": years}

A = equity("ret_close"); B = equity("ret_l5"); C2 = equity("ret_half")

print("=" * 78)
print(f"  ④ 三口径净值（满仓均分当日信号·逐信号日复利·成本{C.EQUITY_TX_COST*100:.1f}%/轮）")
print("=" * 78)
print(f"  {'口径':28s} {'T+1均值':>8s} {'中位':>8s} {'胜率':>6s} {'累计':>12s} {'年化':>8s} {'回撤':>7s}")


def show(name, r):
    print(f"  {name:28s} {r['mean']*100:+7.2f}% {r['median']*100:+7.2f}% "
          f"{r['win']*100:5.0f}% {r['total']*100:+11.1f}% {r['ann']*100:+7.1f}% {r['maxdd']*100:6.1f}%")


show("A 收盘卖(现状)", A)
show("B 挂5%全卖(脉冲)", B)
show("C 5%走一半+剩收盘", C2)
print("-" * 78)
print(f"  起点 {df['date'].min().date()} -> {df['date'].max().date()}（约 {A['years']:.1f} 年，{A['n_days']} 个信号日）")
print(f"  触及+5%成交率: {hit5.mean()*100:.0f}%")
print(f"  总耗时 {time.time()-t0:.0f}s")
