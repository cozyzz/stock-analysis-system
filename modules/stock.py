# -*- coding: utf-8 -*-
"""个股模块 · 多策略单只股票 K 线 + 指标 + 策略信号

为每个策略选信号最密集的前 STOCK_POOL_N 只，对每只内联最近 STOCK_BARS 根 K 线 +
MA3/7/28/56 + ATR14 + KDJ(9,3,3) + RSI14 + 量比，标注该股所有策略信号日 + 各信号
收益。分时（新浪 1min）跨策略按 sym 去重复用，避免重复拉网。前端下拉切换策略与
股票，纯前端绘制（无后端请求）。
"""
import math
import numpy as np
import pandas as pd
from collections import Counter

import config as C
import tdx

KEY = "stock"
NAME = "个股"
DESC = "单只 K 线 + MA/ATR/KDJ/RSI 指标 + 该股策略信号逐日标注"
order = 30

STOCK_BARS = 480          # 每只展示的最近 K 线根数
STOCK_POOL_N = 20         # 展示池股票数（按信号数 top-N）


def build(ctx):
    sig_by_strat = ctx.shared.get("signals_full_by_strategy")
    if not sig_by_strat:
        sname = (ctx.args.strategy if ctx.args else None) or C.DEFAULT_STRATEGY
        sig_by_strat = {sname: ctx.shared.get("signals_full", [])}

    intraday_cache = {}   # sym -> intraday（跨策略去重复用）
    stocks_by_strategy = {}
    name_map = ctx.name_map

    for sname, signals_full in sig_by_strat.items():
        cnt = Counter(r["symbol"] for r in signals_full)
        # 信号最密集的前 STOCK_POOL_N 只
        densest = [sym for sym, _ in cnt.most_common(STOCK_POOL_N)]

        # 每只股票的全量信号（含各期限收益）+ 该股最新信号日
        sig_by_sym = {}
        latest_by_sym = {}      # sym -> 最新信号日 "YYYY-MM-DD"
        for r in signals_full:
            s = r["symbol"]
            sig_by_sym.setdefault(s, []).append(r)
            d = r["date"]
            if s not in latest_by_sym or d > latest_by_sym[s]:
                latest_by_sym[s] = d

        # 最新信号日最新的前 STOCK_POOL_N 只（9-22 出信号的票排前）
        latest = [sym for sym, _ in sorted(latest_by_sym.items(),
                                            key=lambda kv: kv[1], reverse=True)[:STOCK_POOL_N]]
        # 并集去重（保序），上限 2*N 控体积：兼顾"密集"与"最新信号日"两类浏览
        pool = []
        for sym in densest + latest:
            if sym not in pool:
                pool.append(sym)
            if len(pool) >= STOCK_POOL_N * 2:
                break

        # ohlcv 限定到展示池，groupby 一次
        pool_set = set(pool)
        sub = ctx.ohlcv[ctx.ohlcv["symbol"].isin(pool_set)]
        groups = {sym: g.sort_values("date").reset_index(drop=True)
                  for sym, g in sub.groupby("symbol", sort=False)}

        stocks = []
        for sym in pool:
            g = groups.get(sym)
            if g is None or len(g) < C.MIN_BARS:
                continue
            g = g.copy()
            if len(g) > STOCK_BARS:                  # 只取最近 STOCK_BARS 根（控体积）
                g = g.iloc[-STOCK_BARS:].copy()
            g = g.reset_index(drop=True)

            c = g["close"].astype(float)
            h = g["high"].astype(float)
            l = g["low"].astype(float)
            v = g["volume"].astype(float)
            ma3 = tdx.MA(c, 3); ma7 = tdx.MA(c, 7); ma28 = tdx.MA(c, 28); ma56 = tdx.MA(c, 56)
            atr14 = tdx.ATR(h, l, c, 14)
            k, d, j = tdx.KDJ(h, l, c, 9, 3, 3)
            rsi14 = tdx.RSI(c, 14)
            vr5 = tdx.vol_ratio(v, 5)

            # 该股在展示窗口内的信号日 + 索引
            date_to_idx = {d: i for i, d in enumerate(g["date"].astype(str))}
            sigs_all = sig_by_sym.get(sym, [])
            sigs_in = [s for s in sigs_all if s["date"] in date_to_idx]
            sig_idxs = [{
                "idx": date_to_idx[s["date"]],
                "date": s["date"],
                "close": s.get("close"),
                "ret1c": s.get("ret1c"),
                "ret3c": s.get("ret3c"), "ret5c": s.get("ret5c"),
                "ret5o": s.get("ret5o"), "ret10c": s.get("ret10c"),
                "pending": s.get("pending", False),
            } for s in sigs_in]

            # 最新一根的指标快照
            last = len(g) - 1
            cl = _v(c, last)
            at = _v(atr14, last)
            snap = {
                "close": cl,
                "ma3": _v(ma3, last), "ma7": _v(ma7, last),
                "ma28": _v(ma28, last), "ma56": _v(ma56, last),
                "atr14": at,
                "atr_pct": round(at / cl * 100, 2) if at and cl else None,
                "k": _v(k, last), "d": _v(d, last), "j": _v(j, last),
                "rsi14": _v(rsi14, last), "vol_ratio5": _v(vr5, last),
                "asof": g["date"].astype(str).iloc[last],
            }

            prev_close = float(g["close"].iloc[-2]) if len(g) >= 2 else None
            if sym in intraday_cache:
                intraday = intraday_cache[sym]
            else:
                intraday = _fetch_intraday(sym, ctx.data_asof, prev_close)
                intraday_cache[sym] = intraday
            stocks.append({
                "symbol": sym,
                "name": name_map.get(sym, sym),
                "board": tdx.board_tag(sym),
                "n_signals_total": len(sigs_all),
                "n_signals_win": len(sigs_in),
                "latest_sig_date": latest_by_sym.get(sym),
                "snap": snap,
                "sigs": sig_idxs,
                "intraday": intraday,
                "chart": {
                    "d": g["date"].astype(str).tolist(),
                    "o": _r(g["open"].astype(float).to_numpy()),
                    "h": _r(h.to_numpy()), "l": _r(l.to_numpy()),
                    "c": _r(c.to_numpy()), "v": _r(v.to_numpy()),
                    "ma3": _r(ma3.to_numpy()), "ma7": _r(ma7.to_numpy()),
                    "ma28": _r(ma28.to_numpy()), "ma56": _r(ma56.to_numpy()),
                    "atr14": _r(atr14.to_numpy()),
                    "k": _r(k.to_numpy()), "d": _r(d.to_numpy()),
                    "j": _r(j.to_numpy()), "rsi14": _r(rsi14.to_numpy()),
                },
            })
        stocks_by_strategy[sname] = stocks

    active = (ctx.args.strategy if ctx.args else None) or C.DEFAULT_STRATEGY
    if active not in stocks_by_strategy:
        active = list(stocks_by_strategy.keys())[0] if stocks_by_strategy else active

    return {
        "stocks": stocks_by_strategy.get(active, []),
        "stocks_by_strategy": stocks_by_strategy,
        "pool_n": STOCK_POOL_N,
        "active": active,
        "strategy_name": active,
    }


