#!/usr/bin/env bash
set -euo pipefail

# 首次启动生成主机密钥（镜像里不带，保证每实例独立）
ssh-keygen -A >/dev/null 2>&1 || true

# 写入登录后的欢迎信息，方便手工 SSH 登录时确认连上的是哪台
cat > /etc/motd <<EOF

  ╔══════════════════════════════════════════╗
  ║  执钥模拟 Linux 客户端: $(hostname)
  ║  账号: ${OPS_USER:-ops}   (SSH 口令登录)
  ╚══════════════════════════════════════════╝

EOF

echo "[linux-client] sshd 启动于 $(hostname):22"
exec /usr/sbin/sshd -D -e
