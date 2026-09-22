# -*- coding: utf-8 -*-
"""策略模块 · 多策略通达信选股公式历史回测

遍历 config.STRATEGIES 所有策略，逐个跑 engine.run()，结果包成 section payload。
前端用策略选择卡切到不同策略看详情（选股 + 收益 + 净值曲线）。active 策略字段
展平到顶层兼容现有 renderStrategy。每个策略另算一条复利净值曲线（100万初始、
满仓均分当日信号、T+1 卖、逐日复利），供长期收益率查看与跨策略对比。
"""
import config as C
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
    for name in C.STRATEGIES:
        try:
            st = strategies.get_strategy(name)
        except Exception as e:
            print(f"  · 策略 {name} 加载失败：{type(e).__name__}: {e}")
            continue
        print(f"  · 策略 {name}（{st.NAME}）...")
        res = engine.run(ohlcv_pool, st, ctx.name_map,
                         shares_map=shares_map, verbose=True)
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

    ctx.shared["signals_full_by_strategy"] = signals_full_by_strategy
    # 兼容：个股模块旧入口仍读 signals_full
    active0 = getattr(args, "strategy", None) or C.DEFAULT_STRATEGY
    ctx.shared["signals_full"] = signals_full_by_strategy.get(active0, [])
    ctx.shared["strategy_name"] = active0

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
    }
