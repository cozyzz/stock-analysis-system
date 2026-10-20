# -*- coding: utf-8 -*-
"""活跃市值模块 · 0AmV 活筹指数波段统计

数据源：akshare 沪深两市成交额（上证指数 sh000001 + 深证综指 sz399106，东财源
stock_zh_index_daily_em），走子进程拉取（与 market 模块同款 _ak_subproc，隔离 V8）。
0AmV 活筹指数 ≈ 沪深两市日成交额的「近 10 日之和」（社区移植公式，2026-09-29 核验：
末日 18.05 万亿对用户参考 17.96 万亿；SMA(10,1) 平均仅 1.84 万亿、差 10 倍，故用求和
非平均）。同花顺私有 0AmV 无公开 API，此为成交额口径近似，量级与趋势一致。

波段定义（2026-09-29 改数据源后沿用，阈值在 config）：
  起点：2日累计涨幅 r2 = S[t]/S[t-2]-1 > 4%
  终点：首个 j>t 使 日跌幅 r1[j] <= -2%（单日跌≥2%→完整波段结束）
  波段收益 = S[end]/S[start]-1（含终结日跌幅）；波段内最高 = max(S[s..e])/S[s]-1
MA10 = 0AmV 的 10 日简单移动平均。

注意：0AmV 是成交额口径，波动远大于总市值；4%/-2% 阈值原为总市值口径所定，迁过来
  波段数可能大增，需看实测再调（config.ACTIVE_MKTCAP_THRESHOLD/END_DROP）。
口径：成交额 = 上证全市场 + 深证综指（主板+中小板）；创业板(399006)未含，后续可补。
"""
import numpy as np
import pandas as pd

import config as C

KEY = "active_mktcap"
NAME = "活跃市值"
DESC = "0AmV活筹指数波段统计（2日涨>4%触发·单日跌≥2%终结）"
order = 15  # 全景大盘之后

THRESHOLD = C.ACTIVE_MKTCAP_THRESHOLD    # 2日累计涨幅 4%（触发）
END_DROP = C.ACTIVE_MKTCAP_END_DROP      # 单日跌幅 -2%（终结，完整波段）
MA_N = C.ACTIVE_MKTCAP_MA                # MA 均线周期
ROLL_SUM_N = 10                          # 0AmV = 近 N 日成交额之和（核验定）
RECENT_N = 12                            # 最近 N 个波段列表

_SH_SYM = "sh000001"   # 上证指数（全市场）
_SZ_SYM = "sz399106"   # 深证综指（主板+中小板，全市场口径；399001 仅 500 成分股偏低）


