# -*- coding: utf-8 -*-
"""诊断探针：反转低吸信号按涨停基因强度分子集 · 看连板子集胜率/收益是否更高。

卖出用现有策略 B 口径（次日挂+5%限价）：跳空高开 open>=1.05e→ret=open/e-1；
盘中 high>=1.05e→ret=+5%；否则 10点撤单收盘走 ret=close[i+1]/e-1。T+1。
涨停基因强度两口径：
  ① 10日内涨停次数（count）：1次/2次/3+次
  ② 10日内最大连板数（最长连续涨停段）：1板/2连/3+连
问：连板/涨停基因强的子集，+5%成交率、胜率、年化是否更高？
对照：反转低吸整体 B口径 年化112.6%/18.8%（盘中最高中位+3.79%）
"""
import sys, os, time
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import numpy as np
import pandas as pd
import config as C
import datasource as ds
import tdx
import strategies.reversal_low_absorb as rla

t0 = time.time()
ohlcv = ds.load_ohlcv(verbose=False)
uni = ds.load_universe(verbose=False)
allowed, _ = ds.filter_pool(uni)
pool_set = set(allowed)
ohlcv_pool = ohlcv[ohlcv["symbol"].isin(pool_set)].copy()
ohlcv_pool["date"] = pd.to_datetime(ohlcv_pool["date"])
print(f"数据加载完成（{time.time()-t0:.0f}s），池 {len(pool_set)} 只\n")


def max_consec(arr):
    """arr(bool) 的最长连续 True 段长度。"""
    m = 0; cur = 0
    for x in arr:
        if x:
            cur += 1
            if cur > m:
                m = cur
        else:
            cur = 0
    return m


def scan_all():
    rows = []; n_cens = 0
    for sym, g in ohlcv_pool.groupby("symbol"):
        g = g.sort_values("date").reset_index(drop=True)
        n = len(g)
        if n < 60:
            continue
        c = g["close"].astype(float); h = g["high"].astype(float); o = g["open"].astype(float)
        sig = rla.select(g)
        idx = np.where(sig.fillna(False).values)[0]
        if len(idx) == 0:
            continue
        prev_c = c.shift(1)
        zt = tdx.zt_price(prev_c, str(sym))
        zt_day = ((c >= zt - 1e-9) & np.isfinite(zt)).fillna(False).values
        c_arr = c.values; h_arr = h.values; o_arr = o.values; dates = g["date"].values
        for i in idx:
            if i + 1 >= n:
                n_cens += 1
                continue
            entry = c_arr[i]
            if not (np.isfinite(entry) and entry > 0):
                continue
            # 涨停基因强度（10日内含 i）
            lo = max(0, i - 9)
            seg = zt_day[lo:i + 1]
            zt_cnt = int(seg.sum())
            zt_consec = max_consec(seg)
            # B口径卖出（次日 i+1）
            o1 = o_arr[i + 1]; h1 = h_arr[i + 1]; c1 = c_arr[i + 1]
            if o1 >= 1.05 * entry:                # 跳空高开过+5%
                ret = o1 / entry - 1.0; tp = True
            elif h1 >= 1.05 * entry:               # 盘中触+5%
                ret = 0.05; tp = True
            else:                                  # 撤单收盘走
                ret = c1 / entry - 1.0; tp = False
            rows.append({"date": pd.Timestamp(dates[i]), "ret": ret, "tp": tp,
                         "zt_cnt": zt_cnt, "zt_consec": zt_consec})
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
    return {"ann": ann, "maxdd": maxdd}


print("扫描中（反转低吸信号 × B口径+5%脉冲 × 涨停基因强度分档）...")
df, n_cens = scan_all()
print(f"扫描完成，耗时 {time.time()-t0:.0f}s，信号 {len(df)}（删失 {n_cens}）\n")

if len(df) == 0:
    print("无信号"); sys.exit(0)

eq_all = equity(df)


def row(df, label):
    eq = equity(df)
    print(f"  {label:12s} n={len(df):6d}  +5%成交 {(df['tp'].mean())*100:3.0f}%  "
          f"胜率 {(df['ret']>0).mean()*100:3.0f}%  均收 {df['ret'].mean()*100:+6.2f}%  "
          f"中收 {df['ret'].median()*100:+6.2f}%  年化 {eq['ann']*100:+7.1f}%  "
          f"回撤 {eq['maxdd']*100:5.1f}%")


print("=" * 100)
print(f"  整体（B口径+5%脉冲T+1）  n={len(df)}  +5%成交 {df['tp'].mean()*100:.0f}%  "
      f"胜率 {(df['ret']>0).mean()*100:.0f}%  均收 {df['ret'].mean()*100:+.2f}%  "
      f"中收 {df['ret'].median()*100:+.2f}%  年化 {eq_all['ann']*100:+.1f}%  "
      f"回撤 {eq_all['maxdd']*100:.1f}%  （对照记忆 112.6%/18.8%）")
print("=" * 100)
print("  口径①：按 10 日内涨停次数分档")
print("-" * 100)
for k, lo, hi in [("1次涨停", 1, 1), ("2次涨停", 2, 2), ("3+次涨停", 3, 999)]:
    sub = df[(df["zt_cnt"] >= lo) & (df["zt_cnt"] <= hi)]
    if len(sub):
        row(sub, k)
print("-" * 100)
print("  口径②：按 10 日内最大连板数分档")
print("-" * 100)
for k, lo, hi in [("1板(单涨)", 1, 1), ("2连板", 2, 2), ("3+连板", 3, 999)]:
    sub = df[(df["zt_consec"] >= lo) & (df["zt_consec"] <= hi)]
    if len(sub):
        row(sub, k)
print("=" * 100)
print("  问：连板/涨停基因强的子集，+5%成交率/胜率/年化是否高于整体？")
print(f"  总耗时 {time.time()-t0:.0f}s")
