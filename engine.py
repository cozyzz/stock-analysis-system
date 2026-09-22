# -*- coding: utf-8 -*-
"""策略回测引擎

在 ohlcv 缓存（已过滤股票池）上跑策略 select()，计算选出后 1/3/5/10 日前向收益，
汇总统计 + 按信号日聚合（收益曲线）+ 打包 per-signal K 线数据（列式压缩）。

前向收益口径：
  retNc = close[T+N] / close[T] - 1        信号日收盘买、T+N 收盘卖（headline）
  retNo = close[T+N] / open[T+1] - 1       次日开盘买、T+N 收盘卖（实盘口径）
  T 为信号日；T+N 不存在（最新信号）→ None，标 pending，不进已实现统计。
"""
import math
import numpy as np
import pandas as pd

import config as C
import tdx


def run(ohlcv_pool, strategy, name_map, shares_map=None, verbose=True):
    """跑全池回测。返回 dict(signals, summary, by_date, distribution, pending, pool_size, n_scanned, errors)。"""
    pool_syms = list(dict.fromkeys(ohlcv_pool["symbol"]))  # 保序去重
    # groupby 一次成 dict（绝不循环内 filter，O(n²)）
    groups = {}
    for sym, g in ohlcv_pool.groupby("symbol", sort=False):
        groups[sym] = g.sort_values("date").reset_index(drop=True)

    signals = []
    n_scanned = 0
    errors = 0
    for sym in pool_syms:
        g = groups.get(sym)
        if g is None or len(g) < C.MIN_BARS:
            continue
        n_scanned += 1
        g = g.copy()
        g["symbol"] = sym
        if shares_map:
            _ts, _fs = shares_map.get(sym, (float("nan"), float("nan")))
            g["total_shares"] = float(_ts)
            g["float_shares"] = float(_fs)
        try:
            xg = strategy.select(g)
            xg = xg.fillna(False).astype(bool)
        except Exception as e:
            errors += 1
            if verbose:
                print(f"  {sym} select 失败：{type(e).__name__}: {str(e)[:80]}")
            continue
        idxs = list(g.index[xg])
        if not idxs:
            continue
        c = g["close"].astype(float).to_numpy()
        o = g["open"].astype(float).to_numpy()
        dts = g["date"].astype(str).to_numpy()
        n = len(g)
        name = name_map.get(sym, sym)
        for i in idxs:
            if not np.isfinite(c[i]) or c[i] <= 0:
                continue
            rec = {
                "symbol": sym, "name": name, "date": dts[i],
                "close": float(c[i]), "board": tdx.board_tag(sym),
                "pending": (i + C.HEADLINE_HORIZON >= n),
            }
            for k in C.FORWARD_HORIZONS:
                ik = i + k
                # 收盘→收盘
                rec[f"ret{k}c"] = (
                    float(c[ik] / c[i] - 1.0)
                    if ik < n and np.isfinite(c[ik]) else None
                )
                # 次日开盘→收盘
                io = i + 1
                rec[f"ret{k}o"] = (
                    float(c[ik] / o[io] - 1.0)
                    if (io < n and ik < n and np.isfinite(o[io]) and np.isfinite(c[ik]) and o[io] > 0)
                    else None
                )
            # 隔日开盘卖口径：信号日收盘买→次日开盘卖（o[i+1]/c[i]-1）
            # 供 SELL_BASIS="open_next" 的策略覆盖 ret1c（尾盘买→次日竞价/早盘卖的真实节奏）
            io1 = i + 1
            rec["ret1os"] = (
                float(o[io1] / c[i] - 1.0)
                if io1 < n and np.isfinite(o[io1]) and o[io1] > 0 else None
            )
            signals.append(rec)

    if verbose:
        print(f"  扫描 {n_scanned} 只，命中信号 {len(signals)} 个（{errors} 只 select 报错）")

    # 策略可声明 SELL_BASIS="open_next"：用「收盘买→次日开盘卖」(o[i+1]/c[i]-1) 覆盖 T+1 的
    # ret1c，对应尾盘买→次日竞价/早盘卖的真实节奏。仅 T+1，多日口径不变。其他策略默认收盘卖。
    basis = getattr(strategy, "SELL_BASIS", "close")
    if basis == "open_next":
        for r in signals:
            v = r.get("ret1os")
            if v is not None:
                r["ret1c"] = v
            if "ret1os" in r:           # 临时字段不进 HTML / signals_full
                del r["ret1os"]
        if verbose:
            print(f"  [口径] SELL_BASIS=open_next：T+1 按「收盘买→次日开盘卖」算（o[i+1]/c[i]-1）")

    summary = _summarize(signals)
    by_date = _by_date(signals)
    distribution = _distribution(signals)
    # 表格与 K 线图都只取最近 N 个，控制文件体积；统计用全量
    signals_sorted = sorted(signals, key=lambda r: r["date"], reverse=True)
    signals_capped = signals_sorted[: C.MAX_KLINE_SIGNALS * 3]
    charts = build_charts(signals_sorted[: C.MAX_KLINE_SIGNALS], groups)

    return {
        "signals": signals_capped,
        "signals_full": signals,   # 全量（仅内存共享给个股模块选展示池，不进 HTML）
        "signals_total": len(signals),
        "summary": summary,
        "by_date": by_date,
        "distribution": distribution,
        "equity": _equity_curve(by_date, C.HEADLINE_HORIZON, C.EQUITY_TX_COST),
        "pending": sum(1 for r in signals if r.get("pending")),
        "pool_size": len(pool_syms),
        "n_scanned": n_scanned,
        "errors": errors,
        "charts": charts,
    }


