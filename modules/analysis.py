# -*- coding: utf-8 -*-
"""分析模块 · 策略横截面分析

回答"这个策略在什么行情 / 什么板别 / 什么时段里有效"。离线，复用策略模块共享的
全量信号（ctx.shared["signals_full"]）。四个子分析：
  A 板别横截面    主板/中小板/其他的 5 日收益/中位/胜率/信号占比
  B 年度漂移      信号数 + 5 日均值 + 胜率随年份（看策略是否衰减）
  C 月度密度散点  月信号数 vs 月均 5 日收益（信号密集期=情绪过热？收益更差？）
  D 持有期衰减    1/3/5/10 日 avg/中位/胜率曲线
"""
import numpy as np

import config as C

KEY = "analysis"
NAME = "分析"
DESC = "策略横截面：板别 / 年度漂移 / 月度密度 / 持有期衰减"
order = 40


def build(ctx):
    sigs = ctx.shared.get("signals_full", [])
    realized5 = [r for r in sigs if r.get("ret5c") is not None]

    by_board = _group(realized5, lambda r: r.get("board") or "其他")
    by_year = _group(realized5, lambda r: r["date"][:4])
    by_month = _group(realized5, lambda r: r["date"][:7])

    # 月度密度散点：过滤信号过少的月（<3 个）降噪
    month_scatter = [{"month": g["key"], "n": g["n"], "avg5c": g["avg"]}
                     for g in by_month if g["n"] >= 3]

    # 持有期衰减
    decay = [_stats(k, [r[f"ret{k}c"] for r in sigs if r.get(f"ret{k}c") is not None])
             for k in C.FORWARD_HORIZONS]

    return {
        "by_board": by_board,
        "by_year": by_year,
        "month_scatter": month_scatter,
        "horizon_decay": decay,
        "n_realized": len(realized5),
        "n_total": len(sigs),
    }


def _group(rows, keyfn):
    buckets = {}
    for r in rows:
        k = keyfn(r)
        buckets.setdefault(k, []).append(r["ret5c"])
    out = []
    for k in sorted(buckets):
        arr = np.array(buckets[k], dtype=float)
        out.append({
            "key": k, "n": int(len(arr)),
            "avg": float(arr.mean()),
            "median": float(np.median(arr)),
            "win": float((arr > 0).mean()),
        })
    return out


def _stats(k, vals):
    if not vals:
        return {"k": k, "n": 0, "avg": None, "median": None, "win": None}
    arr = np.array(vals, dtype=float)
    return {
        "k": k, "n": int(len(arr)),
        "avg": float(arr.mean()),
        "median": float(np.median(arr)),
        "win": float((arr > 0).mean()),
    }
