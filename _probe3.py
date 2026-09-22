# -*- coding: utf-8 -*-
"""探测全球指数历史数据接口（画走势线需要近期日线）。"""
import threading
import akshare as ak
import pandas as pd

pd.set_option("display.width", 200)


def to(fn, t=20):
    res = {}
    def run():
        try: res["v"] = fn()
        except Exception as e: res["e"] = e
    th = threading.Thread(target=run, daemon=True); th.start(); th.join(t)
    if th.is_alive(): return None, "TIMEOUT"
    return res.get("v"), res.get("e")


def show(tag, df, e, n=2):
    print(f"\n===== {tag} =====")
    if e is not None:
        print("  ERR:", type(e).__name__, str(e)[:100]); return
    if df is None or not len(df):
        print("  EMPTY"); return
    print("  shape:", df.shape, "cols:", list(df.columns))
    print(df.tail(n).to_string(max_colwidth=14))


# 新浪全球指数历史（需 symbol）
import inspect
for fn_name in ["index_global_hist_sina", "index_global_hist_em", "index_us_stock_sina"]:
    fn = getattr(ak, fn_name, None)
    if fn is None:
        print(fn_name, "不存在"); continue
    try:
        sig = str(inspect.signature(fn))
    except Exception:
        sig = "?"
    print(f"\n[{fn_name}] signature: {sig}")
    # 试几个常见 symbol
    for sym in [".DJI", "DJI", "NDX", "SPX", "IXIC", "HSI", "N225", "GDAXI"]:
        df, e = to(lambda s=sym: fn(symbol=s), 12)
        if e is None and df is not None and len(df):
            print(f"  symbol={sym} OK shape={df.shape}")
            show("  sample", df, None, 2)
            break
        else:
            print(f"  symbol={sym} -> {type(e).__name__ if e else 'empty'}")
    else:
        print(f"  （所有 symbol 都失败）")

# 美股/港股 daily（个股型，可能也适合作指数代理）
for fn_name, args in [("stock_us_daily", {"symbol":".DJI","adjust":""}),
                       ("stock_hk_index_daily_em", {"symbol":"HSI"})]:
    fn = getattr(ak, fn_name, None)
    if fn is None: print(fn_name,"不存在"); continue
    df, e = to(lambda: fn(**args), 15)
    show(fn_name, df, e, 2)
