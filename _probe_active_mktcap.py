# -*- coding: utf-8 -*-
"""活跃市值波段统计 · 探针（接口数据版，稳妥）

数据源：akshare stock_buffett_index_lg（乐咕·巴菲特指数），直接给"总市值"列，
  2005-04~今 日频，亿元单位。权威、干净、有水平值（万亿），不依赖本地 ohlcv 缓存
  （缓存有覆盖残缺/快照股本漂移/qfq 三重问题，不稳妥）。

活跃市值 = 全A总市值（legu，作为"活跃市值"水平序列）。
波段定义（用户口径，2026-09-27 定稿）：
  起点 t：2日累计涨幅 r2[t] = S[t]/S[t-2] - 1 > 4%（2天内活跃市值涨超4%触发）
  终点：首个 j>t 使 日跌幅 r1[j] <= -2%（活跃市值单日跌≥2%→完整波段结束）
  波段收益 = S[end]/S[start] - 1（触发日到终结日的区间涨幅，含终结日跌幅）
  波段内最高 = max(S[start..end])/S[start] - 1（波段冲高幅度，对照回吐）
MA10 = 活跃市值 10日均线（"平均活跃市值"，列出当前值与全程均值）。

口径说明：源列是"总市值"（含限售股），非严格"流通市值合计"——akshare 无干净的
  流通市值历史日序列；波段检测基于收益率，总/流通的限售解禁是慢变量，触发日几乎
  一致；总市值水平无解禁漂移，更干净。若用户要严格流通口径需另接数据源。
"""
import sys, os, time
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import numpy as np
import pandas as pd
import config as C

THRESHOLD = 0.04    # 2日累计涨幅阈值 4%（触发）
END_DROP = -0.02    # 单日跌幅阈值 -2%（终结，完整波段）


def fetch_active_mktcap():
    """从 akshare stock_buffett_index_lg 拉全A总市值日序列。返回 pd.Series(index=date, 值=亿元)。"""
    import akshare as ak
    last = None
    for attempt in range(4):
        try:
            df = ak.stock_buffett_index_lg()
            break
        except Exception as e:
            last = e
            time.sleep(1.0 * (attempt + 1))
    else:
        raise RuntimeError(f"stock_buffett_index_lg 拉取失败：{last}")
    df = df.copy()
    df["日期"] = pd.to_datetime(df["日期"])
    df = df.sort_values("日期").reset_index(drop=True)
    df = df.drop_duplicates(subset="日期", keep="last")
    s = pd.Series(df["总市值"].astype(float).values, index=df["日期"], name="mktcap_yi")
    return s


t0 = time.time()
print("  从 akshare 拉取活跃市值（legu 全A总市值）...")
S = fetch_active_mktcap()
print(f"  序列：{len(S)} 日 / {S.index.min().date()} ~ {S.index.max().date()}")
print(f"  期初 {S.iloc[0]/1e4:.2f} 万亿 / 期末 {S.iloc[-1]/1e4:.2f} 万亿 / "
      f"全程 {S.iloc[-1]/S.iloc[0]-1:+.1%}")

ma10 = S.rolling(10).mean()                              # MA10 平均活跃市值（亿元）
print(f"  最新 MA10 平均活跃市值 {ma10.iloc[-1]/1e4:.2f} 万亿 / "
      f"全程 MA10 均值 {ma10.mean()/1e4:.2f} 万亿\n")

# 收益率
r1 = S.pct_change()                  # 逐日
r2 = S / S.shift(2) - 1.0             # 2日累计

n = len(S)
dates = S.index

# ---- 波段检测 ----
bands = []
in_band = False
start = None
for t in range(n):
    if not in_band:
        if t >= 2 and pd.notna(r2.iloc[t]) and r2.iloc[t] > THRESHOLD:
            start = t
            in_band = True
    else:
        if t > start and pd.notna(r1.iloc[t]) and r1.iloc[t] <= END_DROP:
            bands.append((start, t))
            in_band = False
censored = 1 if in_band else 0     # 末尾未终结的开放波段

