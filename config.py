# -*- coding: utf-8 -*-
"""stock-analysis-system · 全局配置

路径、回测参数、策略注册表、数据来源。所有可调参数集中在此，便于以后加新策略。
"""
import os

# ---------- 路径 ----------
BASE_DIR = os.path.dirname(os.path.abspath(__file__))
DATA_DIR = os.path.join(BASE_DIR, "data")
OUTPUT_HTML = os.path.join(BASE_DIR, "回测看板.html")
TEMPLATE_PATH = os.path.join(BASE_DIR, "template.html")

# breakout 项目位置（仅供 seed 子命令一次性拷贝 8 年历史用；运行时不再依赖）
BREAKOUT_DIR = r"e:\ai-stock\stock-breakout-in-volume"
BREAKOUT_DATA_DIR = os.path.join(BREAKOUT_DIR, "data")

# ---------- 数据来源 ----------
# 运行时只读自己 data/ 目录（独立，不再回退 breakout）。
# CACHE_SOURCE 保留只为向后兼容，始终按 local 处理。
CACHE_SOURCE = os.environ.get("SAS_CACHE_SOURCE", "local")

OHLCV_FILE = "ohlcv.pkl"
UNIVERSE_FILE = "universe.pkl"

# ---------- 增量刷新参数（每天启动自动拉最新交易日） ----------
AKSHARE_WORKERS = 6          # 新浪源并发数（已验证安全：578s/2405只/0失败；调高有被限流风险）
INCREMENTAL_BARS = 25        # 每只 stale 票拉的 tail 长度（覆盖漏几天的情况）
UNIVERSE_REFRESH_DAYS = 7    # universe 文件超过 N 天才重抓（避免每天抓全市场）

# ---------- 回测参数 ----------
FORWARD_HORIZONS = [1, 3, 5, 10]      # 计算选出后 N 日收益
HEADLINE_HORIZON = 1                   # 看板头条展示口径（T+1：信号日收盘买→次日收盘卖）
MIN_BARS = 60                          # 不足此数的股票跳过（指标算不出）
BUY_BASIS_CLOSE = "close"              # 收盘→收盘（headline）
BUY_BASIS_OPEN = "open_next"           # 次日开盘→收盘（实盘口径）
# 净值曲线交易成本（每轮买卖=买+卖）：印花税0.05%+佣金0.025%×2+滑点≈0.2%。
# T+1 每日换手，成本是主要摩擦；毛收益几何约0.59%/日，净收益对成本极度敏感。
EQUITY_TX_COST = 0.003                 # 0.3%/轮（可调：调高→净收益降）

# ---------- K 线图 ----------
CHART_BARS = 120                       # 每个信号抽屉的 K 线根数
CHART_PAD_AFTER = 30                   # 信号日之后再画多少根（看后续走势）
MAX_KLINE_SIGNALS = 200                # 内联 K 线数据上限（控文件体积）；其余表格可见但无抽屉
CHART_DPR = 2                          # canvas devicePixelRatio

# ---------- 信号抽屉分时（1min） ----------
# 策略信号抽屉加"分时"tab，看信号当天的1分钟走势（同个股模块）。
# 新浪 stock_zh_a_minute(period=1) 只回最近约8个交易日1min K，故仅最近信号可得，
# 更早信号抽屉如实提示"分时不可得"。
FETCH_STRATEGY_INTRADAY = True         # 总开关：False 跳过整段分时取数（秒级构建不变）
INTRADAY_LOOKBACK_CAL_DAYS = 14        # 信号日 >= asof-此天数 才拉分时（覆盖 ~8 交易日）
INTRADAY_WORKERS = 1                   # 分时并发：1=顺序（同个股模块，最稳）；调高有 sina SSL burst 风险
INTRADAY_DISK_CACHE = True             # 分时磁盘缓存：data/intraday/{sym}.pkl，命中（含该信号日）则跳过 sina，同日重跑秒级

# ---------- 涨停 ----------
LIMIT_UP_RATIO = 0.10                  # 主板 / 中小板
LIMIT_UP_RATIO_GROWTH = 0.20           # 创业板 300 / 科创板 688
# ST 5%、北交所 30% 不处理：ST 在 universe 层已剔除，北交所不在缓存

# ---------- 版本 ----------
VERSION = "0.2.0"

# ---------- 模块系统 ----------
# 看板分 4 大模块，顶部 tab 切换。随构建进度逐个启用。
#   strategy  策略：通达信公式回测（反转低吸…）
#   market    全景大盘：全球指数/温度计/情绪/资金流
#   stock     个股：单只 K 线 + 指标 + 策略信号
#   analysis  分析：综合分析
ENABLED_MODULES = ["market", "strategy", "stock", "analysis"]   # market=默认首页

# ---------- 策略注册表 ----------
# 以后加新通达信公式：写 strategies/xxx.py（实现 NAME/DESC/select），在此注册即可
STRATEGIES = {
    "reversal_low_absorb": "strategies.reversal_low_absorb",
    # 以下 6 个先不监控（收益惨，2026-09-26 暂停）：next_day_short / trend_strict / trend_observation
    # / ultra_strict / low_trend / next_day_short_pb。策略文件保留，去掉注释即恢复。
    # "next_day_short": "strategies.next_day_short",
    # "trend_strict": "strategies.trend_strict",
    # "trend_observation": "strategies.trend_observation",
    # "ultra_strict": "strategies.ultra_strict",
    # "low_trend": "strategies.low_trend",
    # "next_day_short_pb": "strategies.next_day_short_pb",
    "chanlun_bottom_fx": "strategies.chanlun_bottom_fx",    # 缠论底分型（底分型+10日涨停+市值50亿）
}
DEFAULT_STRATEGY = "reversal_low_absorb"


def ohlcv_path(source=None):
    """返回本地 ohlcv 缓存路径（运行时只读 data/，不再回退 breakout）。"""
    return os.path.join(DATA_DIR, OHLCV_FILE)


def universe_path(source=None):
    """返回本地 universe 缓存路径。"""
    return os.path.join(DATA_DIR, UNIVERSE_FILE)
