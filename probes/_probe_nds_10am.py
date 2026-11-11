# -*- coding: utf-8 -*-
"""探针：隔日超短 · 10点前过滤 分时小样本验证。

对近 ~38 天满足日线5条件(+市值口径)的信号，拉信号日 5 分钟数据，
看 10:00 时累计涨幅是否已达 2%（即 10 点前已启动强势）。
对比：全部信号 vs 10点前达标子集 的次日开盘收益(open_next)。

新浪 5 分钟源只回最近约 41 天，故为小样本方向性验证，非严格回测。
口径：昨收=close[i-1]；10点价=10:00 那根 5min bar 的 close；
  10点前达标 = 10:00 时累计涨幅 ≥ 2%（其余量比/换手/涨停/市值日线条件已满足）。
"""
import sys, os, time
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import numpy as np
import pandas as pd
import config as C
import datasource as ds
import tdx
import akshare as ak
from concurrent.futures import ThreadPoolExecutor, as_completed

t0 = time.time()
ohlcv = ds.load_ohlcv(verbose=False)
uni = ds.load_universe(verbose=False)
allowed, _ = ds.filter_pool(uni)
shares = ds.build_shares_map(uni)
pool_set = set(allowed)
ohlcv_pool = ohlcv[ohlcv["symbol"].isin(pool_set)].copy()
print(f"数据加载（{time.time()-t0:.0f}s）")

CUTOFF = pd.Timestamp.today().normalize() - pd.Timedelta(days=38)


def scan(mkt_lo, mkt_hi):
    """收近38天信号：date, prev_close, close, open_next, sym。"""
    rows = []
    for sym, g in ohlcv_pool.groupby("symbol"):
        g = g.sort_values("date").reset_index(drop=True)
        _ts, _fs = shares.get(sym, (float("nan"), float("nan")))
        if not (np.isfinite(_ts) and np.isfinite(_fs) and _ts > 0 and _fs > 0):
            continue
        c = g["close"].astype(float)
        v = g["volume"].astype(float)
        o = g["open"].astype(float)
        ts = float(_ts); fs = float(_fs)
        prev = c.shift(1)
        chg = c / prev - 1.0
        vr = v / tdx.MA(v, 5)
        turnover = (v * 100.0) / fs * 100.0
        mktcap = ts * c
        sym_s = str(sym)
        zt = tdx.zt_price(prev, sym_s)
        have_zt = tdx.EXIST(c >= zt - 1e-9, 10)
        cond = (
            (chg >= 0.02) & (chg <= 0.05)
            & (vr > 1) & (vr < 5)
            & (turnover > 5) & (turnover < 10)
            & have_zt
            & (mktcap > mkt_lo) & (mktcap < mkt_hi)
        )
        mask = cond.fillna(False).astype(bool)
        idx = np.where(mask.values)[0]
        if len(idx) == 0:
            continue
        c_v = c.values; o_v = o.values; d_v = g["date"].values; n = len(c_v)
        for i in idx:
            if i + 1 >= n or i < 1:
                continue
            buy = c_v[i]; pc = c_v[i - 1]; no = o_v[i + 1]
            d = pd.Timestamp(d_v[i])
            if d < CUTOFF:
                continue
            if not (np.isfinite(buy) and buy > 0 and np.isfinite(pc) and pc > 0
                    and np.isfinite(no) and no > 0):
                continue
            rows.append({"date": d, "sym": sym_s, "prev_close": float(pc),
                         "close": float(buy), "ret": no / buy - 1.0})
    return pd.DataFrame(rows)


def prefix(sym):
    if sym.startswith(("sh", "sz")):
        return sym
    return ("sh" if sym[:1] in ("6", "9") else "sz") + sym


def fetch_10am(s):
    """拉信号日 5 分钟数据，返回 10:00 时累计涨幅(10点价/昨收-1)。"""
    sym, d, pc = s["sym"], s["date"].strftime("%Y-%m-%d"), s["prev_close"]
    try:
        m = ak.stock_zh_a_minute(symbol=prefix(sym), period="5")
        if m is None or len(m) == 0:
            return None
        m = m.copy()
        m["day"] = m["day"].astype(str)
        md = m[m["day"].str[:10] == d].reset_index(drop=True)
        if len(md) == 0:
            return None
        # 10:00 那根 bar（09:30-10:00 区间最后一根=10:00 标记）
        bar = md[md["day"].str[11:16] == "10:00"]
        if len(bar) == 0:
            # 取 10:00 之前最后一根
            bar = md[md["day"].str[11:16] <= "10:00"]
            if len(bar) == 0:
                return None
            bar = bar.iloc[[-1]]
        p10 = float(bar.iloc[0]["close"])
        if not (np.isfinite(p10) and p10 > 0 and pc > 0):
            return None
        return p10 / pc - 1.0
    except Exception:
        return None


def run(name, df):
    if not len(df):
        print(f"\n{name}：近38天无信号")
        return
    samples = df.to_dict("records")
    print(f"\n{'='*70}\n  {name}\n{'='*70}")
    print(f"  近38天信号 {len(samples)}，拉分时（新浪5分钟源，4并发）...")
    res = {}
    with ThreadPoolExecutor(max_workers=4) as ex:
        futs = {ex.submit(fetch_10am, s): i for i, s in enumerate(samples)}
        done = 0
        for f in as_completed(futs):
            i = futs[f]
            res[i] = f.result()
            done += 1
            if done % 20 == 0:
                print(f"    进度 {done}/{len(samples)}")
    df = df.copy()
    df["chg_10am"] = [res.get(i) for i in range(len(df))]
    got = df["chg_10am"].notna()
    df_g = df[got].copy()
    print(f"  分时拉取成功 {got.sum()}/{len(df)}")
    if not len(df_g):
        print("  无成功样本")
        return

    def stat(d2, label):
        if not len(d2):
            print(f"  {label}：无样本")
            return
        r = d2["ret"]
        print(f"  {label:20s}  n={len(d2):3d}  T+1均={r.mean()*100:+6.2f}%  "
              f"中位={r.median()*100:+6.2f}%  胜率={(r>0).mean()*100:3.0f}%")
    stat(df_g, "全部(对照)")
    before10 = df_g[df_g["chg_10am"] >= 0.02]
    stat(before10, "10点前涨幅≥2%")
    before10_strong = df_g[(df_g["chg_10am"] >= 0.02) & (df_g["chg_10am"] <= 0.05)]
    stat(before10_strong, "10点前2-5%")
    # 明细：10点前达标的
    if len(before10):
        print(f"\n  10点前达标样本明细（{len(before10)}）：")
        print(f"  {'代码':8s} {'日期':12s} {'10点涨幅':>8s} {'T+1收益':>8s}")
        for _, r in before10.sort_values("ret", ascending=False).head(20).iterrows():
            print(f"  {r['sym']:8s} {r['date'].strftime('%Y-%m-%d'):12s} "
                  f"{r['chg_10am']*100:+7.2f}% {r['ret']*100:+7.2f}%")


A = scan(1e10, 1e18)    # 原版 >100亿
B = scan(4e9, 1.5e10)   # 改版 40-150亿
print(f"近38天信号：原版>100亿 {len(A)}，改版40-150亿 {len(B)}，耗时 {time.time()-t0:.0f}s")
run("原版 >100亿 · 10点前过滤", A)
run("改版 40-150亿 · 10点前过滤", B)
print(f"\n总耗时 {time.time()-t0:.0f}s")
