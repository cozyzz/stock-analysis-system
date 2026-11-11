# -*- coding: utf-8 -*-
"""探针：隔日超短(next_day_short) 市值口径对比 8 年净值 + 年度收益/回撤。

原版 >100亿 vs 改版 40-150亿，均 open_next 卖（信号日收盘买→次日开盘卖）。
其余条件同 next_day_short.select：涨幅2-5%|量比1-5|换手5-10%|近10日涨停。
净值模型同 engine：满仓均分当日信号、逐信号日复利、cost=0.003/轮。
"""
import sys, os, time
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import numpy as np
import pandas as pd
import config as C
import datasource as ds
import tdx

t0 = time.time()
ohlcv = ds.load_ohlcv(verbose=False)
uni = ds.load_universe(verbose=False)
allowed, _ = ds.filter_pool(uni)
shares = ds.build_shares_map(uni)
pool_set = set(allowed)
ohlcv_pool = ohlcv[ohlcv["symbol"].isin(pool_set)].copy()
print(f"数据加载完成（{time.time()-t0:.0f}s），扫描隔日超短信号...")


def scan(mkt_lo, mkt_hi):
    """市值 [mkt_lo, mkt_hi) 元，返回 DataFrame(date, ret, sym)。ret=open_next/c-1。"""
    rows = []
    for sym, g in ohlcv_pool.groupby("symbol"):
        g = g.sort_values("date").reset_index(drop=True)
        _ts, _fs = shares.get(sym, (float("nan"), float("nan")))
        if not (np.isfinite(_ts) and np.isfinite(_fs) and _ts > 0 and _fs > 0):
            continue
        c = g["close"].astype(float)
        v = g["volume"].astype(float)
        o = g["open"].astype(float)
        ts = float(_ts)
        fs = float(_fs)
        prev = c.shift(1)
        chg = c / prev - 1.0
        vr = v / tdx.MA(v, 5)
        turnover = (v * 100.0) / fs * 100.0
        mktcap = ts * c
        sym_s = str(sym)
        zt = tdx.zt_price(prev, sym_s)
        have_zt = tdx.EXIST(c >= zt - 1e-9, 10)
        cond = (
            (chg >= 0.02) & (chg <= 0.05)
            & (vr > 1) & (vr < 5)
            & (turnover > 5) & (turnover < 10)
            & have_zt
            & (mktcap > mkt_lo) & (mktcap < mkt_hi)
        )
        mask = cond.fillna(False).astype(bool)
        idx = np.where(mask.values)[0]
        if len(idx) == 0:
            continue
        c_v = c.values; o_v = o.values; d_v = g["date"].values
        n = len(c_v)
        for i in idx:
            if i + 1 >= n:
                continue
            buy = c_v[i]
            no = o_v[i + 1]
            if not (np.isfinite(buy) and buy > 0 and np.isfinite(no) and no > 0):
                continue
            rows.append({"date": pd.Timestamp(d_v[i]), "ret": no / buy - 1.0, "sym": sym_s})
    return pd.DataFrame(rows)


def equity(df, col="ret"):
    if not len(df):
        return {"mean": 0, "median": 0, "win": 0, "total": -1, "ann": 0,
                "maxdd": 0, "n_days": 0, "years": 0, "n_sig": 0}
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
            "total": e - 1.0, "ann": ann, "maxdd": maxdd, "n_days": len(daily),
            "years": years, "n_sig": len(df)}


def yearly(df, col="ret"):
    """按年独立算：每年初净值=1.0，年内逐信号日复利，返回 DataFrame。"""
    if not len(df):
        return pd.DataFrame()
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


A = scan(1e10, 1e18)    # 原版 >100亿
rA = equity(A)
B = scan(4e9, 1.5e10)   # 改版 40-150亿
rB = equity(B)
yA = yearly(A)
yB = yearly(B)

print(f"信号数：原版>100亿 {len(A)}，改版40-150亿 {len(B)}，耗时 {time.time()-t0:.0f}s")
print()
print("=" * 86)
print(f"  {'市值口径':16s} {'T+1均':>8s} {'中位':>8s} {'胜率':>6s} {'累计':>10s} {'年化':>8s} {'回撤':>7s} {'信号':>6s}")
print("=" * 86)


def show(name, r):
    print(f"  {name:16s} {r['mean']*100:+7.2f}% {r['median']*100:+7.2f}% "
          f"{r['win']*100:5.0f}% {r['total']*100:+9.1f}% {r['ann']*100:+7.1f}% "
          f"{r['maxdd']*100:6.1f}% {r['n_sig']:6d}")


show("原版 >100亿", rA)
show("改版 40-150亿", rB)
print("-" * 86)
if len(A):
    print(f"  区间 {A['date'].min().date()} → {A['date'].max().date()}（约 {rA['years']:.1f}年）"
          f"  成本 {C.EQUITY_TX_COST*100:.1f}%/轮  open_next：收盘买→次日开盘卖")
d = rB['total'] - rA['total']
sign = "提升" if d > 0 else ("降低" if d < 0 else "持平")
print(f"  改版 vs 原版：累计 {rB['total']*100:+.1f}% vs {rA['total']*100:+.1f}%（{sign} {abs(d)*100:.1f}pct）")


def print_yearly(title, yd):
    if yd is None or not len(yd):
        print(f"\n{title}：（无样本）")
        return
    print()
    print("=" * 86)
    print(f"  {title}")
    print("=" * 86)
    print(f"  {'年份':6s} {'信号':>6s} {'信号日':>8s} {'胜率':>6s} {'年收益':>10s} {'最大回撤':>10s}")
    print("-" * 86)
    best = yd.loc[yd["ret"].idxmax()]
    worst = yd.loc[yd["ret"].idxmin()]
    full_years = yd[yd["n_days"] >= 200]  # 完整年粗判（≥200 信号日）
    for _, r in yd.iterrows():
        mark = " *" if r["year"] == best["year"] else ("  " if r["year"] != worst["year"] else " !")
        incomplete = "" if r["n_days"] >= 200 else " (不全)"
        print(f"  {int(r['year'])}  {int(r['n_sig']):6d} {int(r['n_days']):8d} "
              f"{r['win']*100:5.0f}% {r['ret']*100:+9.1f}% {r['maxdd']*100:9.1f}%{incomplete}{mark}")
    print("-" * 86)
    print(f"  * 收益最佳：{int(best['year'])} 年 {best['ret']*100:+.1f}%（回撤 {best['maxdd']*100:.1f}%）")
    print(f"  ! 收益最差：{int(worst['year'])} 年 {worst['ret']*100:+.1f}%（回撤 {worst['maxdd']*100:.1f}%）")
    if len(full_years):
        pos = (full_years["ret"] > 0).sum()
        print(f"  完整年中盈利 {pos}/{len(full_years)}")


print_yearly("原版 >100亿 · 年度收益/回撤（每年独立，年初净值=1.0）", yA)
print_yearly("改版 40-150亿 · 年度收益/回撤（每年独立，年初净值=1.0）", yB)
print(f"\n总耗时 {time.time()-t0:.0f}s")
