#!/usr/bin/env python3
"""Shadowrocket 节点 & TG 代理 & 下载加速 Web 管理器"""

import subprocess, json, os, io, base64, re, time, hashlib, tempfile, shutil, fnmatch
from urllib.parse import urlparse, quote, unquote
from flask import Flask, jsonify, request, render_template_string, Response, stream_with_context, send_file
import markdown as md_lib

app = Flask(__name__)
CLI = "/root/bin/ss-manager-cli"

# GitHub Token 认证（提高 API 限制从 60/小时 到 5000/小时）
# 可在 https://github.com/settings/tokens 创建 Personal Access Token
GITHUB_TOKEN = os.environ.get("GITHUB_TOKEN", "")  # 从环境变量读取

def gh_headers():
    """返回 GitHub API 请求头"""
    headers = {
        "User-Agent": "Mozilla/5.0",
        "Accept": "application/vnd.github.v3+json"
    }
    if GITHUB_TOKEN:
        headers["Authorization"] = f"Bearer {GITHUB_TOKEN}"
    return headers

# 下载加速缓存目录
DL_CACHE_DIR = "/tmp/dl-cache"
os.makedirs(DL_CACHE_DIR, exist_ok=True)

# GitHub 仓库加速（通过API访问，不缓存）
GH_REPOS_DIR = "/tmp/gh-repos"

# ── 工具函数 ──────────────────────────────────────────────

def run_cli(*args, timeout=10):
    try:
        r = subprocess.run([CLI] + list(args), capture_output=True, text=True, timeout=timeout)
        return r.stdout.strip(), r.stderr.strip()
    except Exception as e:
        return "", str(e)

def strip_ansi(text):
    return re.sub(r'\x1b\[[0-9;]*m', '', text)

def parse_ss_status(out):
    out = strip_ansi(out)
    info = {"server": "未知", "port": "未知", "cipher": "未知", "pid": "未知",
            "uptime": "未知", "mem": "未知", "active_conn": "0", "password": "未知"}
    for line in out.split("\n"):
        line = line.strip()
        if "服务器地址:" in line: info["server"] = line.split(":")[-1].strip()
        elif "监听端口:" in line: info["port"] = line.split(":")[-1].strip()
        elif "加密方式:" in line: info["cipher"] = line.split(":")[-1].strip()
        elif "连接密码:" in line: info["password"] = line.split(":")[-1].strip()
        elif "进程 PID:" in line: info["pid"] = line.split(":")[-1].strip()
        elif "运行时间:" in line: info["uptime"] = line.split(":")[-1].strip()
        elif "内存占用:" in line: info["mem"] = line.split(":")[-1].strip()
        elif "活跃连接:" in line: info["active_conn"] = line.split(":")[-1].strip().split()[0]
    return info

def parse_tg_status(out):
    out = strip_ansi(out)
    info = {"server": "未知", "port": "未知", "secret": "未知", "pid": "未知",
            "uptime": "未知", "running": False, "link": ""}
    for line in out.split("\n"):
        line = line.strip()
        if line.startswith("服务器:"):
            v = line.split(":", 1)[-1].strip()
            if v and v != "未知": info["server"] = v
        elif "端口:" in line and "监听" not in line:
            info["port"] = line.split(":")[-1].strip()
        elif "密钥:" in line:
            info["secret"] = line.split(":")[-1].strip()
        elif "运行中" in line:
            info["running"] = True
            # 提取 PID
            m = re.search(r'PID:\s*(\d+)', line)
            if m: info["pid"] = m.group(1)
        elif "运行时间:" in line:
            info["uptime"] = line.split(":")[-1].strip()
    if info["server"] != "未知" and info["port"] != "未知":
        info["link"] = f"https://t.me/proxy?server={info['server']}&port={info['port']}&secret={info['secret']}"
    return info

def parse_tg_stats(out):
    # 只提取我们关心的统计字段
    wanted = {"inbound_connections_accepted", "active_outbound_connections",
              "total_encrypted_connections", "tot_forwarded_queries",
              "ready_targets", "total_connections", "total_ready_targets",
              "outbound_connections", "inbound_connections",
              "active_inbound_connections"}
    stats = {}
    for line in out.split("\n"):
        line = line.strip()
        if not line: continue
        parts = line.split()
        if len(parts) >= 2 and parts[0] in wanted:
            stats[parts[0]] = parts[1]
    # 映射别名
    if "total_ready_targets" in stats:
        stats["ready_targets"] = stats["total_ready_targets"]
    return stats

def get_ss_config():
    try:
        with open("/root/ss-config.json") as f:
            return json.load(f)
    except:
        return {}

def save_ss_config(cfg):
    with open("/root/ss-config.json", "w") as f:
        json.dump(cfg, f, indent=2, ensure_ascii=False)

# ── API 路由 ───────────────────────────────────────────────

@app.route("/api/status")
def api_status():
    ss_out, _ = run_cli("status")
    tg_out, _ = run_cli("tg-status")
    ss = parse_ss_status(ss_out)
    tg = parse_tg_status(tg_out)

    # TG stats
    try:
        import requests
        r = requests.get("http://127.0.0.1:8888/stats", timeout=3)
        tg_stats = parse_tg_stats(r.text)
    except:
        tg_stats = {}

    return jsonify({
        "ss": ss,
        "tg": tg,
        "tg_stats": tg_stats,
        "server_ip": ss.get("server", "未知")
    })

@app.route("/api/ss/<action>", methods=["POST"])
def api_ss_action(action):
    actions = {"start": "start", "stop": "stop", "restart": "restart"}
    if action not in actions:
        return jsonify({"ok": False, "msg": "无效操作"}), 400
    out, err = run_cli(actions[action])
    return jsonify({"ok": True, "msg": out or err})

@app.route("/api/ss/config", methods=["GET", "POST"])
def api_ss_config():
    if request.method == "GET":
        return jsonify(get_ss_config())
    data = request.get_json()
    save_ss_config(data)
    # Restart to apply
    run_cli("restart")
    return jsonify({"ok": True, "msg": "配置已保存并重启"})

@app.route("/api/ss/qr")
def api_ss_qr():
    cfg = get_ss_config()
    server = cfg.get("server", "154.219.121.231")
    port = cfg.get("server_port", 18388)
    password = cfg.get("password", "")
    method = cfg.get("method", "chacha20-ietf-poly1305")
    # SS URI: ss://base64(method:password)@server:port
    b64 = base64.urlsafe_b64encode(f"{method}:{password}".encode()).decode().rstrip("=")
    uri = f"ss://{b64}@{server}:{port}"

    try:
        import qrcode
        img = qrcode.make(uri)
        buf = io.BytesIO()
        img.save(buf, format="PNG")
        return jsonify({"uri": uri, "qr": base64.b64encode(buf.getvalue()).decode()})
    except:
        return jsonify({"uri": uri, "qr": ""})

@app.route("/api/tg/<action>", methods=["POST"])
def api_tg_action(action):
    actions = {"start": "tg-start", "stop": "tg-stop", "restart": "tg-restart"}
    if action not in actions:
        return jsonify({"ok": False, "msg": "无效操作"}), 400
    out, err = run_cli(actions[action])
    return jsonify({"ok": True, "msg": out or err})

@app.route("/api/tg/fix", methods=["POST"])
def api_tg_fix():
    out, err = run_cli("tg-fix", timeout=20)
    return jsonify({"ok": True, "msg": out or err})

@app.route("/api/tg/qr")
def api_tg_qr():
    out, _ = run_cli("tg-status")
    tg = parse_tg_status(out)
    link = tg.get("link", "")
    try:
        import qrcode
        img = qrcode.make(link)
        buf = io.BytesIO()
        img.save(buf, format="PNG")
        return jsonify({"link": link, "qr": base64.b64encode(buf.getvalue()).decode()})
    except:
        return jsonify({"link": link, "qr": ""})

@app.route("/api/ss/fix", methods=["POST"])
def api_ss_fix():
    out, err = run_cli("fix", timeout=15)
    return jsonify({"ok": True, "msg": out or err})

@app.route("/api/ss/logs")
def api_ss_logs():
    out, _ = run_cli("log", "50")
    return jsonify({"logs": out})

@app.route("/api/tg/logs")
def api_tg_logs():
    try:
        with open("/tmp/mtproto-proxy.log") as f:
            lines = f.readlines()[-50:]
        return jsonify({"logs": "".join(lines)})
    except:
        return jsonify({"logs": "无日志"})

# ── 下载加速代理 ───────────────────────────────────────────

# GitHub 域名列表，这些源站添加特殊 headers 加速
GITHUB_HOSTS = {
    "github.com", "raw.githubusercontent.com", "api.github.com",
    "objects.githubusercontent.com", "github-releases.githubusercontent.com",
    "codeload.github.com", "gist.githubusercontent.com"
}

def is_github_url(url):
    try:
        host = urlparse(url).hostname or ""
        return any(host == h or host.endswith("." + h) for h in GITHUB_HOSTS)
    except:
        return False

def get_download_headers(url):
    """根据源站类型返回优化的请求头"""
    headers = {
        "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/125.0.0.0 Safari/537.36",
        "Accept": "*/*",
        "Accept-Encoding": "gzip, deflate, br",
        "Connection": "keep-alive",
    }
    if is_github_url(url):
        headers["Accept"] = "application/octet-stream"
    return headers

def safe_filename(url):
    """从 URL 提取安全的文件名"""
    try:
        path = urlparse(url).path
        name = os.path.basename(path)
        if name:
            return quote(name)
    except:
        pass
    return "download"

# ── 下载加速 API ───────────────────────────────────────────

@app.route("/dl")
def dl_proxy():
    """下载加速代理: /dl?url=ENCODED_URL"""
    url = request.args.get("url", "")
    if not url:
        return render_template_string(DL_PAGE)
    return stream_download(url)

@app.route("/dl/<path:encoded>")
def dl_path(encoded):
    """下载加速代理: /dl/ENCODED_URL (支持路径方式)"""
    url = unquote(encoded)
    # 处理 http:/ 被合并为 http:/ 的情况
    if not url.startswith("http"):
        url = "https://" + url.lstrip("/")
    return stream_download(url)

def stream_download(url):
    """核心：流式下载代理，支持断点续传"""
    import requests as req

    # 验证 URL
    try:
        parsed = urlparse(url)
        if parsed.scheme not in ("http", "https"):
            return jsonify({"ok": False, "msg": "仅支持 http/https 链接"}), 400
    except:
        return jsonify({"ok": False, "msg": "无效的 URL"}), 400

    filename = safe_filename(url)
    client_range = request.headers.get("Range")

    # 先获取文件大小和 Content-Type（HEAD 请求）
    content_length = None
    content_type = "application/octet-stream"
    try:
        head_resp = req.head(url, headers=get_download_headers(url), timeout=30, allow_redirects=True)
        if head_resp.status_code == 200:
            cl = head_resp.headers.get("Content-Length")
            if cl:
                content_length = int(cl)
            ct = head_resp.headers.get("Content-Type")
            if ct:
                content_type = ct
        head_resp.close()
    except Exception:
        pass

    # 解析 Range 请求
    range_start = 0
    range_end = None
    total_size = content_length

    if client_range and total_size:
        m = re.match(r"bytes=(\d*)-(\d*)", client_range)
        if m:
            range_start = int(m.group(1)) if m.group(1) else 0
            end_str = m.group(2)
            range_end = int(end_str) if end_str else total_size - 1
            content_length = range_end - range_start + 1
        else:
            client_range = None  # 无效的 Range，忽略

    def generate():
        try:
            headers = get_download_headers(url)
            if client_range:
                headers["Range"] = client_range

            resp = req.get(url, headers=headers, stream=True, timeout=60, allow_redirects=True)
            resp.raise_for_status()

            chunk_size = 512 * 1024
            for chunk in resp.iter_content(chunk_size=chunk_size):
                if chunk:
                    yield chunk
            resp.close()
        except Exception as e:
            print(f"[DL Error] {url}: {e}")

    resp_headers = {
        "Content-Type": content_type,
        "Content-Disposition": f'attachment; filename="{filename}"',
        "Accept-Ranges": "bytes",
        "X-Cache": "MISS",
        "Cache-Control": "public, max-age=3600",
    }

    if client_range and total_size:
        # 206 Partial Content 响应
        resp_headers["Content-Range"] = f"bytes {range_start}-{range_end}/{total_size}"
        resp_headers["Content-Length"] = str(content_length)
        return Response(
            stream_with_context(generate()),
            status=206,
            headers=resp_headers,
        )
    elif total_size:
        resp_headers["Content-Length"] = str(total_size)
        return Response(
            stream_with_context(generate()),
            status=200,
            headers=resp_headers,
        )
    else:
        # 未知大小，用 chunked transfer
        return Response(
            stream_with_context(generate()),
            status=200,
            headers=resp_headers,
        )

