# -*- coding: utf-8 -*-
"""分析模块 · 当日大盘情况分析

盘后跑 run.py 生成一份当日大盘盘后分析报告（文字总结为主）。**复用全景大盘模块
同一轮 akshare 拉取的结果**（ctx.shared["market"]），不二次请求——东财系接口在服务器
IP 限流，二次拉很可能失败。market(order=10) 先于 analysis(40) 构建，共享已就绪。

七节（对应用户模板）：
  一、大盘分析       指数收盘/涨跌幅 + 温度计评分 + 30日回撤 + MA20/60乖离 + 大盘vs成长
  二、情绪与赚钱     涨跌家数/涨停跌停/封板率/炸板 + 情绪评分；连板·晋级率 Phase2
  三、流动性         市值集中度(HHI/Gini/Top占比) + 风险评分/仓位；两融/择时 Phase2
  四、拥挤度与资金   主力净流入(同花顺,已抓) + 拥挤度近似；个股流 Phase2
  五、成交与事件     Phase2（两市成交额需扩指数日线 amount；电报走东财易限流）
  六、技术形态与位   每指数 MA/MACD/RSI6/KDJ/ADX/Pivot/R1/S1/支撑压力（指数近120日算）
  七、综合判断       规则化文字合成

数据铁律：拿不到就标「数据获取失败/Phase2 待接入」，不网络回填、不臆测、不编。
温度计/情绪/风险评分为**本系统启发式测算**（非外部数据源口径），UI 已标注。
"""
import math

import numpy as np
import pandas as pd

import config as C

KEY = "analysis"
NAME = "分析"
DESC = "当日大盘情况分析（盘后文字总结 + 技术面/集中度/资金流）"
order = 40


def build(ctx):
    shared = getattr(ctx, "shared", None) or {}
    mkt = shared.get("market") or {}
    indices = mkt.get("indices") or []
    thermo = mkt.get("thermometer") or {}
    sentiment = mkt.get("sentiment") or {}
    distrib = mkt.get("distribution") or {}
    fund_sector = mkt.get("fund_sector") or []
    asof = mkt.get("asof") or ctx.data_asof

    degraded = not bool(indices)

    # 一+六：每指数技术面（MA/回撤/MACD/RSI6/KDJ/ADX/Pivot/支撑压力/趋势）
    idx_tech = [_index_tech(x) for x in indices]

    # 大盘 vs 成长
    diverge = _divergence(indices)

    # 二/三：评分（本系统测算）
    thermo_score = _thermo_score(thermo, sentiment)
    senti_score = _sentiment_score(thermo, sentiment)
    risk_score, position = _risk_score(thermo, sentiment, idx_tech)

    # 三：市值集中度
    conc = _concentration(ctx.universe)

    # 四：拥挤度近似（暂以 |主力净流入| 排序，换手率口径 Phase2）
    crowding = _crowding_approx(fund_sector)

    # 七：综合判断文字
    judgment = _judgment(thermo, sentiment, indices, idx_tech,
                         thermo_score, senti_score, risk_score, position,
                         diverge, conc)

    return {
        "asof": asof,
        "phase": 1,
        "degraded": degraded,
        "scores": {
            "thermo": thermo_score,
            "sentiment": senti_score,
            "risk": risk_score,
            "position": position,
        },
        "indices_tech": idx_tech,
        "thermometer": thermo,
        "sentiment": sentiment,
        "distribution": distrib,
        "concentration": conc,
        "divergence": diverge,
        "fund_sector": fund_sector,
        "crowding": crowding,
        "judgment": judgment,
        # Phase2 待接入项（前端如实展示「待接入」，不假装有数据）
        "pending": [
            "两融余额/融资买入/维持担保比/流动性择时信号",
            "连板梯队·晋级率·炸板率明细（涨停池）",
            "两市成交额·成交额宽度分组",
            "电报/业绩/政策事件驱动",
        ],
    }


# ============================ 技术指标（纯 numpy，无新依赖） ============================

def _to_np(a):
    try:
        return np.array([float(v) for v in (a or [])], dtype=float)
    except (TypeError, ValueError):
        return np.array([], dtype=float)


def _ffill(a):
    """前向/后向填充缺失，指数日线防御性处理（正常应为连续序列）。"""
    s = pd.Series(a, dtype=float)
    return s.ffill().bfill().to_numpy()


def _last(arr):
    if arr is None:
        return None
    arr = np.asarray(arr, dtype=float)
    f = arr[np.isfinite(arr)]
    return float(f[-1]) if len(f) else None


