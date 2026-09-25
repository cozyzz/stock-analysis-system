# -*- coding: utf-8 -*-
"""探针：通道突破三策略 · 11 年回测（唐奇安/布林/肯特纳）· 单遍扫描。

唐奇安 Donchian：前20日最高突破入场；退出前10日低(海龟默认)+变体前20日低(慢出)
布林   Bollinger：MA20 ± 2σ(ddof=0)，收盘破上轨入场；退出中轨(MA20)+变体下轨
肯特纳 Keltner ：EMA20 ± 2×ATR14，收盘破上轨入场；退出中轨(EMA20)+变体下轨

入场：信号日 i 收盘破上轨且昨日未破（首破），close[i] 买入。
退出：首个 j>i 使 close[j] < exit_level[j]；持仓期收益=close[j]/close[i]-1。
同时记 T+1 收益（close[i+1]/close[i]-1）看是否冲高回落（对照 next_day_short -1.05%）。
同池（filter_pool 剔 ST/创/科/北），equity=满仓均分当日信号·逐信号日复利·cost 0.003。
注：equity 按入场日记复利，忽略多日持仓重叠（系统T+1口径，仅作可比标杆）；
     判 viability 主要看持仓期收益/胜率/假突破率。
"""
import sys, os, time
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import numpy as np
import pandas as pd
import config as C
import datasource as ds

t0 = time.time()
ohlcv = ds.load_ohlcv(verbose=False)
uni = ds.load_universe(verbose=False)
allowed, _ = ds.filter_pool(uni)
pool_set = set(allowed)
ohlcv_pool = ohlcv[ohlcv["symbol"].isin(pool_set)].copy()
print(f"数据加载完成（{time.time()-t0:.0f}s），池 {len(pool_set)} 只\n")


def compute_indicators(g):
    g = g.sort_values("date").reset_index(drop=True)
    h = g["high"].astype(float); l = g["low"].astype(float)
    c = g["close"].astype(float); pc = c.shift(1)
    ma20 = c.rolling(20).mean(); sd20 = c.rolling(20).std(ddof=0)
    ema20 = c.ewm(span=20, adjust=False).mean()
    tr = pd.concat([(h - l), (h - pc).abs(), (l - pc).abs()], axis=1).max(axis=1)
    atr14 = tr.rolling(14).mean()
    return {
        "n": len(c), "c": c, "h": h, "dates": g["date"].values,
        "don_up": h.rolling(20).max().shift(1),     # 前20日最高（不含今日，突破才可能触发）
        "don_dn10": l.rolling(10).min().shift(1),
        "don_dn20": l.rolling(20).min().shift(1),
        "bb_up": ma20 + 2 * sd20, "bb_mid": ma20, "bb_low": ma20 - 2 * sd20,
        "kc_up": ema20 + 2 * atr14, "kc_mid": ema20, "kc_low": ema20 - 2 * atr14,
    }


KINDS = [
    ("donchian10", "唐奇安 20入/10出", "don_up", "don_dn10"),
    ("donchian20", "唐奇安 20入/20出", "don_up", "don_dn20"),
    ("bb_mid",     "布林 出中轨",      "bb_up",  "bb_mid"),
    ("bb_lower",   "布林 出下轨",      "bb_up",  "bb_low"),
    ("kc_mid",     "肯特纳 出中轨",    "kc_up",  "kc_mid"),
    ("kc_lower",   "肯特纳 出下轨",    "kc_up",  "kc_low"),
]


