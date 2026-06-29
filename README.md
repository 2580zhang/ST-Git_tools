# ST-Git_tools

一站式代理服务器解决方案，集成 Shadowsocks、TG 代理、Web 管理面板和 GitHub 加速下载。

## 功能特性

- 🔒 **Shadowsocks 代理** - 支持 AES-256-GCM 加密
- ✈️ **TG 代理 (MTProto)** - Telegram 官方代理协议
- 🌐 **Web 管理面板** - 可视化管理所有服务
- 📦 **GitHub 加速器** - 仓库浏览、文件下载、Release 下载
- 🚀 **下载加速** - 任意 HTTP/HTTPS 链接代理加速
- 🔍 **仓库搜索** - 支持 GitHub 全局搜索和组织仓库列表
- 📊 **流量监控** - 实时查看服务器网络流量
- 📝 **日志管理** - 查看服务启动日志和运行日志

## 快速部署

### 一键安装

```bash
git clone https://github.com/2580zhang/ST-Git_tools.git
cd ST-Git_tools
chmod +x install.sh
sudo ./install.sh
```

安装过程会自动完成：
- 安装系统依赖
- 编译 MTProto Proxy
- 创建配置文件
- 配置 systemd 服务
- 配置防火墙和 IP 转发

安装日志保存在 `/var/log/ss-proxy-install.log`

### 手动安装

```bash
# 1. 安装依赖
apt-get install -y shadowsocks-libev python3 python3-pip python3-venv jq net-tools

# 2. 部署项目
mkdir -p /opt/ss-proxy-suite/{web-manager,bin,mtproto,data}
cp web-manager/app.py /opt/ss-proxy-suite/web-manager/
cp bin/ss-manager-cli /opt/ss-proxy-suite/bin/
chmod +x /opt/ss-proxy-suite/bin/ss-manager-cli
ln -sf /opt/ss-proxy-suite/bin/ss-manager-cli /usr/local/bin/ss-manager

# 3. 创建虚拟环境并安装依赖
python3 -m venv /opt/ss-proxy-suite/venv
source /opt/ss-proxy-suite/venv/bin/activate
pip install flask waitress markdown requests
deactivate

# 4. 配置
cp web-manager/config.env.example /opt/ss-proxy-suite/web-manager/config.env
# 编辑 config.env 配置端口和 Token

# 5. 启动
systemctl start shadowsocks mtproto-proxy ss-web-manager
```

## 使用说明

### Web 管理面板

访问 `http://服务器IP:9090`

- 仪表盘 - 查看服务状态、流量监控
- SS 代理配置 - 修改端口、密码、加密方式
- TG 代理配置 - 修改密钥和端口
- GitHub 加速器 - 浏览仓库、下载文件
- 日志查看 - 点击各服务卡片的「日志」按钮

### GitHub 加速器

**浏览仓库：**
```
http://服务器IP:9090/gh?url=https://github.com/owner/repo
```

**查看组织/用户仓库列表：**
```
http://服务器IP:9090/gh?url=https://github.com/owner
```

**下载加速：**
```
http://服务器IP:9090/dl?url=下载链接
```

**下载整个仓库 ZIP：**
```
http://服务器IP:9090/gh/api/zip?url=仓库链接
```

### 命令行管理

```bash
ss-manager          # 启动 CLI 管理界面
```

## API 接口

| 接口 | 说明 |
|------|------|
| `/api/status` | 服务状态（含流量监控） |
| `/api/ss/restart` | 重启 SS 服务 |
| `/api/tg/restart` | 重启 TG 服务 |
| `/api/logs/ss` | 获取 SS 服务日志 |
| `/api/logs/tg` | 获取 TG 服务日志 |
| `/api/logs/web` | 获取 Web 面板日志 |
| `/gh/api/tree?url=...` | 获取目录树 |
| `/gh/api/file?url=...&path=...` | 查看文件内容 |
| `/gh/api/download?url=...&path=...` | 下载文件 |
| `/gh/api/download-dir?url=...&path=...` | 下载文件夹 |
| `/gh/api/zip?url=...` | 下载仓库 ZIP |
| `/gh/api/repos?owner=...` | 获取仓库列表 |
| `/gh/api/search?q=...` | 搜索仓库 |

## 配置 GitHub Token

配置后 API 限额从 60次/小时 提升到 5000次/小时：

1. 访问 https://github.com/settings/tokens
2. 生成 Personal Access Token (classic)
3. 勾选 `public_repo` 权限
4. 编辑 `/opt/ss-proxy-suite/web-manager/config.env`
5. 设置 `GITHUB_TOKEN=你的token`
6. 重启服务：`systemctl restart ss-web-manager`

## 服务管理

```bash
# 查看状态
systemctl status shadowsocks mtproto-proxy ss-web-manager

# 启动/停止/重启
systemctl start shadowsocks
systemctl stop mtproto-proxy
systemctl restart ss-web-manager

# 开机自启
systemctl enable shadowsocks mtproto-proxy ss-web-manager

# 查看实时日志
journalctl -u shadowsocks -f      # SS 服务日志
journalctl -u mtproto-proxy -f    # TG 代理日志
journalctl -u ss-web-manager -f   # Web 面板日志

# 查看安装日志
cat /var/log/ss-proxy-install.log
```

## 目录结构

```
ss-proxy-suite/
├── install.sh              # 一键安装脚本
├── README.md               # 说明文档
├── web-manager/            # Web 管理面板
│   ├── app.py              # 主程序
│   ├── requirements.txt    # Python 依赖
│   └── config.env.example  # 配置示例
├── bin/
│   └── ss-manager-cli      # CLI 管理脚本
└── mtproto/                # TG 代理配置示例
    ├── proxy-multi.conf
    ├── tg-port.conf
    └── tg-secret.conf
```

## 系统要求

- Ubuntu 20.04 / Debian 11+
- Python 3.8+
- 至少 512MB 内存
- 公网 IP

## 安全提示

- 部署后请修改默认密码
- 建议使用防火墙限制管理面板访问
- 定期更新系统和依赖

## License

MIT
