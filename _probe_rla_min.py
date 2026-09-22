# -*- coding: utf-8 -*-
"""探针：反转低吸信号票次日分时冲高时点分布（新浪 5 分钟源）。

从反转低吸近 ~38 天信号里抽样（新浪分钟源只返回最近约 41 天），拉次日 5 分钟
数据，看：① 次日最高价出现在哪个时段；② 首次触及 +5% 在哪个时段。
时段桶按实盘经验切：10点前/11点半前/13-13:30/14点后等。
东财分钟接口(push2his)在本环境被断连，改用新浪 stock_zh_a_minute。
"""
import sys, os, time, random
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import numpy as np
import pandas as pd
import datasource as ds
import strategies
import akshare as ak
from concurrent.futures import ThreadPoolExecutor, as_completed
from collections import Counter

t0 = time.time()
ohlcv = ds.load_ohlcv(verbose=False)
uni = ds.load_universe(verbose=False)
allowed, _ = ds.filter_pool(uni)
shares = ds.build_shares_map(uni)
pool_set = set(allowed)
ohlcv_pool = ohlcv[ohlcv["symbol"].isin(pool_set)].copy()
print(f"数据加载完成（{time.time()-t0:.0f}s），扫描信号...")

st = strategies.get_strategy("reversal_low_absorb")

sigs = []
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
    c = g["close"].astype(float).values
    h = g["high"].astype(float).values
    dates = g["date"].values
    n = len(c)
    for i in idx:
        if i + 1 >= n:
            continue
        buy = c[i]
        if not (np.isfinite(buy) and buy > 0):
            continue
        sigs.append({
            "sym": str(sym),
            "next_date": pd.Timestamp(dates[i+1]).strftime("%Y-%m-%d"),
            "buy": float(buy),
            "next_high_ret": float(h[i+1] / buy - 1.0),
        })

df = pd.DataFrame(sigs)
print(f"信号总数 {len(df)}，耗时 {time.time()-t0:.0f}s")
print(f"次日冲过+5%: {(df['next_high_ret']>=0.05).sum()} ({(df['next_high_ret']>=0.05).mean()*100:.0f}%)")

# 新浪分钟源只回最近 ~41 天，cutoff 取 today-38 保险
cutoff = pd.Timestamp.today().normalize() - pd.Timedelta(days=38)
df["nd"] = pd.to_datetime(df["next_date"])
recent = df[df["nd"] >= cutoff].copy()
over5 = recent[recent["next_high_ret"] >= 0.05].copy()
print(f"近38天信号 {len(recent)}，其中冲过+5% {len(over5)}")

random.seed(42)
samp_over5 = over5.sample(min(25, len(over5))).to_dict("records")
samp_all = recent.sample(min(25, len(recent))).to_dict("records")
samples = {f"{s['sym']}@{s['next_date']}": s for s in samp_over5 + samp_all}
print(f"抽样 {len(samples)} 只（去重后），开始拉分时（新浪5分钟源）...")

BUCKETS = [
    ("09:30-10:00  开盘30min/10点前", lambda t: t < "10:00"),
    ("10:00-10:30",                   lambda t: "10:00" <= t < "10:30"),
    ("10:30-11:00",                   lambda t: "10:30" <= t < "11:00"),
    ("11:00-11:30  临近午收",         lambda t: "11:00" <= t < "11:30"),
    ("13:00-13:30  午后开盘",         lambda t: "13:00" <= t < "13:30"),
    ("13:30-14:00",                   lambda t: "13:30" <= t < "14:00"),
    ("14:00-14:30  2点后",            lambda t: "14:00" <= t < "14:30"),
    ("14:30-15:00  尾盘",             lambda t: "14:30" <= t < "15:00"),
]

def bucket_of(hhmm):
    for name, pred in BUCKETS:
        if pred(hhmm):
            return name
    return None

def prefix(sym):
    # ohlcv symbol 已带 sh/sz 前缀；兼容纯6位
    if sym.startswith(("sh", "sz")):
        return sym
    return ("sh" if sym[:1] in ("6", "9") else "sz") + sym

