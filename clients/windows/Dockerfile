# 模拟 "Windows" 被纳管主机：Debian + xrdp + xfce（Xvnc 后端，容器内更稳）。
# 对外提供真实的 RDP (3389) 服务，Windows 远程桌面 / mstsc、
# Remmina、FreeRDP 均可直接连上去看到图形桌面。
FROM debian:bookworm-slim

ARG RDP_USER=admin
ARG RDP_PASSWORD=Windows@2026

ENV DEBIAN_FRONTEND=noninteractive

RUN apt-get update \
    && apt-get install -y --no-install-recommends \
        xrdp \
        tigervnc-standalone-server \
        tigervnc-common \
        xfce4-session \
        xfce4-panel \
        xfwm4 \
        xfdesktop4 \
        xfce4-settings \
        xfce4-terminal \
        dbus-x11 \
        x11-xserver-utils \
        xfonts-base \
        xfonts-75dpi \
        procps \
        ca-certificates \
    && rm -rf /var/lib/apt/lists/* \
    # RDP 登录账号
    && useradd -m -s /bin/bash "${RDP_USER}" \
    && echo "${RDP_USER}:${RDP_PASSWORD}" | chpasswd \
    && adduser "${RDP_USER}" ssl-cert \
    && echo "startxfce4" > "/home/${RDP_USER}/.xsession" \
    && chown "${RDP_USER}:${RDP_USER}" "/home/${RDP_USER}/.xsession" \
    && chmod 644 "/home/${RDP_USER}/.xsession"

# 3389 + 安全协商；注释掉 [Xorg] 段，只保留 Xvnc 会话后端（容器兼容性最好）
RUN sed -i 's/^port=.*/port=3389/' /etc/xrdp/xrdp.ini \
    && sed -i 's/^#\?security_layer=.*/security_layer=negotiate/' /etc/xrdp/xrdp.ini \
    && sed -i 's/^#\?crypt_level=.*/crypt_level=high/' /etc/xrdp/xrdp.ini \
    && awk '{ if ($0 ~ /^\[.*\]/) skip = ($0 ~ /^\[Xorg\]/); \
             if (skip && $0 !~ /^[[:space:]]*#/ && $0 !~ /^[[:space:]]*$/) \
                print "#" $0; else print $0 }' \
            /etc/xrdp/xrdp.ini > /tmp/xrdp.ini \
    && mv /tmp/xrdp.ini /etc/xrdp/xrdp.ini

# 桌面放身份说明，连上 RDP 后能确认这台主机
RUN mkdir -p "/home/${RDP_USER}/Desktop" \
    && printf 'xrdp + Xfce 模拟 Windows 主机\nRDP 账号: %s\n口令见编排环境变量\n' "${RDP_USER}" \
        > "/home/${RDP_USER}/Desktop/连接说明.txt" \
    && chown -R "${RDP_USER}:${RDP_USER}" "/home/${RDP_USER}/Desktop"

COPY entrypoint.sh /entrypoint.sh
RUN chmod +x /entrypoint.sh

EXPOSE 3389

# 健康检查：3389 可建立 TCP 连接即视为 RDP 在线
HEALTHCHECK --interval=10s --timeout=4s --retries=6 \
    CMD bash -c 'exec 3<>/dev/tcp/127.0.0.1/3389'

ENTRYPOINT ["/entrypoint.sh"]
