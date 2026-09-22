# -*- coding: utf-8 -*-
"""数据层（独立，不再运行时依赖 breakout）

- 运行时只读自己 data/ 目录的 ohlcv.pkl / universe.pkl
- 首次使用：py -3 run.py seed（从 breakout 一次性拷贝 8 年历史）或 py -3 run.py fetch（akshare 从零拉）
- 每天启动：refresh_incremental() 自动拉最新交易日补齐 stale 票（新浪源 stock_zh_a_daily）
- universe 按 mtime 周期性重抓（保持名称/ST/市值新鲜）
- 交易日历、股票池过滤（ST / 板别）

缓存 schema（已验证）：
  ohlcv:   symbol,date,open,high,low,close,volume （date 为 "YYYY-MM-DD" 字符串，volume 单位手）
  universe: symbol,code,name,trade,pct,amount,mktcap,nmc,turnover （mktcap/nmc 单位万元，trade 单位元）
"""
import os
import re
import ssl
import json
import gzip
import random
import shutil
import time
import urllib.request
import urllib.error
from datetime import datetime, timedelta
from concurrent.futures import ThreadPoolExecutor, as_completed

import pandas as pd

import config as C


# ============================================================================
# 一、本地缓存读写（只读 data/，不再回退 breakout）
# ============================================================================

def _ohlcv_path():
    return os.path.join(C.DATA_DIR, C.OHLCV_FILE)


def _universe_path():
    return os.path.join(C.DATA_DIR, C.UNIVERSE_FILE)


def _save_pickle(df, path):
    """原子写盘：先 .tmp 再 os.replace，避免崩溃留下坏文件。"""
    os.makedirs(os.path.dirname(path), exist_ok=True)
    tmp = path + ".tmp"
    df.to_pickle(tmp)
    os.replace(tmp, path)


def load_ohlcv(verbose=True):
    """只读本地 data/ohlcv.pkl。缺失时抛清晰错误，引导用户 seed/fetch。"""
    p = _ohlcv_path()
    if not os.path.exists(p):
        raise FileNotFoundError(
            f"找不到本地 ohlcv 缓存：{p}\n"
            f"首次使用请先跑：\n"
            f"  py -3 run.py seed      # 从 breakout 一次性拷贝 8 年历史（推荐，秒级）\n"
            f"  py -3 run.py fetch    # 或用 akshare 从零独立拉取（慢）"
        )
    df = pd.read_pickle(p)
    if verbose:
        print(f"  加载 ohlcv：{len(df):,} 行 / {df['symbol'].nunique()} 只 / "
              f"{df['date'].min()} ~ {df['date'].max()}")
    return df


def load_universe(verbose=True):
    p = _universe_path()
    if not os.path.exists(p):
        raise FileNotFoundError(
            f"找不到本地 universe 缓存：{p}\n"
            f"首次使用请先跑：py -3 run.py seed（或 fetch）"
        )
    df = pd.read_pickle(p)
    if verbose:
        print(f"  加载 universe：{len(df)} 只（含名称/市值）")
    return df


# ============================================================================
# 二、派生工具（不改 schema）
# ============================================================================

def symbol_name_map(uni):
    return dict(zip(uni["symbol"].astype(str), uni["name"].astype(str)))


def build_shares_map(uni):
    """从 universe 派生每股 总股本/流通股本（股），供策略估算逐日换手/市值。

    universe 的 mktcap/nmc 单位万元、trade 单位元 → 总股本 = mktcap*1e4/trade。
    这是当前快照股本（非点在时），套到历史价量上有 look-ahead 偏差（配股/解禁/转股
    时不准），作为粗筛可接受。返回 {symbol: (total_shares, float_shares)}。
    """
    import numpy as np
    u = uni.copy()
    u["trade_f"] = pd.to_numeric(u["trade"], errors="coerce")
    u["mktcap_f"] = pd.to_numeric(u["mktcap"], errors="coerce")
    u["nmc_f"] = pd.to_numeric(u["nmc"], errors="coerce")
    u["total_shares"] = u["mktcap_f"] * 1e4 / u["trade_f"]     # 股
    u["float_shares"] = u["nmc_f"] * 1e4 / u["trade_f"]
    smap = {}
    for sym, ts, fs in zip(u["symbol"].astype(str), u["total_shares"], u["float_shares"]):
        if pd.notna(ts) and pd.notna(fs) and np.isfinite(ts) and np.isfinite(fs) and ts > 0:
            smap[sym] = (float(ts), float(fs))
    return smap


