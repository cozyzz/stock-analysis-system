# -*- coding: utf-8 -*-
"""一次性探针：只跑数据层 + 引擎，跳过个股分时与渲染，快速看隔日超短在新口径下的数字。
用法：py -3 _probe_nds.py
"""
import sys, os, time
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import config as C
import datasource as ds
import engine
import strategies

t0 = time.time()
ohlcv = ds.load_ohlcv(verbose=False)
uni = ds.load_universe(verbose=False)
name_map = ds.symbol_name_map(uni)
allowed, _ = ds.filter_pool(uni)
shares = ds.build_shares_map(uni)
pool_set = set(allowed)
ohlcv_pool = ohlcv[ohlcv["symbol"].isin(pool_set)]

NAME2 = "next_day_short_pb"
st = strategies.get_strategy(NAME2)
print(f"Strategy={st.NAME}  SELL_BASIS={getattr(st, 'SELL_BASIS', '(missing)')!r}")
print(f"数据加载完成（{time.time()-t0:.0f}s），跑 {NAME2}...")
res = engine.run(ohlcv_pool, st, name_map, shares_map=shares, verbose=True)
c1 = res["summary"]["close_1"] or {}
eq = res.get("equity", {})
print("=" * 56)
print(f"  {st.NAME} · 收盘买(回踩MA5)→次日开盘卖")
print("=" * 56)
print(f"  信号数   : {res['signals_total']}")
print(f"  T+1 均值 : {(c1.get('avg') or 0)*100:+.2f}%")
print(f"  胜率     : {(c1.get('win') or 0)*100:.0f}%")
print(f"  中位     : {(c1.get('median') or 0)*100:+.2f}%")
print(f"  8年累计  : {(eq.get('total_ret') or 0)*100:+.1f}%")
print(f"  最大回撤 : {(eq.get('max_dd') or 0)*100:.1f}%")
print(f"  信号日数 : {eq.get('n_days')}")
print("-" * 56)
print("  对照：")
print("  隔日超短(尾盘买→次日收盘卖):  T+1 -0.51% 胜率40% 累计-100% 回撤100%")
print("  隔日超短(尾盘买→次日开盘卖):  T+1 -0.83% 胜率28% 累计-100% 回撤100%")
print(f"  耗时 {time.time()-t0:.0f}s")
