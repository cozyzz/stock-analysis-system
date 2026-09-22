# -*- coding: utf-8 -*-
"""全景大盘模块 · 当日交易日收盘盘面汇总

默认首页（order 最小）。盘后跑 run.py：
  - 全市场快照 stock_zh_a_spot（akshare/东方财富）→ 涨停/跌停/涨跌家数/温度计/分布/封板率
    东财反爬不稳，重试 2 次；仍失败则降级用 ohlcv 缓存最近一日（标注"离线缓存"口径）
  - 国内主要指数 stock_zh_index_daily → 当日收盘 + 涨跌幅（较稳）
  - 板块/个股资金流 stock_sector/individual_fund_flow_rank → flaky，失败占位
每个子项独立 try/except，失败不阻塞其他，不网络回填。
"""
import time

import numpy as np
import pandas as pd

KEY = "market"
NAME = "全景大盘"
DESC = "当日收盘盘面汇总（指数/温度计/情绪/资金流）"
order = 10  # 默认首页

# 主要市场走势：大A 6 + 美股 3（均有稳定日线历史源）
#   大A：stock_zh_index_daily(symbol=sh/sz 代码)
#   美股：index_us_stock_sina(symbol='.DJI'/'.IXIC'/'.INX')
# 欧洲/亚太（富时/DAX/CAC/日经/KOSPI/台湾）历史接口反爬/KeyError，先跳过
MARKET_TREND_LIST = [
    ("sh000001", "上证综指", "大A"),
    ("sz399001", "深证成指", "大A"),
    ("sz399006", "创业板指", "大A"),
    ("sh000688", "科创50", "大A"),
    ("sh000905", "中证500", "大A"),
    ("sh000852", "中证1000", "大A"),
    (".DJI", "道琼斯", "美股"),
    (".IXIC", "纳斯达克", "美股"),
    (".INX", "标普500", "美股"),
]
HIST_BARS = 120  # 每个指数取最近 N 根日线（sparkline + 抽屉大图共用）


def build(ctx):
    import os
    import akshare as ak
    errors = []
    asof = None
    degraded = False

    # 全市场快照：stock_zh_a_spot 经 py_mini_racer(V8) 反扒，段错误随机且会连累主进程。
    # 盘后看板用 ohlcv 缓存最近一日（=当日收盘，口径与 spot 收盘一致）已足够，默认跳过 spot；
    # 需要盘中实时快照设 SAS_USE_SPOT=1（走子进程隔离，仍可能崩）。
    thermo = {}
    distrib = {}
    sentiment = {}
    spot = None
    if os.environ.get("SAS_USE_SPOT", "0") == "1":
        _, spot, _ = _safe("全市场快照", lambda: _retry_spot(ak), errors)
    if spot is not None and isinstance(spot, pd.DataFrame) and len(spot):
        asof = _today_str()
        thermo = _thermometer(spot)
        distrib = _distribution(spot["涨跌幅"])
        sentiment = _sentiment(spot)
    else:
        thermo, distrib, sentiment, o_asof = _from_ohlcv(ctx)
        if o_asof:
            asof = o_asof  # ohlcv 最近一日 = 当日收盘，盘后口径
        else:
            errors.append({"name": "全市场快照", "err": "ohlcv 缓存不足"})

    # 主要市场走势（大A + 美股，带历史日线，画 sparkline + 点击抽屉大图）
    indices = _fetch_markets(ak, errors)
    if not asof and indices:
        asof = indices[0]["hist"]["d"][-1]

    # 板块资金流（同花顺 stock_fund_flow_industry 优先，带"净额(亿)"列；东财 fallback）
    fund_sector = _fetch_fund_sector(ak, errors)
    # 个股资金流（东财，V8 反爬；默认跳过，SAS_USE_FUND=1 时子进程隔离拉取）
    fund_in = None
    fund_out = None
    if os.environ.get("SAS_USE_FUND", "0") == "1":
        fi_ok, fi, _ = _safe("个股资金流",
                             lambda: _ak_subproc("stock_individual_fund_flow_rank",
                                                  {"indicator": "今日"}, tries=2,
                                                  timeout=60, tag="findiv"),
                             errors)
        if fi_ok and fi is not None:
            fund_in = _parse_fund_individual(fi, 10, "in")
            fund_out = _parse_fund_individual(fi, 10, "out")

    # 热度排行 TOP10：雪球关注度 + 百度热搜（东财被封，改用稳定源）
    hot_follow, hot_search = _fetch_hot(ak, errors)

    interp = _interpret(thermo, sentiment, indices, degraded)

    return {
        "asof": asof,
        "indices": indices,
        "thermometer": thermo,
        "distribution": distrib,
        "sentiment": sentiment,
        "fund_sector": fund_sector,
        "fund_in": fund_in,
        "fund_out": fund_out,
        "hot_follow": hot_follow,
        "hot_search": hot_search,
        "interp": interp,
        "degraded": degraded,
        "errors": errors,
    }