def filter_pool(uni):
    """股票池过滤：剔 ST + 北交所 + 创业板 + 科创板（对应公式 COND_NOTST/NOT_BJ/NOT_KCB/NOT_688）。

    返回 (allowed_symbols_list, filtered_uni_df)。
    """
    names = uni["name"].astype(str)
    not_st = ~names.str.upper().str.contains("ST")
    codes = uni["symbol"].astype(str).str[2:]
    not_bj = ~codes.str.startswith(("920", "83", "87", "8", "4"))
    not_cyb = ~codes.str.startswith(("300", "301"))
    not_kcb = ~codes.str.startswith("688")
    mask = not_st & not_bj & not_cyb & not_kcb
    sub = uni[mask].copy()
    return sub["symbol"].tolist(), sub


def build_calendar(ohlcv):
    """交易日历 = 缓存里出现过的日期并集，字符串字典序即日期序。"""
    return sorted(ohlcv["date"].unique())


# ============================================================================
# 三、合并缓存（移植自 breakout datasource.py:1091-1108）
# ============================================================================

def merge_cache(old_df, new_df):
    """把新下载的行情合并进老缓存：同一 symbol+date 用新的覆盖旧的（允许 qfq 重定基）。"""
    if old_df is None or len(old_df) == 0:
        return new_df.drop_duplicates(subset=["symbol", "date"]).reset_index(drop=True)
    if new_df is None or len(new_df) == 0:
        return old_df
    keys = ["symbol", "date"]
    merged = pd.concat([old_df, new_df], ignore_index=True)
    merged = merged.drop_duplicates(subset=keys, keep="last")   # 保留新的
    merged = merged.sort_values(keys).reset_index(drop=True)
    return merged


# ============================================================================
# 四、新鲜度检查
# ============================================================================

def data_asof(ohlcv=None):
    """返回 (ohlcv_max_date, universe_age_days, n_laggards) 供看板披露新鲜度。

    n_laggards = 缓存里 last_date < 全市场 max_date 的股票数（落后于自己最新日的票）。
    这是内部一致性指标（不联网）；真正"是否落后于今天"由 refresh_incremental 用交易日历判断。
    """
    if ohlcv is None:
        try:
            ohlcv = load_ohlcv(verbose=False)
        except Exception:
            return ("?", None, 0)
    if len(ohlcv) == 0:
        return ("?", None, 0)
    max_date = str(ohlcv["date"].max())
    last_by_sym = ohlcv.groupby("symbol")["date"].max()
    n_lag = int((last_by_sym < max_date).sum())
    age = None
    up = _universe_path()
    if os.path.exists(up):
        age = (time.time() - os.path.getmtime(up)) / 86400.0
    return (max_date, age, n_lag)


# ============================================================================
# 五、交易日历（外部源，判断"今天是否应已更新"）
# ============================================================================