def _sma(c, n):
    c = np.asarray(c, float)
    return pd.Series(c).rolling(n).mean().to_numpy()


def _ema(c, span):
    c = np.asarray(c, float)
    return pd.Series(c).ewm(span=span, adjust=False).mean().to_numpy()


def _macd(c):
    dif = _ema(c, 12) - _ema(c, 26)
    dea = _ema(dif, 9)
    bar = (dif - dea) * 2
    return dif, dea, bar


def _rsi(c, n=6):
    """Wilder RSI。"""
    c = np.asarray(c, float)
    m = len(c)
    out = np.full(m, np.nan)
    if m <= n:
        return out
    d = np.diff(c)
    up = np.where(d > 0, d, 0.0)
    dn = np.where(d < 0, -d, 0.0)
    au = up[:n].mean()
    ad = dn[:n].mean()
    out[n] = 100 - 100 / (1 + (au / ad if ad else np.inf))
    for i in range(n + 1, m):
        au = (au * (n - 1) + up[i - 1]) / n
        ad = (ad * (n - 1) + dn[i - 1]) / n
        out[i] = 100 - 100 / (1 + (au / ad if ad else np.inf))
    return out


def _kdj(h, l, c, n=9):
    h = np.asarray(h, float)
    l = np.asarray(l, float)
    c = np.asarray(c, float)
    m = len(c)
    k = np.full(m, 50.0)
    d = np.full(m, 50.0)
    for i in range(n - 1, m):
        hh = h[i - n + 1:i + 1].max()
        ll = l[i - n + 1:i + 1].min()
        rsv = (c[i] - ll) / (hh - ll) * 100 if hh > ll else 50.0
        k[i] = (2 / 3) * k[i - 1] + (1 / 3) * rsv
        d[i] = (2 / 3) * d[i - 1] + (1 / 3) * k[i]
    j = 3 * k - 2 * d
    return k, d, j


def _adx(h, l, c, n=14):
    """Wilder ADX + DI。"""
    h = np.asarray(h, float)
    l = np.asarray(l, float)
    c = np.asarray(c, float)
    m = len(c)
    if m < n + 1:
        return None, None, None
    tr = np.zeros(m)
    pdm = np.zeros(m)
    mdm = np.zeros(m)
    for i in range(1, m):
        up = h[i] - h[i - 1]
        dn = l[i - 1] - l[i]
        pdm[i] = up if (up > dn and up > 0) else 0.0
        mdm[i] = dn if (dn > up and dn > 0) else 0.0
        tr[i] = max(h[i] - l[i], abs(h[i] - c[i - 1]), abs(l[i] - c[i - 1]))

    def wilder_sum(arr):
        out = np.full(m, np.nan)
        s = arr[1:n + 1].sum()
        out[n] = s
        for i in range(n + 1, m):
            s = s - s / n + arr[i]
            out[i] = s
        return out

    atr = wilder_sum(tr)
    ap = wilder_sum(pdm)
    am = wilder_sum(mdm)
    with np.errstate(divide="ignore", invalid="ignore"):
        pdi = np.where(atr > 0, 100 * ap / atr, np.nan)
        mdi = np.where(atr > 0, 100 * am / atr, np.nan)
        dx = np.where((pdi + mdi) > 0,
                      100 * np.abs(pdi - mdi) / (pdi + mdi), 0.0)
    adx_arr = wilder_sum(dx) / n  # ADX = Wilder 平滑(DX)
    return _last(adx_arr), _last(pdi), _last(mdi)


