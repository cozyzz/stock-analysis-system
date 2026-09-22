# -*- coding: utf-8 -*-
"""stock-analysis-system · 入口（独立项目，自有数据层）

用法：
  py -3 run.py                     # 默认：启动自动增量刷新最新交易日 → 跑策略 → 渲染看板
  py -3 run.py --no-update         # 跳过启动刷新（离线/快速重渲染，用本地缓存）
  py -3 run.py --refresh-universe  # 强制重抓 universe（名称/市值）
  py -3 run.py --limit 50          # 冒烟：只扫前 50 只
  py -3 run.py --strategy <name>   # 选策略（config.STRATEGIES）
  py -3 run.py --output <path>     # 自定义输出
  py -3 run.py --no-render         # 只算不画
  py -3 run.py seed                # 一次性从 breakout 拷 8 年历史到 data/（首次迁移）
  py -3 run.py fetch [--limit N]   # 用 akshare 从零独立拉数据到 data/（慢）
"""
import sys
import os
import time
import argparse

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import config as C
import datasource as ds
import render
import modules


def cmd_fetch(args):
    print("=" * 60)
    print("  独立拉取数据（akshare，慢）")
    print("=" * 60)
    ds.fetch_via_akshare(limit=args.limit)
    print("  完成。后续 py -3 run.py 会自动增量刷新最新交易日。")


def cmd_seed(args):
    print("=" * 60)
    print("  一次性迁移：从 breakout 拷 8 年历史到 data/")
    print("=" * 60)
    ds.seed_from_breakout()
    print("  完成。后续 py -3 run.py 会自动增量刷新最新交易日，breakout 可弃用。")


def cmd_run(args):
    t0 = time.time()
    print("=" * 60)
    print("  stock-analysis-system · 看板生成")
    print(f"  策略：{args.strategy}" + (f"  limit={args.limit}" if args.limit else ""))
    print("=" * 60)

    # 0. 数据刷新（每天启动自动拉最新交易日；--no-update 跳过）
    if not args.no_update:
        print("[刷新] 检查数据新鲜度（新浪源增量补最新交易日）")
        try:
            n_ok, n_fail, asof = ds.refresh_incremental(verbose=True)
            ds.maybe_refresh_universe(verbose=True, force=args.refresh_universe)
            if n_ok or n_fail:
                print(f"  数据截止 {asof}（本次补 {n_ok} 只 / 失败 {n_fail} 只）")
            else:
                print(f"  数据截止 {asof}（已是最新）")
        except FileNotFoundError as e:
            print(f"  ⚠ 本地无缓存：{str(e).splitlines()[0]}")
            print("  先跑 `py -3 run.py seed`（从 breakout 拷贝 8 年历史）或 `py -3 run.py fetch`（akshare 独立拉），再运行。")
            return
        except Exception as e:
            print(f"  [刷新失败] {type(e).__name__}: {e}（继续用本地缓存渲染，可用 --no-update 跳过）")
    else:
        info = ds.data_asof()
        print(f"  --no-update：跳过刷新，本地缓存截止 {info[0]}")

    # 1. 数据
    print("[1/3] 加载数据")
    ohlcv = ds.load_ohlcv()
    uni = ds.load_universe()
    name_map = ds.symbol_name_map(uni)
    allowed_syms, _ = ds.filter_pool(uni)
    calendar = ds.build_calendar(ohlcv)
    print(f"  股票池过滤后：{len(allowed_syms)} 只（剔 ST/创业板/科创板/北交所）")
    print(f"  交易日历：{len(calendar)} 天，截止 {calendar[-1] if calendar else '?'}")

    if args.limit:
        allowed_syms = allowed_syms[: args.limit]
        print(f"  --limit {args.limit}：只扫前 {len(allowed_syms)} 只")

    shares_map = ds.build_shares_map(uni)
    print(f"  派生股本（总/流通，快照）：{len(shares_map)} 只")
    ctx = modules.Ctx(
        ohlcv=ohlcv, universe=uni, name_map=name_map,
        calendar=calendar, args=args, pool_symbols=allowed_syms,
        shares_map=shares_map,
    )

    # 2. 模块
    print("[2/3] 构建模块")
    mods = modules.load_enabled()
    if not mods:
        print("  ⚠ 无可用模块（config.ENABLED_MODULES 为空）")
        return
    modules_data = []
    for m in mods:
        print(f"  · {m.KEY}（{m.NAME}）...")
        t1 = time.time()
        try:
            sec = m.build(ctx)
            modules_data.append((m, sec))
            print(f"    完成（{time.time()-t1:.1f}s）")
        except Exception as e:
            import traceback
            print(f"    失败：{type(e).__name__}: {e}")
            traceback.print_exc()

    # 3. 渲染
    if args.no_render:
        print("[3/3] --no-render，跳过渲染")
        return
    print("[3/3] 渲染看板")
    payload = render.build_payload(modules_data, ctx, elapsed=time.time() - t0)
    info = render.render(payload, output_path=args.output)
    print()
    print("=" * 60)
    print(f"  生成完成：{info['path']}")
    print(f"  大小：{info['size_kb']} KB   校验：{info['hash']}")
    print(f"  耗时：{time.time()-t0:.1f} 秒")
    print(f"  双击打开即可（完全离线可用）")
    print("=" * 60)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("sub", nargs="?", default="run", help="run / seed / fetch")
    ap.add_argument("--strategy", default=C.DEFAULT_STRATEGY)
    ap.add_argument("--limit", type=int, default=None)
    ap.add_argument("--output", default=None)
    ap.add_argument("--no-render", action="store_true")
    ap.add_argument("--no-update", action="store_true", help="跳过启动时自动增量刷新")
    ap.add_argument("--refresh-universe", action="store_true", help="强制重抓 universe")
    args = ap.parse_args()

    if args.sub == "fetch":
        cmd_fetch(args)
    elif args.sub == "seed":
        cmd_seed(args)
    else:
        cmd_run(args)


if __name__ == "__main__":
    main()
