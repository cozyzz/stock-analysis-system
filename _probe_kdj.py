# -*- coding: utf-8 -*-
"""探针：KDJ 超卖买入 + 超买卖出（经典）+ 10日涨停 + 市值50亿。

选股（信号日 i 当日须满足）：
  1. KDJ(9,3,3) J < 20（超卖）且昨 J>=20（首次进入超卖，去重——避免连续超卖重复出信号）
  2. 10 日内曾涨停（zt_price 按板别，同缠论/反转低吸口径）
  3. 总市值 ≥ 50 亿（总股本×收盘，同缠论）

退出：首个 j>i 使 J[j] > 100 → 当日收盘卖，ret=close[j]/close[i]-1（T+1，j>=i+1）。
右删失：未到 J>100 到末尾，跳过（计删失率——高=很多票没冲到超买、套住）。
同池同 equity 口径（满仓均分当日信号、逐信号日复利、cost 0.3%）。
问：经典 KDJ 超卖超买卖法在 A 股 11 年收益如何？（对照：反转低吸 112.6%/18.8%、
   缠论 73.4%/34.1%[T+1超短]；肯特纳-3%硬止损 124.5%/46.5%[多日持仓]）
"""
import sys, os, time
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import numpy as np
import pandas as pd
import config as C
import datasource as ds
import tdx

t0 = time.time()
ohlcv = ds.load_ohlcv(verbose=False)
uni = ds.load_universe(verbose=False)
allowed, _ = ds.filter_pool(uni)
pool_set = set(allowed)
ohlcv_pool = ohlcv[ohlcv["symbol"].isin(pool_set)].copy()
shares_map = ds.build_shares_map(uni)
ohlcv_pool["total_shares"] = ohlcv_pool["symbol"].map(
    lambda s: (shares_map.get(s, (np.nan, np.nan))[0]))
print(f"数据加载完成（{time.time()-t0:.0f}s），池 {len(pool_set)} 只\n")


def compute_kdj(g):
    g = g.sort_values("date").reset_index(drop=True).copy()
    c = g["close"].astype(float); h = g["high"].astype(float); l = g["low"].astype(float)
    llv9 = tdx.LLV(l, 9); hhv9 = tdx.HHV(h, 9)
    rsv = (c - llv9) / (hhv9 - llv9).replace(0, np.nan) * 100.0
    k = tdx.sma_wilder(rsv, 3)          # SMA(RSV,3,1)，同反转低吸
    d = tdx.sma_wilder(k, 3)
    g["j"] = 3 * k - 2 * d
    return g


def scan_all():
    rows = []; n_cens = 0
    for sym, g in ohlcv_pool.groupby("symbol"):
        g = compute_kdj(g)
        n = len(g)
        if n < 30:
            continue
        c = g["close"].astype(float); h = g["high"].astype(float)
        j_s = g["j"]
        dates = g["date"].values
        ts = g["total_shares"].iloc[0]
        if not (np.isfinite(ts) and ts > 0):
            continue
        prev_c = c.shift(1)
        zt = tdx.zt_price(prev_c, str(sym))
        have_zt = tdx.EXIST(c >= zt - 1e-9, 10)
        mkt_ok = (ts * c) >= 5e9                    # 总市值≥50亿
        j_prev = j_s.shift(1)
        fresh = (j_s < 20) & (j_prev >= 20)         # 首次进入超卖
        sig = (fresh & have_zt & mkt_ok).fillna(False)
        idx = np.where(sig.values)[0]
        if len(idx) == 0:
            continue
        c_arr = c.values; h_arr = h.values; j_arr = j_s.values
        for i in idx:
            if i + 1 >= n:
                continue
            entry = c_arr[i]
            if not (np.isfinite(entry) and entry > 0):
                continue
            after = j_arr[i + 1:]
            over = np.where(after > 100)[0]        # 首个 J>100
            if len(over) == 0:
                n_cens += 1
                continue
            k = over[0]
            exit_j = i + 1 + k
            ret = c_arr[exit_j] / entry - 1.0
            t1 = c_arr[i + 1] / entry - 1.0
            seg = h_arr[i + 1:exit_j + 1]
            peak = (seg.max() / entry - 1.0) if len(seg) else 0.0
            rows.append({"date": pd.Timestamp(dates[i]), "hold": exit_j - i,
                         "ret": ret, "t1": t1, "peak": peak})
    return pd.DataFrame(rows), n_cens


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