def build(ctx):
    from modules.market import _ak_subproc, _safe
    errors = []

    ok_sh, df_sh, _ = _safe(
        "上证成交额",
        lambda: _ak_subproc("stock_zh_index_daily_em", {"symbol": _SH_SYM},
                            tries=3, timeout=60, tag="idx_sh"),
        errors)
    ok_sz, df_sz, _ = _safe(
        "深证成交额",
        lambda: _ak_subproc("stock_zh_index_daily_em", {"symbol": _SZ_SYM},
                            tries=3, timeout=60, tag="idx_sz"),
        errors)
    if not (ok_sh and ok_sz and df_sh is not None and df_sz is not None and len(df_sh) and len(df_sz)):
        return {"empty": True, "errors": errors}

    def _amt(df):
        d = df.copy()
        d["日期"] = pd.to_datetime(d["日期"])
        d = d.sort_values("日期").drop_duplicates(subset="日期", keep="last").reset_index(drop=True)
        col = "成交额" if "成交额" in d.columns else None
        if col is None:
            return None
        d[col] = pd.to_numeric(d[col], errors="coerce")
        return d[["日期", col]].dropna().rename(columns={col: "amt"})

    sh = _amt(df_sh)
    sz = _amt(df_sz)
    if sh is None or sz is None:
        return {"empty": True, "errors": errors + [{"name": "活跃市值", "err": "无成交额列"}]}

    m = pd.merge(sh, sz, on="日期", how="inner", suffixes=("_sh", "_sz"))
    m["total"] = m["amt_sh"] + m["amt_sz"]              # 元
    m["total_wy"] = m["total"] / 1e12                    # 万亿（每日成交额）
    m = m.sort_values("日期").reset_index(drop=True)
    if not len(m):
        return {"empty": True, "errors": errors + [{"name": "活跃市值", "err": "合并后无行"}]}

    # 0AmV = 近 ROLL_SUM_N 日成交额之和（万亿）；前 N-1 个为 NaN
    S = m["total_wy"].rolling(ROLL_SUM_N).sum().values
    dates_raw = m["日期"].dt.strftime("%Y-%m-%d").tolist()
    ma10 = pd.Series(S).rolling(MA_N).mean().values
    r1 = np.full(len(S), np.nan)
    r1[1:] = S[1:] / S[:-1] - 1.0
    r2 = np.full(len(S), np.nan)
    r2[2:] = S[2:] / S[:-2] - 1.0

    # ---- 波段检测（跳过 leading NaN）----
    bands = []
    in_band = False
    start = None
    for t in range(len(S)):
        if not np.isfinite(S[t]):
            continue
        if not in_band:
            if t >= 2 and np.isfinite(r2[t]) and r2[t] > THRESHOLD:
                start = t
                in_band = True
        else:
            if t > start and np.isfinite(r1[t]) and r1[t] <= END_DROP:
                bands.append((start, t))
                in_band = False
    censored = 1 if in_band else 0

    # ---- 波段收益 ----
    band_rows = []
    for (s, e) in bands:
        seg = S[s:e + 1]
        ret = S[e] / S[s] - 1.0
        peak = float(seg.max()) / S[s] - 1.0
        band_rows.append({"s": s, "e": e, "ret": ret, "peak": peak, "len": e - s})

    # ---- 统计 ----
    def _stats(arr):
        a = np.asarray([x for x in arr if np.isfinite(x)])
        if not len(a):
            return {}
        return {"n": len(a), "avg": float(a.mean()), "med": float(np.median(a)),
                "win": float((a > 0).mean()), "min": float(a.min()), "max": float(a.max()),
                "p25": float(np.percentile(a, 25)), "p75": float(np.percentile(a, 75))}

    rets = np.array([b["ret"] for b in band_rows]) if band_rows else np.array([])
    peaks = np.array([b["peak"] for b in band_rows]) if band_rows else np.array([])
    lens = np.array([b["len"] for b in band_rows]) if band_rows else np.array([])
    rs = _stats(rets)
    ps = _stats(peaks)
    ls = _stats(lens)
    comp = float(np.prod(1 + rets) - 1.0) if len(rets) else 0.0
    yrs = (m["日期"].iloc[-1] - m["日期"].iloc[0]).days / 365.25
    ann = ((1 + comp) ** (1 / yrs) - 1.0) if (yrs > 0 and (1 + comp) > 0) else 0.0

    # ---- 逐年 ----
    by_year = []
    if band_rows:
        bd = pd.DataFrame([{"y": pd.Timestamp(dates_raw[b["s"]]).year,
                             "ret": b["ret"], "len": b["len"]} for b in band_rows])
        for y, g in bd.groupby("y"):
            by_year.append({"year": int(y), "n": len(g),
                            "avg": float(g["ret"].mean()), "med": float(g["ret"].median()),
                            "win": float((g["ret"] > 0).mean()), "avg_len": float(g["len"].mean())})

    # ---- 序列（图表用，万亿；leading NaN→None）----
    v_trk = [round(float(x), 2) if np.isfinite(x) else None for x in S]
    ma_trk = [round(float(x), 2) if np.isfinite(x) else None for x in ma10]

    return {
        "empty": False,
        "asof": dates_raw[-1],
        "since": dates_raw[0],
        "span_years": round(yrs, 1),
        "threshold": THRESHOLD,
        "end_drop": END_DROP,
        "latest": v_trk[-1],                 # 万亿
        "ma10_latest": ma_trk[-1],           # 万亿
        "ma10_mean": round(float(np.nanmean(ma10)), 2),
        "total_ret": float(S[-1] / S[ROLL_SUM_N - 1] - 1.0) if len(S) > ROLL_SUM_N else 0.0,
        "n_bands": len(band_rows),
        "censored": censored,
        "ret": rs,
        "peak": ps,
        "len": ls,
        "comp": comp,
        "ann": ann,
        "giveback_avg": float((peaks - rets).mean()) if len(rets) else 0.0,
        "giveback_med": float(np.median(peaks - rets)) if len(rets) else 0.0,
        "series": {"d": dates_raw, "v": v_trk, "ma": ma_trk},
        "bands": band_rows,
        "by_year": by_year,
        "recent": band_rows[-RECENT_N:],
        "errors": errors,
    }
