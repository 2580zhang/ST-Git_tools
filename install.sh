#!/bin/bash
# SS-Proxy-Suite 一键安装脚本
# 功能：Shadowsocks + MTProto(TG) + Web管理面板 + GitHub加速下载

set -e

RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
BLUE='\033[0;34m'
CYAN='\033[0;36m'
NC='\033[0m'

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
INSTALL_DIR="/opt/ss-proxy-suite"
WEB_PORT=9090
SS_PORT=18388
TG_PORT=8443

LOG_FILE="/var/log/ss-proxy-install.log"
mkdir -p /var/log
exec > >(tee -a "$LOG_FILE") 2>&1

SERVER_IP=$(curl -s 4.ipw.cn 2>/dev/null || curl -s ipinfo.io/ip 2>/dev/null || echo "127.0.0.1")

echo -e "${BLUE}╔══════════════════════════════════════════════════╗${NC}"
echo -e "${BLUE}║     SS-Proxy-Suite 一键安装脚本                   ║${NC}"
echo -e "${BLUE}║     Shadowsocks + TG代理 + Web管理 + GitHub加速   ║${NC}"
echo -e "${BLUE}╚══════════════════════════════════════════════════╝${NC}"
echo ""
echo "[$(date '+%Y-%m-%d %H:%M:%S')] 开始安装..."

if [ "$EUID" -ne 0 ]; then 
    echo -e "${RED}请使用 root 权限运行此脚本${NC}"
    exit 1
fi

echo -e "${YELLOW}[1/8] 更新系统并安装依赖...${NC}"
echo "[$(date '+%Y-%m-%d %H:%M:%S')] 更新系统..."
apt-get update -qq
echo "[$(date '+%Y-%m-%d %H:%M:%S')] 安装依赖..."
apt-get install -y -qq git python3 python3-pip python3-venv shadowsocks-libev iptables curl wget build-essential libssl-dev zlib1g-dev jq net-tools
echo -e "${GREEN}✓ 基础依赖安装完成${NC}"

echo ""
echo -e "${YELLOW}[2/8] 安装 MTProto Proxy (TG代理)...${NC}"
if [ ! -f "/usr/local/bin/mtproto-proxy" ]; then
    echo "[$(date '+%Y-%m-%d %H:%M:%S')] 克隆 MTProxy 源码..."
    cd /tmp
    rm -rf MTProxy
    git clone https://github.com/TelegramMessenger/MTProxy.git
    cd MTProxy
    
    echo "[$(date '+%Y-%m-%d %H:%M:%S')] 编译 MTProxy..."
    make -j$(nproc)
    
    if [ ! -f "objs/bin/mtproto-proxy" ]; then
        echo -e "${RED}✗ MTProto Proxy 编译失败${NC}"
        exit 1
    fi
    
    cp objs/bin/mtproto-proxy /usr/local/bin/
    chmod +x /usr/local/bin/mtproto-proxy
    
    cd /tmp && rm -rf MTProxy
    echo -e "${GREEN}✓ MTProto Proxy 安装完成${NC}"
else
    echo -e "${GREEN}✓ MTProto Proxy 已安装${NC}"
fi

echo ""
echo -e "${YELLOW}[3/8] 部署项目文件...${NC}"
echo "[$(date '+%Y-%m-%d %H:%M:%S')] 创建目录结构..."
mkdir -p "$INSTALL_DIR"/{web-manager,bin,mtproto,data,logs}
mkdir -p /etc/mtproto-proxy

echo "[$(date '+%Y-%m-%d %H:%M:%S')] 复制文件..."
cp "$SCRIPT_DIR"/web-manager/app.py "$INSTALL_DIR/web-manager/"
cp "$SCRIPT_DIR"/bin/ss-manager-cli "$INSTALL_DIR/bin/"

