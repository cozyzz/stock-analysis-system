# -*- coding: utf-8 -*-
"""探针：反转低吸 · 年度收益/回撤列表（A收盘卖 vs B挂5%脉冲）。

A 收盘卖(现状) : ret = c[i+1]/c[i]-1
B 挂5%脉冲全卖 : 成交吃 +5%，否则收盘卖   ret = 0.05 if h[i+1]/c[i]>=1.05 else A
净值模型同 engine：满仓均分当日信号、逐信号日复利、cost=0.003/轮。
每年独立：年初净值=1.0，年内逐信号日复利。
"""
import sys, os, time
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
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
print(f"数据加载（{time.time()-t0:.0f}s），扫描反转低吸信号...")

st = strategies.get_strategy("reversal_low_absorb")

rows = []
for sym, g in ohlcv_pool.groupby("symbol"):
    g = g.sort_values("date").reset_index(drop=True)
    _ts, _fs = shares.get(sym, (float("nan"), float("nan")))
    g["total_shares"] = float(_ts)
    g["float_shares"] = float(_fs)
    try:
        mask = st.select(g)
    except Exception:
        continue
    idx = np.where(mask.fillna(False).values)[0]
    if len(idx) == 0:
        continue
    c = g["close"].astype(float).values
    h = g["high"].astype(float).values
    dates = g["date"].values
    n = len(c)
    for i in idx:
        if i + 1 >= n:
            continue
        buy = c[i]
        if not (np.isfinite(buy) and buy > 0):
            continue
        nc = c[i + 1]; nh = h[i + 1]
        rc = nc / buy - 1.0
        rh = nh / buy - 1.0
        rows.append({"date": pd.Timestamp(dates[i]), "ret_close": rc, "ret_high": rh})

df = pd.DataFrame(rows)
df = df[df["ret_close"].notna()].copy()
hit5 = (df["ret_high"] >= 0.05)
df["ret_l5"] = np.where(hit5, 0.05, df["ret_close"])
print(f"信号数 {len(df)}，触及+5%成交率 {hit5.mean()*100:.0f}%，耗时 {time.time()-t0:.0f}s")


def equity(df, col):
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


def yearly(df, col):
    d2 = df.copy()
    d2["year"] = d2["date"].dt.year
    out = []
    for y, gd in d2.groupby("year"):
        daily = gd.groupby("date")[col].mean().sort_index()
        e = 1.0; peak = 1.0; maxdd = 0.0
        for r in daily:
            if pd.isna(r):
                continue
            e *= (1.0 + r) * (1.0 - C.EQUITY_TX_COST)
            if e > peak: peak = e
            dd = (peak - e) / peak if peak > 0 else 0
            if dd > maxdd: maxdd = dd
        out.append({"year": y, "ret": e - 1.0, "maxdd": maxdd, "n_sig": len(gd),
                    "n_days": len(daily), "win": (gd[col] > 0).mean()})
    return pd.DataFrame(out).sort_values("year").reset_index(drop=True)


A = equity(df, "ret_close")
B = equity(df, "ret_l5")
yA = yearly(df, "ret_close")
yB = yearly(df, "ret_l5")

print()
print("=" * 78)
print(f"  {'口径':22s} {'T+1均':>8s} {'中位':>8s} {'胜率':>6s} {'累计':>10s} {'年化':>8s} {'回撤':>7s}")
print("=" * 78)


def show(name, r):
    print(f"  {name:22s} {r['mean']*100:+7.2f}% {r['median']*100:+7.2f}% "
          f"{r['win']*100:5.0f}% {r['total']*100:+9.1f}% {r['ann']*100:+7.1f}% "
          f"{r['maxdd']*100:6.1f}%")


show("A 收盘卖(次日收盘)", A)
show("B 挂5%脉冲全卖", B)
print("-" * 78)
print(f"  区间 {df['date'].min().date()} → {df['date'].max().date()}（约 {A['years']:.1f}年）"
      f"  成本 {C.EQUITY_TX_COST*100:.1f}%/轮  信号 {len(df)}")


def print_yearly(title, yd, r):
    print()
    print("=" * 78)
    print(f"  {title}（每年独立，年初净值=1.0）")
    print("=" * 78)
    print(f"  {'年份':6s} {'信号':>6s} {'信号日':>8s} {'胜率':>6s} {'年收益':>10s} {'最大回撤':>10s}")
    print("-" * 78)
    best = yd.loc[yd["ret"].idxmax()]
    worst = yd.loc[yd["ret"].idxmin()]
    full = yd[yd["n_days"] >= 200]
    for _, row in yd.iterrows():
        mark = " *" if row["year"] == best["year"] else (" !" if row["year"] == worst["year"] else "  ")
        inc = "" if row["n_days"] >= 200 else " (不全)"
        print(f"  {int(row['year'])}  {int(row['n_sig']):6d} {int(row['n_days']):8d} "
              f"{row['win']*100:5.0f}% {row['ret']*100:+9.1f}% {row['maxdd']*100:9.1f}%{inc}{mark}")
    print("-" * 78)
    print(f"  * 收益最佳：{int(best['year'])} 年 {best['ret']*100:+.1f}%（回撤 {best['maxdd']*100:.1f}%）")
    print(f"  ! 收益最差：{int(worst['year'])} 年 {worst['ret']*100:+.1f}%（回撤 {worst['maxdd']*100:.1f}%）")
    if len(full):
        pos = (full["ret"] > 0).sum()
        print(f"  完整年(≥200信号日)盈利 {pos}/{len(full)}，完整年均收益 {full['ret'].mean()*100:+.1f}%")


print_yearly("A 收盘卖 · 年度", yA, A)
print_yearly("B 挂5%脉冲 · 年度", yB, B)
print(f"\n总耗时 {time.time()-t0:.0f}s")
