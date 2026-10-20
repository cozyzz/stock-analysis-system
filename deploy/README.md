# 部署到 Rocky Linux 9 服务器

本看板本质是 `run.py` 跑完生成的**自包含静态 HTML**（`回测看板.html`，18MB，无外部资源），不是常驻 Web 服务。部署 = 服务器每日自动 `run.py` 生成 + 一个静态文件服务托管，浏览器打开即看。

业务代码已验证 Linux 可移植，**无需改代码**。

## 前提

- Rocky Linux 9，root
- 能访问 github（拉代码）与 akshare 源（新浪/东财/同花顺，国内默认可达）
- 时区上海：`timedatectl set-timezone Asia/Shanghai`
- **无域名/未备案**：用 8080 高端口（国内云商对 80/443 无备案会拦，高端口不拦、不需备案），裸 IP 访问 `http://<服务器IP>:8080/`。不用 nginx，用 Python 自带 `http.server` 起静态服务（个人单用户足够）。

## 一次性步骤

```bash
which git || dnf install -y git
git clone https://github.com/cozyzz/stock-analysis-system.git /opt/stock-analysis-system
# 国内服务器访问 github 常被重置（同本机 errno 10054）。若 git clone 卡住/失败，改用压缩包上传：
#   本机：git archive --format=tar.gz -o stock.tgz HEAD    # 只含代码，不含 data/ 与 回测看板.html
#   FinalShell SFTP 上传 stock.tgz 到 /opt，再：
#   mkdir -p /opt/stock-analysis-system && tar xzf /opt/stock.tgz -C /opt/stock-analysis-system
cd /opt/stock-analysis-system
bash deploy/setup.sh                                       # 装 python3/rsync/firewalld + venv + 依赖 + webroot
# 数据初始化（二选一，见下）
/opt/stock-venv/bin/python run.py                          # 首次生成
cp -f 回测看板.html /var/www/stock/index.html
cp deploy/stock-dashboard.service /etc/systemd/system/
systemctl daemon-reload && systemctl enable --now stock-dashboard   # 起静态服务(8080)
(crontab -l 2>/dev/null; cat deploy/stock.cron | grep -v '^#') | crontab -   # 每日定时
firewall-cmd --add-port=8080/tcp --permanent && firewall-cmd --reload        # 放行 8080
```
访问 `http://<服务器IP>:8080/`。

> ⚠ **腾讯云/阿里云安全组**也要放行 8080（控制台 → 安全组 → 入站规则加 TCP 8080）。`firewall-cmd` 只管 OS 层，云安全组是另一道闸——打不开先查这个。
> 时区务必上海：`timedatectl set-timezone Asia/Shanghai`，否则 cron 16:10 不是 A 股收盘后。
> 注：国内服务器访问 github 多半不通，`publish.sh` 里每日 `git pull` 会失败但被 `|| echo` 兜住、用现有代码照常渲染（数据刷新走 sina 不受影响）。要更新代码时重新上传 `stock.tgz` 覆盖即可。

## 数据初始化（二选一）

### A（推荐）上传本地 data/
本机 `data/`（ohlcv.pkl + universe.pkl，486MB）已每日增量、新鲜。传一次，之后 cron 只增量补 stale 票（秒级、低被限风险），不赌 sina 不限服务器 IP。
```bash
# 本机 git-bash 或 FinalShell SFTP
scp -r data/ root@<服务器IP>:/opt/stock-analysis-system/
# 有 rsync 更佳（可断点续传）：
rsync -avzP data/ root@<服务器IP>:/opt/stock-analysis-system/data/
```

### B（备选）服务器 run.py fetch
```bash
/opt/stock-venv/bin/python run.py fetch    # akshare 从零拉 ~5200 只×8 年，约 15-20 分钟
```
依赖 sina 不限服务器 IP；若被限会失败，回退 A。

## 验证

```bash
cd /opt/stock-analysis-system && /opt/stock-venv/bin/python run.py --no-update   # 生成 OK
curl -I http://localhost:8080/          # → HTTP/1.1 200, text/html
bash deploy/publish.sh                  # 手动跑：webroot 更新 + 日志
ls -l /var/www/stock/index.html /var/log/stock-refresh.log
```
浏览器 `http://<服务器IP>:8080/` → 4 模块 tab（market/strategy/stock/analysis）切换正常。

## 日常更新代码

本机改完 → `git push`（github 需代理，见本机已设的 `http.https://github.com.proxy`）→ 服务器下次 `publish.sh` 的 `git pull` 自动取最新，**无需登服务器**。github 偶发不通时 `git pull` 失败但 `publish.sh` 继续用现有代码渲染（不阻塞）。

## 排错

- **看板不刷新**：`tail -50 /var/log/stock-refresh.log`，看 run.py 报什么。sina 偶限会降级用缓存渲染（看板照出、数据略旧，非报错）。
- **服务没起**：`systemctl status stock-dashboard`；改完 service 文件 `systemctl daemon-reload && systemctl restart stock-dashboard`。
- **cron 没触发**：`systemctl status crond`；`crontab -l` 确认行在；时区 `timedatectl`。
- **8080 打不开**：云商安全组也要放行 8080；`firewall-cmd --list-all`。
- **github 慢/不通**：服务器也可设代理 `git config --global http.https://github.com.proxy http://<代理IP>:<端口>`（同本机做法）。

## 日后有域名 + 备案

可换 nginx（80/443）+ 可选 basic auth，或给 http.server 加反代。现 http.server 单线程够个人用。

## 备注

- intraday 分时（`FETCH_STRATEGY_INTRADAY=True`）服务器也拉（sina 国内可达，+5-8min/构建）；想更快可服务器 `config.py` 设 `False`。
- 看板为历史回测验证，**非投资建议，不预测涨跌，不自动下单**。