def _index_tech(x):
    """单指数技术面 dict。"""
    g = x.get("hist") or {}
    c = _ffill(_to_np(g.get("c")))
    h = _ffill(_to_np(g.get("h")))
    l = _ffill(_to_np(g.get("l")))
    close = x.get("close")
    out = {
        "name": x.get("name"), "code": x.get("code"),
        "close": close, "chg_pct": x.get("chg_pct"),
        "ma20": None, "ma20_dev": None, "ma60": None, "ma60_dev": None,
        "drawdown30": None, "macd_bar": None, "rsi6": None,
        "k": None, "d": None, "j": None, "adx": None, "pdi": None, "mdi": None,
        "pivot": None, "r1": None, "s1": None,
        "support": None, "resistance": None, "trend": "—",
    }
    n = len(c)
    if n < 2:
        return out

    ma20 = _sma(c, 20)
    ma60 = _sma(c, 60)
    m20 = _last(ma20)
    m60 = _last(ma60)
    out["ma20"] = m20
    out["ma60"] = m60
    if m20 and close:
        out["ma20_dev"] = (close / m20 - 1) * 100
    if m60 and close:
        out["ma60_dev"] = (close / m60 - 1) * 100

    tail = c[-30:]
    if len(tail):
        pk = np.maximum.accumulate(tail)
        out["drawdown30"] = float(np.max((pk - tail) / pk) * 100)

    _, _, bar = _macd(c)
    out["macd_bar"] = _last(bar)
    out["rsi6"] = _last(_rsi(c, 6))

    k, d, j = _kdj(h, l, c)
    out["k"] = _last(k)
    out["d"] = _last(d)
    out["j"] = _last(j)

    adx, pdi, mdi = _adx(h, l, c, 14)
    out["adx"] = adx
    out["pdi"] = pdi
    out["mdi"] = mdi

    if n >= 2:
        ph, pl, pc = h[-2], l[-2], c[-2]
        piv = (ph + pl + pc) / 3
        out["pivot"] = float(piv)
        out["r1"] = float(2 * piv - pl)
        out["s1"] = float(2 * piv - ph)
    if n >= 20:
        out["support"] = float(np.min(l[-20:]))
        out["resistance"] = float(np.max(h[-20:]))

    if m20 and m60 and close:
        if close > m20 and m20 > m60:
            out["trend"] = "多头"
        elif close < m20 and m20 < m60:
            out["trend"] = "空头"
        else:
            out["trend"] = "震荡"
        if out["adx"] is not None and out["adx"] >= 25 and out["trend"] in ("多头", "空头"):
            out["trend"] += "(趋势)"
    return out


# ============================ 评分（本系统启发式测算） ============================

def _clamp(v, lo=0, hi=100):
    return float(max(lo, min(hi, v)))


def _thermo_score(t, s):
    """市场温度 0-100。越热越高。"""
    if not t:
        return None
    up = t.get("up", 0)
    dn = t.get("down", 1)
    zt = t.get("limit_up", 0)
    dt = t.get("limit_down", 0)
    ratio = t.get("up_down_ratio") or (up / max(dn, 1))
    seal = (s or {}).get("seal_rate", 0) or 0
    base = 50 + 30 * math.tanh((ratio - 1) / 1.5)
    base += min(zt, 80) * 0.12 - min(dt, 80) * 0.12
    base += seal * 8
    return _clamp(base)


def _sentiment_score(t, s):
    """情绪/赚钱效应 0-100。"""
    if not t:
        return None
    zt = t.get("limit_up", 0)
    dt = t.get("limit_down", 0)
    seal = (s or {}).get("seal_rate", 0) or 0
    broken = (s or {}).get("broken", 0) or 0
    lr = zt / max(zt + dt, 1)
    base = lr * 60 + seal * 30
    base -= min(broken, 40) * 0.2
    return _clamp(base)


def _risk_score(t, s, idx_tech):
    """风险评分 0-100 + 参考仓位%。高位+宽幅+跌停多→高风险→低仓位。"""
    if not t:
        return None, None
    dt = t.get("limit_down", 0)
    zt = t.get("limit_up", 0)
    devs = [abs(x.get("ma60_dev") or 0) for x in idx_tech
            if x.get("ma60_dev") is not None]
    avg_dev = sum(devs) / len(devs) if devs else 0
    dt_ratio = dt / max(zt + dt, 1)
    base = 30 + avg_dev * 5 + dt_ratio * 100
    risk = _clamp(base)
    pos = _clamp(80 - risk * 0.6, 20, 90)
    return risk, int(round(pos))


# ============================ 市值集中度 / 资金 / 分歧 ============================

def _concentration(uni):
    """全A 总市值集中度：HHI / Gini / Top5·Top10 占比。mktcap 单位万元。"""
    if uni is None or not len(uni) or "mktcap" not in getattr(uni, "columns", []):
        return {}
    caps = pd.to_numeric(uni["mktcap"], errors="coerce").dropna()
    caps = caps[caps > 0]
    if not len(caps):
        return {}
    total = float(caps.sum())
    s = (caps.sort_values(ascending=False) / total)  # 占比
    top5 = float(s.iloc[:5].sum() * 100)
    top10 = float(s.iloc[:10].sum() * 100)
    hhi = float((s ** 2).sum() * 10000)  # 0-10000
    arr = np.sort(caps.to_numpy())
    nn = len(arr)
    idx = np.arange(1, nn + 1)
    gini = float((2 * (idx * arr).sum()) / (nn * arr.sum()) - (nn + 1) / nn)
    return {
        "n": int(nn),
        "total_yi": total / 1e4,  # 万元 → 亿元
        "top5": top5,
        "top10": top10,
        "hhi": hhi,
        "gini": gini,
    }


