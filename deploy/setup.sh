#!/usr/bin/env bash
# stock-analysis-system · 服务器一次性引导（Rocky Linux 9，root 执行）
# 用法：cd /opt/stock-analysis-system && bash deploy/setup.sh
set -euo pipefail

REPO=$(cd "$(dirname "$0")/.." && pwd)
VENV=/opt/stock-venv
WEBROOT=/var/www/stock

echo "[1/5] 装系统包（python3/rsync/firewalld/git/nginx）"
dnf install -y python3 python3-pip rsync firewalld git nginx

echo "[2/5] 建 venv: $VENV"
python3 -m venv "$VENV"
"$VENV/bin/pip" install --upgrade pip
"$VENV/bin/pip" install -r "$REPO/requirements.txt"

echo "[3/5] 建 webroot"
mkdir -p "$WEBROOT"

echo "[4/5] 装 nginx 配置（8080 + gzip）"
cp -f "$REPO/deploy/stock.conf" /etc/nginx/conf.d/
# SELinux 若 enforcing：restorecon（/var/www 默认已是 httpd_sys_content_t，多半不用）
if getenforce 2>/dev/null | grep -qi enforcing; then restorecon -R "$WEBROOT"; fi
nginx -t

echo "[5/5] 完成 ✓"
echo "  下一步：初始化数据（见 deploy/README.md §数据初始化），再："
echo "  $VENV/bin/python run.py && cp -f 回测看板.html $WEBROOT/index.html"
echo "  起服务：systemctl enable --now nginx"
echo "  低内存实例（2核4G）务必加 4G swap，否则 run.py 会 OOM 被杀（见 README §OOM）"
