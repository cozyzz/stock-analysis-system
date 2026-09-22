# -*- coding: utf-8 -*-
"""渲染层

复刻 breakout 范式：
  - safe_json：内联 JSON 进 <script>，防 </ 、<!-- 、U+2028/9，NaN→null
  - var D = /*__DASH_DATA__*/null; 占位符注入
  - 原子写盘 .tmp + os.replace
  - 自检占位符被替换

看板是单页多 tab（modules），template.html 读 D.modules 渲染 nav。
"""
import io
import os
import json
import math
import datetime

import numpy as np
import pandas as pd

import config as C


# ---------- safe_json ----------
def _json_default(o):
    if isinstance(o, (np.integer,)):
        return int(o)
    if isinstance(o, (np.floating,)):
        v = float(o)
        return v if math.isfinite(v) else None
    if isinstance(o, (np.bool_,)):
        return bool(o)
    if isinstance(o, (pd.Timestamp, datetime.datetime, datetime.date)):
        return str(o)[:19]
    if isinstance(o, pd.DataFrame):
        return None
    return str(o)


def safe_json(obj):
    text = json.dumps(obj, ensure_ascii=False, allow_nan=False,
                      default=_json_default, separators=(",", ":"))
    # 1) "</" 会提前闭合 <script> → "<\/"（JSON 里 \/ 合法且等于 /）
    text = text.replace("</", "<\\/")
    # 2) "<!--" 触发 HTML 注释 → "<\u0021--"（\! 非合法 JSON 转义）
    text = text.replace("<!--", "<\\u0021--")
    # 3) U+2028 / U+2029 在部分 JS 引擎是行终止符
    text = text.replace("\u2028", "\\u2028").replace("\u2029", "\\u2029")
    return text


def _clean(obj):
    """递归把 NaN/Inf→None、float 保留 6 位。"""
    if isinstance(obj, dict):
        return {k: _clean(v) for k, v in obj.items()}
    if isinstance(obj, (list, tuple)):
        return [_clean(v) for v in obj]
    if isinstance(obj, float):
        if not math.isfinite(obj):
            return None
        return round(obj, 6)
    if isinstance(obj, (np.floating,)):
        v = float(obj)
        return round(v, 6) if math.isfinite(v) else None
    if isinstance(obj, (np.integer,)):
        return int(obj)
    if isinstance(obj, (np.bool_,)):
        return bool(obj)
    return obj


# ---------- payload ----------
def build_payload(modules_data, ctx, elapsed=0.0):
    """modules_data: [(module, section_dict), ...]"""
    mods_meta = []
    sections = {}
    active = None
    for m, sec in modules_data:
        mods_meta.append({
            "key": m.KEY, "name": m.NAME, "desc": getattr(m, "DESC", ""),
            "order": getattr(m, "order", 99),
        })
        sections[m.KEY] = sec
        if active is None:
            active = m.KEY

    payload = {
        "meta": {
            "version": C.VERSION,
            "run_at": datetime.datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
            "data_asof": ctx.data_asof,
            "pool_size": len(ctx.pool_symbols) if ctx.pool_symbols else 0,
            "universe_size": len(ctx.universe) if ctx.universe is not None else 0,
            "elapsed_sec": round(elapsed, 1),
            "note": "本看板为策略回测验证工具，收益为历史信号统计，非投资建议。",
        },
        "modules": sorted(mods_meta, key=lambda x: x["order"]),
        "active": active,
        "sections": sections,
    }
    return _clean(payload)


# ---------- render ----------
def render(payload, output_path=None, template_path=None):
    output_path = output_path or C.OUTPUT_HTML
    template_path = template_path or C.TEMPLATE_PATH
    with io.open(template_path, "r", encoding="utf-8") as f:
        tpl = f.read()
    data_json = safe_json(payload)
    if "/*__DASH_DATA__*/null" in tpl:
        html = tpl.replace("/*__DASH_DATA__*/null", data_json)
    elif "__DASH_DATA__" in tpl:
        html = tpl.replace("__DASH_DATA__", data_json)
    else:
        raise RuntimeError("模板未找到数据占位符 /*__DASH_DATA__*/null")
    title = f"stock-analysis-system v{C.VERSION}"
    html = html.replace("__DASH_TITLE__", title)
    # 自检
    if "__DASH_DATA__" in html:
        raise RuntimeError("模板占位符未被替换")
    os.makedirs(os.path.dirname(output_path) or ".", exist_ok=True)
    tmp = output_path + ".tmp"
    with io.open(tmp, "w", encoding="utf-8", newline="") as f:
        f.write(html)
    os.replace(tmp, output_path)
    size_kb = os.path.getsize(output_path) / 1024.0
    import hashlib
    h = hashlib.md5(open(output_path, "rb").read()).hexdigest()[:12]
    return {"path": output_path, "size_kb": round(size_kb, 1), "hash": h}
