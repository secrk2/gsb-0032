# 模拟 Linux 被纳管主机：真实运行 openssh-server，可用账号口令登录。
# 一个镜像，compose 里用不同 CLIENT_ID / hostname 起两份。
FROM debian:bookworm-slim

ARG OPS_USER=ops
ARG OPS_PASSWORD=Linux@2026

ENV DEBIAN_FRONTEND=noninteractive

RUN apt-get update \
    && apt-get install -y --no-install-recommends \
        openssh-server \
        procps \
        ca-certificates \
    && rm -rf /var/lib/apt/lists/* \
    && mkdir -p /run/sshd /var/run/sshd \
    # 登录账号
    && useradd -m -s /bin/bash "${OPS_USER}" \
    && echo "${OPS_USER}:${OPS_PASSWORD}" | chpasswd \
    # 允许口令认证
    && sed -i 's/^#\?PasswordAuthentication.*/PasswordAuthentication yes/' /etc/ssh/sshd_config \
    && sed -i 's/^#\?PermitRootLogin.*/PermitRootLogin yes/' /etc/ssh/sshd_config \
    && echo "root:${OPS_PASSWORD}" | chpasswd

# 给巡检 / 演示作业一点可看的环境
RUN apt-get update \
    && apt-get install -y --no-install-recommends \
        iproute2 coreutils htop tmux curl \
    && rm -rf /var/lib/apt/lists/*

COPY entrypoint.sh /entrypoint.sh
RUN chmod +x /entrypoint.sh

EXPOSE 22

# 健康检查：SSH 端口可达且返回协议头
HEALTHCHECK --interval=10s --timeout=4s --retries=5 \
    CMD bash -c 'exec 3<>/dev/tcp/127.0.0.1/22 && head -c 3 <&3 | grep -q SSH'

ENTRYPOINT ["/entrypoint.sh"]