def _fetch_intraday(sym, asof, prev_close):
    """拉当日 1 分钟分时（新浪 stock_zh_a_minute period=1，子进程隔离）。

    返回 {date,t,price,avg,vol,prev_close} 或 None（失败/超时）。新浪源返回最近
    ~8 个交易日 1min K，筛 asof 当日约 240 根；avg=成交量加权累计均价。
    """
    import subprocess, sys, os, json, tempfile
    mod = os.path.dirname(os.path.abspath(__file__))
    worker = os.path.join(mod, "_ak_worker.py")
    out = os.path.join(tempfile.gettempdir(), f"sas_min_{sym}.pkl")
    kw = json.dumps({"symbol": sym, "period": "1", "adjust": ""})
    try:
        os.remove(out)
    except OSError:
        pass
    try:
        r = subprocess.run([sys.executable, worker, out, "stock_zh_a_minute", kw],
                           cwd=mod, timeout=40, stdout=subprocess.PIPE,
                           stderr=subprocess.PIPE)
    except Exception:
        return None
    if r.returncode != 0 or not os.path.exists(out):
        return None
    try:
        df = pd.read_pickle(out)
    except Exception:
        return None
    finally:
        try:
            os.remove(out)
        except OSError:
            pass
    if df is None or not len(df) or "day" not in df.columns:
        return None
    df = df.copy()
    df["day"] = df["day"].astype(str)
    d0 = df[df["day"].str[:10] == asof]
    if not len(d0):
        d0 = df.tail(240)
    d0 = d0.reset_index(drop=True)
    if not len(d0):
        return None
    price = [float(x) if pd.notna(x) else None for x in d0["close"].tolist()]
    vol = d0["volume"].astype(float)
    amt = d0["amount"].astype(float)
    csum_v = vol.cumsum().replace(0, np.nan)
    csum_a = amt.cumsum()
    avg = (csum_a / csum_v).ffill().tolist()
    t = d0["day"].str[11:16].tolist()
    return {
        "date": asof,
        "t": t,
        "price": _r(price),
        "avg": _r(avg),
        "vol": _r(vol.tolist()),
        "prev_close": round(prev_close, 3) if prev_close else None,
    }


def _v(series, i):
    """取 Series 第 i 个值，NaN→None。"""
    if i >= len(series):
        return None
    x = series.iloc[i]
    if x is None:
        return None
    try:
        xf = float(x)
    except (TypeError, ValueError):
        return None
    return xf if math.isfinite(xf) else None


def _r(arr):
    """numpy → list，round 3 位，NaN→null。"""
    out = []
    for x in arr:
        if x is None or (isinstance(x, float) and math.isnan(x)) or not np.isfinite(x):
            out.append(None)
        else:
            out.append(round(float(x), 3))
    return out