# ── 下载器加速页面 ─────────────────────────────────────────

DL_PAGE = r"""<!DOCTYPE html>
<html lang="zh-CN">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width, initial-scale=1.0">
<title>下载加速器</title>
<style>
*{margin:0;padding:0;box-sizing:border-box}
body{font-family:-apple-system,BlinkMacSystemFont,'Segoe UI',Roboto,sans-serif;background:#0f172a;color:#e2e8f0;min-height:100vh}
.header{background:#1e293b;padding:16px 24px;display:flex;align-items:center;justify-content:space-between;border-bottom:1px solid #334155}
.header h1{font-size:20px;color:#38bdf8}
.header a{color:#94a3b8;text-decoration:none;font-size:14px}
.header a:hover{color:#38bdf8}
.container{max-width:900px;margin:40px auto;padding:0 20px}
.card{background:#1e293b;border-radius:12px;border:1px solid #334155;padding:24px;margin-bottom:20px}
.card h2{font-size:18px;margin-bottom:16px;color:#38bdf8}
.card h3{font-size:14px;color:#94a3b8;margin-bottom:8px}
.input-group{display:flex;gap:8px;margin-bottom:12px}
.input-group input{flex:1;padding:12px 16px;background:#0f172a;border:1px solid #334155;border-radius:10px;color:#e2e8f0;font-size:14px;outline:none}
.input-group input:focus{border-color:#38bdf8}
.input-group button{padding:12px 24px;background:#3b82f6;border:none;border-radius:10px;color:#fff;font-size:14px;font-weight:600;cursor:pointer;white-space:nowrap}
.input-group button:hover{background:#2563eb}
.result-box{background:#0f172a;border:1px solid #334155;border-radius:10px;padding:12px 16px;font-family:monospace;font-size:13px;word-break:break-all;color:#38bdf8;margin-top:8px;user-select:all;display:none}
.result-box.show{display:block}
.copy-btn{background:#334155;border:none;color:#94a3b8;padding:4px 10px;border-radius:6px;cursor:pointer;font-size:12px;margin-left:8px}
.copy-btn:hover{background:#475569;color:#e2e8f0}
.tip{font-size:13px;color:#94a3b8;margin-top:8px;line-height:1.6}
.tip code{background:#334155;padding:2px 6px;border-radius:4px;font-size:12px;color:#38bdf8}
.examples{margin-top:12px}
.examples a{display:inline-block;color:#06b6d4;font-size:13px;margin:4px 8px 4px 0;cursor:pointer;text-decoration:underline}
.examples a:hover{color:#38bdf8}
.preview{display:none;margin-top:12px;padding:12px;background:#0f172a;border-radius:8px}
.preview.show{display:block}
.preview .info{font-size:13px;color:#94a3b8;margin-bottom:4px}
.progress{display:none;margin-top:12px}
.progress.show{display:block}
.progress-bar{height:6px;background:#334155;border-radius:3px;overflow:hidden}
.progress-bar div{height:100%;background:#38bdf8;border-radius:3px;transition:width .3s;width:0%}
.progress-text{font-size:12px;color:#94a3b8;margin-top:4px}
.toast{position:fixed;top:20px;right:20px;padding:12px 20px;border-radius:8px;font-size:14px;z-index:2000;animation:slideIn .3s}
.toast-success{background:#22c55e;color:#fff}
.toast-error{background:#ef4444;color:#fff}
@keyframes slideIn{from{transform:translateX(100px);opacity:0}to{transform:translateX(0);opacity:1}}
@media(max-width:600px){.input-group{flex-direction:column}}
</style>
</head>
<body>
<div class="header">
    <h1>⚡ 下载加速器</h1>
    <a href="/">← 返回管理面板</a>
</div>
<div class="container">
    <div class="card">
        <h2>粘贴下载链接，获取加速地址</h2>
        <form id="dlForm" onsubmit="return genLink(event)">
            <div class="input-group">
                <input type="text" id="urlInput" placeholder="粘贴原始下载链接，例如 https://github.com/xxx/releases/download/v1.0/file.zip" autofocus>
                <button type="submit">生成加速链接</button>
            </div>
        </form>
        <div class="result-box" id="resultBox">
            <span id="accelUrl"></span>
            <button class="copy-btn" onclick="copyLink()">复制</button>
        </div>
        <div class="preview" id="previewBox">
            <div class="info" id="previewInfo"></div>
            <button class="copy-btn" onclick="downloadNow()" style="background:#22c55e;color:#fff">直接下载</button>
        </div>
        <div class="progress" id="progressBox">
            <div class="progress-bar"><div id="progressFill"></div></div>
            <div class="progress-text" id="progressText"></div>
        </div>
        <div class="tip" style="margin-top:16px">
            <p>使用方法：将原下载链接中的域名替换为 <code>SERVER_IP:9090</code>，或直接拼接为：</p>
            <p style="margin-top:6px"><code>http://SERVER_IP:9090/dl?url=原始链接</code></p>
        </div>
        <div class="examples" style="margin-top:12px">
            <span style="font-size:13px;color:#94a3b8">快速测试：</span>
            <a onclick="testGH('raw')">GitHub Raw</a>
            <a onclick="testGH('release')">GitHub Release</a>
            <a onclick="testOther()">其他链接</a>
        </div>
    </div>
</div>

<div id="toast" class="toast"></div>

<script>
var currentAccelUrl = '';

function toast(msg, type) {
    var t = document.getElementById('toast');
    t.textContent = msg;
    t.className = 'toast toast-' + (type || 'success');
    setTimeout(function(){ t.className = 'toast'; }, 2500);
}

function genLink(e) {
    if (e) e.preventDefault();
    var url = document.getElementById('urlInput').value.trim();
    if (!url) { toast('请输入下载链接', 'error'); return false; }
    if (!/^https?:\/\//.test(url)) { toast('请输入完整链接 (http/https)', 'error'); return false; }

    currentAccelUrl = window.location.origin + '/dl?url=' + encodeURIComponent(url);
    document.getElementById('accelUrl').textContent = currentAccelUrl;
    document.getElementById('resultBox').classList.add('show');
    document.getElementById('previewBox').classList.add('show');
    document.getElementById('previewInfo').textContent = '原链接: ' + url;
    toast('加速链接已生成');
    return false;
}

function copyLink() {
    if (!currentAccelUrl) return;
    navigator.clipboard.writeText(currentAccelUrl).then(function() {
        toast('已复制到剪贴板');
    }).catch(function() {
        toast('复制失败，请手动选择', 'error');
    });
}

function downloadNow() {
    if (!currentAccelUrl) return;
    window.open(currentAccelUrl, '_blank');
}

function testGH(type) {
    var url = type === 'raw'
        ? 'https://raw.githubusercontent.com/telegramdesktop/tdesktop/dev/Telegram/Resources/art/logo.png'
        : 'https://github.com/shadowsocks/shadowsocks-libev/releases/download/v3.3.5/shadowsocks-libev-3.3.5.tar.gz';
    document.getElementById('urlInput').value = url;
    genLink();
}

function testOther() {
    document.getElementById('urlInput').value = 'https://speed.hetzner.de/100MB.bin';
    genLink();
}
</script>
</body>
</html>"""

# 替换 SERVER_IP 占位符
@app.route("/dl-page")
def dl_page():
    html = DL_PAGE.replace("SERVER_IP", "154.219.121.231")
    return html

# ── GitHub 仓库加速器 ──────────────────────────────────────

def parse_github_url(url):
    """解析 GitHub URL，返回 (owner, repo, branch, subpath)"""
    url = url.rstrip("/")
    # 去掉 .git 后缀
    if url.endswith(".git"):
        url = url[:-4]

    # 匹配 github.com/owner/repo 格式
    patterns = [
        r"github\.com/([^/]+)/([^/]+?)(?:/tree/([^/]+)(?:/(.*))?)?$",
        r"github\.com/([^/]+)/([^/]+?)(?:/blob/([^/]+)(?:/(.*))?)?$",
        r"github\.com/([^/]+)/([^/]+?)$",
    ]
    for p in patterns:
        m = re.search(p, url)
        if m:
            owner = m.group(1)
            repo = m.group(2)
            branch = m.group(3) or "main"
            subpath = m.group(4) or ""
            return owner, repo, branch, subpath
    return None, None, "main", ""

def parse_github_owner(url):
    """解析 GitHub 用户/组织 URL，返回 owner 或 None"""
    # 清理 URL
    url = url.rstrip("/")
    if url.endswith(".git"):
        url = url[:-4]

    # 匹配 github.com/owner (无 repo)
    m = re.search(r"github\.com/([^/]+)$", url)
    if m:
        return m.group(1)
    return None

def get_owner_repos(owner):
    """从 GitHub API 获取用户/组织的仓库列表"""
    import requests as req
    repos = []
    page = 1
    while page <= 5:  # 最多 5 页，每页 100 个
        try:
            api_url = f"https://api.github.com/users/{owner}/repos?per_page=100&page={page}&sort=updated"
            resp = req.get(api_url, headers=gh_headers(), timeout=15)
            if resp.status_code == 404:
                # 尝试 org API
                api_url = f"https://api.github.com/orgs/{owner}/repos?per_page=100&page={page}&sort=updated"
                resp = req.get(api_url, headers=gh_headers(), timeout=15)
            if resp.status_code != 200:
                break
            data = resp.json()
            if not data:
                break
            for r in data:
                repos.append({
                    "name": r["name"],
                    "full_name": r["full_name"],
                    "description": (r.get("description") or "")[:200],
                    "stars": r.get("stargazers_count", 0),
                    "forks": r.get("forks_count", 0),
                    "language": r.get("language") or "",
                    "updated_at": r.get("updated_at", ""),
                    "private": r.get("private", False),
                    "fork": r.get("fork", False),
                    "html_url": r["html_url"],
                    "default_branch": r.get("default_branch", "main"),
                })
            page += 1
        except Exception:
            break
    return repos

def search_github_repos(query, page=1, per_page=30):
    """搜索 GitHub 仓库"""
    import requests as req
    try:
        api_url = f"https://api.github.com/search/repositories?q={quote(query)}&page={page}&per_page={per_page}&sort=stars&order=desc"
        resp = req.get(api_url, headers=gh_headers(), timeout=15)
        if resp.status_code != 200:
            return {"total": 0, "items": []}
        data = resp.json()
        items = []
        for r in data.get("items", []):
            items.append({
                "name": r["name"],
                "full_name": r["full_name"],
                "description": (r.get("description") or "")[:200],
                "stars": r.get("stargazers_count", 0),
                "forks": r.get("forks_count", 0),
                "language": r.get("language") or "",
                "updated_at": r.get("updated_at", ""),
                "html_url": r["html_url"],
                "owner": r["owner"]["login"],
                "topics": r.get("topics", [])[:5],
                "license": r.get("license", {}).get("spdx_id", "") if r.get("license") else "",
            })
        return {"total": min(data.get("total_count", 0), 1000), "items": items}
    except Exception:
        return {"total": 0, "items": []}

def get_repo_dir(owner, repo):
    """获取仓库本地目录"""
    return os.path.join(GH_REPOS_DIR, f"{owner}_{repo}")

