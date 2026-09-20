#!/usr/bin/env bash
set -euo pipefail

# xrdp 首次启动需要自己的运行目录与密钥
mkdir -p /var/run/xrdp /tmp/.X11-unix
chmod 1777 /tmp/.X11-unix
rm -f /var/run/xrdp/xrdp.pid /var/run/xrdp/xrdp-sesman.pid

if [ ! -f /etc/xrdp/cert.pem ] || [ ! -f /etc/xrdp/key.pem ]; then
  xrdp-keygen xrdp /etc/xrdp/cert.pem >/dev/null 2>&1 || true
  if [ -f /etc/xrdp/cert.pem ] && [ ! -f /etc/xrdp/key.pem ]; then
    cp /etc/xrdp/cert.pem /etc/xrdp/key.pem
  fi
fi
chmod 600 /etc/xrdp/key.pem 2>/dev/null || true

echo "[windows-client] 启动 xrdp-sesman + xrdp，监听 3389（RDP）"
xrdp-sesman
sleep 1
exec xrdp --nodaemon
