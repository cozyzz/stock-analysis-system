# -*- coding: utf-8 -*-
"""模块框架

每个模块是一个 .py（modules/xxx.py），暴露：
  KEY    : str          模块键（tab 路由用）
  NAME   : str          tab 显示名
  DESC   : str          一句话
  order  : int          tab 排序
  build(ctx) -> dict    返回该模块的 section payload（注入看板）

ctx 持有跨模块共享数据：ohlcv / universe / name_map / calendar / args。
未启用的模块不导入（config.ENABLED_MODULES 控制）。
"""
import importlib

import config as C


class Ctx:
    """跨模块共享上下文。"""

    def __init__(self, ohlcv=None, universe=None, name_map=None,
                 calendar=None, args=None, pool_symbols=None, shares_map=None):
        self.ohlcv = ohlcv
        self.universe = universe
        self.name_map = name_map or {}
        self.calendar = calendar or []
        self.args = args
        self.pool_symbols = pool_symbols or []
        self.shares_map = shares_map or {}
        self.data_asof = (calendar[-1] if calendar else None)
        self.shared = {}   # 模块间共享（如 strategy 的全量信号），不进 HTML


def load_enabled():
    """按 config.ENABLED_MODULES 导入模块，返回 [module, ...]（按 order 排序）。"""
    mods = []
    for name in C.ENABLED_MODULES:
        try:
            m = importlib.import_module(f"modules.{name}")
            if not hasattr(m, "build"):
                continue
            mods.append(m)
        except Exception as e:
            print(f"  ⚠ 模块 {name} 加载失败：{type(e).__name__}: {e}")
    mods.sort(key=lambda m: getattr(m, "order", 99))
    return mods
