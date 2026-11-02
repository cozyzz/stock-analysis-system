# -*- coding: utf-8 -*-
"""策略模块 · 多策略通达信选股公式历史回测

遍历 config.STRATEGIES 所有策略，逐个跑 engine.run()，结果包成 section payload。
前端用策略选择卡切到不同策略看详情（选股 + 收益 + 净值曲线）。active 策略字段
展平到顶层兼容现有 renderStrategy。每个策略另算一条复利净值曲线（100万初始、
满仓均分当日信号、T+1 卖、逐日复利），供长期收益率查看与跨策略对比。
"""
import math
import time
from datetime import datetime, timedelta
from concurrent.futures import ThreadPoolExecutor, as_completed

import numpy as np
import pandas as pd

import config as C
import datasource as ds
import engine
import strategies

KEY = "strategy"
NAME = "策略"
DESC = "通达信选股公式历史回测：选股 + 收益 + 净值曲线 + 个股 K 线"
order = 20


def build(ctx):
    args = ctx.args
    shares_map = getattr(ctx, "shares_map", None) or {}

    # 过滤 ohlcv 到股票池（ST/板别已在 datasource.filter_pool 剔除）
    pool_set = set(ctx.pool_symbols) if ctx.pool_symbols else set()
    if pool_set:
        ohlcv_pool = ctx.ohlcv[ctx.ohlcv["symbol"].isin(pool_set)].copy()
    else:
        ohlcv_pool = ctx.ohlcv.copy()

    strategies_data = {}
    signals_full_by_strategy = {}
    strategy_list = []
    strat_names = {}      # key -> 策略显示名（信号追踪用）

    # 信号抽屉分时（1min）：跨策略按 sym 去重，每只只拉一次；仅最近~8交易日信号可得
    intraday_cache = {}                # sym -> raw 多日 df 或 None（None 也缓存避免重试）
    do_intraday = bool(C.FETCH_STRATEGY_INTRADAY)
    asof = getattr(ctx, "data_asof", None)
    cutoff = _intraday_cutoff(asof, C.INTRADAY_LOOKBACK_CAL_DAYS) if (do_intraday and asof) else None
    if do_intraday and not cutoff:
        print("  · 分时跳过：未取到 data_asof，无法定窗口")
    t_intraday = time.time() if cutoff else 0.0
    if hasattr(ds, "_intraday_cache_stats"):
        ds._intraday_cache_stats.update(hit=0, miss=0)

    for name in C.STRATEGIES:
        try:
            st = strategies.get_strategy(name)
        except Exception as e:
            print(f"  · 策略 {name} 加载失败：{type(e).__name__}: {e}")
            continue
        print(f"  · 策略 {name}（{st.NAME}）...")
        strat_names[name] = st.NAME
        res = engine.run(ohlcv_pool, st, ctx.name_map,
                         shares_map=shares_map, verbose=True)
        if cutoff:
            _attach_intraday(res["charts"], intraday_cache, cutoff)
        eq = res.get("equity", {})
        c1 = res["summary"].get("close_1", {}) or {}
        strategies_data[name] = {
            "strategy": {
                "key": name, "name": st.NAME, "desc": st.DESC,
                "author": st.AUTHOR, "params": st.PARAMS,
            },
            "summary": res["summary"],
            "by_date": res["by_date"],
            "distribution": res["distribution"],
            "equity": eq,
            "signals": res["signals"],
            "signals_total": res["signals_total"],
            "charts": res["charts"],
            "pending": res["pending"],
            "pool_size": res["pool_size"],
            "n_scanned": res["n_scanned"],
            "errors": res["errors"],
            "horizons": C.FORWARD_HORIZONS,
            "headline": C.HEADLINE_HORIZON,
        }
        signals_full_by_strategy[name] = res.get("signals_full", [])
        strategy_list.append({
            "key": name, "name": st.NAME, "desc": st.DESC,
            "signals_total": res["signals_total"],
            "t1_avg": c1.get("avg"),
            "t1_win": c1.get("win"),
            "pending": res["pending"],
            "total_ret": eq.get("total_ret"),
            "max_dd": eq.get("max_dd"),
            "n_days": eq.get("n_days"),
        })

    if cutoff:
        n_att = len(intraday_cache)
        n_fail = sum(1 for v in intraday_cache.values() if v is None)
        st = getattr(ds, "_intraday_cache_stats", {})
        nh, nm = st.get("hit", 0), st.get("miss", 0)
        print(f"  · 分时完成：去重 {n_att} 只，磁盘命中 {nh}/{nh+nm}，失败 {n_fail} 只，耗时 {time.time()-t_intraday:.0f}s")

    ctx.shared["signals_full_by_strategy"] = signals_full_by_strategy
    # 兼容：个股模块旧入口仍读 signals_full
    active0 = getattr(args, "strategy", None) or C.DEFAULT_STRATEGY
    ctx.shared["signals_full"] = signals_full_by_strategy.get(active0, [])
    ctx.shared["strategy_name"] = active0

    # 近期信号实盘追踪（脉冲5出口验证）：取最近 N 日信号，查 ohlcv 次日最高，
    # 按次日+5%限价命中→+5%、否则次日收盘走 算真实结局，汇总命中率/均收益 vs 全历史。
    signal_track = _signal_track(ctx, signals_full_by_strategy, strat_names,
                                 C.SIGNAL_TRACK_DAYS, ohlcv_pool)

    active = active0 if active0 in strategies_data else (
        list(strategies_data.keys())[0] if strategies_data else active0)

    # active 策略字段展平到顶层（兼容现有 renderStrategy）
    a = strategies_data.get(active, {})
    return {
        "strategies": strategies_data,
        "active": active,
        "list": strategy_list,
        "strategy": a.get("strategy", {}),
        "summary": a.get("summary", {}),
        "by_date": a.get("by_date", []),
        "distribution": a.get("distribution", {}),
        "equity": a.get("equity", {}),
        "signals": a.get("signals", []),
        "signals_total": a.get("signals_total", 0),
        "charts": a.get("charts", {}),
        "pending": a.get("pending", 0),
        "pool_size": a.get("pool_size", 0),
        "n_scanned": a.get("n_scanned", 0),
        "errors": a.get("errors", 0),
        "horizons": C.FORWARD_HORIZONS,
        "headline": C.HEADLINE_HORIZON,
        "signal_track": signal_track,
    }