def fetch_one(s):
    sym, nd, buy = s["sym"], s["next_date"], s["buy"]
    try:
        m = ak.stock_zh_a_minute(symbol=prefix(sym), period="5")
        if m is None or len(m) == 0:
            return s, None, "empty"
        m = m.copy()
        m["high"] = m["high"].astype(float)
        m["d"] = m["day"].astype(str).str[:10]
        md = m[m["d"] == nd].reset_index(drop=True)
        if len(md) == 0:
            return s, None, f"no_day({nd})"
        hi = md.loc[md["high"].idxmax()]
        hi_t = str(hi["day"])[11:16]
        m5 = md[md["high"] >= buy * 1.05]
        touch5 = str(m5.iloc[0]["day"])[11:16] if len(m5) > 0 else None
        return s, {"hi_t": hi_t, "touch5": touch5, "n": len(md)}, "ok"
    except Exception as e:
        return s, None, str(e)[:50]

results = []
n_ok = n_fail = 0
fail_status = Counter()
with ThreadPoolExecutor(max_workers=4) as ex:
    futs = {ex.submit(fetch_one, s): k for k, s in samples.items()}
    for f in as_completed(futs):
        s, info, status = f.result()
        if status == "ok":
            n_ok += 1
            results.append((s, info))
        else:
            n_fail += 1
            fail_status[status[:20]] += 1
print(f"分时拉取完成：成功 {n_ok}，失败 {n_fail}，耗时 {time.time()-t0:.0f}s")
if fail_status:
    print(f"失败原因: {dict(fail_status)}")
print()

def print_dist(title, dist):
    print("=" * 70)
    print(f"  {title}")
    print("=" * 70)
    total = sum(dist.values())
    if total == 0:
        print("  （无样本）")
        return
    for name, _ in BUCKETS:
        c = dist.get(name, 0)
        print(f"  {name:30s}: {c:3d}  ({c/total*100:4.0f}%)  {'#' * c}")

# ① 最高价时点
hi_dist = Counter()
for s, info in results:
    b = bucket_of(info["hi_t"])
    if b:
        hi_dist[b] += 1
print_dist("① 次日最高价出现在哪个时段（全部成功样本）", hi_dist)
print()

# ② 触及+5% 时点
t5_dist = Counter()
n_t5 = 0
for s, info in results:
    if info["touch5"]:
        n_t5 += 1
        b = bucket_of(info["touch5"])
        if b:
            t5_dist[b] += 1
print(f"② 冲过+5%且取到时点的样本: {n_t5}")
print_dist("② 首次触及 +5% 在哪个时段", t5_dist)
print()

# ③ 实盘重点时段汇总
print("=" * 70)
print("  ③ 实盘重点时段汇总（占该分布总数百分比）")
print("=" * 70)
KEYS = {
    "10点前":       ["09:30-10:00"],
    "午后13-13:30": ["13:00-13:30"],
    "2点后":        ["14:00-14:30", "14:30-15:00"],
    "临近11点半":   ["11:00-11:30"],
}
for label, dist in [("最高价时点", hi_dist), ("触及+5%时点", t5_dist)]:
    tot = sum(dist.values())
    for desc, keys in KEYS.items():
        c = sum(dist.get(n, 0) for n, _ in BUCKETS if any(k in n for k in keys))
        print(f"  {label:14s} | {desc:12s}: {c:3d} / {tot} = {(c/tot*100) if tot else 0:4.0f}%")
    print()

# ④ 样本明细
print("=" * 70)
print("  ④ 样本明细（冲过+5%的前15个）")
print("=" * 70)
print(f"  {'代码':8s} {'次日':12s} {'冲高%':7s} {'最高价时点':10s} {'触及5%时点':10s}")
shown = 0
for s, info in results:
    if info["touch5"] and shown < 15:
        print(f"  {s['sym']:8s} {s['next_date']:12s} {s['next_high_ret']*100:+6.2f}%  {info['hi_t']:8s}   {info['touch5']:8s}")
        shown += 1
print()
print(f"总耗时 {time.time()-t0:.0f}s")
