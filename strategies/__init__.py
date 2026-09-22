# -*- coding: utf-8 -*-
"""策略框架

策略 = 一个 Python 模块，需暴露：
  NAME   : str          策略名
  DESC   : str          一句话描述（看板展示）
  select(g: pd.DataFrame) -> pd.Series[bool]
         输入单只股票的日线（按 date 升序，含 symbol 列），返回与 g 等长的信号序列

注册：在 config.STRATEGIES 里 {name: 模块路径}。以后加新通达信公式照此即可。
"""
import importlib

import config as C


class Strategy:
    def __init__(self, module):
        self.module = module
        self.NAME = getattr(module, "NAME", module.__name__)
        self.DESC = getattr(module, "DESC", "")
        self.AUTHOR = getattr(module, "AUTHOR", "")
        self.PARAMS = getattr(module, "PARAMS", {})
        self.select = module.select
        # 卖出口径：默认 "close"（次日收盘卖）；策略可声明 "open_next"（次日开盘卖）。
        self.SELL_BASIS = getattr(module, "SELL_BASIS", "close")

    def __repr__(self):
        return f"<Strategy {self.NAME}>"


def get_strategy(name=None):
    name = name or C.DEFAULT_STRATEGY
    modpath = C.STRATEGIES.get(name)
    if not modpath:
        raise ValueError(f"未知策略: {name}；已注册: {list(C.STRATEGIES)}")
    module = importlib.import_module(modpath)
    return Strategy(module)


def list_strategies():
    out = []
    for name, modpath in C.STRATEGIES.items():
        try:
            st = get_strategy(name)
            out.append((name, st.NAME, st.DESC))
        except Exception as e:
            out.append((name, "?(加载失败)", str(e)[:40]))
    return out