# ============================================================================
# 信号抽屉分时（1min）· 附加到 build_charts 已生成的 charts 记录
# ============================================================================

def _intraday_cutoff(asof, cal_days):
    """asof("YYYY-MM-DD") - cal_days 天 → ISO 字符串（字典序即日期序）。asof 非法返回 None。"""
    try:
        d = datetime.strptime(asof, "%Y-%m-%d")
    except (TypeError, ValueError):
        return None
    return (d - timedelta(days=cal_days)).strftime("%Y-%m-%d")


def _attach_intraday(charts, cache, cutoff):
    """对 charts（key="sym|date"）中信号日 >= cutoff 的记录附加 intraday 字段。

    跨策略共享 cache（sym->raw df/None）去重，每只只拉一次。df 取到后按信号日切当日
    1min，算均价/昨收，写 rec["intraday"]。取不到（停牌/窗口外/网络失败）则不加，
    抽屉切"分时"显示空状态。
    """
    # 本策略需要拉取的 sym（信号日 >= cutoff），保序去重；need_date=该 sym 窗口内最新信号日
    need_dates = {}
    for key in charts:
        try:
            sym, sdate = key.rsplit("|", 1)
        except ValueError:
            continue
        if sdate < cutoff:
            continue
        if sym not in need_dates or sdate > need_dates[sym]:
            need_dates[sym] = sdate
    to_fetch = [s for s in need_dates if s not in cache]
    if to_fetch:
        _fetch_intraday_batch(to_fetch, cache, need_dates)
    # 逐记录切片附加
    for key, rec in charts.items():
        try:
            sym, sdate = key.rsplit("|", 1)
        except ValueError:
            continue
        if sdate < cutoff:
            continue
        df = cache.get(sym)
        if df is None:
            continue
        intr = _build_intraday_for_day(df, sdate)
        if intr:
            rec["intraday"] = intr