def _safe(name, fn, errors):
    try:
        return True, fn(), None
    except Exception as e:
        msg = f"{type(e).__name__}: {str(e)[:80]}"
        errors.append({"name": name, "err": msg})
        return False, None, msg


def _today_str():
    import datetime
    return datetime.datetime.now().strftime("%Y-%m-%d %H:%M")


def _ak_subproc(func, kwargs=None, tries=3, delay=3.0, timeout=90, tag="ak"):
    """在子进程里跑 akshare 的东财系接口，隔离 py_mini_racer (V8) 段错误。

    V8 段错误是 C 层崩溃，try/except 抓不住，会直接杀掉整个进程。放子进程后：
    段错误只杀子进程（returncode≠0），主进程存活 → 等 delay 秒重新拉起再试。
    全部 tries 次都崩 → 抛异常，由 _safe 记录失败 → 占位/降级，看板照常生成。
    输出用二进制捕获 + errors=replace，避免 Windows gbk 解码 akshare tqdm 进度条崩。
    """
    import json
    import os
    import subprocess
    import sys
    import tempfile

    mod_dir = os.path.dirname(os.path.abspath(__file__))
    worker = os.path.join(mod_dir, "_ak_worker.py")
    out = os.path.join(tempfile.gettempdir(), f"sas_{tag}.pkl")
    kw_json = json.dumps(kwargs or {}, ensure_ascii=False)
    last = "未执行"
    for _ in range(tries):
        try:
            os.remove(out)
        except OSError:
            pass
        try:
            r = subprocess.run(
                [sys.executable, worker, out, func, kw_json],
                cwd=mod_dir,
                timeout=timeout,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
            )
        except subprocess.TimeoutExpired:
            last = f"子进程超时 {timeout}s"
            time.sleep(delay)
            continue
        if r.returncode == 0 and os.path.exists(out):
            df = pd.read_pickle(out)
            try:
                os.remove(out)
            except OSError:
                pass
            return df
        # returncode≠0：可能 V8 segfault，也可能普通异常；二进制安全解码
        raw = r.stderr or r.stdout or b""
        tail = raw.decode("utf-8", errors="replace").strip().replace("\n", " ")[:100]
        last = f"rc={r.returncode} {tail}"
        time.sleep(delay)
    raise RuntimeError(f"{func} 子进程失败 {tries} 次: {last}")


def _retry_spot(ak, tries=3, delay=3.0):
    """stock_zh_a_spot 经子进程拉取（隔离 V8 段错误）。全崩则 _safe 走 ohlcv 降级。"""
    return _ak_subproc("stock_zh_a_spot", {}, tries=tries, delay=delay, timeout=90, tag="spot")


