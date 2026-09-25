# -*- coding: utf-8 -*-
"""探针：通道突破 · 移动止盈 & ATR 动态止损变体（不改 engine，纯探针）。

基线（对照，上版 _probe_channel_stop.py）：+5%脉冲 + -3%硬止损
  唐奇安 +47.7%/67%  布林 +49.3%/69%  肯特纳 +124.5%/46.5%

变体A 移动止盈（让赢家跑，无+5%封顶）：
  peak[j] = max(entry, max(high[i+1..j]))；trail_line = peak×(1-trail)。
  首个 j>i 使 close[j] < trail_line → 当日收盘卖，ret=close/entry-1。
  trail=5%/8%（3%太紧≈固定-3%无移动意义）。一路涨到末尾=右删失（排除）。

变体B ATR动态止损 + +5%脉冲（止损自适应波动率）：
  stop = entry - atr_mult×ATR14[i]（入场日 ATR，固定）。
  逐日 j>i 先到先得，跳空按开盘：open>=1.05e→TP开盘；open<=stop→止损开盘；
  high>=1.05e→+5%；low<=stop→-am×ATR/entry。同日双触 TP 优先。
  atr_mult=1.5/2.0。

入场同通道突破（唐奇安20日高/布林上轨/肯特纳上轨首破，close[i]买，T+1 从 i+1 起）。
问：让赢家跑 / ATR 自适应止损，能否压回撤 + 提收益（vs 基线 -3% 硬止损）？
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
    return {"n": len(c), "c": c, "h": h, "l": l, "o": o, "atr": atr14, "dates": g["date"].values,
            "don_up": h.rolling(20).max().shift(1),
            "bb_up": ma20 + 2 * sd20,
            "kc_up": ema20 + 2 * atr14}


STRATS = [("don", "唐奇安", "don_up"), ("bb", "布林", "bb_up"), ("kc", "肯特纳", "kc_up")]
TRAILS = [0.05, 0.08]
ATR_MULTS = [1.5, 2.0]


def scan_all():
    keys_trail = [f"{sk}_trail{int(t*100)}" for sk, _, _ in STRATS for t in TRAILS]
    keys_atr = [f"{sk}_atr{int(a*10)}" for sk, _, _ in STRATS for a in ATR_MULTS]
    keys = keys_trail + keys_atr
    by = {k: [] for k in keys}; n_cens = {k: 0 for k in keys}
    for sym, g in ohlcv_pool.groupby("symbol"):
        ind = compute_indicators(g)
        n = ind["n"]
        if n < 60:
            continue
        c_arr = ind["c"].values; h_arr = ind["h"].values
        l_arr = ind["l"].values; o_arr = ind["o"].values
        atr_arr = ind["atr"].values; dates = ind["dates"]
        for sk, _name, up_key in STRATS:
            up = ind[up_key].values
            c_up = (c_arr > up) & np.isfinite(up)
            fresh = np.zeros(n, dtype=bool); fresh[1:] = c_up[1:] & ~c_up[:-1]
            idx = np.where(fresh)[0]
            if len(idx) == 0:
                continue
            for i in idx:
                if i + 1 >= n:
                    continue
                entry = c_arr[i]
                if not (np.isfinite(entry) and entry > 0):
                    continue
                h_a = h_arr[i + 1:]; c_a = c_arr[i + 1:]
                l_a = l_arr[i + 1:]; o_a = o_arr[i + 1:]
                m = len(c_a)
                # ---- 变体A：移动止盈（向量化）----
                peak = np.maximum.accumulate(np.concatenate(([entry], h_a)))[1:]  # peak[j]=max(entry,max h[0..j])
                for tr in TRAILS:
                    k = f"{sk}_trail{int(tr*100)}"
                    tl = peak * (1 - tr)
                    hit = c_a < tl
                    if not hit.any():
                        n_cens[k] += 1; continue
                    j = int(np.argmax(hit))
                    by[k].append({"date": pd.Timestamp(dates[i]), "hold": j + 1,
                                  "ret": c_a[j] / entry - 1.0})
                # ---- 变体B：ATR止损 + +5%脉冲（per-signal 循环）----
                atr_i = atr_arr[i]
                for am in ATR_MULTS:
                    k = f"{sk}_atr{int(am*10)}"
                    if not (np.isfinite(atr_i) and atr_i > 0):
                        n_cens[k] += 1; continue
                    stop = entry - am * atr_i
                    tp_lvl = 1.05 * entry
                    resolved = False
                    for j in range(m):
                        ov = o_a[j] / entry - 1
                        if ov >= 0.05:                                       # 跳空高开过+5%
                            by[k].append({"date": pd.Timestamp(dates[i]), "hold": j + 1, "ret": ov}); resolved = True; break
                        if ov <= (stop / entry - 1):                         # 跳空低开过止损
                            by[k].append({"date": pd.Timestamp(dates[i]), "hold": j + 1, "ret": ov}); resolved = True; break
                        if h_a[j] >= tp_lvl:                                  # 盘中触+5%
                            by[k].append({"date": pd.Timestamp(dates[i]), "hold": j + 1, "ret": 0.05}); resolved = True; break
                        if l_a[j] <= stop:                                    # 盘中触止损
                            by[k].append({"date": pd.Timestamp(dates[i]), "hold": j + 1,
                                          "ret": stop / entry - 1}); resolved = True; break
                    if not resolved:
                        n_cens[k] += 1
    return {k: pd.DataFrame(v) for k, v in by.items()}, n_cens


def equity(df):
    if len(df) == 0:
        return {"ann": 0, "maxdd": 0}
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
    return {"ann": ann, "maxdd": maxdd}


print("扫描中（3 策略 × {移动止盈5/8% + ATR1.5/2×} = 12 变体）...")
dfs, n_cens = scan_all()
print(f"扫描完成，耗时 {time.time()-t0:.0f}s\n")

print("=" * 96)
print("  变体A · 移动止盈（让赢家跑，无+5%封顶）")
print(f"  {'策略':10s} {'trail':>6s} {'信号':>7s} {'删失':>5s} {'均收':>7s} {'中收':>7s} "
      f"{'胜率':>5s} {'持仓均':>6s} {'年化':>7s} {'回撤':>6s}  vs 基线-3%硬止损")
print("-" * 96)
BASELINE = {"don": "+47.7%/67%", "bb": "+49.3%/69%", "kc": "+124.5%/46.5%"}
for sk, name, _ in STRATS:
    for tr in TRAILS:
        k = f"{sk}_trail{int(tr*100)}"
        df = dfs[k]
        if len(df) == 0:
            print(f"  {name:10s} {tr*100:5.0f}% 0 信号"); continue
        eq = equity(df)
        print(f"  {name:10s} {tr*100:5.0f}% {len(df):7d} {n_cens[k]:5d} {df['ret'].mean()*100:+7.2f}% "
              f"{df['ret'].median()*100:+7.2f}% {(df['ret']>0).mean()*100:4.0f}% {df['hold'].mean():6.1f} "
              f"{eq['ann']*100:+7.1f}% {eq['maxdd']*100:6.1f}%   基线 {BASELINE[sk]}")
print("=" * 96)
print("  变体B · ATR动态止损 + +5%脉冲（止损随波动率自适应）")
print(f"  {'策略':10s} {'ATR':>5s} {'信号':>7s} {'删失':>5s} {'+5%成交':>8s} {'均收':>7s} {'中收':>7s} "
      f"{'胜率':>5s} {'持仓均':>6s} {'年化':>7s} {'回撤':>6s}  vs 基线-3%硬止损")
print("-" * 96)
for sk, name, _ in STRATS:
    for am in ATR_MULTS:
        k = f"{sk}_atr{int(am*10)}"
        df = dfs[k]
        if len(df) == 0:
            print(f"  {name:10s} {am:4.1f}× 0 信号"); continue
        eq = equity(df)
        tp_rate = (df["ret"] >= 0.049).mean()  # +5%脉冲那批（含跳空>5%）
        print(f"  {name:10s} {am:4.1f}× {len(df):7d} {n_cens[k]:5d} {tp_rate*100:7.0f}% "
              f"{df['ret'].mean()*100:+7.2f}% {df['ret'].median()*100:+7.2f}% "
              f"{(df['ret']>0).mean()*100:4.0f}% {df['hold'].mean():6.1f} "
              f"{eq['ann']*100:+7.1f}% {eq['maxdd']*100:6.1f}%   基线 {BASELINE[sk]}")
print("=" * 96)
print("  移动止盈收分布（让赢家跑到底能吃多少波段）")
for sk, name, _ in STRATS:
    for tr in TRAILS:
        k = f"{sk}_trail{int(tr*100)}"
        df = dfs[k]
        if len(df) == 0:
            continue
        r = df["ret"]
        print(f"  {name:10s} {tr*100:4.0f}%  收益 P25 {r.quantile(0.25)*100:+6.2f}%  中位 {r.median()*100:+6.2f}%  "
              f"P75 {r.quantile(0.75)*100:+6.2f}%  >+10%:{(r>=0.10).mean()*100:.0f}%  >+20%:{(r>=0.20).mean()*100:.0f}%  "
              f"<-5%:{(r<=-0.05).mean()*100:.0f}%")
print("-" * 96)
dmin, dmax = ohlcv_pool["date"].min(), ohlcv_pool["date"].max()
yrs = (pd.Timestamp(dmax) - pd.Timestamp(dmin)).days / 365.25
print(f"  区间 {dmin} ~ {dmax}（约 {yrs:.1f} 年），cost {C.EQUITY_TX_COST*100:.1f}%/轮")
print(f"  基线 = +5%脉冲 + -3%硬止损（上版）：肯特纳 +124.5%/46.5% 为最强标杆")
print(f"  问：移动止盈/ATR止损 能否同时 提收益 + 压回撤（vs 基线）")
print(f"  总耗时 {time.time()-t0:.0f}s")