def _latest_target_trading_day(verbose=True):
    """返回最近一个已完成的交易日 "YYYY-MM-DD"。

    优先用 akshare 交易日历；取失败回退到"最近工作日"（不含节假日，但够用）。
    """
    try:
        import akshare as ak
        cal = ak.tool_trade_date_hist_sina()   # DataFrame，列 trade_date
        # 兼容字符串 "YYYYMMDD" 与 Timestamp "YYYY-MM-DD" 两种返回
        dates = sorted(str(d).replace("-", "") for d in cal["trade_date"].tolist())
        today = datetime.now().strftime("%Y%m%d")
        past = [d for d in dates if d <= today]
        if past:
            ymd = past[-1]
            return f"{ymd[:4]}-{ymd[4:6]}-{ymd[6:8]}"
    except Exception as e:
        if verbose:
            print(f"  [交易日历] akshare 取不到（{type(e).__name__}），回退到最近工作日")
    # 回退：最近工作日（周六→周五，周日→周五；不含节假日，节假日会多一次无害的空刷新）
    d = datetime.now()
    if d.weekday() == 5:      # 周六
        d = d - timedelta(days=1)
    elif d.weekday() == 6:    # 周日
        d = d - timedelta(days=2)
    return d.strftime("%Y-%m-%d")


# ============================================================================
# 六、增量刷新（每天启动自动拉最新交易日）
# ============================================================================

def _code_to_symbol(code):
    """6 位代码 → sh/sz/bj 前缀（与 breakout code_to_symbol 一致）。"""
    c = str(code).strip()
    if c.startswith(("4", "8", "92")):
        return "bj" + c
    if c.startswith(("6", "9")):
        return "sh" + c
    return "sz" + c


def _fetch_recent_bars(symbol, start_date_yyyymmdd):
    """拉一只票从 start_date 至今的 qfq 日线。volume 新浪源单位"股"→/100 转"手"。

    返回 DataFrame（symbol,date,open,high,low,close,volume）或空 DataFrame（失败）。
    """
    import akshare as ak
    df = ak.stock_zh_a_daily(symbol=symbol, adjust="qfq", start_date=start_date_yyyymmdd)
    if df is None or len(df) == 0:
        return pd.DataFrame(columns=["symbol", "date", "open", "high", "low", "close", "volume"])
    df = df.rename(columns=str.lower)
    # 归一 date 为 "YYYY-MM-DD" 字符串（兼容 datetime/字符串两种返回）
    df["date"] = pd.to_datetime(df["date"]).dt.strftime("%Y-%m-%d")
    keep = ["date", "open", "high", "low", "close", "volume"]
    df = df[[c for c in keep if c in df.columns]].copy()
    # 新浪源 volume 单位是"股"，ohlcv 缓存单位是"手"（1手=100股）→ 除 100
    if "volume" in df.columns:
        df["volume"] = pd.to_numeric(df["volume"], errors="coerce") / 100.0
    df["symbol"] = symbol
    return df


