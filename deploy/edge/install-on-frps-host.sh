#!/bin/bash
# 在 frps 云主机上以 sudo 运行一次：创建只读采集账号 mcops-ro。
#   sudo bash install-on-frps-host.sh '<mc-server 上 ~/.config/mcops/edge_ed25519.pub 的内容>'
# 撤销：sudo userdel -r mcops-ro && sudo rm /usr/local/bin/mcops-frps-feed
set -euo pipefail
[ "$(id -u)" = 0 ] || { echo "请用 sudo 运行"; exit 1; }
PUB="${1:?需要传入 mc-server 采集公钥}"
case "$PUB" in ssh-ed25519\ *) ;; *) echo "只接受 ssh-ed25519 公钥"; exit 1;; esac

install -m 755 -o root -g root "$(dirname "$0")/mcops-frps-feed" /usr/local/bin/mcops-frps-feed
id mcops-ro >/dev/null 2>&1 || useradd --system --create-home --shell /bin/sh mcops-ro
usermod -aG systemd-journal mcops-ro          # 只读访问 journald
HOME_DIR="$(getent passwd mcops-ro | cut -d: -f6)"
install -d -m 700 -o mcops-ro -g mcops-ro "$HOME_DIR/.ssh"
printf 'restrict,command="/usr/local/bin/mcops-frps-feed" %s\n' "$PUB" > "$HOME_DIR/.ssh/authorized_keys"
chown mcops-ro:mcops-ro "$HOME_DIR/.ssh/authorized_keys"
chmod 600 "$HOME_DIR/.ssh/authorized_keys"
echo "完成：mcops-ro 只能执行 mcops-frps-feed（只读 frps 日志），不能登录 shell、不能端口转发。"