print("扫描中...")
df, n_cens = scan_all()
print(f"扫描完成，耗时 {time.time()-t0:.0f}s\n")

if len(df) == 0:
    print("无信号"); sys.exit(0)

eq = equity(df)
n_sig = len(df); n_tot = n_sig + n_cens
trig_rate = n_sig / n_tot if n_tot else 0      # J>100 触发率

print("=" * 92)
print(f"  KDJ<20 买 / J>100 卖 + 10日涨停 + 市值≥50亿（11年 {df['date'].min().date()} ~ {df['date'].max().date()}）")
print("=" * 92)
print(f"  信号 {n_sig}  删失 {n_cens}  J>100 触发率 {trig_rate*100:.0f}%")
print(f"  持仓均 {df['hold'].mean():.1f}天  持仓中 {df['hold'].median():.0f}天  "
      f"T+1均 {df['t1'].mean()*100:+.2f}%  T+1中 {df['t1'].median()*100:+.2f}%")
print(f"  均收 {df['ret'].mean()*100:+.2f}%  中收 {df['ret'].median()*100:+.2f}%  "
      f"胜率 {(df['ret']>0).mean()*100:.0f}%")
print(f"  年化 {eq['ann']*100:+.1f}%  回撤 {eq['maxdd']*100:.1f}%  信号日 {eq['n_days']}")
print("-" * 92)
print("  持仓期盘中峰值（突破后能跑到多高）")
pk = df["peak"]
print(f"  peak 均 {pk.mean()*100:+.2f}%  中 {pk.median()*100:+.2f}%  "
      f"冲过+5%:{(pk>=0.05).mean()*100:.0f}%  冲过+10%:{(pk>=0.10).mean()*100:.0f}%  "
      f"冲过+20%:{(pk>=0.20).mean()*100:.0f}%")
print("-" * 92)
print("  收益分布（J>100 卖点实际吃到多少 vs peak 能冲多高）")
r = df["ret"]
print(f"  卖点收益 P10 {r.quantile(0.10)*100:+6.2f}%  P25 {r.quantile(0.25)*100:+6.2f}%  "
      f"中 {r.median()*100:+6.2f}%  P75 {r.quantile(0.75)*100:+6.2f}%  P90 {r.quantile(0.90)*100:+6.2f}%")
print(f"  >+5%:{(r>=0.05).mean()*100:.0f}%  >+10%:{(r>=0.10).mean()*100:.0f}%  "
      f">+20%:{(r>=0.20).mean()*100:.0f}%  <0:{(r<0).mean()*100:.0f}%  <-5%:{(r<=-0.05).mean()*100:.0f}%")
print("-" * 92)
print("  持仓天数分布（多久才到 J>100）")
hd = df["hold"]
print(f"  P25 {hd.quantile(0.25):.0f}天  中 {hd.median():.0f}天  P75 {hd.quantile(0.75):.0f}天  "
      f"均 {hd.mean():.1f}天  >10天:{(hd>10).mean()*100:.0f}%  >20天:{(hd>20).mean()*100:.0f}%")
print("=" * 92)
print("  对照：反转低吸 112.6%/18.8% · 缠论 73.4%/34.1%（T+1超短，挂+5%脉冲B口径）")
print("        肯特纳-3%硬止损 124.5%/46.5%（多日持仓，+5%脉冲+-3%止损）")
print("  问：纯 KDJ 超卖超买（持有到 J>100）在 A 股能否工作？还是冲高回落吃掉利润？")
print(f"  总耗时 {time.time()-t0:.0f}s")
