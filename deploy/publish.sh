#!/usr/bin/env bash
# stock-analysis-system · 生成+发布看板（cron 调，幂等，手动也可跑）
# 日志：/var/log/stock-refresh.log（自动追加，手动跑也回显到终端）
set -euo pipefail

REPO=$(cd "$(dirname "$0")/.." && pwd)
VENV=/opt/stock-venv
WEBROOT=/var/www/stock
LOG=/var/log/stock-refresh.log
HTML="$REPO/回测看板.html"

mkdir -p "$WEBROOT"
exec > >(tee -a "$LOG") 2>&1
echo "============================================================"
echo "[$(date '+%F %T')] publish 开始"
cd "$REPO"

echo "[1/3] git pull（更新代码）"
# 国内服务器到 github 常被墙（TCP 卡死而非快速失败），必须 timeout 包一层：
# 否则 cron 会像交互式 git pull 一样无限挂起，|| echo 接不到（只接非零退出，不接卡死）。
timeout 30 git pull -q || echo "  ⚠ git pull 失败/超时，用现有代码继续（国内服务器→github 常不通，不阻塞渲染）"

echo "[2/3] run.py 生成看板（增量刷新 sina + 渲染）"
if "$VENV/bin/python" run.py; then
  cp -f "$HTML" "$WEBROOT/index.html"
  echo "  → 已发布 $WEBROOT/index.html"
else
  echo "  ⚠ run.py 失败，webroot 不更新（保留上次看板）"
  exit 1
fi

echo "[$(date '+%F %T')] publish 完成"
echo "============================================================"