def clone_or_pull(owner, repo, url):
    """克隆或更新仓库 - 已废弃，改为通过API访问"""
    return get_repo_dir(owner, repo)

def get_default_branch(repo_dir):
    """获取默认分支名"""
    return "main"

def build_file_tree(repo_dir, subpath=""):
    """通过 GitHub API 构建文件树"""
    import requests as req
    owner, repo = os.path.basename(repo_dir).split("_", 1)
    
    try:
        api_url = f"https://api.github.com/repos/{owner}/{repo}/contents/{subpath}"
        resp = req.get(api_url, headers=gh_headers(), timeout=15)
        if resp.status_code != 200:
            return []
        
        data = resp.json()
        items = []
        for item in data:
            items.append({
                "name": item["name"],
                "path": item["path"],
                "type": item["type"],
                "size": item.get("size", 0),
            })
        return items
    except Exception:
        return []

def get_readme(repo_dir):
    """通过 GitHub API 获取 README 内容"""
    import requests as req
    owner, repo = os.path.basename(repo_dir).split("_", 1)
    
    try:
        api_url = f"https://api.github.com/repos/{owner}/{repo}/readme"
        resp = req.get(api_url, headers=gh_headers(), timeout=15)
        if resp.status_code != 200:
            return None, None
        
        data = resp.json()
        content = base64.b64decode(data["content"]).decode("utf-8", errors="replace")
        return content[:50000], data["name"]
    except Exception:
        return None, None

def get_releases(owner, repo):
    """从 GitHub API 获取 releases 列表"""
    import requests as req
    try:
        api_url = f"https://api.github.com/repos/{owner}/{repo}/releases?per_page=20"
        resp = req.get(api_url, headers=gh_headers(), timeout=15)
        if resp.status_code != 200:
            return []
        data = resp.json()
        releases = []
        for r in data:
            # 找到 zip 和 tar.gz 下载链接
            assets = []
            for a in r.get("assets", []):
                assets.append({
                    "name": a["name"],
                    "size": a["size"],
                    "download_url": a["browser_download_url"],
                    "download_count": a.get("download_count", 0),
                })
            releases.append({
                "tag_name": r["tag_name"],
                "name": r.get("name") or r["tag_name"],
                "body": r.get("body", "")[:2000],
                "published_at": r.get("published_at", ""),
                "prerelease": r.get("prerelease", False),
                "zipball_url": r.get("zipball_url", ""),
                "tarball_url": r.get("tarball_url", ""),
                "assets": assets,
            })
        return releases
    except Exception:
        return []

def format_size(size):
    """格式化文件大小"""
    try:
        size = int(size)
    except:
        return str(size)
    for unit in ["B", "KB", "MB", "GB"]:
        if size < 1024:
            return f"{size:.1f} {unit}" if unit != "B" else f"{size} B"
        size /= 1024
    return f"{size:.1f} TB"

def get_releases_html(releases, owner, repo):
    """生成 releases 列表 HTML"""
    if not releases:
        return ""
    html = '<div class="releases-section"><div class="releases-header">Releases</div>'
    for rel in releases:
        published = rel["published_at"]
        if published:
            try:
                dt = published[:10]
            except:
                dt = published
        else:
            dt = ""
        prerelease_badge = ' <span class="prerelease-badge">Pre-release</span>' if rel["prerelease"] else ""
        html += f'<div class="release-item">'
        html += f'<div class="release-title"><span class="release-tag">{rel["tag_name"]}</span> {rel["name"]}{prerelease_badge}</div>'
        html += f'<div class="release-meta">{dt}</div>'
        if rel["body"]:
            # 简单截断 body
            body = rel["body"][:500]
            html += f'<div class="release-body">{render_md(body)}</div>'
        # 资源文件
        if rel["assets"]:
            html += '<div class="release-assets">'
            for a in rel["assets"]:
                size_str = format_size(a["size"])
                html += f'<a class="asset-link" href="/dl?url={quote(a["download_url"], safe="")}" target="_blank">'
                html += f'<span class="asset-name">{a["name"]}</span>'
                html += f'<span class="asset-size">{size_str}</span>'
                html += f'</a>'
            html += '</div>'
        # Source code 下载
        html += '<div class="release-assets">'
        html += f'<a class="asset-link" href="/dl?url={quote(rel["zipball_url"], safe="")}" target="_blank"><span class="asset-name">Source code (zip)</span></a>'
        html += f'<a class="asset-link" href="/dl?url={quote(rel["tarball_url"], safe="")}" target="_blank"><span class="asset-name">Source code (tar.gz)</span></a>'
        html += '</div>'
        html += '</div>'
    html += '</div>'
    return html

def render_repo_list_html(repos, add_data_attr=False):
    """生成仓库列表 HTML"""
    html = ""
    for r in repos:
        desc = r["description"] or ""
        lang = r["language"]
        lang_html = f'<span class="repo-lang">{lang}</span>' if lang else ""
        stars = r["stars"]
        stars_html = f'<span class="repo-stat">⭐ {stars}</span>' if stars else ""
        forks = r["forks"]
        forks_html = f'<span class="repo-stat">🔀 {forks}</span>' if forks else ""
        updated = r["updated_at"]
        if updated:
            try:
                updated = updated[:10]
            except:
                pass
        fork_badge = ' <span class="fork-badge">Fork</span>' if r["fork"] else ""
        data_attr = f' data-search="{r["name"]} {desc} {lang} {r["stars"]}"' if add_data_attr else ""
        html += f'''<a class="repo-card" href="/gh?url={quote(r["html_url"], safe="")}"{data_attr}>
            <div class="repo-name">{r["full_name"]}{fork_badge}</div>
            <div class="repo-desc">{desc}</div>
            <div class="repo-meta">
                {lang_html}{stars_html}{forks_html}
                <span class="repo-updated">{updated}</span>
            </div>
        </a>'''
    return html

def render_md(text):
    """渲染 Markdown 为 HTML"""
    try:
        return md_lib.markdown(text, extensions=["fenced_code", "tables"])
    except:
        return f"<pre>{text}</pre>"

def render_file_list_html(files, url, subpath=""):
    """生成文件列表 HTML"""
    html = ""
    if subpath:
        parent = os.path.dirname(subpath) or ""
        html += f'<li class="file-item"><a onclick="loadDir(\'{parent}\')"><span class="icon">📁</span> ..</a></li>'
    for f in files:
        icon = "📁" if f["type"] == "dir" else "📄"
        cls = "dir" if f["type"] == "dir" else "file"
        size_html = f'<span class="size">{f["size"]}</span>' if f["type"] == "file" else ""
        if f["type"] == "dir":
            html += f'<li class="file-item"><div class="item-row"><a onclick="loadDir(\'{f["path"]}\')"><span class="icon {cls}">{icon}</span> {f["name"]}</a><a href="/gh/api/download-dir?url={quote(url)}&path={quote(f["path"])}" class="download-btn" title="下载文件夹"><span>⬇</span></a></div></li>'
        else:
            html += f'<li class="file-item"><div class="item-row"><a onclick="viewFile(\'{f["path"]}\')"><span class="icon {cls}">{icon}</span> {f["name"]}</a><span class="size">{format_file_size(f.get("size", 0))}</span><a href="/gh/api/download?url={quote(url)}&path={quote(f["path"])}" class="download-btn" title="下载文件"><span>⬇</span></a></div></li>'
    return html

def format_file_size(bytes):
    """格式化文件大小"""
    if bytes < 1024:
        return f"{bytes} B"
    elif bytes < 1024 * 1024:
        return f"{bytes / 1024:.1f} KB"
    else:
        return f"{bytes / (1024 * 1024):.1f} MB"

# ── GitHub 加速 API ────────────────────────────────────────

@app.route("/gh")
def gh_repo():
    """GitHub 仓库加速器入口"""
    url = request.args.get("url", "")
    if not url:
        return render_template_string(GH_INDEX)

    # 先尝试解析为仓库 URL
    owner, repo, branch, subpath = parse_github_url(url)

    if owner and repo:
        # 是仓库 URL
        if not url.startswith("http"):
            url = "https://" + url.lstrip("/")

        actual_branch = branch or "main"
        readme_content, readme_file = get_readme(get_repo_dir(owner, repo))
        files = build_file_tree(get_repo_dir(owner, repo), subpath)
        file_list_html = render_file_list_html(files, url, subpath)
        readme_html = render_md(readme_content) if readme_content else ""
        releases = get_releases(owner, repo)
        releases_html = get_releases_html(releases, owner, repo)

        return render_template_string(GH_REPO_PAGE,
            owner=owner, repo=repo, branch=actual_branch,
            url=url, files=files, subpath=subpath,
            file_list_html=file_list_html,
            readme=readme_html, readme_file=readme_file,
            releases_html=releases_html,
            server_ip="154.219.121.231")

    # 尝试解析为 org/user URL
    owner_only = parse_github_owner(url)
    if owner_only:
        repos = get_owner_repos(owner_only)
        if not repos:
            return render_template_string(GH_INDEX,
                error=f"未找到 {owner_only} 的公开仓库，或 API 请求受限")

        repos_html = render_repo_list_html(repos, add_data_attr=True)
        repos_json = json.dumps(repos, ensure_ascii=False)
        return render_template_string(GH_OWNER_PAGE,
            owner=owner_only,
            repos=repos,
            repos_json=repos_json,
            repos_html=repos_html,
            repo_count=len(repos),
            server_ip="154.219.121.231")

    return render_template_string(GH_INDEX, error="无效的 GitHub 链接")