# ---------- ohlcv 降级路径 ----------
def _from_ohlcv(ctx):
    """用 ohlcv 缓存最近一日算温度计/分布/情绪（全市场，含 ST/创业板/科创板/北交所）。"""
    cal = ctx.calendar
    if not cal or len(cal) < 2 or ctx.ohlcv is None:
        return {}, {}, {}, None
    latest = cal[-1]
    prev = cal[-2]
    df = ctx.ohlcv
    cur = df[df["date"] == latest][["symbol", "open", "close"]].copy()
    pre = df[df["date"] == prev][["symbol", "close"]].rename(columns={"close": "prev"})
    m = cur.merge(pre, on="symbol", how="left").dropna(subset=["prev", "close"])
    if not len(m):
        return {}, {}, {}, None
    m["close"] = m["close"].astype(float)
    m["prev"] = m["prev"].astype(float)
    m["open"] = m["open"].astype(float)
    m["chg"] = (m["close"] / m["prev"] - 1) * 100
    code3 = m["symbol"].astype(str).str[2:5]
    m["ratio"] = np.where(code3.isin(["920", "830", "870", "872", "871", "873"]), 0.30,
                np.where(code3.isin(["300", "301", "688", "689"]), 0.20, 0.10))
    names = m["symbol"].map(ctx.name_map).fillna("").astype(str)
    st = names.str.upper().str.contains("ST")
    m["zt"] = m["chg"] >= m["ratio"] * 100 - 0.15
    m["dt"] = m["chg"] <= -m["ratio"] * 100 + 0.15
    m.loc[st, "zt"] = m.loc[st, "chg"] >= 4.9
    m.loc[st, "dt"] = m.loc[st, "chg"] <= -4.9

    thermo = {
        "total": int(len(m)),
        "up": int((m["chg"] > 0).sum()),
        "down": int((m["chg"] < 0).sum()),
        "flat": int((m["chg"] == 0).sum()),
        "limit_up": int(m["zt"].sum()),
        "limit_down": int(m["dt"].sum()),
        "up_down_ratio": round(int((m["chg"] > 0).sum()) / max(int((m["chg"] < 0).sum()), 1), 2),
        "limit_ratio": round(int(m["zt"].sum()) / max(int(m["zt"].sum() + m["dt"].sum()), 1), 3),
    }
    distrib = _distribution(m["chg"])
    m["open_zt"] = m["open"] >= m["prev"] * (1 + m["ratio"]) - 0.01
    m["close_zt"] = m["zt"]
    open_zt = int(m["open_zt"].sum())
    close_zt = int(m["close_zt"].sum())
    broken = int((m["open_zt"] & ~m["close_zt"]).sum())
    sealed = int((m["open_zt"] & m["close_zt"]).sum())
    sentiment = {
        "open_limit": open_zt,
        "close_limit": close_zt,
        "broken": broken,
        "sealed": sealed,
        "seal_rate": round(sealed / max(open_zt, 1), 3),
    }
    return thermo, distrib, sentiment, latest


# ---------- spot（akshare）路径 ----------
def _ratio_vec(codes):
    c = codes.astype(str)
    return np.where(c.str.startswith(("920", "830", "870", "872", "871", "873")), 0.30,
        np.where(c.str.startswith(("300", "301", "688", "689")), 0.20, 0.10))


def _thermometer(spot):
    df = spot.copy()
    df["涨跌幅"] = pd.to_numeric(df["涨跌幅"], errors="coerce")
    df = df.dropna(subset=["涨跌幅"])
    if not len(df):
        return {}
    names = df["名称"].astype(str)
    st = names.str.upper().str.contains("ST")
    df["_ratio"] = _ratio_vec(df["代码"])
    df["_zt"] = df["涨跌幅"] >= df["_ratio"] * 100 - 0.15
    df["_dt"] = df["涨跌幅"] <= -df["_ratio"] * 100 + 0.15
    df.loc[st, "_zt"] = df.loc[st, "涨跌幅"] >= 4.9
    df.loc[st, "_dt"] = df.loc[st, "涨跌幅"] <= -4.9
    up = int((df["涨跌幅"] > 0).sum())
    dn = int((df["涨跌幅"] < 0).sum())
    fl = int((df["涨跌幅"] == 0).sum())
    zt = int(df["_zt"].sum())
    dt = int(df["_dt"].sum())
    return {
        "total": int(len(df)), "up": up, "down": dn, "flat": fl,
        "limit_up": zt, "limit_down": dt,
        "up_down_ratio": round(up / max(dn, 1), 2),
        "limit_ratio": round(zt / max(zt + dt, 1), 3),
    }


def _distribution(chg):
    chg = pd.to_numeric(chg, errors="coerce").dropna()
    bins = [-30, -20, -10, -8, -5, -3, -1, 0, 1, 3, 5, 8, 10, 20, 30]
    counts = [0] * (len(bins) - 1)
    for v in chg:
        placed = False
        for i in range(len(bins) - 1):
            if bins[i] <= v < bins[i + 1]:
                counts[i] += 1
                placed = True
                break
        if not placed:
            counts[-1 if v >= bins[-1] else 0] += 1
    return {"bins": [str(b) for b in bins], "counts": counts}


def _sentiment(spot):
    df = spot.copy()
    df["今开"] = pd.to_numeric(df["今开"], errors="coerce")
    df["昨收"] = pd.to_numeric(df["昨收"], errors="coerce")
    df["涨跌幅"] = pd.to_numeric(df["涨跌幅"], errors="coerce")
    df = df.dropna(subset=["今开", "昨收", "涨跌幅"])
    if not len(df):
        return {}
    df["_ratio"] = _ratio_vec(df["代码"])
    df["_open_zt"] = df["今开"] >= df["昨收"] * (1 + df["_ratio"]) - 0.01
    df["_close_zt"] = df["涨跌幅"] >= df["_ratio"] * 100 - 0.15
    open_zt = int(df["_open_zt"].sum())
    close_zt = int(df["_close_zt"].sum())
    broken = int((df["_open_zt"] & ~df["_close_zt"]).sum())
    sealed = int((df["_open_zt"] & df["_close_zt"]).sum())
    return {
        "open_limit": open_zt, "close_limit": close_zt,
        "broken": broken, "sealed": sealed,
        "seal_rate": round(sealed / max(open_zt, 1), 3),
    }