def refresh_incremental(verbose=True, force=False):
    """每天启动自动刷新：把落后于最新交易日的票补到最新。

    流程：
      1. 读本地 ohlcv（缺失→抛错让用户 seed/fetch）。
      2. 交易日历求 target（最近已完成交易日）；取失败用最近工作日回退。
      3. 若 cache.max_date >= target 且非 force → 当天已刷新，秒级 no-op。
      4. 找 stale 票（last_date < target），并发 stock_zh_a_daily 拉 tail，volume/100。
      5. merge_cache + 原子落盘。
      返回 (n_refreshed, n_failed, new_max_date)。
    """
    ohlcv = load_ohlcv(verbose=False)
    max_date = str(ohlcv["date"].max())

    target = _latest_target_trading_day(verbose=verbose)
    if max_date >= target and not force:
        if verbose:
            print(f"  [增量] 已是最新（缓存截止 {max_date} >= 目标交易日 {target}），跳过刷新")
        return (0, 0, max_date)

    # 找落后票
    last_by_sym = ohlcv.groupby("symbol")["date"].max()
    stale = last_by_sym[last_by_sym < target].index.tolist()
    if not stale:
        if verbose:
            print(f"  [增量] 无落后票（目标 {target}，缓存截止 {max_date}），跳过")
        return (0, 0, max_date)

    # start_date 留足余量：INCREMENTAL_BARS 个交易日 ≈ 1.8 倍日历日 + 20 天缓冲
    start_cal = datetime.now() - timedelta(days=int(C.INCREMENTAL_BARS * 1.8) + 20)
    start_yyyymmdd = start_cal.strftime("%Y%m%d")

    if verbose:
        print(f"  [增量] 目标交易日 {target}，缓存截止 {max_date}，需补 {len(stale)} 只（start={start_yyyymmdd}）")

    n_ok, n_fail = 0, 0
    new_bars = []
    t0 = time.time()
    done = 0

    def _one(sym):
        try:
            return sym, _fetch_recent_bars(sym, start_yyyymmdd)
        except Exception as e:
            return sym, e

    with ThreadPoolExecutor(max_workers=C.AKSHARE_WORKERS) as pool:
        futs = {pool.submit(_one, s): s for s in stale}
        for f in as_completed(futs):
            sym, res = f.result()
            done += 1
            if isinstance(res, Exception):
                n_fail += 1
                if verbose and n_fail <= 10:
                    print(f"    {sym} 失败：{type(res).__name__}: {str(res)[:60]}")
            elif res is not None and len(res) > 0:
                new_bars.append(res)
                n_ok += 1
            else:
                n_fail += 1
            if verbose and done % 200 == 0:
                el = time.time() - t0
                speed = done / el if el > 0 else 0
                eta = (len(stale) - done) / speed if speed > 0 else 0
                print(f"    进度 {done}/{len(stale)}  成功 {n_ok}  失败 {n_fail}  用时 {el:.0f}s  预计 {eta:.0f}s")

    if new_bars:
        merged = merge_cache(ohlcv, pd.concat(new_bars, ignore_index=True))
        _save_pickle(merged, _ohlcv_path())
        new_max = str(merged["date"].max())
    else:
        merged = ohlcv
        new_max = max_date

    if verbose:
        print(f"  [增量] 完成：补 {n_ok} 只 / 失败 {n_fail} 只 / 用时 {time.time()-t0:.0f}s / 新截止 {new_max}")
    return (n_ok, n_fail, new_max)


# ============================================================================
# 七、universe 刷新（保持名称/ST/市值新鲜）
# ============================================================================
#
# 数据源选择：新浪 HTTP 分页（与 K 线源 stock_zh_a_daily 同源）。
# 不用东财 stock_zh_a_spot_em —— 东财在并发请求后会被 ban（实测
# ConnectionError RemoteDisconnected），新浪分页单线程稳定，且 mktcap/nmc
# 直接返回万元单位，与 build_shares_map 的 mktcap_f*1e4/trade 公式一致，无需换算。
# 移植自 breakout datasource.py 的 http_get / fetch_universe。

_HEADERS = {
    "User-Agent": ("Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
                   "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36"),
    "Accept": "*/*",
    "Accept-Language": "zh-CN,zh;q=0.9",
    "Referer": "https://finance.sina.com.cn",
}
_SSL_CTX = ssl.create_default_context()
_SSL_CTX.check_hostname = False
_SSL_CTX.verify_mode = ssl.CERT_NONE


def _http_get(url, timeout=15, retries=3, encoding="utf-8"):
    """抓网页返回文本，彻底失败返回空串（不抛异常）。移植自 breakout datasource.http_get。"""
    last_err = None
    for attempt in range(retries):
        try:
            req = urllib.request.Request(url, headers=_HEADERS)
            with urllib.request.urlopen(req, timeout=timeout, context=_SSL_CTX) as resp:
                raw = resp.read()
                if resp.headers.get("Content-Encoding") == "gzip":
                    raw = gzip.decompress(raw)
            return raw.decode(encoding, errors="ignore")
        except Exception as e:
            last_err = e
            time.sleep(0.3 * (2 ** attempt) + random.random() * 0.1)
    return ""


def _to_float(v):
    """把接口返回的各种奇怪类型安全转成 float，失败返回 0.0"""
    try:
        if v is None or v == "" or v == "-":
            return 0.0
        return float(v)
    except Exception:
        return 0.0