@app.route("/gh/search")
def gh_search():
    """GitHub 仓库搜索"""
    q = request.args.get("q", "").strip()
    if not q:
        return render_template_string(GH_INDEX)

    page = request.args.get("page", 1, type=int)
    result = search_github_repos(q, page)

    # 生成搜索结果 HTML
    results_html = ""
    for r in result["items"]:
        desc = r["description"] or ""
        lang = r["language"]
        lang_html = f'<span class="repo-lang">{lang}</span>' if lang else ""
        stars = r["stars"]
        stars_html = f'<span class="repo-stat">⭐ {stars}</span>' if stars else ""
        forks = r["forks"]
        forks_html = f'<span class="repo-stat">🔀 {forks}</span>' if forks else ""
        lic = r.get("license", "")
        lic_html = f'<span class="repo-license">{lic}</span>' if lic else ""
        updated = r["updated_at"]
        if updated:
            try:
                updated = updated[:10]
            except:
                pass
        topics_html = ""
        if r.get("topics"):
            topics_html = '<div class="repo-topics">' + ''.join(f'<span class="repo-topic">{t}</span>' for t in r["topics"]) + '</div>'
        results_html += f'''<a class="repo-card" href="/gh?url={quote(r["html_url"], safe="")}">
            <div class="repo-name">{r["full_name"]}</div>
            <div class="repo-desc">{desc}</div>
            {topics_html}
            <div class="repo-meta">
                {lang_html}{lic_html}{stars_html}{forks_html}
                <span class="repo-updated">{updated}</span>
            </div>
        </a>'''

    # 生成分页
    total = result["total"]
    total_pages = min((total + 29) // 30, 33)  # GitHub API 最多 1000 条
    pagination_html = ""
    if total_pages > 1:
        pagination_html = '<div class="pagination">'
        if page > 1:
            pagination_html += f'<a href="/gh/search?q={quote(q)}&page={page-1}">← 上一页</a>'
        else:
            pagination_html += '<span class="disabled">← 上一页</span>'

        pagination_html += f'<span class="current">{page} / {total_pages}</span>'

        if page < total_pages:
            pagination_html += f'<a href="/gh/search?q={quote(q)}&page={page+1}">下一页 →</a>'
        else:
            pagination_html += '<span class="disabled">下一页 →</span>'
        pagination_html += '</div>'

    return render_template_string(GH_SEARCH_PAGE,
        q=q, total=total, results_html=results_html,
        pagination_html=pagination_html)

@app.route("/gh/api/tree")
def gh_api_tree():
    """API: 获取目录树（通过GitHub API）"""
    url = request.args.get("url", "")
    path = request.args.get("path", "")
    if not url:
        return jsonify({"ok": False, "msg": "缺少 url 参数"})

    owner, repo, branch, _ = parse_github_url(url)
    if not owner:
        return jsonify({"ok": False, "msg": "无效 URL"})

    files = build_file_tree(get_repo_dir(owner, repo), path)
    return jsonify({"ok": True, "files": files, "path": path})

@app.route("/gh/api/file")
def gh_api_file():
    """API: 查看文件内容（通过GitHub API）"""
    url = request.args.get("url", "")
    path = request.args.get("path", "")
    if not url or not path:
        return jsonify({"ok": False, "msg": "缺少参数"})

    owner, repo, _, _ = parse_github_url(url)
    if not owner:
        return jsonify({"ok": False, "msg": "无效 URL"})

    import requests as req
    try:
        api_url = f"https://api.github.com/repos/{owner}/{repo}/contents/{path}"
        resp = req.get(api_url, headers=gh_headers(), timeout=15)
        if resp.status_code != 200:
            return jsonify({"ok": False, "msg": "文件不存在"})

        data = resp.json()
        size = data.get("size", 0)

        if size > 2 * 1024 * 1024:
            return jsonify({"ok": True, "content": f"[文件过大 ({size} bytes)，请下载查看]", "too_large": True, "size": size})

        content = base64.b64decode(data["content"]).decode("utf-8", errors="replace")
    except Exception as e:
        return jsonify({"ok": False, "msg": str(e)})

    ext_map = {
        ".py": "python", ".js": "javascript", ".ts": "typescript", ".html": "html",
        ".css": "css", ".json": "json", ".md": "markdown", ".java": "java",
        ".c": "c", ".cpp": "cpp", ".h": "c", ".go": "go", ".rs": "rust",
        ".sh": "bash", ".yml": "yaml", ".yaml": "yaml", ".xml": "xml",
        ".sql": "sql", ".kt": "kotlin", ".swift": "swift", ".rb": "ruby",
        ".php": "php", ".txt": "text", ".gradle": "groovy", ".properties": "properties",
    }
    ext = os.path.splitext(path)[1].lower()
    lang = ext_map.get(ext, "text")

    return jsonify({"ok": True, "content": content, "lang": lang, "size": size, "path": path})

@app.route("/gh/api/download")
def gh_api_download():
    """API: 下载单个文件（通过GitHub原始URL代理加速）"""
    url = request.args.get("url", "")
    path = request.args.get("path", "")
    if not url or not path:
        return jsonify({"ok": False, "msg": "缺少参数"}), 400

    owner, repo, _, _ = parse_github_url(url)
    if not owner:
        return jsonify({"ok": False, "msg": "无效 URL"}), 400

    import requests as req
    try:
        api_url = f"https://api.github.com/repos/{owner}/{repo}/contents/{path}"
        resp = req.get(api_url, headers=gh_headers(), timeout=15)
        if resp.status_code != 200:
            return jsonify({"ok": False, "msg": "文件不存在"}), 404

        data = resp.json()
        download_url = data.get("download_url", "")
        if not download_url:
            return jsonify({"ok": False, "msg": "无法获取下载链接"}), 500

        filename = os.path.basename(path)

        r = req.get(download_url, stream=True, timeout=30, allow_redirects=True)
        if r.status_code != 200:
            return jsonify({"ok": False, "msg": f"下载失败，HTTP {r.status_code}"}), 500

        headers = {
            "Content-Type": r.headers.get("Content-Type", "application/octet-stream"),
            "Content-Disposition": f"attachment; filename={quote(filename)}",
        }
        if "Content-Length" in r.headers:
            headers["Content-Length"] = r.headers["Content-Length"]

        return Response(r.iter_content(chunk_size=8192), headers=headers)
    except Exception as e:
        return jsonify({"ok": False, "msg": str(e)}), 500

@app.route("/gh/api/download-dir")
def gh_api_download_dir():
    """API: 下载文件夹为 ZIP（通过GitHub API）"""
    url = request.args.get("url", "")
    path = request.args.get("path", "")
    if not url:
        return jsonify({"ok": False, "msg": "缺少参数"}), 400

    owner, repo, _, _ = parse_github_url(url)
    if not owner:
        return jsonify({"ok": False, "msg": "无效 URL"}), 400

    import requests as req, zipfile

    def fetch_dir(base_path, zf, root_path=""):
        api_url = f"https://api.github.com/repos/{owner}/{repo}/contents/{base_path}"
        resp = req.get(api_url, headers=gh_headers(), timeout=15)
        if resp.status_code != 200:
            return

        for item in resp.json():
            if item["type"] == "dir":
                fetch_dir(item["path"], zf, root_path)
            else:
                content_resp = req.get(item["download_url"], timeout=30)
                arc_path = os.path.relpath(item["path"], root_path if root_path else path)
                zf.writestr(arc_path, content_resp.content)

    try:
        zip_buffer = io.BytesIO()
        with zipfile.ZipFile(zip_buffer, 'w', zipfile.ZIP_DEFLATED) as zf:
            fetch_dir(path, zf, path)

        zip_buffer.seek(0)
        base_name = os.path.basename(path) or repo
        return send_file(zip_buffer, as_attachment=True, download_name=f"{base_name}.zip", mimetype="application/zip")
    except Exception as e:
        return jsonify({"ok": False, "msg": str(e)}), 500

@app.route("/gh/api/zip")
def gh_api_zip():
    """API: 下载整个仓库为 zip（通过GitHub原始URL代理加速）"""
    url = request.args.get("url", "")
    if not url:
        return jsonify({"ok": False, "msg": "缺少 url 参数"}), 400

    owner, repo, branch, _ = parse_github_url(url)
    if not owner:
        return jsonify({"ok": False, "msg": "无效 URL"}), 400

    import requests as req
    try:
        api_url = f"https://api.github.com/repos/{owner}/{repo}"
        resp = req.get(api_url, headers=gh_headers(), timeout=15)
        if resp.status_code != 200:
            return jsonify({"ok": False, "msg": f"获取仓库信息失败，HTTP {resp.status_code}"}), 500
        
        repo_info = resp.json()
        default_branch = branch or repo_info.get("default_branch", "main")
        download_url = f"https://github.com/{owner}/{repo}/archive/refs/heads/{default_branch}.zip"

        r = req.get(download_url, stream=True, timeout=30, allow_redirects=True)
        if r.status_code != 200:
            return jsonify({"ok": False, "msg": f"下载失败，HTTP {r.status_code}"}), 500

        headers = {
            "Content-Type": r.headers.get("Content-Type", "application/zip"),
            "Content-Disposition": f"attachment; filename={repo}.zip",
        }
        if "Content-Length" in r.headers:
            headers["Content-Length"] = r.headers["Content-Length"]

        return Response(r.iter_content(chunk_size=8192), headers=headers)
    except Exception as e:
        return jsonify({"ok": False, "msg": str(e)}), 500

@app.route("/gh/api/releases")
def gh_api_releases():
    """API: 获取 releases 列表"""
    url = request.args.get("url", "")
    if not url:
        return jsonify({"ok": False, "msg": "缺少 url 参数"}), 400
    owner, repo, _, _ = parse_github_url(url)
    if not owner:
        return jsonify({"ok": False, "msg": "无效 URL"}), 400
    releases = get_releases(owner, repo)
    return jsonify({"ok": True, "releases": releases})

@app.route("/gh/api/repos")
def gh_api_repos():
    """API: 获取用户/组织的仓库列表"""
    owner = request.args.get("owner", "")
    if not owner:
        return jsonify({"ok": False, "msg": "缺少 owner 参数"}), 400
    repos = get_owner_repos(owner)
    return jsonify({"ok": True, "owner": owner, "repos": repos})

@app.route("/gh/api/search")
def gh_api_search():
    """API: 搜索 GitHub 仓库"""
    q = request.args.get("q", "")
    page = request.args.get("page", 1, type=int)
    if not q:
        return jsonify({"ok": False, "msg": "缺少搜索关键词"}), 400
    result = search_github_repos(q, page)
    return jsonify({"ok": True, "q": q, "total": result["total"], "items": result["items"]})

# ── GitHub 加速页面 ────────────────────────────────────────

GH_INDEX = r"""<!DOCTYPE html>
<html lang="zh-CN">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width, initial-scale=1.0">
<title>GitHub 加速器</title>
<style>
*{margin:0;padding:0;box-sizing:border-box}
body{font-family:-apple-system,BlinkMacSystemFont,'Segoe UI',Roboto,sans-serif;background:#0d1117;color:#c9d1d9;min-height:100vh}
/* 顶部导航 */
.navbar{background:#161b22;border-bottom:1px solid #30363d;padding:0 24px;display:flex;align-items:center;height:56px;gap:16px}
.navbar .logo{color:#58a6ff;font-size:20px;font-weight:700;text-decoration:none;white-space:nowrap}
.navbar .search-wrap{flex:1;max-width:500px;position:relative}
.navbar .search-wrap input{width:100%;padding:6px 12px;background:#0d1117;border:1px solid #30363d;border-radius:6px;color:#c9d1d9;font-size:13px;outline:none;transition:all .2s}
.navbar .search-wrap input:focus{border-color:#58a6ff;width:100%}
.navbar .search-wrap input::placeholder{color:#484f58}
.navbar .nav-links{display:flex;gap:12px;margin-left:auto}
.navbar .nav-links a{color:#c9d1d9;text-decoration:none;font-size:13px;padding:6px 10px;border-radius:6px;transition:all .2s}
.navbar .nav-links a:hover{background:#30363d;color:#58a6ff}
/* 主内容 */
.main{max-width:960px;margin:0 auto;padding:32px 20px}
.hero{text-align:center;padding:40px 0 20px}
.hero h1{font-size:32px;color:#58a6ff;margin-bottom:8px}
.hero p{font-size:16px;color:#8b949e;margin-bottom:24px}
.hero .search-box{display:flex;max-width:600px;margin:0 auto}
.hero .search-box input{flex:1;padding:12px 16px;background:#0d1117;border:1px solid #30363d;border-radius:6px 0 0 6px;color:#c9d1d9;font-size:15px;outline:none}
.hero .search-box input:focus{border-color:#58a6ff}
.hero .search-box button{padding:12px 24px;background:#238636;border:none;border-radius:0 6px 6px 0;color:#fff;font-size:15px;font-weight:600;cursor:pointer}
.hero .search-box button:hover{background:#2ea043}
.section-title{font-size:18px;font-weight:600;color:#c9d1d9;margin:32px 0 16px;padding-bottom:8px;border-bottom:1px solid #30363d}
/* 快捷入口 */
.quick-links{display:grid;grid-template-columns:repeat(auto-fill,minmax(280px,1fr));gap:12px}
.quick-card{background:#161b22;border:1px solid #30363d;border-radius:8px;padding:16px;text-decoration:none;transition:all .2s}
.quick-card:hover{border-color:#58a6ff}
.quick-card .name{color:#58a6ff;font-size:14px;font-weight:600;margin-bottom:4px}
.quick-card .desc{color:#8b949e;font-size:12px;line-height:1.5}
.quick-card .meta{display:flex;gap:12px;margin-top:8px;font-size:11px;color:#484f58}
/* 组织列表 */
.org-list{display:flex;flex-wrap:wrap;gap:8px}
.org-link{display:inline-block;padding:6px 14px;background:#161b22;border:1px solid #30363d;border-radius:20px;color:#58a6ff;text-decoration:none;font-size:13px;transition:all .2s}
.org-link:hover{border-color:#58a6ff;background:#1f2937}
.error{background:#490202;border:1px solid #da3633;border-radius:8px;padding:12px 16px;margin:12px 0;color:#f85149;font-size:13px;text-align:left}
.toast{position:fixed;top:20px;right:20px;padding:12px 20px;border-radius:8px;font-size:14px;z-index:2000;animation:slideIn .3s}
.toast-success{background:#238636;color:#fff}
.toast-error{background:#da3633;color:#fff}
@keyframes slideIn{from{transform:translateX(100px);opacity:0}to{transform:translateX(0);opacity:1}}
@media(max-width:600px){.navbar{gap:8px}.navbar .nav-links a{font-size:11px;padding:4px 6px}.hero h1{font-size:24px}}
</style>
</head>
<body>
<div class="navbar">
    <a href="/gh" class="logo">GH Accelerator</a>
    <div class="search-wrap">
        <input type="text" id="navSearch" placeholder="搜索 GitHub 仓库..." onkeydown="if(event.key==='Enter')doSearch()">
    </div>
    <div class="nav-links">
        <a href="/dl">下载加速</a>
        <a href="/">管理面板</a>
    </div>
</div>
<div class="main">
    <div class="hero">
        <h1>GitHub 加速器</h1>
        <p>通过服务器加速访问 GitHub，搜索仓库、浏览代码、下载文件</p>
        {{ error|safe }}
        <div class="search-box">
            <input type="text" id="heroSearch" placeholder="搜索 GitHub 仓库..." onkeydown="if(event.key==='Enter')doSearch()">
            <button onclick="doSearch()">搜索</button>
        </div>
    </div>

    <div class="section-title">热门组织</div>
    <div class="org-list">
        <a href="/gh?url=https://github.com/Uotan-Dev" class="org-link">Uotan-Dev</a>
        <a href="/gh?url=https://github.com/microsoft" class="org-link">Microsoft</a>
        <a href="/gh?url=https://github.com/google" class="org-link">Google</a>
        <a href="/gh?url=https://github.com/facebook" class="org-link">Meta</a>
        <a href="/gh?url=https://github.com/apple" class="org-link">Apple</a>
        <a href="/gh?url=https://github.com/kubernetes" class="org-link">Kubernetes</a>
        <a href="/gh?url=https://github.com/torvalds" class="org-link">Linus Torvalds</a>
        <a href="/gh?url=https://github.com/vuejs" class="org-link">Vue.js</a>
    </div>

    <div class="section-title">快速浏览</div>
    <div class="quick-links">
        <a href="/gh?url=https://github.com/Uotan-Dev/UotanToolboxNT" class="quick-card">
            <div class="name">UotanToolboxNT</div>
            <div class="desc">柚坛搞机工具箱全新版本</div>
            <div class="meta"><span>C#</span><span>⭐ 2.5k+</span></div>
        </a>
        <a href="/gh?url=https://github.com/Uotan-Dev/UotanToolBox" class="quick-card">
            <div class="name">UotanToolBox</div>
            <div class="desc">柚坛搞机工具箱</div>
            <div class="meta"><span>C#</span><span>⭐ 337</span></div>
        </a>
        <a href="/gh?url=https://github.com/microsoft/vscode" class="quick-card">
            <div class="name">vscode</div>
            <div class="desc">Visual Studio Code</div>
            <div class="meta"><span>TypeScript</span><span>⭐ 160k+</span></div>
        </a>
        <a href="/gh?url=https://github.com/facebook/react" class="quick-card">
            <div class="name">react</div>
            <div class="desc">The library for web and native user interfaces.</div>
            <div class="meta"><span>JavaScript</span><span>⭐ 230k+</span></div>
        </a>
    </div>

    <div class="section-title">URL 直接访问</div>
    <div class="quick-links">
        <a href="/gh?url=https://github.com/owner/repo" class="quick-card" style="border-style:dashed">
            <div class="name">浏览仓库</div>
            <div class="desc">/gh?url=https://github.com/owner/repo</div>
        </a>
        <a href="/gh?url=https://github.com/owner" class="quick-card" style="border-style:dashed">
            <div class="name">仓库列表</div>
            <div class="desc">/gh?url=https://github.com/owner</div>
        </a>
    </div>
</div>
<div id="toast" class="toast"></div>
<script>
function toast(m,t){var e=document.getElementById('toast');e.textContent=m;e.className='toast toast-'+(t||'success');setTimeout(function(){e.className='toast'},2500)}
function doSearch(){
    var q=document.getElementById('heroSearch').value||document.getElementById('navSearch').value;
    q=q.trim();if(!q){toast('请输入搜索关键词','error');return}
    window.location.href='/gh/search?q='+encodeURIComponent(q);
}
</script>
</body>
</html>"""

GH_OWNER_PAGE = r"""<!DOCTYPE html>
<html lang="zh-CN">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width, initial-scale=1.0">
<title>{{ owner }} - 仓库列表</title>
<style>
*{margin:0;padding:0;box-sizing:border-box}
body{font-family:-apple-system,BlinkMacSystemFont,'Segoe UI',Roboto,sans-serif;background:#0d1117;color:#c9d1d9;min-height:100vh}
.navbar{background:#161b22;border-bottom:1px solid #30363d;padding:0 24px;display:flex;align-items:center;height:56px;gap:16px}
.navbar .logo{color:#58a6ff;font-size:20px;font-weight:700;text-decoration:none;white-space:nowrap}
.navbar .search-wrap{flex:1;max-width:400px;position:relative}
.navbar .search-wrap input{width:100%;padding:6px 12px;background:#0d1117;border:1px solid #30363d;border-radius:6px;color:#c9d1d9;font-size:13px;outline:none}
.navbar .search-wrap input:focus{border-color:#58a6ff}
.navbar .nav-links{display:flex;gap:12px;margin-left:auto}
.navbar .nav-links a{color:#c9d1d9;text-decoration:none;font-size:13px;padding:6px 10px;border-radius:6px;transition:all .2s}
.navbar .nav-links a:hover{background:#30363d;color:#58a6ff}
.container{max-width:960px;margin:0 auto;padding:20px}
.owner-header{display:flex;align-items:center;justify-content:space-between;flex-wrap:wrap;gap:8px;margin-bottom:16px;padding-bottom:12px;border-bottom:1px solid #30363d}
.owner-header h1{font-size:20px;color:#58a6ff}
.owner-header .sub{font-size:13px;color:#8b949e}
.filter-bar{display:flex;gap:8px;margin-bottom:16px}
.filter-bar input{flex:1;padding:8px 12px;background:#0d1117;border:1px solid #30363d;border-radius:6px;color:#c9d1d9;font-size:13px;outline:none}
.filter-bar input:focus{border-color:#58a6ff}
.filter-bar select{padding:8px 12px;background:#0d1117;border:1px solid #30363d;border-radius:6px;color:#c9d1d9;font-size:13px;outline:none}
.repo-list{display:flex;flex-direction:column;gap:8px}
.repo-card{display:block;background:#161b22;border:1px solid #30363d;border-radius:8px;padding:16px 20px;text-decoration:none;transition:all .2s}
.repo-card:hover{border-color:#58a6ff}
.repo-card.hidden{display:none}
.repo-name{font-size:15px;font-weight:600;color:#58a6ff;margin-bottom:4px}
.repo-desc{font-size:13px;color:#8b949e;line-height:1.5;margin-bottom:8px}
.repo-meta{display:flex;flex-wrap:wrap;gap:12px;align-items:center;font-size:12px;color:#484f58}
.repo-lang{color:#c9d1d9}
.repo-stat{color:#484f58}
.repo-updated{margin-left:auto;color:#30363d}
.fork-badge{display:inline-block;background:#21262d;color:#8b949e;padding:1px 6px;border-radius:4px;font-size:10px;vertical-align:middle}
.no-results{text-align:center;padding:40px;color:#8b949e;font-size:14px}
.toast{position:fixed;top:20px;right:20px;padding:12px 20px;border-radius:8px;font-size:14px;z-index:2000;animation:slideIn .3s}
.toast-success{background:#238636;color:#fff}
.toast-error{background:#da3633;color:#fff}
@keyframes slideIn{from{transform:translateX(100px);opacity:0}to{transform:translateX(0);opacity:1}}
@media(max-width:600px){.repo-meta{flex-direction:column;align-items:flex-start;gap:4px}.repo-updated{margin-left:0}.filter-bar{flex-direction:column}}
</style>
</head>
<body>
<div class="navbar">
    <a href="/gh" class="logo">GH Accelerator</a>
    <div class="search-wrap">
        <input type="text" placeholder="搜索 GitHub 仓库..." onkeydown="if(event.key==='Enter'){var v=this.value.trim();if(v)location.href='/gh/search?q='+encodeURIComponent(v)}">
    </div>
    <div class="nav-links">
        <a href="/gh">首页</a>
        <a href="/dl">下载加速</a>
    </div>
</div>
<div class="container">
    <div class="owner-header">
        <div>
            <h1>{{ owner }}</h1>
            <div class="sub" id="repoCount">{{ repo_count }} 个仓库</div>
        </div>
        <div>
            <a href="https://github.com/{{ owner }}" target="_blank" style="color:#58a6ff;text-decoration:none;font-size:13px">GitHub 原站</a>
        </div>
    </div>
    <div class="filter-bar">
        <input type="text" id="filterInput" placeholder="过滤仓库名称、描述、语言..." oninput="filterRepos()">
        <select id="sortSelect" onchange="filterRepos()">
            <option value="updated">最近更新</option>
            <option value="stars">最多 Star</option>
            <option value="name">名称 A-Z</option>
        </select>
    </div>
    <div class="repo-list" id="repoList">
        {{ repos_html|safe }}
    </div>
    <div class="no-results" id="noResults" style="display:none">没有匹配的仓库</div>
</div>
<div id="toast" class="toast"></div>
<script>
var allRepos = {{ repos_json|safe }};
function toast(m,t){var e=document.getElementById('toast');e.textContent=m;e.className='toast toast-'+(t||'success');setTimeout(function(){e.className='toast'},2500)}
function filterRepos(){
    var q=document.getElementById('filterInput').value.toLowerCase();
    var sort=document.getElementById('sortSelect').value;
    var cards=document.querySelectorAll('.repo-card');
    var visible=0;
    cards.forEach(function(c){
        var text=(c.getAttribute('data-search')||'').toLowerCase();
        var match=!q||text.indexOf(q)>=0;
        c.classList.toggle('hidden',!match);
        if(match)visible++;
    });
    document.getElementById('noResults').style.display=visible?'none':'block';
    document.getElementById('repoCount').textContent=visible+' / {{ repo_count }} 个仓库';
}
</script>
</body>
</html>"""

GH_SEARCH_PAGE = r"""<!DOCTYPE html>
<html lang="zh-CN">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width, initial-scale=1.0">
<title>搜索: {{ q }} - GitHub 加速器</title>
<style>
*{margin:0;padding:0;box-sizing:border-box}
body{font-family:-apple-system,BlinkMacSystemFont,'Segoe UI',Roboto,sans-serif;background:#0d1117;color:#c9d1d9;min-height:100vh}
.navbar{background:#161b22;border-bottom:1px solid #30363d;padding:0 24px;display:flex;align-items:center;height:56px;gap:16px}
.navbar .logo{color:#58a6ff;font-size:20px;font-weight:700;text-decoration:none;white-space:nowrap}
.navbar .search-wrap{flex:1;max-width:500px;position:relative}
.navbar .search-wrap input{width:100%;padding:6px 12px;background:#0d1117;border:1px solid #30363d;border-radius:6px;color:#c9d1d9;font-size:13px;outline:none}
.navbar .search-wrap input:focus{border-color:#58a6ff}
.navbar .nav-links{display:flex;gap:12px;margin-left:auto}
.navbar .nav-links a{color:#c9d1d9;text-decoration:none;font-size:13px;padding:6px 10px;border-radius:6px;transition:all .2s}
.navbar .nav-links a:hover{background:#30363d;color:#58a6ff}
.container{max-width:960px;margin:0 auto;padding:20px}
.search-info{padding:12px 0;margin-bottom:8px;font-size:14px;color:#8b949e;border-bottom:1px solid #30363d}
.search-info em{color:#c9d1d9;font-style:normal}
.repo-list{display:flex;flex-direction:column;gap:8px;margin-top:8px}
.repo-card{display:block;background:#161b22;border:1px solid #30363d;border-radius:8px;padding:16px 20px;text-decoration:none;transition:all .2s}
.repo-card:hover{border-color:#58a6ff}
.repo-name{font-size:15px;font-weight:600;color:#58a6ff;margin-bottom:4px}
.repo-desc{font-size:13px;color:#8b949e;line-height:1.5;margin-bottom:8px}
.repo-topics{display:flex;flex-wrap:wrap;gap:4px;margin-bottom:8px}
.repo-topic{background:#1c3a5c;color:#58a6ff;padding:2px 8px;border-radius:12px;font-size:11px}
.repo-meta{display:flex;flex-wrap:wrap;gap:12px;align-items:center;font-size:12px;color:#484f58}
.repo-lang{color:#c9d1d9}
.repo-license{color:#484f58}
.repo-stat{color:#484f58}
.repo-updated{margin-left:auto;color:#30363d}
.pagination{display:flex;justify-content:center;gap:8px;margin:24px 0;align-items:center}
.pagination a,.pagination span{padding:6px 14px;background:#161b22;border:1px solid #30363d;border-radius:6px;color:#58a6ff;text-decoration:none;font-size:13px;transition:all .2s}
.pagination a:hover{border-color:#58a6ff;background:#1f2937}
.pagination .current{background:#1f6feb;border-color:#1f6feb;color:#fff}
.pagination .disabled{color:#484f58;pointer-events:none}
.loading{text-align:center;padding:60px;color:#8b949e;font-size:14px}
.toast{position:fixed;top:20px;right:20px;padding:12px 20px;border-radius:8px;font-size:14px;z-index:2000;animation:slideIn .3s}
.toast-success{background:#238636;color:#fff}
.toast-error{background:#da3633;color:#fff}
@keyframes slideIn{from{transform:translateX(100px);opacity:0}to{transform:translateX(0);opacity:1}}
@media(max-width:600px){.repo-meta{flex-direction:column;align-items:flex-start;gap:4px}.repo-updated{margin-left:0}}
</style>
</head>
<body>
<div class="navbar">
    <a href="/gh" class="logo">GH Accelerator</a>
    <div class="search-wrap">
        <input type="text" id="searchInput" value="{{ q }}" placeholder="搜索 GitHub 仓库..." onkeydown="if(event.key==='Enter')doSearch()">
    </div>
    <div class="nav-links">
        <a href="/gh">首页</a>
        <a href="/dl">下载加速</a>
    </div>
</div>
<div class="container">
    <div class="search-info">
        搜索 <em>{{ q }}</em> 共找到 <em>{{ total }}</em> 个仓库
    </div>
    <div class="repo-list" id="repoList">
        {{ results_html|safe }}
    </div>
    {{ pagination_html|safe }}
</div>
<div id="toast" class="toast"></div>
<script>
function toast(m,t){var e=document.getElementById('toast');e.textContent=m;e.className='toast toast-'+(t||'success');setTimeout(function(){e.className='toast'},2500)}
function doSearch(){
    var q=document.getElementById('searchInput').value.trim();
    if(!q){toast('请输入搜索关键词','error');return}
    window.location.href='/gh/search?q='+encodeURIComponent(q);
}
</script>
</body>
</html>"""

GH_PAGE = r"""<!DOCTYPE html>
<html lang="zh-CN">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width, initial-scale=1.0">
<title>GitHub 仓库加速器</title>
<style>
*{margin:0;padding:0;box-sizing:border-box}
body{font-family:-apple-system,BlinkMacSystemFont,'Segoe UI',Roboto,sans-serif;background:#0f172a;color:#e2e8f0;min-height:100vh}
.header{background:#1e293b;padding:14px 24px;display:flex;align-items:center;justify-content:space-between;border-bottom:1px solid #334155}
.header h1{font-size:18px;color:#38bdf8}
.header a{color:#94a3b8;text-decoration:none;font-size:13px}
.header a:hover{color:#38bdf8}
.container{max-width:900px;margin:60px auto;padding:0 20px}
.card{background:#1e293b;border-radius:12px;border:1px solid #334155;padding:28px}
.card h2{font-size:20px;margin-bottom:8px;color:#38bdf8}
.card .sub{font-size:14px;color:#94a3b8;margin-bottom:20px}
.input-group{display:flex;gap:8px}
.input-group input{flex:1;padding:12px 16px;background:#0f172a;border:1px solid #334155;border-radius:10px;color:#e2e8f0;font-size:14px;outline:none}
.input-group input:focus{border-color:#38bdf8}
.input-group button{padding:12px 24px;background:#3b82f6;border:none;border-radius:10px;color:#fff;font-size:14px;font-weight:600;cursor:pointer;white-space:nowrap}
.input-group button:hover{background:#2563eb}
.error{background:#7f1d1d;border:1px solid #ef4444;border-radius:8px;padding:12px;margin-top:12px;color:#fca5a5;font-size:13px}
.tip{font-size:13px;color:#94a3b8;margin-top:16px;line-height:1.8}
.tip code{background:#334155;padding:2px 6px;border-radius:4px;font-size:12px;color:#38bdf8}
.tip a{color:#06b6d4;cursor:pointer;text-decoration:underline}
.toast{position:fixed;top:20px;right:20px;padding:12px 20px;border-radius:8px;font-size:14px;z-index:2000;animation:slideIn .3s}
.toast-success{background:#22c55e;color:#fff}
.toast-error{background:#ef4444;color:#fff}
@keyframes slideIn{from{transform:translateX(100px);opacity:0}to{transform:translateX(0);opacity:1}}
@media(max-width:600px){.input-group{flex-direction:column}}
</style>
</head>
<body>
<div class="header">
    <h1>GitHub 仓库加速器</h1>
    <div>
        <a href="/dl">下载加速</a> &nbsp;
        <a href="/">管理面板</a>
    </div>
</div>
<div class="container">
    <div class="card">
        <h2>输入 GitHub 仓库地址</h2>
        <p class="sub">通过服务器加速访问 GitHub 仓库，浏览代码、下载文件</p>
        <form onsubmit="return goRepo(event)">
            <div class="input-group">
                <input type="text" id="urlInput" placeholder="https://github.com/Uotan-Dev/UotanToolBox" autofocus>
                <button type="submit">加速访问</button>
            </div>
        </form>
        {{ error|safe }}
        <div class="tip">
            <p>支持格式:</p>
            <p><code>https://github.com/owner/repo</code> &nbsp; 浏览仓库</p>
            <p><code>https://github.com/owner</code> &nbsp; 查看仓库列表</p>
            <p style="margin-top:8px">快速测试: <a onclick="testRepo()">Uotan-Dev/UotanToolBox</a> &nbsp;|&nbsp; <a onclick="testOwner()">Uotan-Dev</a></p>
        </div>
    </div>
</div>
<div id="toast" class="toast"></div>
<script>
function toast(msg, type) {
    var t = document.getElementById('toast');
    t.textContent = msg;
    t.className = 'toast toast-' + (type || 'success');
    setTimeout(function(){ t.className = 'toast'; }, 2500);
}
function goRepo(e) {
    if (e) e.preventDefault();
    var url = document.getElementById('urlInput').value.trim();
    if (!url) { toast('请输入 GitHub 仓库链接', 'error'); return false; }
    if (!/github\.com/.test(url)) { toast('仅支持 GitHub 仓库链接', 'error'); return false; }
    window.location.href = '/gh?url=' + encodeURIComponent(url);
    return false;
}
function testRepo() {
    document.getElementById('urlInput').value = 'https://github.com/Uotan-Dev/UotanToolBox';
    goRepo();
}
function testOwner() {
    document.getElementById('urlInput').value = 'https://github.com/Uotan-Dev';
    goRepo();
}
</script>
</body>
</html>"""

GH_REPO_PAGE = r"""<!DOCTYPE html>
<html lang="zh-CN">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width, initial-scale=1.0">
<title>{{ owner }}/{{ repo }} - 仓库加速</title>
<style>
*{margin:0;padding:0;box-sizing:border-box}
body{font-family:-apple-system,BlinkMacSystemFont,'Segoe UI',Roboto,sans-serif;background:#0f172a;color:#e2e8f0;min-height:100vh}
.header{background:#1e293b;padding:12px 20px;display:flex;align-items:center;justify-content:space-between;border-bottom:1px solid #334155;flex-wrap:wrap;gap:8px}
.header .repo-info{display:flex;align-items:center;gap:10px}
.header .repo-info h1{font-size:16px;color:#e2e8f0}
.header .repo-info h1 a{color:#38bdf8;text-decoration:none}
.header .repo-info .branch{font-size:11px;background:#334155;padding:2px 8px;border-radius:12px;color:#94a3b8}
.header .actions{display:flex;gap:8px}
.header a{color:#94a3b8;text-decoration:none;font-size:13px}
.header a:hover{color:#38bdf8}
.layout{display:flex;height:calc(100vh - 56px)}
.sidebar{width:300px;background:#1a2332;border-right:1px solid #334155;overflow-y:auto;flex-shrink:0}
.sidebar-header{padding:12px 16px;font-size:12px;color:#94a3b8;border-bottom:1px solid #334155;display:flex;justify-content:space-between}
.file-list{list-style:none}
.file-item{border-bottom:1px solid #1e293b}
.file-item .item-row{display:flex;align-items:center;gap:8px;padding:8px 16px}
.file-item .item-row a{flex:1;display:flex;align-items:center;gap:8px;color:#cbd5e1;text-decoration:none;font-size:13px;cursor:pointer}
.file-item .item-row a:hover{color:#38bdf8}
.file-item .item-row .size{font-size:12px;color:#475569;flex-shrink:0;margin-right:8px}
.file-item .download-btn{display:flex;align-items:center;justify-content:center;width:28px;height:28px;border-radius:6px;background:#334155;color:#94a3b8;text-decoration:none;font-size:12px;transition:all .2s;flex-shrink:0}
.file-item .download-btn:hover{background:#3b82f6;color:#fff}
.file-item .icon{width:16px;text-align:center;font-size:14px}
.file-item .dir{color:#38bdf8}
.file-item .file{color:#94a3b8}
.main{flex:1;overflow-y:auto;display:flex;flex-direction:column}
.main-header{padding:12px 20px;border-bottom:1px solid #334155;display:flex;justify-content:space-between;align-items:center;flex-wrap:wrap;gap:8px}
.main-header .breadcrumb{font-size:13px;color:#94a3b8}
.main-header .breadcrumb a{color:#38bdf8;text-decoration:none}
.main-header .breadcrumb a:hover{text-decoration:underline}
.main-content{flex:1;padding:0}
.readme{padding:0}
.readme-header{padding:10px 20px;background:#162032;border-bottom:1px solid #334155;font-size:13px;font-weight:600;color:#94a3b8}
.readme-body{padding:20px;font-size:14px;line-height:1.7;color:#cbd5e1}
.readme-body h1,.readme-body h2,.readme-body h3{color:#e2e8f0;margin:16px 0 8px}
.readme-body h1{font-size:22px;border-bottom:1px solid #334155;padding-bottom:8px}
.readme-body h2{font-size:18px;border-bottom:1px solid #334155;padding-bottom:6px}
.readme-body h3{font-size:15px}
.readme-body code{background:#1e293b;padding:2px 6px;border-radius:4px;font-size:12px;color:#38bdf8}
.readme-body pre{background:#1e293b;padding:16px;border-radius:8px;overflow-x:auto;margin:8px 0;font-size:13px;line-height:1.5}
.readme-body pre code{background:none;padding:0;color:#cbd5e1}
.readme-body a{color:#38bdf8}
.readme-body img{max-width:100%}
.readme-body table{border-collapse:collapse;width:100%;margin:8px 0}
.readme-body th,.readme-body td{border:1px solid #334155;padding:8px 12px;text-align:left}
.readme-body th{background:#1e293b}
.readme-body ul,.readme-body ol{padding-left:24px}
.readme-body blockquote{border-left:3px solid #3b82f6;padding-left:12px;color:#94a3b8;margin:8px 0}
.code-view{padding:0}
.code-header{padding:10px 20px;background:#162032;border-bottom:1px solid #334155;display:flex;justify-content:space-between;align-items:center;font-size:13px}
.code-header .lang{color:#94a3b8}
.code-body{padding:16px 20px;overflow-x:auto}
.code-body pre{margin:0;font-family:'JetBrains Mono','Fira Code',monospace;font-size:13px;line-height:1.6;color:#cbd5e1}
.code-body .line{display:flex}
.code-body .line-num{color:#475569;min-width:40px;text-align:right;padding-right:12px;user-select:none}
.empty{padding:60px;text-align:center;color:#94a3b8;font-size:14px}
.btn{padding:8px 16px;border:none;border-radius:6px;cursor:pointer;font-size:12px;font-weight:500}
.btn-primary{background:#3b82f6;color:#fff}
.btn-primary:hover{background:#2563eb}
.btn-outline{background:transparent;border:1px solid #475569;color:#94a3b8}
.btn-outline:hover{background:#334155}
.loading{text-align:center;padding:40px;color:#94a3b8}
.toast{position:fixed;top:20px;right:20px;padding:12px 20px;border-radius:8px;font-size:14px;z-index:2000;animation:slideIn .3s}
.toast-success{background:#22c55e;color:#fff}
.toast-error{background:#ef4444;color:#fff}
@keyframes slideIn{from{transform:translateX(100px);opacity:0}to{transform:translateX(0);opacity:1}}
/* Releases */
.releases-section{margin:0;padding:0}
.releases-header{padding:10px 20px;background:#162032;border-bottom:1px solid #334155;font-size:13px;font-weight:600;color:#94a3b8}
.release-item{padding:16px 20px;border-bottom:1px solid #1e293b}
.release-title{font-size:15px;font-weight:600;color:#e2e8f0;margin-bottom:4px}
.release-tag{display:inline-block;background:#1e293b;color:#38bdf8;padding:2px 8px;border-radius:4px;font-size:12px;margin-right:8px;font-family:monospace}
.prerelease-badge{display:inline-block;background:#92400e;color:#fbbf24;padding:2px 6px;border-radius:4px;font-size:10px;vertical-align:middle}
.release-meta{font-size:12px;color:#64748b;margin-bottom:8px}
.release-body{font-size:13px;line-height:1.6;color:#94a3b8;margin-bottom:12px;max-height:200px;overflow-y:auto}
.release-body h1,.release-body h2,.release-body h3{font-size:14px;color:#cbd5e1;margin:8px 0 4px}
.release-body code{background:#1e293b;padding:1px 4px;border-radius:3px;font-size:11px;color:#38bdf8}
.release-body pre{background:#1e293b;padding:8px;border-radius:4px;overflow-x:auto;font-size:11px}
.release-assets{display:flex;flex-wrap:wrap;gap:6px;margin-top:8px}
.asset-link{display:inline-flex;align-items:center;gap:6px;padding:6px 12px;background:#1e293b;border:1px solid #334155;border-radius:6px;color:#cbd5e1;text-decoration:none;font-size:12px;transition:all .2s}
.asset-link:hover{border-color:#38bdf8;color:#38bdf8}
.asset-name{max-width:250px;overflow:hidden;text-overflow:ellipsis;white-space:nowrap}
.asset-size{color:#64748b;font-size:11px}
@media(max-width:768px){.layout{flex-direction:column}.sidebar{width:100%;max-height:200px}}
</style>
</head>
<body>
<div class="header">
    <div class="repo-info">
        <h1><a href="/gh?url={{ url }}">{{ owner }}/{{ repo }}</a></h1>
        <span class="branch">{{ branch }}</span>
    </div>
    <div class="actions">
        <a href="/gh/api/zip?url={{ url }}" class="btn btn-primary">下载 ZIP</a>
        <a href="https://github.com/{{ owner }}/{{ repo }}" target="_blank" class="btn btn-outline">GitHub 原站</a>
        <a href="/gh" class="btn btn-outline">← 返回</a>
    </div>
</div>
<div class="layout">
    <div class="sidebar">
        <div class="sidebar-header">
            <span>文件列表</span>
            <a href="/gh?url={{ url }}" style="font-size:12px;color:#38bdf8">根目录</a>
        </div>
        <ul class="file-list" id="fileList">
            {{ file_list_html|safe }}
        </ul>
    </div>
    <div class="main">
        <div class="main-header">
            <div class="breadcrumb" id="breadcrumb">
                <a href="/gh?url={{ url }}">{{ owner }}/{{ repo }}</a>
            </div>
            <span style="font-size:12px;color:#94a3b8">加速访问: {{ server_ip }}:9090</span>
        </div>
        <div class="main-content" id="mainContent">
            {% if readme %}
            <div class="readme">
                <div class="readme-header">{{ readme_file }}</div>
                <div class="readme-body">{{ readme|safe }}</div>
            </div>
            {% else %}
            <div class="empty">选择一个文件查看内容</div>
            {% endif %}
            {{ releases_html|safe }}
        </div>
    </div>
</div>
<div id="toast" class="toast"></div>
<script>
var repoUrl = '{{ url }}';
var repoOwner = '{{ owner }}';
var repoRepo = '{{ repo }}';
var currentPath = '{{ subpath }}';

function toast(msg, type) {
    var t = document.getElementById('toast');
    t.textContent = msg;
    t.className = 'toast toast-' + (type || 'success');
    setTimeout(function(){ t.className = 'toast'; }, 2500);
}

function loadDir(path) {
    currentPath = path;
    fetch('/gh/api/tree?url=' + encodeURIComponent(repoUrl) + '&path=' + encodeURIComponent(path))
    .then(function(r) { return r.json(); })
    .then(function(d) {
        if (!d.ok) { toast(d.msg, 'error'); return; }
        renderFileList(d.files, path);
        updateBreadcrumb(path);
    });
}

function renderFileList(files, basePath) {
    var html = '';
    if (basePath) {
        var parent = basePath.split('/').slice(0, -1).join('/');
        html += '<li class="file-item"><div class="item-row"><a onclick="loadDir(' + JSON.stringify(parent) + ')"><span class="icon">📁</span> ..</a></div></li>';
    }
    files.forEach(function(f) {
        var icon = f.type === 'dir' ? '📁' : '📄';
        var cls = f.type === 'dir' ? 'dir' : 'file';
        var size = f.type === 'file' ? '<span class="size">' + formatSize(f.size || 0) + '</span>' : '';
        var downloadUrl = '/gh/api/download' + (f.type === 'dir' ? '-dir' : '') + '?url=' + encodeURIComponent(repoUrl) + '&path=' + encodeURIComponent(f.path);
        if (f.type === 'dir') {
            html += '<li class="file-item"><div class="item-row"><a onclick="loadDir(' + JSON.stringify(f.path) + ')"><span class="icon ' + cls + '">' + icon + '</span> ' + f.name + '</a><a href="' + downloadUrl + '" class="download-btn" title="下载文件夹"><span>⬇</span></a></div></li>';
        } else {
            html += '<li class="file-item"><div class="item-row"><a onclick="viewFile(' + JSON.stringify(f.path) + ')"><span class="icon ' + cls + '">' + icon + '</span> ' + f.name + '</a>' + size + '<a href="' + downloadUrl + '" class="download-btn" title="下载文件"><span>⬇</span></a></div></li>';
        }
    });
    document.getElementById('fileList').innerHTML = html;
}

function updateBreadcrumb(path) {
    var parts = path.split('/').filter(Boolean);
    var html = '<a href="/gh?url=' + encodeURIComponent(repoUrl) + '">' + repoOwner + '/' + repoRepo + '</a>';
    var cum = '';
    parts.forEach(function(p) {
        cum += '/' + p;
        html += ' / <a onclick="loadDir(' + JSON.stringify(cum.substring(1)) + ')" style="cursor:pointer;color:#38bdf8">' + p + '</a>';
    });
    document.getElementById('breadcrumb').innerHTML = html;
}

function viewFile(path) {
    currentPath = path;
    var main = document.getElementById('mainContent');
    main.innerHTML = '<div class="loading">加载中...</div>';

    fetch('/gh/api/file?url=' + encodeURIComponent(repoUrl) + '&path=' + encodeURIComponent(path))
    .then(function(r) { return r.json(); })
    .then(function(d) {
        if (!d.ok) { main.innerHTML = '<div class="empty">' + d.msg + '</div>'; return; }
        if (d.too_large) {
            main.innerHTML = '<div class="empty"><p>' + d.content + '</p><p style="margin-top:12px"><a href="/gh/api/download?url=' + encodeURIComponent(repoUrl) + '&path=' + encodeURIComponent(path) + '" class="btn btn-primary">下载文件</a></p></div>';
            return;
        }
        var lines = d.content.split('\n');
        var html = '<div class="code-view"><div class="code-header"><span class="lang">' + d.lang + '</span><span style="font-size:12px;color:#94a3b8">' + lines.length + ' 行 | ' + formatSize(d.size) + '</span></div><div class="code-body"><pre>';
        lines.forEach(function(line, i) {
            html += '<div class="line"><span class="line-num">' + (i + 1) + '</span>' + escapeHtml(line) + '</div>';
        });
        html += '</pre></div></div>';
        main.innerHTML = html;
        updateBreadcrumbForFile(path);
    });
}

function updateBreadcrumbForFile(path) {
    var parts = path.split('/').filter(Boolean);
    var html = '<a href="/gh?url=' + encodeURIComponent(repoUrl) + '">' + repoOwner + '/' + repoRepo + '</a>';
    var cum = '';
    parts.forEach(function(p, i) {
        cum += '/' + p;
        if (i < parts.length - 1) {
            html += ' / <a onclick="loadDir(' + JSON.stringify(cum.substring(1)) + ')" style="cursor:pointer;color:#38bdf8">' + p + '</a>';
        } else {
            html += ' / <span style="color:#94a3b8">' + p + '</span>';
        }
    });
    document.getElementById('breadcrumb').innerHTML = html;
}

function escapeHtml(text) {
    return text.replace(/&/g, '&amp;').replace(/</g, '&lt;').replace(/>/g, '&gt;').replace(/"/g, '&quot;');
}

function formatSize(bytes) {
    if (bytes < 1024) return bytes + ' B';
    if (bytes < 1048576) return (bytes / 1024).toFixed(1) + ' KB';
    return (bytes / 1048576).toFixed(1) + ' MB';
}
</script>
</body>
</html>"""

# ── 前端页面 ───────────────────────────────────────────────

HTML = r"""<!DOCTYPE html>
<html lang="zh-CN">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width, initial-scale=1.0">
<title>代理管理面板</title>
<style>
*{margin:0;padding:0;box-sizing:border-box}
body{font-family:-apple-system,BlinkMacSystemFont,'Segoe UI',Roboto,sans-serif;background:#0f172a;color:#e2e8f0;min-height:100vh}
.header{background:#1e293b;padding:16px 24px;display:flex;align-items:center;justify-content:space-between;border-bottom:1px solid #334155}
.header h1{font-size:20px;color:#38bdf8}
.header .ip{font-size:13px;color:#94a3b8}
.container{max-width:1200px;margin:0 auto;padding:20px}
.grid{display:grid;grid-template-columns:repeat(auto-fit,minmax(500px,1fr));gap:20px}
.card{background:#1e293b;border-radius:12px;border:1px solid #334155;overflow:hidden}
.card-header{padding:14px 20px;background:#162032;display:flex;align-items:center;justify-content:space-between;border-bottom:1px solid #334155}
.card-header h2{font-size:16px;font-weight:600}
.card-body{padding:16px 20px}
.status-dot{width:10px;height:10px;border-radius:50%;display:inline-block;margin-right:8px}
.status-on{background:#22c55e;box-shadow:0 0 8px #22c55e}
.status-off{background:#ef4444;box-shadow:0 0 8px #ef4444}
.info-row{display:flex;justify-content:space-between;padding:6px 0;border-bottom:1px solid #1e293b;font-size:14px}
.info-row .label{color:#94a3b8}
.info-row .value{color:#e2e8f0;font-family:monospace;word-break:break-all;text-align:right;max-width:60%}
.btn-group{display:flex;gap:8px;margin-top:12px;flex-wrap:wrap}
.btn{padding:8px 16px;border:none;border-radius:8px;cursor:pointer;font-size:13px;font-weight:500;transition:all .2s}
.btn:hover{opacity:.85;transform:translateY(-1px)}
.btn-primary{background:#3b82f6;color:#fff}
.btn-success{background:#22c55e;color:#fff}
.btn-danger{background:#ef4444;color:#fff}
.btn-warning{background:#f59e0b;color:#000}
.btn-info{background:#06b6d4;color:#fff}
.btn-outline{background:transparent;border:1px solid #475569;color:#94a3b8}
.btn-outline:hover{background:#334155;color:#e2e8f0}
.btn-sm{padding:4px 10px;font-size:12px}
.qr-section{text-align:center;margin-top:12px}
.qr-section img{max-width:180px;border-radius:8px;background:#fff;padding:8px}
.link-box{background:#0f172a;border:1px solid #334155;border-radius:8px;padding:10px;font-family:monospace;font-size:12px;word-break:break-all;margin-top:8px;color:#38bdf8;user-select:all}
.copy-btn{cursor:pointer;color:#94a3b8;font-size:12px;margin-left:8px}
.copy-btn:hover{color:#38bdf8}
.stats-grid{display:grid;grid-template-columns:repeat(3,1fr);gap:8px;margin-top:8px}
.stat-item{background:#0f172a;border-radius:8px;padding:10px;text-align:center}
.stat-item .num{font-size:20px;font-weight:700;color:#38bdf8}
.stat-item .txt{font-size:11px;color:#94a3b8;margin-top:2px}
.log-box{background:#0f172a;border-radius:8px;padding:12px;font-family:monospace;font-size:12px;max-height:200px;overflow-y:auto;white-space:pre-wrap;line-height:1.5;color:#94a3b8;margin-top:8px}
.modal{display:none;position:fixed;top:0;left:0;width:100%;height:100%;background:rgba(0,0,0,.7);z-index:1000;align-items:center;justify-content:center}
.modal.show{display:flex}
.modal-content{background:#1e293b;border-radius:12px;padding:24px;width:90%;max-width:500px;border:1px solid #334155}
.modal-content h3{font-size:18px;margin-bottom:12px;color:#38bdf8}
.modal-content input{width:100%;padding:10px;background:#0f172a;border:1px solid #334155;border-radius:8px;color:#e2e8f0;margin-bottom:10px;font-size:14px}
.toast{position:fixed;top:20px;right:20px;padding:12px 20px;border-radius:8px;font-size:14px;z-index:2000;animation:slideIn .3s}
.toast-success{background:#22c55e;color:#fff}
.toast-error{background:#ef4444;color:#fff}
@keyframes slideIn{from{transform:translateX(100px);opacity:0}to{transform:translateX(0);opacity:1}}
.loading{text-align:center;padding:20px;color:#94a3b8}
.refresh-indicator{font-size:12px;color:#94a3b8}
@media(max-width:600px){.grid{grid-template-columns:1fr}.btn-group{flex-direction:column}}
</style>
</head>
<body>
<div class="header">
    <h1>代理管理面板</h1>
    <div>
        <a href="/gh" style="color:#22c55e;text-decoration:none;margin-right:16px;font-size:14px">GitHub加速</a>
        <a href="/dl" style="color:#38bdf8;text-decoration:none;margin-right:16px;font-size:14px">⚡ 下载加速</a>
        <span class="refresh-indicator" id="refreshTime"></span>
        <button class="btn btn-outline btn-sm" onclick="refreshAll()" style="margin-left:12px">刷新</button>
    </div>
</div>
<div class="container">
    <div class="grid">
        <!-- SS 卡片 -->
        <div class="card">
            <div class="card-header">
                <h2><span class="status-dot" id="ssDot"></span>Shadowsocks 代理</h2>
                <span style="font-size:13px;color:#94a3b8" id="ssPort"></span>
            </div>
            <div class="card-body">
                <div class="info-row"><span class="label">服务器</span><span class="value" id="ssServer"></span></div>
                <div class="info-row"><span class="label">密码</span><span class="value" id="ssPass"></span></div>
                <div class="info-row"><span class="label">加密</span><span class="value" id="ssCipher"></span></div>
                <div class="info-row"><span class="label">运行时间</span><span class="value" id="ssUptime"></span></div>
                <div class="info-row"><span class="label">内存</span><span class="value" id="ssMem"></span></div>
                <div class="info-row"><span class="label">活跃连接</span><span class="value" id="ssConn"></span></div>
                <div class="link-box" id="ssUri" style="margin-top:10px"></div>
                <div class="btn-group">
                    <button class="btn btn-success" onclick="ssAction('start')">启动</button>
                    <button class="btn btn-danger" onclick="ssAction('stop')">停止</button>
                    <button class="btn btn-warning" onclick="ssAction('restart')">重启</button>
                    <button class="btn btn-info" onclick="loadSSQR()">二维码</button>
                    <button class="btn btn-outline" onclick="ssAction('fix')">一键修复</button>
                    <button class="btn btn-outline" onclick="toggleSSLogs()">日志</button>
                </div>
                <div id="ssQR" class="qr-section"></div>
                <div id="ssLogs" class="log-box" style="display:none"></div>
            </div>
        </div>

        <!-- TG 卡片 -->
        <div class="card">
            <div class="card-header">
                <h2><span class="status-dot" id="tgDot"></span>TG 代理 (MTProto)</h2>
                <span style="font-size:13px;color:#94a3b8" id="tgPort"></span>
            </div>
            <div class="card-body">
                <div class="info-row"><span class="label">服务器</span><span class="value" id="tgServer"></span></div>
                <div class="info-row"><span class="label">密钥</span><span class="value" id="tgSecret"></span></div>
                <div class="info-row"><span class="label">运行时间</span><span class="value" id="tgUptime"></span></div>
                <div class="stats-grid" id="tgStatsGrid"></div>
                <div class="link-box" id="tgLink" style="margin-top:10px"></div>
                <div class="btn-group">
                    <button class="btn btn-success" onclick="tgAction('start')">启动</button>
                    <button class="btn btn-danger" onclick="tgAction('stop')">停止</button>
                    <button class="btn btn-warning" onclick="tgAction('restart')">重启</button>
                    <button class="btn btn-info" onclick="loadTGQR()">二维码</button>
                    <button class="btn btn-outline" onclick="tgAction('fix')">一键修复</button>
                    <button class="btn btn-outline" onclick="toggleTGLogs()">日志</button>
                </div>
                <div id="tgQR" class="qr-section"></div>
                <div id="tgLogs" class="log-box" style="display:none"></div>
            </div>
        </div>
    </div>
</div>

<div id="toast" class="toast"></div>

<script>
function toast(msg, type) {
    const t = document.getElementById('toast');
    t.textContent = msg;
    t.className = 'toast toast-' + (type || 'success');
    setTimeout(() => t.className = 'toast', 2500);
}

async function refreshAll() {
    try {
        const r = await fetch('/api/status');
        const d = await r.json();
        updateSS(d.ss);
        updateTG(d.tg, d.tg_stats);
        document.getElementById('refreshTime').textContent = new Date().toLocaleTimeString();
    } catch(e) {
        console.error(e);
    }
}

function updateSS(s) {
    const running = s.pid !== '未知' && s.pid !== '';
    document.getElementById('ssDot').className = 'status-dot ' + (running ? 'status-on' : 'status-off');
    document.getElementById('ssPort').textContent = '端口 ' + s.port;
    document.getElementById('ssServer').textContent = s.server;
    document.getElementById('ssPass').textContent = s.password;
    document.getElementById('ssCipher').textContent = s.cipher;
    document.getElementById('ssUptime').textContent = s.uptime;
    document.getElementById('ssMem').textContent = s.mem;
    document.getElementById('ssConn').textContent = s.active_conn;
    const b64 = btoa(s.cipher + ':' + s.password).replace(/=/g, '');
    document.getElementById('ssUri').textContent = 'ss://' + b64 + '@' + s.server + ':' + s.port;
}

function updateTG(t, stats) {
    document.getElementById('tgDot').className = 'status-dot ' + (t.running ? 'status-on' : 'status-off');
    document.getElementById('tgPort').textContent = '端口 ' + t.port;
    document.getElementById('tgServer').textContent = t.server;
    document.getElementById('tgSecret').textContent = t.secret;
    document.getElementById('tgUptime').textContent = t.uptime;
    document.getElementById('tgLink').textContent = t.link || '';

    if (stats && Object.keys(stats).length > 0) {
        const keys = [
            ['inbound_connections_accepted', '入站连接'],
            ['active_outbound_connections', '活跃出站'],
            ['total_encrypted_connections', '加密连接'],
            ['tot_forwarded_queries', '转发查询'],
            ['ready_targets', '就绪DC'],
            ['total_connections', '总连接']
        ];
        let html = '';
        keys.forEach(([k, label]) => {
            html += '<div class="stat-item"><div class="num">' + (stats[k] || '0') + '</div><div class="txt">' + label + '</div></div>';
        });
        document.getElementById('tgStatsGrid').innerHTML = html;
    }
}

async function ssAction(action) {
    try {
        const r = await fetch('/api/ss/' + action, { method: 'POST' });
        const d = await r.json();
        toast(d.msg || '操作完成', d.ok ? 'success' : 'error');
        setTimeout(refreshAll, 1500);
    } catch(e) {
        toast('请求失败: ' + e, 'error');
    }
}

async function tgAction(action) {
    try {
        const r = await fetch('/api/tg/' + action, { method: 'POST' });
        const d = await r.json();
        toast(d.msg || '操作完成', d.ok ? 'success' : 'error');
        setTimeout(refreshAll, 1500);
    } catch(e) {
        toast('请求失败: ' + e, 'error');
    }
}

async function loadSSQR() {
    try {
        const r = await fetch('/api/ss/qr');
        const d = await r.json();
        const el = document.getElementById('ssQR');
        el.innerHTML = d.qr ? '<img src="data:image/png;base64,' + d.qr + '">' : '<p>无法生成二维码</p>';
        el.style.display = 'block';
    } catch(e) {}
}

async function loadTGQR() {
    try {
        const r = await fetch('/api/tg/qr');
        const d = await r.json();
        const el = document.getElementById('tgQR');
        el.innerHTML = d.qr ? '<img src="data:image/png;base64,' + d.qr + '">' : '<p>无法生成二维码</p>';
        el.style.display = 'block';
    } catch(e) {}
}

async function toggleSSLogs() {
    const el = document.getElementById('ssLogs');
    if (el.style.display === 'none') {
        try {
            const r = await fetch('/api/ss/logs');
            const d = await r.json();
            el.textContent = d.logs;
        } catch(e) {}
        el.style.display = 'block';
    } else {
        el.style.display = 'none';
    }
}

async function toggleTGLogs() {
    const el = document.getElementById('tgLogs');
    if (el.style.display === 'none') {
        try {
            const r = await fetch('/api/tg/logs');
            const d = await r.json();
            el.textContent = d.logs;
        } catch(e) {}
        el.style.display = 'block';
    } else {
        el.style.display = 'none';
    }
}

refreshAll();
setInterval(refreshAll, 30000);
</script>
</body>
</html>"""

@app.route("/")
def index():
    return render_template_string(HTML)

if __name__ == "__main__":
    from waitress import serve
    print(" * Running on http://0.0.0.0:9090/ (waitress)")
    serve(app, host="0.0.0.0", port=9090, threads=8)