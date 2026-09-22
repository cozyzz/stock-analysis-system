# -*- coding: utf-8 -*-
"""探针：反转低吸·挂5%脉冲卖出 三口径 8 年完整净值对比。

A 收盘卖(现状)   : ret = c[i+1]/c[i]-1
B 挂5%全卖       : 成交吃 +5%，否则收盘卖   ret = 0.05 if h[i+1]/c[i]>=1.05 else A
C 5%走一半剩收盘 : 成交一半吃5%一半收盘，否则收盘卖（用户构想·T+1简化）
净值模型同 engine._equity_curve：满仓均分当日信号、逐信号日复利、cost=0.003/轮。
"""
import sys, os, time
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import numpy as np
import pandas as pd
import config as C
import datasource as ds
import strategies

t0 = time.time()
ohlcv = ds.load_ohlcv(verbose=False)
uni = ds.load_universe(verbose=False)
allowed, _ = ds.filter_pool(uni)
shares = ds.build_shares_map(uni)
pool_set = set(allowed)
ohlcv_pool = ohlcv[ohlcv["symbol"].isin(pool_set)].copy()
print(f"数据加载完成（{time.time()-t0:.0f}s），扫描反转低吸信号...")

st = strategies.get_strategy("reversal_low_absorb")

# 收信号：(sig_date, ret_close, ret_high, is_limit_up_next)
import tdx
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
    prev = pd.Series(c).shift(1).values
    zt = tdx.zt_price(pd.Series(prev), str(sym)).values if hasattr(tdx, "zt_price") else None
    for i in idx:
        if i + 1 >= n:
            continue
        buy = c[i]
        if not (np.isfinite(buy) and buy > 0):
            continue
        nc = c[i+1]; nh = h[i+1]
        rc = nc / buy - 1.0
        rh = nh / buy - 1.0
        # 次日是否封涨停（收盘达涨停价）
        zt_next = zt[i+1] if zt is not None else None
        is_zt = bool(zt_next is not None and np.isfinite(zt_next) and nc >= zt_next - 1e-9)
        rows.append({
            "date": pd.Timestamp(dates[i]),
            "ret_close": rc,
            "ret_high": rh,
            "is_zt": is_zt,
        })

df = pd.DataFrame(rows)
df = df[df["ret_close"].notna()].copy()
hit5 = (df["ret_high"] >= 0.05)
df["ret_l5"]   = np.where(hit5, 0.05, df["ret_close"])
df["ret_half"] = np.where(hit5, 0.5 * 0.05 + 0.5 * df["ret_close"], df["ret_close"])
print(f"信号数 {len(df)}（封涨停次日 {df['is_zt'].sum()}），耗时 {time.time()-t0:.0f}s")
print(f"触及+5%成交率: {hit5.mean()*100:.0f}%")
print()

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
    return {
        "mean": df[col].mean(),
        "median": df[col].median(),
        "win": (df[col] > 0).mean(),
        "total": e - 1.0,
        "ann": ann,
        "maxdd": maxdd,
        "n_days": len(daily),
        "years": years,
    }

A = equity("ret_close")
B = equity("ret_l5")
C2 = equity("ret_half")

print("=" * 78)
print(f"  {'口径':28s} {'T+1均值':>8s} {'中位':>8s} {'胜率':>6s} {'8年累计':>12s} {'年化':>8s} {'回撤':>7s}")
print("=" * 78)
def show(name, r):
    print(f"  {name:28s} {r['mean']*100:+7.2f}% {r['median']*100:+7.2f}% "
          f"{r['win']*100:5.0f}% {r['total']*100:+11.1f}% {r['ann']*100:+7.1f}% {r['maxdd']*100:6.1f}%")
show("A 收盘卖(现状)", A)
show("B 挂5%全卖(脉冲)", B)
show("C 5%走一半+剩收盘", C2)
print("-" * 78)
print(f"  值起点 {df['date'].min().date()} → {df['date'].max().date()}（约 {A['years']:.1f} 年，{A['n_days']} 个信号日）")
print(f"  成本 {C.EQUITY_TX_COST*100:.1f}%/轮")
print()
print("  解读：")
def cmp(name, r, base):
    d = r['total'] - base['total']
    sign = "提升" if d > 0 else ("降低" if d < 0 else "持平")
    pct = d / max(abs(base['total']), 1e-9) * 100
    print(f"  · {name}：累计 {r['total']*100:+.0f}% vs {base['total']*100:+.0f}%（{sign} {abs(pct):.0f}%）")
cmp("B 挂5%脉冲全卖 vs A", B, A)
cmp("C 5%走一半剩收盘 vs A", C2, A)
print(f"  总耗时 {time.time()-t0:.0f}s")