def scan_all():
    """单遍：每股算一次指标，对6个kind各自求首破信号+首个退出。"""
    by_kind = {k: [] for k, _, _, _ in KINDS}
    n_cens = {k: 0 for k, _, _, _ in KINDS}
    n_syms = 0
    for sym, g in ohlcv_pool.groupby("symbol"):
        n_syms += 1
        ind = compute_indicators(g)
        n = ind["n"]
        if n < 60:
            continue
        c_arr = ind["c"].values
        h_arr = ind["h"].values
        dates = ind["dates"]
        for key, _name, up_key, dn_key in KINDS:
            up = ind[up_key].values
            dn = ind[dn_key].values
            c_up = (c_arr > up) & np.isfinite(up)        # 今日收>上轨（NaN区不触发）
            fresh = np.zeros(n, dtype=bool)
            fresh[1:] = c_up[1:] & ~c_up[:-1]            # 首破：今True且昨False
            idx = np.where(fresh)[0]
            for i in idx:
                if i + 1 >= n:
                    continue
                entry = c_arr[i]
                if not (np.isfinite(entry) and entry > 0):
                    continue
                fc = c_arr[i + 1:]
                fd = dn[i + 1:]
                mask = (fc < fd) & np.isfinite(fd)
                if not mask.any():
                    n_cens[key] += 1                     # 右删失：未触发退出
                    continue
                k = int(np.argmax(mask))
                exit_j = i + 1 + k
                ret = c_arr[exit_j] / entry - 1.0
                t1 = c_arr[i + 1] / entry - 1.0
                seg = h_arr[i + 1:exit_j + 1]
                peak = (seg.max() / entry - 1.0) if len(seg) else 0.0
                by_kind[key].append({
                    "date": pd.Timestamp(dates[i]), "hold": exit_j - i,
                    "ret": ret, "t1": t1, "peak": peak,
                })
    dfs = {k: pd.DataFrame(v) for k, v in by_kind.items()}
    return dfs, n_cens, n_syms


def equity(df, col="ret"):
    daily = df.groupby("date")[col].mean().sort_index()
    e = 1.0; peak = 1.0; maxdd = 0.0
    for r in daily:
        if pd.isna(r):
            continue
        e *= (1.0 + r) * (1.0 - C.EQUITY_TX_COST)
        if e > peak:
            peak = e
        dd = (peak - e) / peak if peak > 0 else 0
        if dd > maxdd:
            maxdd = dd
    years = (daily.index.max() - daily.index.min()).days / 365.25
    ann = (e ** (1.0 / years) - 1.0) if years > 0 and e > 0 else 0.0
    return {"total": e - 1.0, "ann": ann, "maxdd": maxdd, "years": years, "n_days": len(daily)}


print(f"扫描中（{len(KINDS)} 策略 × 池）...")
dfs, n_cens, n_syms = scan_all()
print(f"扫描完成：{n_syms} 只，耗时 {time.time()-t0:.0f}s\n")

print("=" * 100)
print(f"  {'策略':22s} {'信号':>7s} {'删失':>5s} {'持仓均':>6s} {'持仓中':>6s} "
      f"{'T+1均':>7s} {'T+1中':>7s} {'持仓均收':>8s} {'持仓中收':>8s} {'胜率':>5s} "
      f"{'假突破':>7s} {'年化':>7s} {'回撤':>6s}")
print("=" * 100)

results = {}
for key, name, _, _ in KINDS:
    df = dfs[key]
    if len(df) == 0:
        print(f"  {name:22s} 0 信号")
        continue
    eq = equity(df)
    hold_m = df["hold"].mean(); hold_md = df["hold"].median()
    t1_m = df["t1"].mean(); t1_md = df["t1"].median()
    ret_m = df["ret"].mean(); ret_md = df["ret"].median()
    win = (df["ret"] > 0).mean()
    fake = (df["hold"] <= 5).mean()
    results[key] = df
    print(f"  {name:22s} {len(df):7d} {n_cens[key]:5d} {hold_m:6.1f} {hold_md:6.1f} "
          f"{t1_m*100:+7.2f}% {t1_md*100:+7.2f}% {ret_m*100:+8.2f}% {ret_md*100:+8.2f}% "
          f"{win*100:4.0f}% {fake*100:6.0f}% {eq['ann']*100:+7.1f}% {eq['maxdd']*100:6.1f}%")

print("=" * 100)
print("  持仓期盘中峰值(peak)：突破后能跑到多高 —— 看趋势有没有得骑")
for key, name, _, _ in KINDS:
    if key not in results:
        continue
    pk = results[key]["peak"]
    print(f"  {name:22s} 均值{pk.mean()*100:+7.2f}%  中位{pk.median()*100:+7.2f}%  "
          f"冲过+5%:{(pk>=0.05).mean()*100:.0f}%  冲过+10%:{(pk>=0.10).mean()*100:.0f}%")
print("-" * 100)
dmin, dmax = ohlcv_pool["date"].min(), ohlcv_pool["date"].max()
yrs = (pd.Timestamp(dmax) - pd.Timestamp(dmin)).days / 365.25
print(f"  区间 {dmin} ~ {dmax}（约 {yrs:.1f} 年）")
print(f"  对照：next_day_short 次日收盘中位 -1.05%（冲高回落）；T+1均/中列看通道突破是否同病")
print(f"  总耗时 {time.time()-t0:.0f}s")
