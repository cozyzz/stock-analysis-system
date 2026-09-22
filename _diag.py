# -*- coding: utf-8 -*-
"""逐个测 market 子调用，定位哪个触发 V8 段错误。"""
import sys, os
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import akshare as ak
from modules.market import _fetch_markets, _fetch_fund_sector, _fetch_hot


def p(s):
    print(s, flush=True)


errors = []
p("1/3 _fetch_fund_sector (同花顺 stock_fund_flow_industry)...")
try:
    fs = _fetch_fund_sector(ak, errors)
    p("  ok len=%d err=%d" % (len(fs) if fs else 0, len(errors)))
except Exception as e:
    p("  EXC %s: %s" % (type(e).__name__, e))

p("2/3 _fetch_hot (雪球关注 + 百度热搜)...")
try:
    hf, hs = _fetch_hot(ak, errors)
    p("  ok follow=%s search=%s err=%d" % (hf is not None, hs is not None, len(errors)))
except Exception as e:
    p("  EXC %s: %s" % (type(e).__name__, e))

p("3/3 _fetch_markets (sina 指数历史)...")
try:
    idx = _fetch_markets(ak, errors)
    p("  ok len=%d err=%d" % (len(idx), len(errors)))
except Exception as e:
    p("  EXC %s: %s" % (type(e).__name__, e))

p("DONE")
