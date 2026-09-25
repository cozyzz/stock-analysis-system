# -*- coding: utf-8 -*-
"""探针：通道突破 + +5%脉冲 + 固定硬止损（-3%/-4%）。

入场同通道突破（唐奇安20日高/布林上轨/肯特纳上轨首破，close[i] 买）。
退出逐日（j=i+1..，T+1）先到先得，跳空按开盘价成交：
  ① 开盘 >= entry×1.05  → +5%限价在开盘成交（吃跳空溢价），ret=open/e-1
  ② 开盘 <= entry×(1-st) → 止损在开盘成交（吃跳空折损），ret=open/e-1
  ③ high >= entry×1.05  → 盘中触+5%，ret=+5%
  ④ low  <= entry×(1-st) → 盘中触止损，ret=-st
  同日③④都触（开盘在区间内）→ 计 +5% 先成（略乐观，少见）。
未触发到末尾 = 右删失（不计）。问：硬止损后赔率翻正了吗？三个都正？回撤收窄多少？
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
    c = g["close"].astype(float); o = g["open"].astype(float); pc = c.shift(1)
    ma20 = c.rolling(20).mean(); sd20 = c.rolling(20).std(ddof=0)
    ema20 = c.ewm(span=20, adjust=False).mean()
    tr = pd.concat([(h - l), (h - pc).abs(), (l - pc).abs()], axis=1).max(axis=1)
    atr14 = tr.rolling(14).mean()
    return {"n": len(c), "c": c, "h": h, "l": l, "o": o, "dates": g["date"].values,
            "don_up": h.rolling(20).max().shift(1),
            "bb_up": ma20 + 2 * sd20,
            "kc_up": ema20 + 2 * atr14}


STRATS = [("don", "唐奇安", "don_up"), ("bb", "布林", "bb_up"), ("kc", "肯特纳", "kc_up")]
STOPS = [0.03, 0.04]


def scan_all():
    keys = [f"{sk}_{int(st*100)}" for sk, _, _ in STRATS for st in STOPS]
    by = {k: [] for k in keys}; n_cens = {k: 0 for k in keys}
    for sym, g in ohlcv_pool.groupby("symbol"):
        ind = compute_indicators(g)
        n = ind["n"]
        if n < 60:
            continue
        c_arr = ind["c"].values; h_arr = ind["h"].values
        l_arr = ind["l"].values; o_arr = ind["o"].values; dates = ind["dates"]
        for sk, _name, up_key in STRATS:
            up = ind[up_key].values
            c_up = (c_arr > up) & np.isfinite(up)
            fresh = np.zeros(n, dtype=bool); fresh[1:] = c_up[1:] & ~c_up[:-1]
            idx = np.where(fresh)[0]
            if len(idx) == 0:
                continue
            for st in STOPS:
                k = f"{sk}_{int(st*100)}"
                for i in idx:
                    if i + 1 >= n:
                        continue
                    entry = c_arr[i]
                    if not (np.isfinite(entry) and entry > 0):
                        continue
                    tp_lvl = 1.05 * entry; stop_lvl = (1 - st) * entry
                    resolved = False
                    for j in range(i + 1, n):
                        ov = o_arr[j] / entry - 1
                        if ov >= 0.05:                                   # 跳空高开过+5%
                            by[k].append({"date": pd.Timestamp(dates[i]), "hold": j - i,
                                          "ret": ov, "tp": True}); resolved = True; break
                        if ov <= -st:                                     # 跳空低开过止损
                            by[k].append({"date": pd.Timestamp(dates[i]), "hold": j - i,
                                          "ret": ov, "tp": False}); resolved = True; break
                        if h_arr[j] >= tp_lvl:                            # 盘中触+5%
                            by[k].append({"date": pd.Timestamp(dates[i]), "hold": j - i,
                                          "ret": 0.05, "tp": True}); resolved = True; break
                        if l_arr[j] <= stop_lvl:                          # 盘中触止损
                            by[k].append({"date": pd.Timestamp(dates[i]), "hold": j - i,
                                          "ret": -st, "tp": False}); resolved = True; break
                    if not resolved:
                        n_cens[k] += 1
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
    return {"ann": ann, "maxdd": maxdd, "n_days": len(daily)}


print("扫描中（3 策略 × 2 止损 = 6 变体）...")
dfs, n_cens = scan_all()
print(f"扫描完成，耗时 {time.time()-t0:.0f}s\n")

print("=" * 104)
print(f"  {'策略':14s} {'止损':>5s} {'信号':>7s} {'删失':>5s} {'+5%成交':>8s} {'均收':>7s} "
      f"{'中收':>7s} {'胜率':>5s} {'持仓均':>6s} {'年化':>7s} {'回撤':>6s}")
print("=" * 104)
for sk, name, _ in STRATS:
    for st in STOPS:
        k = f"{sk}_{int(st*100)}"
        df = dfs[k]
        if len(df) == 0:
            print(f"  {name:14s} -{st*100:.0f}% 0 信号"); continue
        eq = equity(df)
        tp_rate = df["tp"].mean()
        ret_m = df["ret"].mean(); ret_md = df["ret"].median()
        win = (df["ret"] > 0).mean()
        hold_m = df["hold"].mean()
        print(f"  {name:14s} -{st*100:4.0f}% {len(df):7d} {n_cens[k]:5d} {tp_rate*100:7.0f}% "
              f"{ret_m*100:+7.2f}% {ret_md*100:+7.2f}% {win*100:4.0f}% {hold_m:6.1f} "
              f"{eq['ann']*100:+7.1f}% {eq['maxdd']*100:6.1f}%")
print("=" * 104)

print("  分解：+5%走人 vs 硬止损")
print(f"  {'策略':14s} {'止损':>5s} | {'+5%成交':>7s} {'均':>6s} | {'止损':>6s} {'均':>6s} {'中':>6s}")
for sk, name, _ in STRATS:
    for st in STOPS:
        k = f"{sk}_{int(st*100)}"
        df = dfs[k]
        if len(df) == 0:
            continue
        tp = df[df["tp"]]; sl = df[~df["tp"]]
        print(f"  {name:14s} -{st*100:4.0f}% | {len(tp):7d} {tp['ret'].mean()*100:+5.2f}% "
              f"| {len(sl):6d} {sl['ret'].mean()*100:+5.2f}% {sl['ret'].median()*100:+5.2f}%")
print("-" * 104)
dmin, dmax = ohlcv_pool["date"].min(), ohlcv_pool["date"].max()
yrs = (pd.Timestamp(dmax) - pd.Timestamp(dmin)).days / 365.25
print(f"  区间 {dmin} ~ {dmax}（约 {yrs:.1f} 年），cost {C.EQUITY_TX_COST*100:.1f}%/轮")
print(f"  对照：+5%脉冲+破通道兜底 → 唐奇安-28.5%/布林-15.5%/肯特纳+10.4%（回撤91~99%）")
print(f"  本版把兜底换成 -3%/-4% 硬止损，看赔率与回撤改善")
print(f"  总耗时 {time.time()-t0:.0f}s")
