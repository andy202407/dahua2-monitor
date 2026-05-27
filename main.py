"""
远程监控服务端 - 部署到 Render/Railway

功能：
- 接收本地脚本 POST 的日志和截图
- WebSocket 实时推送到网页
- 响应式网页，手机电脑都能看

部署到 Render:
1. 把 server/ 目录推到一个单独的 GitHub 仓库
2. Render 上 New Web Service → 连接仓库
3. Build Command: pip install -r requirements.txt
4. Start Command: uvicorn main:app --host 0.0.0.0 --port $PORT
"""

import json
import asyncio
from datetime import datetime
from fastapi import FastAPI, WebSocket, WebSocketDisconnect, Request
from fastapi.responses import HTMLResponse

app = FastAPI()

# 存储连接的 WebSocket 客户端
clients: set = set()

# 最近的日志（保留最新200条）
log_history: list = []
MAX_HISTORY = 200

# 最近的截图（base64）
last_screenshot: str = ""


HTML_PAGE = """<!DOCTYPE html>
<html lang="zh">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width, initial-scale=1.0">
<title>大话2 远程监控</title>
<style>
* { margin: 0; padding: 0; box-sizing: border-box; }
body { font-family: -apple-system, sans-serif; background: #1a1a2e; color: #eee; padding: 12px; }
h1 { font-size: 1.2rem; margin-bottom: 12px; color: #a78bfa; }
.status { font-size: 0.85rem; color: #888; margin-bottom: 8px; }
.status .dot { display: inline-block; width: 8px; height: 8px; border-radius: 50%; margin-right: 4px; }
.dot.on { background: #4ade80; }
.dot.off { background: #f87171; }
#screenshot { width: 100%; max-width: 800px; border-radius: 8px; border: 1px solid #333; margin-bottom: 12px; display: none; }
#log-box { background: #0f0f1a; border: 1px solid #333; border-radius: 8px; padding: 10px; height: 55vh; overflow-y: auto; font-family: Consolas, monospace; font-size: 0.8rem; line-height: 1.6; }
.log-line { border-bottom: 1px solid #1a1a2e; padding: 2px 0; word-break: break-all; }
.log-warn { color: #fbbf24; }
.log-err { color: #f87171; }
.time { color: #666; margin-right: 6px; font-size: 0.75rem; }
@media (min-width: 768px) { body { padding: 24px; } h1 { font-size: 1.5rem; } #log-box { height: 60vh; font-size: 0.85rem; } }
</style>
</head>
<body>
<h1>🎮 大话2 远程监控</h1>
<div class="status"><span class="dot off" id="dot"></span><span id="ws-status">连接中...</span></div>
<img id="screenshot" />
<div id="log-box"></div>
<script>
const logBox = document.getElementById('log-box');
const screenshot = document.getElementById('screenshot');
const dot = document.getElementById('dot');
const wsStatus = document.getElementById('ws-status');

function connect() {
    const proto = location.protocol === 'https:' ? 'wss:' : 'ws:';
    const ws = new WebSocket(proto + '//' + location.host + '/ws');
    ws.onopen = () => { dot.className = 'dot on'; wsStatus.textContent = '已连接'; };
    ws.onclose = () => { dot.className = 'dot off'; wsStatus.textContent = '断开，重连中...'; setTimeout(connect, 3000); };
    ws.onmessage = (e) => {
        const msg = JSON.parse(e.data);
        if (msg.type === 'log') {
            const div = document.createElement('div');
            div.className = 'log-line';
            if (msg.text.includes('⚠') || msg.text.includes('WARN')) div.className += ' log-warn';
            if (msg.text.includes('错误') || msg.text.includes('ERROR') || msg.text.includes('失败')) div.className += ' log-err';
            const time = msg.time || '';
            div.innerHTML = '<span class="time">' + time + '</span>' + msg.text;
            logBox.appendChild(div);
            logBox.scrollTop = logBox.scrollHeight;
            if (logBox.children.length > 500) logBox.removeChild(logBox.firstChild);
        } else if (msg.type === 'screenshot') {
            screenshot.src = 'data:image/jpeg;base64,' + msg.data;
            screenshot.style.display = 'block';
        } else if (msg.type === 'history') {
            msg.logs.forEach(log => {
                const div = document.createElement('div');
                div.className = 'log-line';
                if (log.text.includes('⚠')) div.className += ' log-warn';
                if (log.text.includes('错误') || log.text.includes('失败')) div.className += ' log-err';
                div.innerHTML = '<span class="time">' + (log.time || '') + '</span>' + log.text;
                logBox.appendChild(div);
            });
            logBox.scrollTop = logBox.scrollHeight;
            if (msg.screenshot) {
                screenshot.src = 'data:image/jpeg;base64,' + msg.screenshot;
                screenshot.style.display = 'block';
            }
        }
    };
}
connect();
</script>
</body>
</html>"""


@app.get("/", response_class=HTMLResponse)
async def index():
    return HTML_PAGE


@app.websocket("/ws")
async def websocket_endpoint(websocket: WebSocket):
    await websocket.accept()
    clients.add(websocket)
    try:
        # 发送历史日志
        await websocket.send_json({
            "type": "history",
            "logs": log_history[-100:],
            "screenshot": last_screenshot if last_screenshot else None
        })
        # 保持连接
        while True:
            await websocket.receive_text()
    except WebSocketDisconnect:
        pass
    finally:
        clients.discard(websocket)


@app.post("/api/log")
async def receive_log(request: Request):
    """接收本地脚本推送的日志"""
    data = await request.json()
    text = data.get("text", "")
    time_str = datetime.now().strftime("%H:%M:%S")

    entry = {"text": text, "time": time_str}
    log_history.append(entry)
    if len(log_history) > MAX_HISTORY:
        log_history.pop(0)

    # 广播到所有 WebSocket 客户端
    msg = json.dumps({"type": "log", "text": text, "time": time_str}, ensure_ascii=False)
    dead = set()
    for ws in clients:
        try:
            await ws.send_text(msg)
        except Exception:
            dead.add(ws)
    clients -= dead

    return {"ok": True}


@app.post("/api/screenshot")
async def receive_screenshot(request: Request):
    """接收本地脚本推送的截图（base64 JPEG）"""
    global last_screenshot
    data = await request.json()
    b64 = data.get("data", "")
    last_screenshot = b64

    # 广播到所有 WebSocket 客户端
    msg = json.dumps({"type": "screenshot", "data": b64})
    dead = set()
    for ws in clients:
        try:
            await ws.send_text(msg)
        except Exception:
            dead.add(ws)
    clients -= dead

    return {"ok": True}


@app.get("/api/status")
async def status():
    """健康检查"""
    return {
        "status": "running",
        "clients": len(clients),
        "logs": len(log_history)
    }