if [ -d "$SCRIPT_DIR"/mtproto ]; then
    cp "$SCRIPT_DIR"/mtproto/* "$INSTALL_DIR/mtproto/" 2>/dev/null || true
fi

python3 -m venv "$INSTALL_DIR/venv"
source "$INSTALL_DIR/venv/bin/activate"
echo "[$(date '+%Y-%m-%d %H:%M:%S')] 安装 Python 依赖..."
pip install -q flask waitress markdown requests
deactivate

chmod +x "$INSTALL_DIR/bin/ss-manager-cli"
ln -sf "$INSTALL_DIR/bin/ss-manager-cli" /usr/local/bin/ss-manager
echo -e "${GREEN}✓ 项目文件部署完成${NC}"

echo ""
echo -e "${YELLOW}[4/8] 配置 Shadowsocks...${NC}"
echo "[$(date '+%Y-%m-%d %H:%M:%S')] 生成 SS 配置..."
SS_PASSWORD=$(openssl rand -base64 12 | tr -dc 'a-zA-Z0-9' | head -c 16)
mkdir -p /root
cat > /root/ss-config.json <<EOF
{
    "server":"0.0.0.0",
    "server_port":$SS_PORT,
    "password":"$SS_PASSWORD",
    "method":"aes-256-gcm",
    "timeout":300
}
EOF
cat > /etc/shadowsocks-libev/config.json <<EOF
{
    "server":"0.0.0.0",
    "server_port":$SS_PORT,
    "password":"$SS_PASSWORD",
    "method":"aes-256-gcm",
    "timeout":300
}
EOF
echo -e "${GREEN}✓ Shadowsocks 配置完成${NC}"

echo ""
echo -e "${YELLOW}[5/8] 配置 MTProto Proxy...${NC}"
echo "[$(date '+%Y-%m-%d %H:%M:%S')] 生成 TG 配置..."
TG_SECRET=$(openssl rand -hex 16)

mkdir -p /etc/mtproto-proxy
mkdir -p /root/mtproto

echo "$TG_SECRET" > /etc/mtproto-proxy/proxy-secret
echo "$TG_PORT" > /etc/mtproto-proxy/tg-port.conf
echo "$TG_SECRET" > /etc/mtproto-proxy/tg-secret.conf

echo "$TG_SECRET" > /root/mtproto/proxy-secret
echo "$TG_PORT" > /root/mtproto/tg-port.conf
echo "$TG_SECRET" > /root/mtproto/tg-secret.conf

cat > /etc/mtproto-proxy/proxy-multi.conf <<'EOF'
# force_probability 10 10
default 2;
proxy_for 1 149.154.175.50:8888;
proxy_for -1 149.154.175.50:8888;
proxy_for 2 149.154.161.144:8888;
proxy_for -2 149.154.161.184:8888;
proxy_for 203 91.105.192.110:443;
proxy_for -203 91.105.192.110:443;
proxy_for 3 149.154.175.100:8888;
proxy_for -3 149.154.175.100:8888;
proxy_for 4 91.108.4.217:8888;
proxy_for 4 91.108.4.177:8888;
proxy_for 4 91.108.4.133:8888;
proxy_for 4 91.108.4.166:8888;
proxy_for 4 91.108.4.223:8888;
proxy_for 4 91.108.4.203:8888;
proxy_for 4 91.108.4.218:8888;
proxy_for 4 91.108.4.215:8888;
proxy_for 4 91.108.4.209:8888;
proxy_for 4 91.108.4.183:8888;
proxy_for -4 149.154.165.250:8888;
proxy_for -4 149.154.165.109:8888;
proxy_for 5 91.108.56.152:8888;
proxy_for 5 91.108.56.103:8888;
proxy_for -5 91.108.56.152:8888;
proxy_for -5 91.108.56.103:8888;
EOF

cp /etc/mtproto-proxy/proxy-multi.conf /root/mtproto/proxy-multi.conf
echo -e "${GREEN}✓ MTProto Proxy 配置完成${NC}"

echo ""
echo -e "${YELLOW}[6/8] 配置 Web 管理面板...${NC}"
echo "[$(date '+%Y-%m-%d %H:%M:%S')] 生成 Web 配置..."
cat > "$INSTALL_DIR/web-manager/config.env" <<EOF
GITHUB_TOKEN=
WEB_PORT=$WEB_PORT
SS_PORT=$SS_PORT
TG_PORT=$TG_PORT
SERVER_IP=$SERVER_IP
SS_CLI_PATH=$INSTALL_DIR/bin/ss-manager-cli
EOF
echo -e "${GREEN}✓ Web 面板配置完成${NC}"

echo ""
echo -e "${YELLOW}[7/8] 安装 systemd 服务...${NC}"
echo "[$(date '+%Y-%m-%d %H:%M:%S')] 创建 systemd 服务..."

cat > /etc/systemd/system/shadowsocks.service <<'EOF'
[Unit]
Description=Shadowsocks-libev Server
After=network.target

[Service]
Type=simple
ExecStart=/usr/bin/ss-server -c /etc/shadowsocks-libev/config.json
Restart=always
RestartSec=5
StandardOutput=journal
StandardError=journal

[Install]
WantedBy=multi-user.target
EOF

cat > /etc/systemd/system/mtproto-proxy.service <<EOF
[Unit]
Description=MTProto Proxy Server
After=network.target

[Service]
Type=simple
ExecStart=/usr/local/bin/mtproto-proxy -u nobody -p 8888 -H $TG_PORT -S $TG_SECRET --aes-pwd /etc/mtproto-proxy/proxy-secret --nat-info $SERVER_IP:$SERVER_IP --http-stats /etc/mtproto-proxy/proxy-multi.conf -M 1
Restart=always
RestartSec=5
WorkingDirectory=/etc/mtproto-proxy
StandardOutput=journal
StandardError=journal

[Install]
WantedBy=multi-user.target
EOF

cat > /etc/systemd/system/ss-web-manager.service <<EOF
[Unit]
Description=SS Proxy Web Manager
After=network.target

[Service]
Type=simple
EnvironmentFile=$INSTALL_DIR/web-manager/config.env
ExecStart=$INSTALL_DIR/venv/bin/python $INSTALL_DIR/web-manager/app.py
Restart=always
RestartSec=5
WorkingDirectory=$INSTALL_DIR/web-manager
StandardOutput=journal
StandardError=journal

[Install]
WantedBy=multi-user.target
EOF

systemctl daemon-reload
systemctl enable shadowsocks mtproto-proxy ss-web-manager
echo -e "${GREEN}✓ systemd 服务安装完成${NC}"

echo ""
echo -e "${YELLOW}[8/8] 配置防火墙和 IP 转发...${NC}"
echo "[$(date '+%Y-%m-%d %H:%M:%S')] 配置网络..."
echo "net.ipv4.ip_forward=1" >> /etc/sysctl.conf
sysctl -p >/dev/null 2>&1

iface_list=$(ls /sys/class/net/ | grep -v '^lo$')
for iface in $iface_list; do
    iptables -t nat -A POSTROUTING -o "$iface" -j MASQUERADE 2>/dev/null || true
done

iptables -A INPUT -p tcp --dport $SS_PORT -j ACCEPT 2>/dev/null || true
iptables -A INPUT -p tcp --dport $TG_PORT -j ACCEPT 2>/dev/null || true
iptables -A INPUT -p tcp --dport $WEB_PORT -j ACCEPT 2>/dev/null || true

mkdir -p /etc/iptables
iptables-save > /etc/iptables/rules.v4 2>/dev/null || true
echo -e "${GREEN}✓ 网络配置完成${NC}"

echo ""
echo -e "${GREEN}══════════════════════════════════════════════════${NC}"
echo -e "${GREEN}           安装完成！服务信息如下               ${NC}"
echo -e "${GREEN}══════════════════════════════════════════════════${NC}"
echo ""
echo -e "${CYAN}🌐 Web 管理面板:${NC}  http://$SERVER_IP:$WEB_PORT"
echo ""
echo -e "${CYAN}🔒 Shadowsocks 节点:${NC}"
echo -e "    地址: $SERVER_IP"
echo -e "    端口: $SS_PORT"
echo -e "    密码: $SS_PASSWORD"
echo -e "    加密: aes-256-gcm"
echo ""
echo -e "${CYAN}✈️  TG 代理 (MTProto):${NC}"
echo -e "    地址: $SERVER_IP"
echo -e "    端口: $TG_PORT"
echo -e "    密钥: $TG_SECRET"
echo -e "    链接: tg://proxy?server=$SERVER_IP&port=$TG_PORT&secret=$TG_SECRET"
echo ""
echo -e "${CYAN}📦 GitHub 加速器:${NC}"
echo -e "    入口: http://$SERVER_IP:$WEB_PORT/gh"
echo -e "    下载加速: http://$SERVER_IP:$WEB_PORT/dl?url=下载链接"
echo ""
echo -e "${YELLOW}管理命令:${NC}"
echo -e "    ss-manager          # CLI 管理界面"
echo -e "    systemctl status shadowsocks mtproto-proxy ss-web-manager"
echo -e "    journalctl -u shadowsocks -f      # SS 日志"
echo -e "    journalctl -u mtproto-proxy -f    # TG 日志"
echo -e "    journalctl -u ss-web-manager -f   # Web 日志"
echo -e "    cat /var/log/ss-proxy-install.log # 安装日志"
echo ""
echo -e "${YELLOW}提示: 配置 GitHub Token 可提升 API 限额到 5000次/小时${NC}"
echo -e "      编辑 $INSTALL_DIR/web-manager/config.env 添加 GITHUB_TOKEN"
echo ""

echo -e "${GREEN}启动服务中...${NC}"
echo "[$(date '+%Y-%m-%d %H:%M:%S')] 启动所有服务..."
systemctl start shadowsocks mtproto-proxy ss-web-manager
sleep 3

echo "[$(date '+%Y-%m-%d %H:%M:%S')] 检查服务状态..."
echo ""
echo -e "${YELLOW}服务状态检查:${NC}"
for svc in shadowsocks mtproto-proxy ss-web-manager; do
    if systemctl is-active --quiet "$svc"; then
        echo -e "  ${GREEN}✓ $svc 运行中${NC}"
    else
        echo -e "  ${RED}✗ $svc 未运行${NC}"
        echo -e "    日志: journalctl -u $svc --no-pager -n 20"
    fi
done

echo ""
echo "[$(date '+%Y-%m-%d %H:%M:%S')] 安装完成"