def _fetch_intraday_batch(syms, cache, need_dates):
    """顺序或并发拉多只 sym 的1min原始 df，填 cache（None 也缓存，避免重试）。
    need_dates: sym->该 sym 最新信号日，传给 fetch_minute_raw 做磁盘命中判定。

    默认顺序（INTRADAY_WORKERS=1，同个股模块，最稳）；>1 时子进程隔离并发，
    但 sina 并发易 SSL burst，失败该只回 None 不阻塞其他。
    """
    workers = max(1, getattr(C, "INTRADAY_WORKERS", 1))
    n = len(syms)
    if workers == 1 or n == 1:
        for i, sym in enumerate(syms):
            cache[sym] = ds.fetch_minute_raw(sym, need_date=need_dates.get(sym))
            if (i + 1) % 25 == 0:
                print(f"  · 分时取数 {i + 1}/{n}", flush=True)
        return
    with ThreadPoolExecutor(max_workers=workers) as ex:
        futs = {ex.submit(ds.fetch_minute_raw, sym, need_dates.get(sym)): sym for sym in syms}
        done = 0
        for fut in as_completed(futs):
            sym = futs[fut]
            try:
                cache[sym] = fut.result()
            except Exception:
                cache[sym] = None
            done += 1
            if done % 25 == 0:
                print(f"  · 分时取数 {done}/{n}", flush=True)


def _build_intraday_for_day(df, sdate):
    """从多日1min df 切出 sdate 当日，构造分时 {date,t,price,avg,vol,prev_close}。

    当日无数据返回 None。avg=当日累计VWAP（cumsum(amount)/cumsum(volume) 前填）；
    prev_close=同一 df 内 sdate 前一交易日的最后一根收盘（raw，与分时同口径，
    保证 % 轴自洽；sdate 是窗口最早日时取不到→None，渲染器降级不画昨收线）。
    """
    d0 = df[df["day"].str[:10] == sdate].reset_index(drop=True)
    if not len(d0):
        return None
    price = [float(x) if pd.notna(x) else None for x in d0["close"].tolist()]
    vol = d0["volume"].astype(float)
    amt = d0["amount"].astype(float)
    csum_v = vol.cumsum().replace(0, np.nan)
    csum_a = amt.cumsum()
    avg = (csum_a / csum_v).ffill().tolist()
    t = d0["day"].str[11:16].tolist()
    prev_close = _raw_prev_close(df, sdate)
    return {
        "date": sdate,
        "t": t,
        "price": _r(price),
        "avg": _r(avg),
        "vol": _r(vol.tolist()),
        "prev_close": round(prev_close, 3) if prev_close else None,
    }


def _raw_prev_close(df, sdate):
    """df 内 sdate 前一交易日的最后一根收盘（raw，与分时同口径）。取不到返回 None。"""
    days = df["day"].str[:10]
    prior = days[days < sdate]
    if not len(prior):
        return None
    last_day = prior.max()
    d_prev = df[days == last_day]
    if not len(d_prev):
        return None
    try:
        return float(d_prev["close"].iloc[-1])
    except (TypeError, ValueError):
        return None


def _r(arr):
    """序列 → list，round 3 位，NaN/None → null。"""
    out = []
    for x in arr:
        if x is None or (isinstance(x, float) and math.isnan(x)) or not np.isfinite(x):
            out.append(None)
        else:
            out.append(round(float(x), 3))
    return out


# ============================================================================
# 近期信号实盘追踪（脉冲5出口验证）· 嵌策略 tab
# ============================================================================

def _next_high_map(ohlcv_pool, cal, n_days):
    """(sym, date) → 次日 high（float）。只覆盖最近 n_days+6 个交易日，够信号追踪窗口用。

    信号日 d 的「次日」= d 在该 symbol 序列里的下一根 bar（T+1）。窗口内除最后一日外都有次日。
    """
    need = set(cal[-(n_days + 6):])
    sub = ohlcv_pool[ohlcv_pool["date"].isin(need)]
    nxt = {}
    for sym, g in sub.groupby("symbol", sort=False):
        g = g.sort_values("date").reset_index(drop=True)
        d = g["date"].astype(str).tolist()
        h = g["high"].astype(float).to_numpy()
        for i in range(len(g) - 1):
            nxt[(sym, str(d[i])[:10])] = float(h[i + 1])
    return nxt


