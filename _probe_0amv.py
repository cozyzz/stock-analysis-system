# -*- coding: utf-8 -*-
"""0AmV 活筹指数 · 公式核验探针

目标：确定哪个公式能复现用户参考值（当前 0AmV≈17.96万亿 / MA10≈18.97万亿）。
用户脚本是 SMA(沪深两市成交额, 10, 1)，但 SMA 是 EMA 式平均（稳态≈日均成交额，
~1.8万亿），与参考 17.96万亿差 ~10 倍；17.96 更像近 10 日成交额之和（10×1.8≈18）。
本探针把两候选都算出来对比。

候选：
  A) SMA(A, 10, 1)            用户脚本公式（EMA-like 平均）
  B) 滚动 10 日 A 之和          10 日累计成交额
数据：上证指数 sh000001 + 深证成指 sz399001 日线成交额（akshare 东财源）。

注意：深证成指 399001 仅含 500 成分股，其成交额 < 深市全市场，故 B 会略偏低；
但 SMA(平均) vs 10日和 的 ~10 倍量级差异仍能判清。如需精确"两市合计"后续换
深证综指 399106 或全市场口径。
"""
import sys, os
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import pandas as pd
import akshare as ak


def sma_tdx(s, n, m):
    """通达信 SMA(X,N,M) = (M*X + (N-M)*prev)/N，EMA-like。"""
    res, pre = [], None
    for x in s:
        if pre is None:
            pre = x
        else:
            pre = (x * m + pre * (n - m)) / n
        res.append(pre)
    return pd.Series(res, index=s.index)


def fetch_idx_amt(symbol):
    """取指数日线成交额，返回 DataFrame[date, amt(元)]。"""
    df = None
    for fn_name in ("stock_zh_index_daily_em", "stock_zh_index_daily"):
        try:
            fn = getattr(ak, fn_name)
            df = fn(symbol=symbol)
            break
        except Exception as e:
            print(f"  {fn_name}({symbol}) 失败: {e}")
    if df is None or len(df) == 0:
        return None
    amt_col = None
    for c in df.columns:
        if "成交额" in str(c) or str(c).lower() in ("amount", "amt"):
            amt_col = c
            break
    if amt_col is None:
        print(f"  {symbol} 无成交额列: {list(df.columns)}")
        return None
    date_col = "日期" if "日期" in df.columns else df.columns[0]
    out = pd.DataFrame({
        "date": pd.to_datetime(df[date_col]),
        "amt": pd.to_numeric(df[amt_col], errors="coerce"),
    })
    return out.dropna().sort_values("date").reset_index(drop=True)


print("拉取上证指数 + 深证成指 日线成交额 ...")
sh = fetch_idx_amt("sh000001")
sz = fetch_idx_amt("sz399001")
if sh is None or sz is None:
    print("取数失败，退出。")
    raise SystemExit(1)

print(f"  上证 {len(sh)} 行 {sh['date'].min().date()}~{sh['date'].max().date()}  末日成交额 {sh['amt'].iloc[-1]/1e12:.3f}万亿")
print(f"  深成 {len(sz)} 行 {sz['date'].min().date()}~{sz['date'].max().date()}  末日成交额 {sz['amt'].iloc[-1]/1e12:.3f}万亿")

m = pd.merge(sh, sz, on="date", how="inner", suffixes=("_sh", "_sz"))
m["total"] = m["amt_sh"] + m["amt_sz"]          # 元
m["total_wy"] = m["total"] / 1e12               # 万亿（每日成交额）
m = m.sort_values("date").reset_index(drop=True)

# A) SMA(10,1) —— 用户脚本公式
m["A_sma"] = sma_tdx(m["total_wy"], 10, 1)
# B) 10 日滚动求和
m["B_sum10"] = m["total_wy"].rolling(10).sum()
# B 的 MA10
m["B_ma10"] = m["B_sum10"].rolling(10).mean()

print("\n=== 最近 5 日对比（单位：万亿）===")
print(m[["date", "total_wy", "A_sma", "B_sum10", "B_ma10"]].tail(5).to_string(index=False))

last = m.iloc[-1]
print(f"\n末日 {last['date'].date()}:")
print(f"  日成交额       = {last['total_wy']:.3f} 万亿")
print(f"  A) SMA(10,1)   = {last['A_sma']:.3f} 万亿   ← 用户脚本公式（平均，预期 ~1.8）")
print(f"  B) 10日求和    = {last['B_sum10']:.3f} 万亿   ← 累计（预期 ~18）")
print(f"  B 的 MA10      = {last['B_ma10']:.3f} 万亿")
print(f"\n用户参考: 0AmV ≈ 17.96 万亿 / MA10 ≈ 18.97 万亿")
print(f"结论: 与参考最接近的是 → {'B 10日求和' if abs(last['B_sum10']-17.96) < abs(last['A_sma']-17.96) else 'A SMA'}")
