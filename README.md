# stock-analysis-system

通达信公式策略回测系统：自有数据层 + 多策略回测 + 单页离线看板（HTML）。
从 A 股 8 年历史日线里扫描策略信号，统计次日/多日收益分布与累计净值，渲染成一个可双击离线打开的看板。

---

## 环境依赖

- **Windows + Python 3.8+**（启动器用 `py -3`，**不要用** `python` / `python3`——本机这两个是失效 stub）
- 依赖包：

  ```bash
  py -3 -m pip install akshare pandas numpy
  ```

  （akshare 会自动带 requests 等子依赖；render 用纯字符串模板，不依赖 jinja2）

---

## 数据获取（首次使用必看）

`data/` 目录（`ohlcv.pkl` + `universe.pkl`，约 486M）**不进 git**，clone 后没有数据，需自行拉取，二选一：

### 方式一：akshare 独立拉（推荐，无需其他项目）

```bash
py -3 run.py fetch
```

- 全市场 ~5200 只、8 年前复权日线，新浪源 6 并发，约 15-20 分钟
- 完成后生成 `data/ohlcv.pkl` + `data/universe.pkl`

### 方式二：从 breakout 项目一次性拷贝（如果你本地有 `stock-breakout-in-volume`）

```bash
# 先编辑 config.py 的 BREAKOUT_DIR 指向 breakout 路径
py -3 run.py seed
```

之后每次 `py -3 run.py` 启动会**自动增量刷新最新交易日**（秒级，仅补 stale 票）。

---

## 启动命令

```bash
py -3 run.py                     # 默认：增量刷新 → 跑策略 → 渲染 回测看板.html
py -3 run.py --no-update         # 跳过刷新（离线/快速重渲染，用本地缓存）
py -3 run.py --refresh-universe  # 强制重抓 universe（名称/市值，默认超7天才重抓）
py -3 run.py --limit 50          # 冒烟：只扫前 50 只（快速验证用）
py -3 run.py --strategy <name>   # 指定策略（见下表，config.STRATEGIES 的 key）
py -3 run.py --output x.html     # 自定义输出路径
py -3 run.py --no-render         # 只算不画（验数字）
py -3 run.py fetch               # 独立拉历史数据（首次）
py -3 run.py seed                # 从 breakout 拷历史（首次）
```

默认跑 `DEFAULT_STRATEGY = reversal_low_absorb`，生成 `回测看板.html`，**双击离线打开**。

---

## 看板模块

`config.ENABLED_MODULES` 控制，顶部 tab 切换：

| key | 名称 | 说明 |
|---|---|---|
| market | 全景大盘 | 全球指数/温度计/情绪/资金流 |
| strategy | 策略回测 | 通达信公式信号扫描 + 收益统计 + 净值曲线 |
| stock | 个股 | 单只 K 线 + 指标 + 策略信号 |
| analysis | 分析 | 综合分析 |

---

## 策略

`config.STRATEGIES` 注册（写 `strategies/xxx.py` 实现 `NAME/DESC/select`，在此注册即可加新策略）：

| key | 名称 | 说明 |
|---|---|---|
| `reversal_low_absorb` | 反转低吸 | **自创·通达信原式逐字翻译**，超卖跌透后反转启动（默认策略） |
| `next_day_short` | 隔日超短 | 涨2-5%放量+涨停基因+大盘，次日开盘卖 |
| `next_day_short_pb` | 隔日超短(回踩版) | 隔日超短筛选 + 5日内回踩MA5买 + 次日开盘卖 |
| `chanlun_bottom_fx` | 缠论底分型 | 缠论底分型 + 10日内涨停 + 总市值≥50亿 |
| `trend_strict` | 严格趋势 | 移植自 Big-A-chovy，顺势严格 |
| `trend_observation` | 趋势观察 | 移植，回踩宽松 |
| `ultra_strict` | 严格超短 | 移植，小盘动能 |
| `low_trend` | 低吸趋势 | 移植改造，低位低吸 |

**策略研究文档**：[策略研究/反转低吸-挂5脉冲卖出.md](策略研究/反转低吸-挂5脉冲卖出.md)——反转低吸次日挂 +5% 限价脉冲卖出，12 年回测年化 112.6%、回撤 18.8%（盘中最高中位 +3.79%，84% 冲过正）。

**策略条件速查**：[策略清单.md](策略清单.md)——8 个策略的筛选条件、阈值、卖出口径、风格定位一览。

---

## 目录结构

```
stock-analysis-system/
├── config.py              # 全局配置（路径/参数/策略注册表）
├── datasource.py          # 数据层（load/refresh/fetch/seed）
├── tdx.py                 # 通达信公式库（MA/REF/CROSS/zt_price 等）
├── engine.py              # 回测引擎（信号扫描 + 收益 + 净值）
├── render.py              # 渲染层（JSON→template.html→离线HTML）
├── run.py                 # 入口（run/seed/fetch 子命令）
├── template.html          # 看板模板
├── modules/               # 4 大看板模块（market/strategy/stock/analysis）
├── strategies/            # 策略实现（7 个）
├── 策略研究/              # 策略研究文档
├── refs/                  # 参考截图
├── data/                  # 数据缓存（gitignore，需 fetch/seed 生成）
├── _probe_nds_high.py     # 探针：次日盘中分布 + 挂单成交模拟
├── _probe_rla_l5.py       # 探针：反转低吸三口径12年净值
└── _probe_rla_min.py      # 探针：次日早盘冲高时点（新浪5分钟源）
```

---

## 数据口径与来源

- **日线**：前复权（akshare/新浪 `stock_zh_a_daily`，volume 除 100：新浪"股"→"手"）
- **universe**：东财 `stock_zh_a_spot_em`（总市值/流通市值/换手率），超 7 天才重抓
- **启动增量**：交易日历用 `ak.tool_trade_date_hist_sina()`，仅补 stale 票（last_date < 最新交易日）
- **分时（5分钟）**：新浪 `stock_zh_a_minute`，仅回最近约 41 天；东财 `push2his` 分钟历史在部分网络环境被 IP 断连，不可用
- **回测口径**：信号日 i 尾盘收盘价 c[i] 买入，ret = 次日收盘/开盘/最高 相对 c[i]。策略可声明 `SELL_BASIS`：`close`（默认次日收盘卖）/ `open_next`（次日开盘卖）/ `limit_5pct`（次日挂+5%限价，触及成交否则收盘卖，待接入）
- **净值模型**：满仓均分当日信号、逐信号日复利、成本 0.3%/轮（`EQUITY_TX_COST`）。简化口径，非真实组合

---

## 常见问题

**Q: clone 后跑 `run.py` 报"本地无缓存"**
A: `data/` 不进 git，先跑 `py -3 run.py fetch` 拉历史数据（约15-20分钟）。

**Q: 怎么加新策略**
A: 写 `strategies/xxx.py`（实现 `NAME/DESC/select`，可选 `SELL_BASIS`/`PARAMS`），在 `config.STRATEGIES` 注册即可。

**Q: 数据多久没更新需要重抓**
A: 启动时自动增量补最新交易日，秒级。universe 超 7 天自动重抓。强制重抓用 `--refresh-universe`。

**Q: 推送到 GitHub**
A: `git push -u origin main`，首次弹 GitHub 登录窗（Git Credential Manager），浏览器授权后自动上传并缓存凭证。

---

## 声明

本系统为历史回测验证工具，所有收益为历史信号统计，**不构成投资建议，不预测涨跌**。数据仅供研究用途。