def _summarize(signals):
    """分期限统计：avg/median/winrate/best/worst/count。"""
    out = {}
    for k in C.FORWARD_HORIZONS:
        key = f"ret{k}c"
        vals = [r[key] for r in signals if r.get(key) is not None]
        out[f"close_{k}"] = _stats(vals)
        keyo = f"ret{k}o"
        valso = [r[keyo] for r in signals if r.get(keyo) is not None]
        out[f"open_{k}"] = _stats(valso)
    return out


def _stats(vals):
    if not vals:
        return {"n": 0, "avg": None, "median": None, "win": None, "best": None, "worst": None}
    arr = np.array(vals, dtype=float)
    return {
        "n": int(len(arr)),
        "avg": float(arr.mean()),
        "median": float(np.median(arr)),
        "win": float((arr > 0).mean()),
        "best": float(arr.max()),
        "worst": float(arr.min()),
    }


def _by_date(signals):
    """按信号日聚合：date / count / 每期限 avg{N}c / avg{N}o。供收益曲线（前端选期限）。"""
    rows = {}
    for r in signals:
        if r.get("pending"):
            continue
        d = r["date"]
        if d not in rows:
            x = {"date": d, "n": 0}
            for k in C.FORWARD_HORIZONS:
                x[f"sum{k}c"] = 0.0; x[f"n{k}c"] = 0
                x[f"sum{k}o"] = 0.0; x[f"n{k}o"] = 0
            rows[d] = x
        x = rows[d]
        x["n"] += 1
        for k in C.FORWARD_HORIZONS:
            vc = r.get(f"ret{k}c")
            if vc is not None:
                x[f"sum{k}c"] += vc; x[f"n{k}c"] += 1
            vo = r.get(f"ret{k}o")
            if vo is not None:
                x[f"sum{k}o"] += vo; x[f"n{k}o"] += 1
    out = []
    for d in sorted(rows):
        x = rows[d]
        row = {"date": d, "n": x["n"]}
        for k in C.FORWARD_HORIZONS:
            row[f"avg{k}c"] = (x[f"sum{k}c"] / x[f"n{k}c"]) if x[f"n{k}c"] else None
            row[f"avg{k}o"] = (x[f"sum{k}o"] / x[f"n{k}o"]) if x[f"n{k}o"] else None
        out.append(row)
    return out


def _distribution(signals):
    """收益分布直方图（5% 一档，-30%..+30%），按各期限分别统计。
    返回 {bins, by_h: {"1":[counts], "3":..., "5":..., "10":...}} 供前端切换。
    """
    bins = list(range(-30, 35, 5))
    nb = len(bins) - 1
    by_h = {str(k): [0] * nb for k in C.FORWARD_HORIZONS}
    for r in signals:
        for k in C.FORWARD_HORIZONS:
            v = r.get(f"ret{k}c")
            if v is None:
                continue
            pct = v * 100
            if pct < bins[0]:
                idx = 0
            elif pct >= bins[-1]:
                idx = nb - 1
            else:
                idx = int((pct - bins[0]) / 5)
            by_h[str(k)][idx] += 1
    return {"bins": [f"{b}%" for b in bins], "by_h": by_h}


def _equity_curve(by_date, horizon, cost=0.0):
    """逐信号日复利净值（满仓均分当日信号、T+horizon 收盘卖、逐日复利）。

    by_date 每行 avg{horizon}c = 当日信号均权收益。起始净值 1.0；无信号日净值
    持平（资金空仓）。cost=每轮买卖成本（印花税+佣金+滑点），每信号日扣一次。
    返回 dates/equity/total_ret/max_dd/n_days/tx_cost。
    简化口径（均权 + 满仓 + 逐日复利），非真实组合（信号重叠/资金管理未精确建模）。
    """
    key = f"avg{horizon}c"
    dates, eq = [], []
    e = 1.0; peak = 1.0; max_dd = 0.0
    for row in by_date:
        r = row.get(key)
        if r is None:
            continue
        e *= (1.0 + r) * (1.0 - cost)
        if e > peak:
            peak = e
        dd = (peak - e) / peak if peak > 0 else 0.0
        if dd > max_dd:
            max_dd = dd
        dates.append(row["date"])
        eq.append(round(e, 4))
    return {
        "dates": dates, "equity": eq,
        "total_ret": round(e - 1.0, 4),
        "max_dd": round(max_dd, 4),
        "n_days": len(dates), "horizon": horizon, "tx_cost": cost,
    }