_SINA_LIST_URL = ("https://vip.stock.finance.sina.com.cn/quotes_service/api/json_v2.php/"
                  "Market_Center.getHQNodeData?page={page}&num={num}&sort=symbol&asc=1&node={node}")
_SINA_COUNT_URL = ("https://vip.stock.finance.sina.com.cn/quotes_service/api/json_v2.php/"
                   "Market_Center.getHQNodeStockCount?node={node}")


def fetch_universe_sina(node="hs_a", max_pages=None, verbose=True):
    """新浪源分页抓全市场 A 股列表（含实时快照）。移植自 breakout datasource.fetch_universe。

    返回字段：symbol,code,name,trade,pct,amount,mktcap,nmc,turnover
    （mktcap/nmc 单位万元，trade 单位元 —— 与 build_shares_map 现有公式一致，无需换算）。
    """
    count_txt = _http_get(_SINA_COUNT_URL.format(node=node))
    total = None
    m = re.search(r'"(\d+)"', count_txt)
    if m:
        total = int(m.group(1))
    if verbose:
        print("  [universe] 全市场股票总数：%s" % (total if total else "未知"))

    page_size = 100          # 新浪单页最多 100 条
    rows = []
    page = 1
    empty_pages = 0

    while True:
        url = _SINA_LIST_URL.format(page=page, num=page_size, node=node)
        text = _http_get(url)

        # 区分"没拿到"和"确实没数据"：把网络失败当失败计数，不静默丢页
        if text is None or text.strip() == "":
            empty_pages += 1
            if verbose:
                print("    [警告] 第 %d 页未拿到响应，连续失败 %d/3" % (page, empty_pages))
            if empty_pages >= 3:
                break
            page += 1
            time.sleep(0.2)
            continue

        if text.strip() in ("null", "[]"):
            empty_pages += 1
            if empty_pages >= 2:
                break
            page += 1
            continue

        try:
            arr = json.loads(text)
        except Exception:
            empty_pages += 1
            if empty_pages >= 3:
                break
            page += 1
            continue

        if not isinstance(arr, list) or len(arr) == 0:
            empty_pages += 1
            if empty_pages >= 2:
                break
            page += 1
            continue

        empty_pages = 0   # 拿到有效数据就清零失败计数
        for it in arr:
            rows.append({
                "symbol": str(it.get("symbol", "")).lower(),
                "code": str(it.get("code", "")),
                "name": str(it.get("name", "")).strip(),
                "trade": _to_float(it.get("trade")),
                "pct": _to_float(it.get("changepercent")),
                "amount": _to_float(it.get("amount")),
                "mktcap": _to_float(it.get("mktcap")),
                "nmc": _to_float(it.get("nmc")),
                "turnover": _to_float(it.get("turnoverratio")),
            })

        if verbose and page % 10 == 0:
            print("    已抓取 %d 只..." % len(rows))

        if total and len(rows) >= total:
            break
        if max_pages and page >= max_pages:
            break
        page += 1
        time.sleep(0.05)

    df = pd.DataFrame(rows)
    if len(df) == 0:
        return df
    df = df[df["symbol"].str.len() >= 8].copy()      # 去掉没有代码的脏数据
    df = df.drop_duplicates(subset=["symbol"]).reset_index(drop=True)
    return df


def refresh_universe(verbose=True):
    """新浪源重抓全市场，写入本地 universe.pkl（schema 不变，mktcap/nmc 万元）。

    单线程分页（非并发），限流风险低。抓取失败返回 None（不覆盖旧缓存）。
    """
    if verbose:
        print("  [universe] 抓取全市场快照（新浪源分页）...")
    uni = fetch_universe_sina(verbose=verbose)
    if len(uni) == 0:
        if verbose:
            print("  [universe] ⚠ 抓取失败（保留旧缓存）")
        return None
    _save_pickle(uni, _universe_path())
    if verbose:
        print(f"  [universe] 完成：{len(uni)} 只，已写入 {_universe_path()}")
    return uni


