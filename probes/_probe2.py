# -*- coding: utf-8 -*-
"""探测 akshare 可用接口：全球指数/资金流(带金额)/热度榜。逐项 try/except，单线程超时。"""
import threading
import json

import akshare as ak
import pandas as pd

pd.set_option("display.width", 200)
pd.set_option("display.max_columns", 30)


def _to(obj, timeout=20):
    """带超时地调用 akshare 函数，返回 (df_or_None, err_str)。"""
    res = {}

    def run():
        try:
            res["v"] = obj()
        except Exception as e:
            res["e"] = f"{type(e).__name__}: {str(e)[:90]}"

    t = threading.Thread(target=run, daemon=True)
    t.start()
    t.join(timeout)
    if t.is_alive():
        return None, f"TIMEOUT({timeout}s)"
    if "e" in res:
        return None, res["e"]
    return res.get("v"), None


def show(tag, df, err, n=3):
    print(f"\n===== {tag} =====")
    if err:
        print("  ERR:", err)
        return
    if df is None or not len(df):
        print("  EMPTY")
        return
    print("  shape:", df.shape)
    print("  cols:", list(df.columns))
    print("  head:")
    print(df.head(n).to_string(max_colwidth=16))


# 1. 全球指数
df, e = _to(lambda: ak.index_global_spot_em(), 25)
show("index_global_spot_em(东财全球指数)", df, e)
if df is not None:
    try:
        print("  全部名称样本:", df["名称"].head(40).tolist() if "名称" in df.columns else list(df.columns))
    except Exception:
        pass

# 2. 美股 / 港股 实时
df, e = _to(lambda: ak.stock_us_spot_em(symbol=".DJI"), 25)
show("stock_us_spot_em(.DJI)", df, e)
df, e = _to(lambda: ak.stock_hk_index_spot_em(), 25)
show("stock_hk_index_spot_em(港股指数)", df, e)

# 3. 资金流 —— 同花顺（可能更稳）+ 东财重试
df, e = _to(lambda: ak.stock_fund_flow_industry(symbol="即时"), 25)
show("stock_fund_flow_industry(同花顺即时)", df, e)
df, e = _to(lambda: ak.stock_fund_flow_concept(symbol="即时"), 25)
show("stock_fund_flow_concept(同花顺即时)", df, e)
df, e = _to(lambda: ak.stock_sector_fund_flow_rank(indicator="今日", sector_type="行业资金流"), 25)
show("stock_sector_fund_flow_rank(东财行业今日)", df, e)

# 4. 热度榜
df, e = _to(lambda: ak.stock_hot_rank_em(), 25)
show("stock_hot_rank_em(东财人气榜)", df, e)
df, e = _to(lambda: ak.stock_hot_up_em(), 25)
show("stock_hot_up_em(东财飙升榜)", df, e)
df, e = _to(lambda: ak.stock_hot_search_baidu(symbol="A股"), 25)
show("stock_hot_search_baidu(百度热搜A股)", df, e)
df, e = _to(lambda: ak.stock_hot_follow_xq(), 25)
show("stock_hot_follow_xq(雪球关注)", df, e)
