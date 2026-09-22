# -*- coding: utf-8 -*-
"""通用 akshare 子进程 worker。

为什么需要：akshare 的东财系接口（stock_zh_a_spot / stock_individual_fund_flow_rank /
stock_sector_fund_flow_rank 等）会用 py_mini_racer (V8) 执行反爬 JS 解密。V8 偶发
段错误（C 层 CHECK failed），try/except 抓不住，直接杀掉整个进程。放子进程后：
段错误只杀子进程（rc≠0），主进程存活 → 重试或降级，看板照常生成。

用法：py -3 _ak_worker.py <output_pkl_path> <ak_func_name> [kwargs_json]
  例：py -3 _ak_worker.py out.pkl stock_zh_a_spot
      py -3 _ak_worker.py out.pkl stock_individual_fund_flow_rank '{"indicator":"今日"}'
退出码 0 + 写出 pkl = 成功；非 0 = 崩溃（含 segfault）/异常。
"""
import sys


def main():
    if len(sys.argv) < 3:
        print("ERR usage: _ak_worker.py <out> <func> [kwargs_json]", file=sys.stderr)
        sys.exit(2)
    out, func = sys.argv[1], sys.argv[2]
    kw_json = sys.argv[3] if len(sys.argv) > 3 else "{}"
    import json
    import akshare as ak  # 子进程内 import，隔离 V8 崩溃
    fn = getattr(ak, func, None)
    if fn is None:
        print(f"ERR no ak.{func}", file=sys.stderr)
        sys.exit(2)
    kwargs = json.loads(kw_json) if kw_json else {}
    obj = fn(**kwargs)  # 反爬 JS 解密可能在这里 segfault
    if obj is None:
        print("ERR empty")
        sys.exit(1)
    import pickle
    with open(out, "wb") as f:
        pickle.dump(obj, f)
    print("OK", len(obj) if hasattr(obj, "__len__") else 1)


if __name__ == "__main__":
    main()
