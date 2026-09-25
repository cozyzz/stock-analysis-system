# -*- coding: utf-8 -*-
"""探针：通道突破 + 挂+5%脉冲退出（粗暴版）。

入场同通道突破（唐奇安20日高/布林上轨/肯特纳上轨首破，close[i] 买）。
退出 = 先到先得：
  ① 任一日 high 触及 entry×1.05 → +5% 走人（限价成交，ret=+5%；保守不计跳空溢价）；
  ② 否则破通道兜底止损（唐奇安10日低/布林中轨/肯特纳中轨 收盘破）。
A 股 T+1：最早次日（i+1）可卖，循环从 i+1 起。
问：+5% 成交的胜率多高？年化多少？兜底那批亏多少？
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
        "don_up": h.rolling(20).max().shift(1), "don_fb": l.rolling(10).min().shift(1),
        "bb_up": ma20 + 2 * sd20, "bb_fb": ma20,
        "kc_up": ema20 + 2 * atr14, "kc_fb": ema20,
    }


VARIANTS = [
    ("don5", "唐奇安+5%脉冲", "don_up", "don_fb"),
    ("bb5",  "布林+5%脉冲",   "bb_up",  "bb_fb"),
    ("kc5",  "肯特纳+5%脉冲",  "kc_up",  "kc_fb"),
]


def scan_all():
    by = {k: [] for k, _, _, _ in VARIANTS}
    n_cens = {k: 0 for k, _, _, _ in VARIANTS}
    for sym, g in ohlcv_pool.groupby("symbol"):
        ind = compute_indicators(g)
        n = ind["n"]
        if n < 60:
            continue
        c_arr = ind["c"].values; h_arr = ind["h"].values; dates = ind["dates"]
        for key, _n, up_key, fb_key in VARIANTS:
            up = ind[up_key].values; fb = ind[fb_key].values
            c_up = (c_arr > up) & np.isfinite(up)
            fresh = np.zeros(n, dtype=bool); fresh[1:] = c_up[1:] & ~c_up[:-1]
            idx = np.where(fresh)[0]
            for i in idx:
                if i + 1 >= n:
                    continue
                entry = c_arr[i]
                if not (np.isfinite(entry) and entry > 0):
                    continue
                tgt = 1.05 * entry
                fh = h_arr[i + 1:]; fc = c_arr[i + 1:]; ffb = fb[i + 1:]
                tp = (fh >= tgt) & np.isfinite(fh)          # 触及+5%
                stop = (fc < ffb) & np.isfinite(ffb)        # 破通道
                combined = tp | stop
                if not combined.any():
                    n_cens[key] += 1
                    continue
                k = int(np.argmax(combined))
                tp_hit = bool(tp[k])
                # 同日两者都触发：+5% 限价盘中先成交（先于收盘破通道）→ TP 优先
                ret = 0.05 if tp_hit else (fc[k] / entry - 1.0)
                by[key].append({"date": pd.Timestamp(dates[i]), "hold": k + 1,
                                "ret": ret, "tp": tp_hit})
    return {k: pd.DataFrame(v) for k, v in by.items()}, n_cens


def equity(df):
    daily = df.groupby("date")["ret"].mean().sort_index()
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
    yrs = (daily.index.max() - daily.index.min()).days / 365.25
    ann = (e ** (1.0 / yrs) - 1.0) if yrs > 0 and e > 0 else 0.0
    return {"total": e - 1.0, "ann": ann, "maxdd": maxdd, "n_days": len(daily)}


print("扫描中...")
dfs, n_cens = scan_all()
print(f"扫描完成，耗时 {time.time()-t0:.0f}s\n")

print("=" * 96)
print(f"  {'策略':18s} {'信号':>7s} {'删失':>5s} {'+5%成交率':>9s} {'均收':>7s} {'中收':>7s} {'胜率':>5s} "
      f"{'持仓均':>6s} {'年化':>7s} {'回撤':>6s}")
print("=" * 96)
for key, name, _, _ in VARIANTS:
    df = dfs[key]
    if len(df) == 0:
        print(f"  {name:18s} 0 信号")
        continue
    eq = equity(df)
    tp_rate = df["tp"].mean()
    ret_m = df["ret"].mean(); ret_md = df["ret"].median()
    win = (df["ret"] > 0).mean()
    hold_m = df["hold"].mean()
    print(f"  {name:18s} {len(df):7d} {n_cens[key]:5d} {tp_rate*100:8.0f}% "
          f"{ret_m*100:+7.2f}% {ret_md*100:+7.2f}% {win*100:4.0f}% {hold_m:6.1f} "
          f"{eq['ann']*100:+7.1f}% {eq['maxdd']*100:6.1f}%")
print("=" * 96)

print("  分解：+5%走人 vs 破通道兜底")
print(f"  {'策略':18s} {'+5%成交':>8s} {'均收':>7s} | {'兜底':>6s} {'均收':>7s} {'中收':>7s}")
for key, name, _, _ in VARIANTS:
    df = dfs[key]
    if len(df) == 0:
        continue
    tp = df[df["tp"]]; st = df[~df["tp"]]
    line = f"  {name:18s} {len(tp):8d} {tp['ret'].mean()*100:+6.2f}%"
    line += f" | {len(st):6d} {st['ret'].mean()*100:+6.2f}% {st['ret'].median()*100:+6.2f}%"
    print(line)
print("-" * 96)
dmin, dmax = ohlcv_pool["date"].min(), ohlcv_pool["date"].max()
yrs = (pd.Timestamp(dmax) - pd.Timestamp(dmin)).days / 365.25
print(f"  区间 {dmin} ~ {dmax}（约 {yrs:.1f} 年），cost {C.EQUITY_TX_COST*100:.1f}%/轮")
print(f"  对照：纯通道退出年化 -70.9%~-96.0%（唐奇安20/10 持仓中收 -4.28%）")
print(f"  总耗时 {time.time()-t0:.0f}s")
