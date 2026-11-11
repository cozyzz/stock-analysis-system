# -*- coding: utf-8 -*-
"""探针：反转低吸 & 缠论底分型 · 三口径收益对比(最接近用户盘中买入实际)。

用户实际操作：盘中价格合适就买入(非尾盘)，能买进非一字板涨停票(封板前)，
只有一字板(开盘即涨停)真买不进。故最接近实际的口径=剔除一字板,保留其他涨停。

三口径：
  全信号(含所有涨停,理论)        —— 上界(假设所有涨停都能在收盘价买进)
  剔除一字板(用户盘中买入近似)    —— 最接近实际(只有一字板买不进)
  剔除所有涨停(尾盘买不进)        —— 下界(过于悲观,假设涨停全买不进)

买入价用收盘价 c[i](保守：用户盘中低吸成本可能更低→实际收益更高)。
A 收盘卖 / B 挂5%脉冲。净值模型同 engine：满仓均分、逐信号日复利、cost=0.003/轮。
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
print(f"数据加载（{time.time()-t0:.0f}s）")


def scan(strategy_name):
    st = strategies.get_strategy(strategy_name)
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
        c = g["close"].astype(float); o = g["open"].astype(float); h = g["high"].astype(float)
        prev = c.shift(1)
        sym_s = str(sym)
        zt = tdx.zt_price(prev, sym_s)
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
            is_zt = bool(np.isfinite(zt_i) and c_v[i] >= zt_i - 1e-9)
            is_yzb = bool(np.isfinite(zt_i) and o_v[i] >= zt_i - 1e-9)
            rows.append({"date": pd.Timestamp(d_v[i]), "ret_close": rc, "ret_high": rh,
                         "is_zt": is_zt, "is_yzb": is_yzb})
    df = pd.DataFrame(rows)
    df = df[df["ret_close"].notna()].copy()
    hit5 = (df["ret_high"] >= 0.05)
    df["ret_l5"] = np.where(hit5, 0.05, df["ret_close"])
    return df


def equity(d, col):
    if not len(d):
        return {"ann": 0, "maxdd": 0, "win": 0, "n": 0, "mean": 0}
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
    return {"ann": ann, "maxdd": maxdd, "win": (d[col] > 0).mean(), "n": len(d),
            "mean": d[col].mean()}


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


for name in ["reversal_low_absorb", "chanlun_bottom_fx"]:
    df = scan(name)
    n = len(df)
    n_zt = int(df["is_zt"].sum())
    n_yzb = int(df["is_yzb"].sum())
    df_noyzb = df[~df["is_yzb"]].copy()    # 剔除一字板(用户盘中买入近似)
    df_nozt = df[~df["is_zt"]].copy()      # 剔除所有涨停(尾盘买不进)
    print()
    print("=" * 100)
    print(f"  {name}：信号 {n}，涨停 {n_zt}({n_zt/n*100:.1f}%)，一字板 {n_yzb}({n_yzb/n*100:.1f}%)")
    print("=" * 100)
    print(f"  {'口径':14s} {'集合':26s} {'T+1均':>8s} {'胜率':>6s} {'年化':>8s} {'回撤':>7s} {'信号':>7s}")
    print("-" * 100)
    sets = [("全信号(理论上界)", df),
            ("剔除一字板(盘中买入近似)", df_noyzb),
            ("剔除所有涨停(尾盘买不进)", df_nozt)]
    for col_name, col in [("A 收盘卖", "ret_close"), ("B 挂5%脉冲", "ret_l5")]:
        for set_name, d in sets:
            r = equity(d, col)
            print(f"  {col_name:14s} {set_name:26s} {r['mean']*100:+7.2f}% {r['win']*100:5.0f}% "
                  f"{r['ann']*100:+7.1f}% {r['maxdd']*100:6.1f}% {len(d):7d}")
        print("-" * 100)
    yB = yearly(df_noyzb, "ret_l5")
    print(f"\n  B 挂5%脉冲 · 剔除一字板(盘中买入近似) · 年度（每年独立）：")
    if len(yB):
        print(f"  {'年份':6s} {'信号':>6s} {'信号日':>8s} {'胜率':>6s} {'年收益':>10s} {'最大回撤':>10s}")
        full = yB[yB["n_days"] >= 200]
        for _, row in yB.iterrows():
            inc = "" if row["n_days"] >= 200 else " (不全)"
            print(f"  {int(row['year'])}  {int(row['n_sig']):6d} {int(row['n_days']):8d} "
                  f"{row['win']*100:5.0f}% {row['ret']*100:+9.1f}% {row['maxdd']*100:9.1f}%{inc}")
        if len(full):
            pos = (full["ret"] > 0).sum()
            print(f"  完整年(≥200信号日)盈利 {pos}/{len(full)}，完整年均收益 {full['ret'].mean()*100:+.1f}%")
print(f"\n总耗时 {time.time()-t0:.0f}s")
