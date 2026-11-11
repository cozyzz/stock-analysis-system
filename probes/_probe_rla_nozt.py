# -*- coding: utf-8 -*-
"""探针：反转低吸 · 剔除信号日涨停（含一字板）后收益对比。

质疑：很多收益可能来自连板/涨停票——尾盘封板买不进，一字板 100% 买不进。
剔除信号日收盘涨停（close[i]>=zt_price(prev,sym)）的信号，重算 A/B 口径净值+年度。
一字板子类：open[i]>=zt（开盘即涨停，全天封死）。
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
    c = g["close"].astype(float)
    o = g["open"].astype(float)
    h = g["high"].astype(float)
    prev = c.shift(1)
    sym_s = str(sym)
    zt = tdx.zt_price(prev, sym_s)          # 信号日涨停价序列
    c_v = c.values; o_v = o.values; h_v = h.values
    zt_v = zt.values if hasattr(zt, "values") else zt
    d_v = g["date"].values
    n = len(c_v)
    for i in idx:
        if i + 1 >= n or i < 1:
            continue
        buy = c_v[i]
        if not (np.isfinite(buy) and buy > 0):
            continue
        nc = c_v[i + 1]; nh = h_v[i + 1]
        if not (np.isfinite(nc) and np.isfinite(nh)):
            continue
        rc = nc / buy - 1.0
        rh = nh / buy - 1.0
        zt_i = zt_v[i]
        is_zt = bool(np.isfinite(zt_i) and c_v[i] >= zt_i - 1e-9)   # 信号日收盘涨停
        is_yzb = bool(np.isfinite(zt_i) and o_v[i] >= zt_i - 1e-9)  # 一字板(开盘即涨停)
        rows.append({"date": pd.Timestamp(d_v[i]), "ret_close": rc, "ret_high": rh,
                     "is_zt": is_zt, "is_yzb": is_yzb})

df = pd.DataFrame(rows)
df = df[df["ret_close"].notna()].copy()
hit5 = (df["ret_high"] >= 0.05)
df["ret_l5"] = np.where(hit5, 0.05, df["ret_close"])

n = len(df)
n_zt = int(df["is_zt"].sum())
n_yzb = int(df["is_yzb"].sum())
print(f"信号数 {n}，耗时 {time.time()-t0:.0f}s")
print(f"信号日收盘涨停 {n_zt}（{n_zt/n*100:.1f}%），其中一字板 {n_yzb}（{n_yzb/n*100:.1f}%）")
print(f"触及+5%成交率(全信号) {hit5.mean()*100:.0f}%")
# 涨停票 vs 非涨停票 次日冲高对比
zt_g = df[df["is_zt"]]; nozt_g = df[~df["is_zt"]]
print(f"  涨停信号 T+1均(收盘卖) {zt_g['ret_close'].mean()*100:+.2f}%  冲+5%率 {(zt_g['ret_high']>=0.05).mean()*100:.0f}%")
print(f"  非涨停信号 T+1均(收盘卖) {nozt_g['ret_close'].mean()*100:+.2f}%  冲+5%率 {(nozt_g['ret_high']>=0.05).mean()*100:.0f}%")
print()

df_nozt = df[~df["is_zt"]].copy()
print(f"剔除信号日涨停后剩余 {len(df_nozt)} 信号（{(1-len(df_nozt)/n)*100:.1f}% 被剔除）")


def equity(d, col):
    if not len(d):
        return {"mean": 0, "win": 0, "total": -1, "ann": 0, "maxdd": 0, "years": 0}
    daily = d.groupby("date")[col].mean().sort_index()
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
    return {"mean": d[col].mean(), "win": (d[col] > 0).mean(), "total": e - 1.0,
            "ann": ann, "maxdd": maxdd, "years": years}


def yearly(d, col):
    if not len(d):
        return pd.DataFrame()
    d2 = d.copy()
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


sets = [("全信号(对照)", df), ("剔除信号日涨停", df_nozt)]
print()
print("=" * 88)
print(f"  {'口径':14s} {'集合':16s} {'T+1均':>8s} {'胜率':>6s} {'累计':>12s} {'年化':>8s} {'回撤':>7s} {'信号':>7s}")
print("=" * 88)
res = {}
for col_name, col in [("A 收盘卖", "ret_close"), ("B 挂5%脉冲", "ret_l5")]:
    for set_name, d in sets:
        r = equity(d, col)
        res[(col_name, set_name)] = r
        print(f"  {col_name:14s} {set_name:16s} {r['mean']*100:+7.2f}% {r['win']*100:5.0f}% "
              f"{r['total']*100:+11.1f}% {r['ann']*100:+7.1f}% {r['maxdd']*100:6.1f}% {len(d):7d}")
    print("-" * 88)

# 剔除后 B 口径年度
yB_nozt = yearly(df_nozt, "ret_l5")
print()
print("=" * 78)
print(f"  B 挂5%脉冲 · 剔除信号日涨停 · 年度（每年独立，年初净值=1.0）")
print("=" * 78)
if len(yB_nozt):
    print(f"  {'年份':6s} {'信号':>6s} {'信号日':>8s} {'胜率':>6s} {'年收益':>10s} {'最大回撤':>10s}")
    print("-" * 78)
    best = yB_nozt.loc[yB_nozt["ret"].idxmax()]
    worst = yB_nozt.loc[yB_nozt["ret"].idxmin()]
    full = yB_nozt[yB_nozt["n_days"] >= 200]
    for _, row in yB_nozt.iterrows():
        mark = " *" if row["year"] == best["year"] else (" !" if row["year"] == worst["year"] else "  ")
        inc = "" if row["n_days"] >= 200 else " (不全)"
        print(f"  {int(row['year'])}  {int(row['n_sig']):6d} {int(row['n_days']):8d} "
              f"{row['win']*100:5.0f}% {row['ret']*100:+9.1f}% {row['maxdd']*100:9.1f}%{inc}{mark}")
    print("-" * 78)
    print(f"  * 最佳：{int(best['year'])} 年 {best['ret']*100:+.1f}%（回撤 {best['maxdd']*100:.1f}%）")
    print(f"  ! 最差：{int(worst['year'])} 年 {worst['ret']*100:+.1f}%（回撤 {worst['maxdd']*100:.1f}%）")
    if len(full):
        pos = (full["ret"] > 0).sum()
        print(f"  完整年(≥200信号日)盈利 {pos}/{len(full)}，完整年均收益 {full['ret'].mean()*100:+.1f}%")
else:
    print("  （无样本）")
print(f"\n总耗时 {time.time()-t0:.0f}s")