def _signal_track(ctx, signals_full_by_strategy, strat_names, n_days, ohlcv_pool):
    """近期信号实盘追踪（脉冲5出口验证）。

    取最近 n_days 个交易日的信号（不重跑选股——用 engine 已算好的 signals_full），查 ohlcv
    次日最高，按用户实际出口算真实结局：
      次日9:25挂+5%限价 → 次日最高≥+5% 视为成交，收益=+5%（毛）；
      10点没成交撤单收盘走 → 否则按次日收盘卖，收益=ret1c（信号日收盘→次日收盘）。
    净收益=毛收益-交易成本（与净值曲线同口径 EQUITY_TX_COST）。pending（无次日，如最新交易日
    信号）不计入已实现。汇总近窗命中率/脉冲均收益/持有均收益 vs 全历史 T+1 均值，看实盘窗口
    里策略 edge 是否还在。纯本地 ohlcv，不碰东财，服务器可跑。
    """
    cal = ctx.calendar
    if not cal or len(cal) < n_days + 2 or not signals_full_by_strategy:
        return {}
    cutoff = str(cal[-n_days])[:10]
    nxt = _next_high_map(ohlcv_pool, cal, n_days)
    target = C.PULSE5_TARGET
    cost = C.EQUITY_TX_COST

    by_strategy = []
    by_day = {}
    recent = {}
    for key, sigs in signals_full_by_strategy.items():
        sigs_win = [r for r in sigs if str(r["date"])[:10] >= cutoff]
        # 全历史 T+1 均值（收盘→收盘，已实现）作对比基准
        hist_rets = [float(r["ret1c"]) for r in sigs if r.get("ret1c") is not None]
        hist_avg = (sum(hist_rets) / len(hist_rets)) if hist_rets else None

        realized = []
        n_pending = 0
        for r in sigs_win:
            if r.get("pending") or r.get("ret1c") is None:
                n_pending += 1
                continue
            nh = nxt.get((r["symbol"], str(r["date"])[:10]))
            if nh is None or not np.isfinite(float(nh)):
                n_pending += 1
                continue
            close = float(r["close"])
            nh = float(nh)
            hit = nh >= close * (1 + target)
            pulse_gross = target if hit else float(r["ret1c"])
            pulse_net = pulse_gross - cost
            realized.append({
                "symbol": r["symbol"],
                "name": r.get("name", r["symbol"]),
                "date": str(r["date"])[:10],
                "close": round(close, 2),
                "next_high": round(nh, 2),
                "next_high_pct": round(nh / close - 1, 4),
                "hit": bool(hit),
                "pulse_ret": round(pulse_net, 4),
                "hold_ret": round(float(r["ret1c"]), 4),
            })
        n_real = len(realized)
        n_hits = sum(1 for x in realized if x["hit"])
        avg_pulse = (sum(x["pulse_ret"] for x in realized) / n_real) if n_real else None
        avg_hold = (sum(x["hold_ret"] for x in realized) / n_real) if n_real else None
        by_strategy.append({
            "key": key, "name": strat_names.get(key, key),
            "n_recent": len(sigs_win), "n_realized": n_real, "n_pending": n_pending,
            "n_hits": n_hits,
            "hit_rate": round(n_hits / n_real, 3) if n_real else None,
            "avg_pulse": round(avg_pulse, 4) if avg_pulse is not None else None,
            "avg_hold": round(avg_hold, 4) if avg_hold is not None else None,
            "hist_avg": round(hist_avg, 4) if hist_avg is not None else None,
        })
        # by_day（按信号日升序）：供前端画近窗脉冲累计净值曲线
        bd = {}
        for x in realized:
            v = bd.setdefault(x["date"], {"date": x["date"], "n": 0, "hits": 0, "sp": 0.0})
            v["n"] += 1
            v["hits"] += 1 if x["hit"] else 0
            v["sp"] += x["pulse_ret"]
        by_day[key] = [{"date": v["date"], "n": v["n"], "hits": v["hits"],
                        "avg_pulse": round(v["sp"] / v["n"], 4)}
                       for v in sorted(bd.values(), key=lambda x: x["date"])]
        recent[key] = sorted(realized, key=lambda x: (x["date"], x["symbol"]), reverse=True)[:40]

    return {
        "n_days": n_days,
        "target_pct": round(target * 100, 1),
        "tx_cost_pct": round(cost * 100, 2),
        "by_strategy": by_strategy,
        "by_day": by_day,
        "recent": recent,
    }