def _fetch_markets(ak, errors):
    """大A + 美股 9 个指数，各取最近 HIST_BARS 日线，带 hist{d,c,h,l}。
    走子进程（同 _fetch_fund_sector：主进程加载 ohlcv 后 V8 初始化会段错误）。
    串行 9 个子进程，单个失败/崩只跳过该项，不杀主进程。"""
    out = []
    for code, name, mkt in MARKET_TREND_LIST:
        func = "stock_zh_index_daily" if mkt == "大A" else "index_us_stock_sina"
        ok, df, _ = _safe(
            f"市场{name}",
            lambda c=code, f=func: _ak_subproc(
                f, {"symbol": c}, tries=2, timeout=30, tag=f"mkt_{c}"),
            errors,
        )
        if not (ok and df is not None and len(df) >= 2):
            continue
        df = df.tail(HIST_BARS).reset_index(drop=True)
        d = df["date"].astype(str).str[:10].tolist()
        c = pd.to_numeric(df["close"], errors="coerce").tolist()
        h = pd.to_numeric(df["high"], errors="coerce").tolist()
        l = pd.to_numeric(df["low"], errors="coerce").tolist()
        price = c[-1]
        prev = c[-2] if len(c) >= 2 else price
        chg = price - prev
        chg_pct = (price / prev - 1) * 100 if prev else 0.0
        out.append({
            "name": name, "code": code, "market": mkt,
            "close": price, "chg": chg, "chg_pct": chg_pct,
            "hist": {"d": d, "c": c, "h": h, "l": l},
        })
    return out


def _parse_fund_sector(df):
    if df is None or not len(df):
        return None
    cols = list(df.columns)
    try:
        take = df.head(8).copy()
        rows = []
        for _, r in take.iterrows():
            rows.append({
                "name": str(r.get(cols[1], "")),
                "main": _num(r, "主力净流入-净额") or _num(r, cols[2] if len(cols) > 2 else ""),
            })
        return rows
    except Exception:
        return None


def _parse_fund_individual(df, n, direction):
    if df is None or not len(df):
        return None
    try:
        col = "主力净流入-净额"
        if col not in df.columns and len(df.columns) > 3:
            col = df.columns[3]
        df2 = df.copy()
        df2["_v"] = pd.to_numeric(df2[col], errors="coerce")
        df2 = df2.dropna(subset=["_v"])
        df2 = df2.sort_values("_v", ascending=(direction != "in")).head(n)
        rows = []
        for _, r in df2.iterrows():
            rows.append({
                "code": str(r.get("代码", "")),
                "name": str(r.get("名称", "")),
                "main": float(r["_v"]),
            })
        return rows
    except Exception:
        return None


def _num(r, key):
    v = r.get(key)
    try:
        return float(v)
    except (TypeError, ValueError):
        return None


def _interpret(thermo, sentiment, indices, degraded=False):
    if not thermo:
        return "全市场快照获取失败，无法生成盘面解读。资金流端点（东方财富）历史上不稳，可盘后重试或后完善替代接口。"
    zt = thermo.get("limit_up", 0)
    dt = thermo.get("limit_down", 0)
    ratio = thermo.get("up_down_ratio", 0)
    up = thermo.get("up", 0)
    dn = thermo.get("down", 0)
    if zt >= 50 and dt < 10:
        tone = "情绪偏热"
    elif zt < 20 and dt >= 30:
        tone = "情绪偏冷"
    else:
        tone = "情绪中性"
    idx_txt = ""
    if indices:
        pos = [i for i in indices if i.get("chg_pct") is not None and i["chg_pct"] > 0]
        idx_txt = f"主要指数 {len(indices)} 只，{len(pos)} 只收红。"
    seal = sentiment.get("seal_rate", 0) if sentiment else 0
    broken = sentiment.get("broken", 0) if sentiment else 0
    tag = "（基于离线缓存日线，非实时快照）" if degraded else ""
    return (f"今日涨停 {zt} 家、跌停 {dt} 家，涨跌比 {ratio}:1（涨 {up}/跌 {dn}），{tone}{tag}。"
            f"{idx_txt}封板率 {seal*100:.0f}%（开板 {broken} 家未封）。"
            f"非投资建议，仅为盘后数据汇总。")


