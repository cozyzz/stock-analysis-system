# -*- coding: utf-8 -*-
"""通达信(TDX)指标函数库 · pandas 实现

复用 stock-breakout-in-volume/engine.py 已验证的惯用法：
  - MA         = rolling().mean()                    (engine.py:68-70)
  - SMA(X,N,1) = ewm(alpha=1/N, adjust=False)        (engine.py:45 ATR)
  - REF/LLV/HHV = shift/rolling.min/rolling.max
  - CROSS/EXIST                                (engine.py:490-501 / 129-130)
  - ZTPRICE 用 +0.5 避免 banker's rounding           (datasource.py:150-177)

所有函数接受/返回 pandas Series，按 TDX 语义：基于该股自身 bar 序列，
停牌日不出现为 bar（rolling 自动跳过）。
"""
import numpy as np
import pandas as pd


# ---------- 基础 ----------
def MA(x, n):
    """MA(X, N) 简单移动平均。"""
    return x.rolling(n).mean()


def sma_wilder(x, n):
    """TDX SMA(X, N, 1) = Wilder 平滑 = ewm(alpha=1/N, adjust=False)。

    递归式 Y = (X + (N-1)*Y_prev) / N，首值种子为 X[0]（pandas ewm adjust=False 行为一致）。
    用于 RSI(14) 的 UP/DN、KDJ 的 K/D。
    """
    return x.ewm(alpha=1.0 / n, adjust=False).mean()


def REF(x, n):
    """REF(X, N) 前 n 周期值。"""
    return x.shift(n)


def LLV(x, n):
    """LLV(X, N) N 周期最低。"""
    return x.rolling(n).min()


def HHV(x, n):
    """HHV(X, N) N 周期最高。"""
    return x.rolling(n).max()


def MAX(a, b):
    """TDX MAX(A, B) 逐项取大（b 为标量时用 np.maximum）。"""
    if np.isscalar(b):
        return a.clip(lower=b) if isinstance(b, (int, float)) else np.maximum(a, b)
    return pd.concat([a, b], axis=1).max(axis=1)


def ABS(x):
    return x.abs()


# ---------- 逻辑 ----------
def CROSS(a, b):
    """CROSS(A, B) A 上穿 B：昨日 A<=B，今日 A>B。"""
    return (a > b) & (a.shift(1) <= b)


def EXIST(cond, n):
    """EXIST(cond, N) 最近 N 周期（含当期）至少一次为真。"""
    s = cond.astype("boolean") if cond.dtype != bool else cond
    return s.rolling(n).sum() > 0


def COUNT(cond, n):
    """COUNT(cond, N) 最近 N 周期为真的次数。"""
    return cond.astype(int).rolling(n).sum()


# ---------- 衍生指标（个股模块用）----------
def TR(h, l, c):
    """True Range：max(H-L, |H-昨收|, |L-昨收|)。"""
    prev_c = c.shift(1)
    return pd.concat([(h - l), (h - prev_c).abs(), (l - prev_c).abs()], axis=1).max(axis=1)


def ATR(h, l, c, n=14):
    """ATR(N) = Wilder 平滑的 TR，等价 TDX SMA(TR, N, 1)。"""
    return sma_wilder(TR(h, l, c), n)


def KDJ(h, l, c, n=9, m1=3, m2=3):
    """KDJ(9,3,3)。返回 (k, d, j) 三条 Series。

    RSV=(C-LLV(L,9))/(HHV(H,9)-LLV(L,9))*100
    K=SMA(RSV,3,1)  D=SMA(K,3,1)  J=3K-2D
    """
    llv = LLV(l, n)
    hhv = HHV(h, n)
    rsv = (c - llv) / (hhv - llv).replace(0, np.nan) * 100.0
    k = sma_wilder(rsv, m1)
    d = sma_wilder(k, m2)
    j = 3 * k - 2 * d
    return k, d, j


def RSI(c, n=14):
    """RSI(N) = 100 - 100/(1+RS)，RS=SM(UP)/SM(DN)，SM=Wilder 平滑。

    UP=SMA(MAX(chg,0),N,1)  DN=SMA(ABS(chg),N,1)  ← 与用户反转低吸公式一致用 ABS
    """
    chg = c.diff()
    up = sma_wilder(chg.clip(lower=0), n)
    dn = sma_wilder(chg.abs(), n)
    rs = up / dn.replace(0, np.nan)
    return 100.0 - 100.0 / (1.0 + rs)


def vol_ratio(v, n=5):
    """量比 = 当量 / N 日均量。"""
    return v / MA(v, n)


def MACD(c, fast=12, slow=26, n=9):
    """MACD：DIF=EMA(fast)-EMA(slow)，DEA=EMA(n)(DIF)，BAR=2*(DIF-DEA)。

    返回 (dif, dea, bar) 三条 Series。EMA 用 ewm(span, adjust=False)。
    """
    ema_f = c.ewm(span=fast, adjust=False).mean()
    ema_s = c.ewm(span=slow, adjust=False).mean()
    dif = ema_f - ema_s
    dea = dif.ewm(span=n, adjust=False).mean()
    bar = 2 * (dif - dea)
    return dif, dea, bar


# ---------- 板别 / 涨停 ----------
def code_of(symbol):
    """sh600186 -> 600186。"""
    return symbol[2:] if len(symbol) > 2 else symbol


def is_growth_board(symbol):
    """创业板(300/301) 或 科创板(688) → 20% 涨跌停。"""
    code = code_of(symbol)
    return code.startswith(("300", "301")) or code.startswith("688")


def is_st_name(name):
    """名称含 ST（含 *ST）。"""
    if name is None:
        return False
    return "ST" in str(name).upper()


def board_tag(symbol):
    """板别标签（展示用）。"""
    code = code_of(symbol)
    if code.startswith("300"):
        return "创业板"
    if code.startswith("688"):
        return "科创板"
    if code.startswith("920") or code.startswith("83") or code.startswith("87"):
        return "北交所"
    if code.startswith(("600", "601", "603", "605")):
        return "主板"
    if code.startswith(("000", "001", "002", "003")):
        return "中小板"
    return "其他"


def limit_up_ratio(symbol):
    return 0.20 if is_growth_board(symbol) else 0.10


def zt_price(prev_close, symbol):
    """涨停价 = round(昨收 * (1+ratio), 2)，用 +0.5 地板取整避免 banker's rounding。

    对 prev_close 中的 NaN（首 bar）返回 NaN，不报错。
    返回与 prev_close 同 index 的 Series。
    """
    ratio = limit_up_ratio(symbol)
    arr = prev_close.to_numpy(dtype=float)
    out = np.where(
        np.isfinite(arr),
        np.floor(arr * (1.0 + ratio) * 100.0 + 0.5) / 100.0,
        np.nan,
    )
    return pd.Series(out, index=prev_close.index, dtype=float)


def is_limit_up(close, prev_close, symbol):
    """涨停标志：收盘 >= 涨停价（1e-9 浮点容差）。"""
    zt = zt_price(prev_close, symbol)
    return (prev_close.notna()) & (close >= zt - 1e-9)
