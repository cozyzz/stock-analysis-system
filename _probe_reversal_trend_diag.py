# -*- coding: utf-8 -*-
"""诊断探针：反转低吸信号 + 趋势过滤(均线多头+周K上升)子集 · peak分布与破MA5卖点回吐。

问：趋势好的子集能不能吃到波段？
  1. peak 中位是否真比整体(+3.79% 盘中最高中位)高一档？
  2. 冲过+10%/+20% 比例高不高？
  3. 卖点(持有到破MA5)收益中位是正还是负？
     正 → 趋势好的能吃到波段，可放开+5%；
     负 → 连趋势好的也回吐，+5%快出仍最优。

入场：反转低吸信号（J<20+突破MA3+放量+20日涨幅<40%+10日涨停+均线，正式口径
       import strategies.reversal_low_absorb.select，保证与监控策略一致）
退出：首个 j>i 使 close[j]<MA5[j] → 当日收盘卖（T+1），ret=close[j]/close[i]-1
趋势子集：信号日 c>MA5>MA20>MA60（多头排列，价站上三均线）
         且 上一完整周收阳且环比上升（周K上升，无未来函数）
peak = 持仓期最高 high/entry-1；回吐 = peak中 - ret中
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


def scan_all():
    rows = []; n_cens = 0
    for sym, g in ohlcv_pool.groupby("symbol"):
        g = g.sort_values("date").reset_index(drop=True)
        n = len(g)
        if n < 60:
            continue
        c = g["close"].astype(float); h = g["high"].astype(float)
        sig = rla.select(g)                                   # 反转低吸信号（正式口径）
        idx = np.where(sig.fillna(False).values)[0]
        if len(idx) == 0:
            continue
        ma5 = tdx.MA(c, 5).values; ma20 = tdx.MA(c, 20).values; ma60 = tdx.MA(c, 60).values
        c_arr = c.values; h_arr = h.values; dates = g["date"].values
        # 周K上升：上一完整周收阳且环比上升
        g2 = g.copy()
        g2["week"] = g2["date"].dt.to_period("W-FRI")
        weekly = g2.groupby("week").agg(wclose=("close", "last"), wopen=("open", "first"))
        weekly["prev_close"] = weekly["wclose"].shift(1)
        weekly["up"] = (weekly["wclose"] > weekly["wopen"]) & (weekly["wclose"] > weekly["prev_close"])
        g2["prev_week"] = g2["week"] - 1
        g2 = g2.merge(weekly[["up"]].rename(columns={"up": "prev_week_up"}),
                      left_on="prev_week", right_index=True, how="left")
        prev_week_up = g2["prev_week_up"].fillna(False).values
        for i in idx:
            if i + 1 >= n:
                continue
            entry = c_arr[i]
            if not (np.isfinite(entry) and entry > 0):
                continue
            after_c = c_arr[i + 1:]; after_ma5 = ma5[i + 1:]
            brk = (after_c < after_ma5) & np.isfinite(after_ma5)
            if not brk.any():
                n_cens += 1
                continue
            k = int(np.argmax(brk))
            exit_j = i + 1 + k
            ret = c_arr[exit_j] / entry - 1.0
            seg = h_arr[i + 1:exit_j + 1]
            peak = (seg.max() / entry - 1.0) if len(seg) else 0.0
            bull = (np.isfinite(ma60[i]) and c_arr[i] > ma5[i] and
                    ma5[i] > ma20[i] and ma20[i] > ma60[i])
            wk_up = bool(prev_week_up[i])
            rows.append({"date": pd.Timestamp(dates[i]), "hold": exit_j - i,
                         "ret": ret, "peak": peak, "trend": bool(bull and wk_up)})
    return pd.DataFrame(rows), n_cens


print("扫描中（反转低吸信号 × 破MA5持有，算 peak 与回吐）...")
df, n_cens = scan_all()
print(f"扫描完成，耗时 {time.time()-t0:.0f}s，信号 {len(df)}（删失 {n_cens}），"
      f"趋势子集 {(df['trend'].mean()*100):.0f}%\n")

if len(df) == 0:
    print("无信号"); sys.exit(0)


def report(d, label):
    pk = d["peak"]; r = d["ret"]; hd = d["hold"]
    print(f"  {label}（n={len(d)}）")
    print(f"    peak   中 {pk.median()*100:+6.2f}%  均 {pk.mean()*100:+6.2f}%  "
          f"冲+5%:{(pk>=0.05).mean()*100:3.0f}%  冲+10%:{(pk>=0.10).mean()*100:3.0f}%  "
          f"冲+20%:{(pk>=0.20).mean()*100:3.0f}%")
    print(f"    卖点ret 中 {r.median()*100:+6.2f}%  均 {r.mean()*100:+6.2f}%  "
          f"胜率 {(r>0).mean()*100:3.0f}%  P25 {r.quantile(0.25)*100:+6.2f}%  "
          f"P75 {r.quantile(0.75)*100:+6.2f}%")
    print(f"    hold   均 {hd.mean():5.1f}天  中 {hd.median():3.0f}天")
    print(f"    回吐   peak中 {pk.median()*100:+.1f}% − ret中 {r.median()*100:+.1f}% "
          f"= 回吐 {(pk.median()-r.median())*100:+.1f}%（吃到的波段比例）")
    return pk.median(), r.median()


print("=" * 92)
print("  破MA5持有口径：整体 vs 趋势子集(均线多头+周K上升)")
print("-" * 92)
p_all, r_all = report(df, "整体（全部反转低吸信号）")
print()
tdf = df[df["trend"]]
p_tr, r_tr = report(tdf, "趋势子集(均线多头+周K上升)") if len(tdf) else (0, 0)
print("=" * 92)
print("  关键对比")
print(f"  peak 中位：整体 {p_all*100:+.2f}% → 趋势子集 {p_tr*100:+.2f}% "
      f"（{(p_tr-p_all)*100:+.2f} 个百分点，{'高一档✓' if p_tr>p_all+0.02 else '没高一档✗'}）")
print(f"  危点ret 中位：整体 {r_all*100:+.2f}% → 趋势子集 {r_tr*100:+.2f}% "
      f"（{'正→能吃到波段，可放开+5%探索' if r_tr>0 else '负→连趋势好的也回吐，+5%快出仍最优'}）")
print("-" * 92)
print("  对照：反转低吸 +5%脉冲T+1 → 年化112.6%/18.8%（盘中最高中位+3.79%）")
print("  问：趋势子集 peak 是否高一档 + 卖点ret中位是否为正")
print(f"  总耗时 {time.time()-t0:.0f}s")