def maybe_refresh_universe(verbose=True, force=False):
    """universe 文件超过 UNIVERSE_REFRESH_DAYS 天才重抓（避免每天抓全市场）。"""
    up = _universe_path()
    if not os.path.exists(up):
        return None
    age = (time.time() - os.path.getmtime(up)) / 86400.0
    if force or age > C.UNIVERSE_REFRESH_DAYS:
        return refresh_universe(verbose=verbose)
    if verbose:
        print(f"  [universe] 缓存 {age:.1f} 天前更新（<= {C.UNIVERSE_REFRESH_DAYS}），跳过")
    return None


# ============================================================================
# 八、一次性迁移 / 从零拉取
# ============================================================================

def seed_from_breakout(verbose=True):
    """一次性把 breakout 的 ohlcv.pkl / universe.pkl 拷到自己 data/ 目录。

    拷完 breakout 即可弃用，本项目运行时不再依赖它。
    """
    os.makedirs(C.DATA_DIR, exist_ok=True)
    for f in (C.OHLCV_FILE, C.UNIVERSE_FILE):
        src = os.path.join(C.BREAKOUT_DATA_DIR, f)
        dst = os.path.join(C.DATA_DIR, f)
        if not os.path.exists(src):
            if verbose:
                print(f"  ⚠ breakout 无 {f}（{src}），跳过")
            continue
        if os.path.exists(dst):
            if verbose:
                print(f"  已存在 {dst}，跳过拷贝（删除后可重拷）")
            continue
        shutil.copy2(src, dst)
        if verbose:
            print(f"  拷贝 {f}：{os.path.getsize(dst)/1e6:.1f} MB")
    if verbose:
        print("  完成。本项目已独立，breakout 可弃用。后续每天启动自动增量刷新最新交易日。")


def fetch_via_akshare(limit=None, verbose=True):
    """用 akshare 独立拉取全市场 qfq 日线 + 全列 universe，写入自己 data/。

    universe 用 stock_zh_a_spot_em（含市值/换手率，修旧版只有 3 列的缺口）；
    K线用 stock_zh_a_daily（新浪 qfq，volume/100 转手）。
    慢（逐只请求），仅用于 `py -3 run.py fetch` 从零独立。
    """
    os.makedirs(C.DATA_DIR, exist_ok=True)

    # universe（全列，新浪源）
    if verbose:
        print("  [akshare] 抓取全市场列表（新浪源分页）...")
    uni = refresh_universe(verbose=verbose)
    if uni is None or len(uni) == 0:
        if verbose:
            print("  ⚠ universe 抓取失败，fetch 中止。可先 `py -3 run.py seed` 从 breakout 拷贝。")
        return None, None

    # 过滤池
    allowed, _ = filter_pool(uni)
    if limit:
        allowed = allowed[:limit]
    if verbose:
        print(f"  待下载 {len(allowed)} 只" + (f"（limit={limit}）" if limit else ""))

    parts = []
    n_fail = 0
    for i, sym in enumerate(allowed, 1):
        try:
            df = _fetch_recent_bars(sym, "20100101")   # 从 2010 拉全历史
            if len(df) > 0:
                parts.append(df)
            if i % 50 == 0 and verbose:
                print(f"    进度 {i}/{len(allowed)}")
        except Exception as e:
            n_fail += 1
            if verbose and n_fail <= 10:
                print(f"    {sym} 失败：{type(e).__name__}: {str(e)[:60]}")
    ohlcv = pd.concat(parts, ignore_index=True) if parts else pd.DataFrame(
        columns=["symbol", "date", "open", "high", "low", "close", "volume"]
    )
    _save_pickle(ohlcv, _ohlcv_path())
    if verbose:
        print(f"  完成：{len(ohlcv):,} 行 / {ohlcv['symbol'].nunique()} 只 / 失败 {n_fail}，已写入 {C.DATA_DIR}")
    return ohlcv, uni