# ---------- 板块资金流（同花顺优先，带金额）----------
def _fetch_fund_sector(ak, errors):
    """同花顺 stock_fund_flow_industry(即时)：返回 行业/净额(亿)/流入/流出/领涨股。
    走子进程：主进程加载 8.78M 行 ohlcv 后虚拟地址空间紧张，py_mini_racer (V8)
    在主进程初始化会段错误（IsConfigurablePoolInitialized CHECK failed）；子进程
    不加载 ohlcv，V8 初始化正常。解析 _parse_fund_ths 在主进程（不触发 V8）。"""
    ok, df, _ = _safe("板块资金流(同花顺)",
                     lambda: _ak_subproc("stock_fund_flow_industry", {"symbol": "即时"},
                                          tries=2, timeout=30, tag="fundsec"),
                     errors)
    if ok and df is not None and len(df):
        rows = _parse_fund_ths(df, 8)
        if rows:
            return rows
    return None


def _parse_fund_ths(df, n):
    """同花顺资金流解析：净额单位=亿元。按净额降序取 n 个行业。"""
    try:
        d = df.copy()
        for c in ["净额", "流入资金", "流出资金"]:
            if c in d.columns:
                d[c] = pd.to_numeric(d[c], errors="coerce")
        if "净额" not in d.columns:
            return None
        d = d.dropna(subset=["净额"]).sort_values("净额", ascending=False).head(n)
        rows = []
        for _, r in d.iterrows():
            rows.append({
                "name": str(r.get("行业", "")),
                "main": float(r["净额"]),
                "in": float(r.get("流入资金", 0) or 0),
                "out": float(r.get("流出资金", 0) or 0),
                "lead": str(r.get("领涨股", "")),
                "lead_chg": _num(r, "领涨股-涨跌幅"),
            })
        return rows
    except Exception:
        return None


# ---------- 热度排行 TOP10 ----------
def _with_timeout(fn, timeout=15):
    """线程超时包装（Windows 无 signal.alarm）。返回 (value_or_None, err_or_None)。"""
    import threading
    res = {}

    def run():
        try:
            res["v"] = fn()
        except Exception as e:
            res["e"] = e

    t = threading.Thread(target=run, daemon=True)
    t.start()
    t.join(timeout)
    if t.is_alive():
        return None, "TIMEOUT"
    return res.get("v"), res.get("e")


def _fetch_hot(ak, errors):
    """热度 TOP10：雪球关注度 + 百度热搜。走子进程（主进程 ohlcv 占地址空间，
    V8 在主进程初始化会段错误）；子进程自带超时，不用 _with_timeout。"""
    hot_follow = None
    ok, df, _ = _safe("雪球关注度",
                      lambda: _ak_subproc("stock_hot_follow_xq", {}, tries=2,
                                           timeout=25, tag="hotfol"), errors)
    if ok and df is not None and len(df):
        hot_follow = _parse_hot_follow(df, 10)
    hot_search = None
    ok2, df2, _ = _safe("百度热搜",
                        lambda: _ak_subproc("stock_hot_search_baidu", {"symbol": "A股"},
                                             tries=2, timeout=20, tag="hotsea"), errors)
    if ok2 and df2 is not None and len(df2):
        hot_search = _parse_hot_baidu(df2, 10)
    return hot_follow, hot_search


def _parse_hot_follow(df, n):
    """雪球关注度：股票代码/股票简称/关注/最新价。"""
    if df is None or not len(df):
        return None
    rows = []
    for i, (_, r) in enumerate(df.head(n).iterrows()):
        rows.append({
            "rank": i + 1,
            "code": str(r.get("股票代码", "")),
            "name": str(r.get("股票简称", "")),
            "price": _num(r, "最新价"),
            "follow": _num(r, "关注"),
        })
    return rows


def _pct(v):
    """解析带 % 的涨跌幅字符串，如 '-2.54%' -> -2.54。"""
    if v is None:
        return None
    try:
        return float(str(v).replace("%", "").replace("+", "").strip())
    except (ValueError, TypeError):
        return None


def _parse_hot_baidu(df, n):
    if df is None or not len(df):
        return None
    rows = []
    for _, r in df.head(n).iterrows():
        rows.append({
            "name": str(r.get("名称/代码", "")),
            "chg_pct": _pct(r.get("涨跌幅")),
            "heat": _num(r, "综合热度"),
        })
    return rows