def _crowding_approx(fund_sector):
    """行业拥挤度近似：暂以 |主力净流入| 排序（换手率/成交集中度口径 Phase2）。"""
    if not fund_sector:
        return []
    rows = [f for f in fund_sector if f.get("main") is not None]
    rows.sort(key=lambda f: abs(f["main"]), reverse=True)
    out = []
    for i, f in enumerate(rows[:8]):
        out.append({
            "rank": i + 1,
            "name": f.get("name", ""),
            "main": f.get("main"),
            "note": "|净额|近似排序",
        })
    return out


def _divergence(indices):
    by = {x.get("name"): x for x in indices}
    sh = by.get("上证综指")
    cy = by.get("创业板指")
    if not sh or not cy:
        return {}
    sp = sh.get("chg_pct")
    cp = cy.get("chg_pct")
    diff = (cp - sp) if (sp is not None and cp is not None) else None
    return {
        "main_pct": sp,
        "growth_pct": cp,
        "diff": diff,
        "note": "大盘(上证)vs成长(创业板)当日涨跌幅差；正=成长强于大盘",
    }


# ============================ 综合判断（规则化文字） ============================

def _p1(v):
    """百分比点 → '+1.23%' / '—'。"""
    if v is None or not math.isfinite(v):
        return "—"
    return ("+" if v >= 0 else "") + f"{v:.2f}%"


def _judgment(t, s, indices, idx_tech, tscore, sscore, rscore, pos,
              diverge, conc):
    paras = []
    if not t:
        paras.append("全市场盘面数据获取失败，无法生成当日大盘判断（数据层失败，"
                     "不网络回填、不臆测；可盘后重试或检查 akshare 源连通）。")
        return paras

    zt = t.get("limit_up", 0)
    dt = t.get("limit_down", 0)
    ratio = t.get("up_down_ratio", 0)
    tone = ("偏热" if (tscore or 0) >= 65
            else "偏冷" if (tscore or 0) <= 40 else "中性")
    paras.append(
        f"市场温度 {_p1_safe(tscore)}/100（本系统测算·{tone}）。"
        f"今日涨停 {zt} 家、跌停 {dt} 家，涨跌比 {ratio}:1，"
        f"情绪评分 {_p1_safe(sscore)}/100。"
    )

    sh = next((x for x in idx_tech if x["name"] == "上证综指"), None)
    cy = next((x for x in idx_tech if x["name"] == "创业板指"), None)
    if sh:
        paras.append(
            f"上证 {sh['trend']}，收 {_num(sh['close'])}，"
            f"MA20乖离 {_p1(sh['ma20_dev'])}、MA60乖离 {_p1(sh['ma60_dev'])}，"
            f"ADX {_num(sh['adx'], 0)}，30日最大回撤 {_p1(sh['drawdown30'])}，"
            f"RSI6 {_num(sh['rsi6'], 0)}、KDJ {_num(sh['k'], 0)}/{_num(sh['d'], 0)}/{_num(sh['j'], 0)}。"
        )
    if diverge and diverge.get("diff") is not None:
        d = diverge["diff"]
        who = "成长强于大盘" if d > 0 else "大盘强于成长"
        paras.append(
            f"大盘/成长分歧：上证 {_p1(diverge['main_pct'])} vs 创业板 {_p1(diverge['growth_pct'])}，"
            f"差 {_p1(d)}（{who}）。"
        )

    paras.append(
        f"风险评分 {_p1_safe(rscore)}/100，参考仓位约 {pos}%"
        f"（本系统测算·启发式：高位+宽幅+跌停多→高风险→低仓位；非投资建议）。"
    )

    if conc and conc.get("hhi") is not None:
        paras.append(
            f"全A 总市值集中度：HHI {_num(conc['hhi'], 0)}、"
            f"Top5 占比 {_p1(conc['top5'])}、Top10 占比 {_p1(conc['top10'])}、"
            f"Gini {_num(conc['gini'], 3)}（共 {conc['n']} 只）。"
        )

    paras.append(
        "以上为盘后数据规则化汇总，非预测、非投资建议、不自动下单。"
        "两融余额/连板梯队/两市成交额/电报新闻 Phase 2 待接入（多走东财，服务器易限流，"
        "失败将如实标注「数据获取失败」）。"
    )
    return paras


def _p1_safe(score):
    return "—" if score is None else f"{score:.0f}"


def _num(v, d=2):
    if v is None or (isinstance(v, float) and not math.isfinite(v)):
        return "—"
    return f"{float(v):.{d}f}"