# ---- 波段收益 ----
rows = []
for (s, e) in bands:
    seg = S.iloc[s:e + 1]
    ret = S.iloc[e] / S.iloc[s] - 1.0                       # 触发日→终结日（含终结日跌幅）
    peak_ret = seg.max() / S.iloc[s] - 1.0                 # 波段内最高冲幅
    rows.append({"start": dates[s].date(), "end": dates[e].date(),
                 "ret": ret, "peak": peak_ret, "length": e - s})
bdf = pd.DataFrame(rows)

# ---- 存序列 CSV（记录活跃市值变化）----
out = pd.DataFrame({"date": dates.date,
                     "mktcap_yi": S.values.round(2),
                     "ma10_yi": ma10.values.round(2),
                     "r1": r1.values, "r2": r2.values})
out["in_band"] = False
for (s, e) in bands:
    out.loc[s:e, "in_band"] = True
csv_path = os.path.join(C.DATA_DIR, "active_mktcap_series.csv")
out.to_csv(csv_path, index=False, encoding="utf-8-sig")
print(f"  序列已存：{csv_path}（{len(out)} 行，含 MA10/in_band 标记）\n")


def stats(x, label):
    if len(x) == 0:
        print(f"  {label}：无样本")
        return
    print(f"  {label}（n={len(x)}）")
    print(f"    均 {x.mean()*100:+6.2f}%  中 {x.median()*100:+6.2f}%  "
          f"胜率 {(x>0).mean()*100:3.0f}%  P25 {x.quantile(.25)*100:+6.2f}%  "
          f"P75 {x.quantile(.75)*100:+6.2f}%")
    print(f"    min {x.min()*100:+6.2f}%  max {x.max()*100:+6.2f}%")


print("=" * 92)
print(f"  波段检测：2日涨幅>{THRESHOLD*100:.0f}%触发，单日跌幅<={END_DROP*100:.0f}%终结")
print(f"  波段数 {len(bands)}，末尾开放(未终结) {censored}")
print("-" * 92)
if len(bdf):
    stats(bdf["ret"], "波段收益（触发日→终结日，含终结日跌幅）")
    stats(bdf["peak"], "波段内最高冲幅（max/start-1）")
    print(f"  回吐 = 波段内最高 − 波段收益：均 {(bdf['peak']-bdf['ret']).mean()*100:+.2f}% "
          f"中 {(bdf['peak']-bdf['ret']).median()*100:+.2f}%")
    ld = bdf["length"]
    print(f"  波段长度(日)：均 {ld.mean():.1f}  中 {ld.median():.0f}  "
          f"min {ld.min()}  max {ld.max()}")
    print("-" * 92)
    comp = np.prod(1 + bdf["ret"].values) - 1.0
    yrs = (dates[-1] - dates[0]).days / 365.25
    ann = (1 + comp) ** (1 / yrs) - 1.0 if yrs > 0 and (1 + comp) > 0 else 0.0
    print(f"  依次骑波段复合收益 {comp*100:+.1f}%（{yrs:.1f}年，年化 {ann*100:+.1f}%）"
          f"—— 仅骑波段、波段间空仓")
else:
    print("  无波段（阈值过高或样本期无此级别异动）")
print("=" * 92)

# ---- 逐年明细 ----
if len(bdf):
    bdf["year"] = pd.to_datetime(bdf["start"]).dt.year
    print("  逐年波段明细：")
    print(f"    {'年份':6s} {'波段数':6s} {'均收益':8s} {'中收益':8s} {'胜率':6s} {'均长':6s}")
    for yr, g in bdf.groupby("year"):
        print(f"    {yr:<6d} {len(g):<6d} {g['ret'].mean()*100:+7.2f}% "
              f"{g['ret'].median()*100:+7.2f}% {(g['ret']>0).mean()*100:5.0f}% "
              f"{g['length'].mean():5.1f}天")
    print("-" * 92)
    print("  最近 12 个波段：")
    print(f"    {'起点':12s} {'终点':12s} {'长度':5s} {'收益':8s} {'最高':8s} {'回吐':8s}")
    for _, r in bdf.tail(12).iterrows():
        print(f"    {str(r['start']):12s} {str(r['end']):12s} {r['length']:3d}天 "
              f"{r['ret']*100:+7.2f}% {r['peak']*100:+7.2f}% "
              f"{(r['peak']-r['ret'])*100:+7.2f}%")
print("=" * 92)
print(f"  总耗时 {time.time()-t0:.0f}s")
