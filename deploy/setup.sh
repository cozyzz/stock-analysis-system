#!/usr/bin/env bash
# stock-analysis-system · 服务器一次性引导（Rocky Linux 9，root 执行）
# 用法：cd /opt/stock-analysis-system && bash deploy/setup.sh
set -euo pipefail

REPO=$(cd "$(dirname "$0")/.." && pwd)
VENV=/opt/stock-venv
WEBROOT=/var/www/stock

echo "[1/4] 装系统包（python3/rsync/firewalld/git；不用 nginx）"
dnf install -y python3 python3-pip rsync firewalld git

echo "[2/4] 建 venv: $VENV"
python3 -m venv "$VENV"
"$VENV/bin/pip" install --upgrade pip
"$VENV/bin/pip" install -r "$REPO/requirements.txt"

echo "[3/4] 建 webroot"
mkdir -p "$WEBROOT"

echo "[4/4] 完成 ✓"
echo "  下一步：初始化数据（见 deploy/README.md §数据初始化），再："
echo "  $VENV/bin/python run.py && cp -f 回测看板.html $WEBROOT/index.html"
echo "  然后装看板服务：cp deploy/stock-dashboard.service /etc/systemd/system/ && systemctl enable --now stock-dashboard"