def build_charts(signals, groups):
    """为给定信号打包列式 K 线数据，key = "sym|date"。

    每个信号取锚定日前后共 CHART_BARS+CHART_PAD_AFTER 根，带：
    OHLCV + MA3/7/28/56 + RSI14 + KDJ(9,3,3) + MACD + ATR14 + 量比5
    + 涨停价/跌停价；并附信号日指标快照 snap（供抽屉数值卡）。
    """
    out = {}
    for sig in signals:
        sym = sig["symbol"]
        date = sig["date"]
        g = groups.get(sym)
        if g is None:
            continue
        g = g.reset_index(drop=True)
        try:
            anchor = g.index[g["date"] == date][0]
        except (KeyError, IndexError):
            continue
        start = max(0, anchor - (C.CHART_BARS - C.CHART_PAD_AFTER))
        end = min(len(g), anchor + C.CHART_PAD_AFTER + 1)
        sl = g.iloc[start:end].copy()
        if len(sl) < 10:
            continue
        c = sl["close"].astype(float)
        v = sl["volume"].astype(float)
        h = sl["high"].astype(float)
        l = sl["low"].astype(float)
        ma3 = tdx.MA(c, 3); ma7 = tdx.MA(c, 7); ma28 = tdx.MA(c, 28); ma56 = tdx.MA(c, 56)
        rsi = tdx.RSI(c, 14)
        k, d, j = tdx.KDJ(h, l, c)
        dif, dea, bar = tdx.MACD(c)
        atr = tdx.ATR(h, l, c, 14)
        vr = tdx.vol_ratio(v, 5)
        prev_c = c.shift(1)
        zt_up = tdx.zt_price(prev_c, sym)
        ratio = tdx.limit_up_ratio(sym)
        zt_dn_arr = np.floor(prev_c.to_numpy(dtype=float) * (1.0 - ratio) * 100.0 + 0.5) / 100.0
        zt_dn = pd.Series(zt_dn_arr, index=prev_c.index)
        si = int(anchor - start)

        def _sv(s):
            try:
                x = float(s.iloc[si])
                return round(x, 3) if np.isfinite(x) else None
            except Exception:
                return None

        snap_c = _sv(c)
        snap_atr = _sv(atr)
        snap_open = _sv(sl["open"].astype(float))
        snap_high = _sv(h)
        snap_low = _sv(l)
        snap_prev = _sv(prev_c)
        snap = {
            "open": snap_open, "high": snap_high, "low": snap_low, "close": snap_c,
            "prev_close": snap_prev,
            "chg_pct": round((snap_c / snap_prev - 1) * 100, 2) if (snap_c and snap_prev) else None,
            "ma3": _sv(ma3), "ma7": _sv(ma7), "ma28": _sv(ma28), "ma56": _sv(ma56),
            "rsi14": _sv(rsi), "k": _sv(k), "d": _sv(d), "j": _sv(j),
            "dif": _sv(dif), "dea": _sv(dea), "bar": _sv(bar),
            "atr14": snap_atr,
            "atr_pct": round(snap_atr / snap_c * 100, 2) if (snap_atr and snap_c) else None,
            "vr5": _sv(vr),
            "zt_up": _sv(zt_up), "zt_dn": _sv(zt_dn),
        }
        rec = {
            "d": sl["date"].astype(str).tolist(),
            "o": _r(sl["open"].astype(float).to_numpy()),
            "h": _r(h.to_numpy()),
            "l": _r(l.to_numpy()),
            "c": _r(c.to_numpy()),
            "v": _r(v.to_numpy()),
            "ma3": _r(ma3.to_numpy()),
            "ma7": _r(ma7.to_numpy()),
            "ma28": _r(ma28.to_numpy()),
            "ma56": _r(ma56.to_numpy()),
            "rsi": _r(rsi.to_numpy()),
            "k": _r(k.to_numpy()), "d": _r(d.to_numpy()), "j": _r(j.to_numpy()),
            "dif": _r(dif.to_numpy()), "dea": _r(dea.to_numpy()), "bar": _r(bar.to_numpy()),
            "atr": _r(atr.to_numpy()), "vr": _r(vr.to_numpy()),
            "zt_up": _r(zt_up.to_numpy()), "zt_dn": _r(zt_dn.to_numpy()),
            "signal_idx": si,
            "snap": snap,
        }
        out[f"{sym}|{date}"] = rec
    return out


def _r(arr):
    """numpy → list，round 3 位，NaN→null。"""
    out = []
    for x in arr:
        if x is None or (isinstance(x, float) and math.isnan(x)) or not np.isfinite(x):
            out.append(None)
        else:
            out.append(round(float(x), 3))
    return out
