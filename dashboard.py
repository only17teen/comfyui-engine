"""Real-time web dashboard for ComfyUI Engine monitoring and control.

Built with FastAPI + WebSocket for real-time updates.
Serves a self-contained HTML dashboard with live metrics,
job queue visualization, and remote control capabilities.
"""

import asyncio
import json
import logging
import os
import time
from contextlib import asynccontextmanager
from dataclasses import asdict, dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, List, Optional, Set

import aiohttp
from fastapi import FastAPI, WebSocket, WebSocketDisconnect
from fastapi.responses import HTMLResponse, JSONResponse
from fastapi.staticfiles import StaticFiles

logger = logging.getLogger(__name__)


# Dashboard HTML - self-contained, no external dependencies
DASHBOARD_HTML = """
<!DOCTYPE html>
<html lang="en">
<head>
    <meta charset="UTF-8">
    <meta name="viewport" content="width=device-width, initial-scale=1.0">
    <title>ComfyUI Engine Dashboard</title>
    <style>
        :root {
            --bg: #0f172a;
            --card: #1e293b;
            --text: #e2e8f0;
            --muted: #94a3b8;
            --accent: #3b82f6;
            --success: #22c55e;
            --warning: #f59e0b;
            --danger: #ef4444;
            --border: #334155;
        }
        * { margin: 0; padding: 0; box-sizing: border-box; }
        body {
            font-family: -apple-system, BlinkMacSystemFont, 'Segoe UI', Roboto, sans-serif;
            background: var(--bg);
            color: var(--text);
            line-height: 1.6;
        }
        .header {
            background: var(--card);
            border-bottom: 1px solid var(--border);
            padding: 1rem 2rem;
            display: flex;
            justify-content: space-between;
            align-items: center;
            position: sticky;
            top: 0;
            z-index: 100;
        }
        .header h1 { font-size: 1.5rem; font-weight: 600; }
        .status-badge {
            padding: 0.25rem 0.75rem;
            border-radius: 9999px;
            font-size: 0.875rem;
            font-weight: 500;
        }
        .status-online { background: var(--success); color: #000; }
        .status-offline { background: var(--danger); color: #fff; }
        .container { padding: 2rem; max-width: 1400px; margin: 0 auto; }
        .grid {
            display: grid;
            grid-template-columns: repeat(auto-fit, minmax(280px, 1fr));
            gap: 1.5rem;
            margin-bottom: 2rem;
        }
        .card {
            background: var(--card);
            border: 1px solid var(--border);
            border-radius: 0.75rem;
            padding: 1.5rem;
        }
        .card h3 {
            font-size: 0.875rem;
            text-transform: uppercase;
            letter-spacing: 0.05em;
            color: var(--muted);
            margin-bottom: 0.5rem;
        }
        .metric-value {
            font-size: 2.5rem;
            font-weight: 700;
            color: var(--accent);
        }
        .metric-delta {
            font-size: 0.875rem;
            margin-top: 0.25rem;
        }
        .metric-delta.up { color: var(--success); }
        .metric-delta.down { color: var(--danger); }
        .section-title {
            font-size: 1.25rem;
            font-weight: 600;
            margin-bottom: 1rem;
            display: flex;
            align-items: center;
            gap: 0.5rem;
        }
        .queue-list {
            list-style: none;
        }
        .queue-item {
            display: flex;
            justify-content: space-between;
            align-items: center;
            padding: 0.75rem;
            border-bottom: 1px solid var(--border);
            transition: background 0.2s;
        }
        .queue-item:hover { background: rgba(255,255,255,0.05); }
        .queue-item:last-child { border-bottom: none; }
        .job-id { font-family: monospace; font-size: 0.875rem; color: var(--muted); }
        .job-status {
            padding: 0.25rem 0.5rem;
            border-radius: 0.25rem;
            font-size: 0.75rem;
            font-weight: 500;
            text-transform: uppercase;
        }
        .status-pending { background: var(--warning); color: #000; }
        .status-running { background: var(--accent); color: #fff; }
        .status-completed { background: var(--success); color: #000; }
        .status-failed { background: var(--danger); color: #fff; }
        .chart-container {
            height: 200px;
            margin-top: 1rem;
            position: relative;
        }
        .chart-canvas {
            width: 100%;
            height: 100%;
        }
        .controls {
            display: flex;
            gap: 0.5rem;
            margin-bottom: 1rem;
        }
        .btn {
            padding: 0.5rem 1rem;
            border: 1px solid var(--border);
            background: var(--card);
            color: var(--text);
            border-radius: 0.5rem;
            cursor: pointer;
            font-size: 0.875rem;
            transition: all 0.2s;
        }
        .btn:hover { background: var(--border); }
        .btn-primary { background: var(--accent); border-color: var(--accent); }
        .btn-primary:hover { background: #2563eb; }
        .btn-danger { background: var(--danger); border-color: var(--danger); }
        .btn-danger:hover { background: #dc2626; }
        .log-viewer {
            background: #000;
            border-radius: 0.5rem;
            padding: 1rem;
            font-family: 'Courier New', monospace;
            font-size: 0.875rem;
            height: 300px;
            overflow-y: auto;
            white-space: pre-wrap;
            word-break: break-all;
        }
        .log-entry { margin-bottom: 0.25rem; }
        .log-time { color: var(--muted); }
        .log-info { color: var(--accent); }
        .log-error { color: var(--danger); }
        .log-warn { color: var(--warning); }
        .websocket-indicator {
            width: 8px;
            height: 8px;
            border-radius: 50%;
            display: inline-block;
            margin-right: 0.5rem;
        }
        .ws-connected { background: var(--success); }
        .ws-disconnected { background: var(--danger); }
        @keyframes pulse {
            0%, 100% { opacity: 1; }
            50% { opacity: 0.5; }
        }
        .pulse { animation: pulse 2s infinite; }
    </style>
</head>
<body>
    <div class="header">
        <h1>ComfyUI Engine Dashboard</h1>
        <div>
            <span id="ws-indicator" class="websocket-indicator ws-disconnected"></span>
            <span id="status-badge" class="status-badge status-offline">Offline</span>
        </div>
    </div>

    <div class="container">
        <div class="grid">
            <div class="card">
                <h3>Queue Length</h3>
                <div class="metric-value" id="queue-length">0</div>
                <div class="metric-delta" id="queue-delta">-</div>
            </div>
            <div class="card">
                <h3>Active Jobs</h3>
                <div class="metric-value" id="active-jobs">0</div>
                <div class="metric-delta" id="jobs-delta">-</div>
            </div>
            <div class="card">
                <h3>Completed Today</h3>
                <div class="metric-value" id="completed-today">0</div>
                <div class="metric-delta up" id="completed-rate">0/hr</div>
            </div>
            <div class="card">
                <h3>Avg Generation Time</h3>
                <div class="metric-value" id="avg-time">0s</div>
                <div class="metric-delta" id="time-delta">-</div>
            </div>
            <div class="card">
                <h3>Success Rate</h3>
                <div class="metric-value" id="success-rate">100%</div>
                <div class="metric-delta" id="success-delta">-</div>
            </div>
            <div class="card">
                <h3>GPU Utilization</h3>
                <div class="metric-value" id="gpu-util">0%</div>
                <div class="metric-delta" id="gpu-delta">-</div>
            </div>
        </div>

        <div class="card">
            <div class="section-title">
                Job Queue
                <span id="queue-count" class="status-badge status-pending">0 pending</span>
            </div>
            <div class="controls">
                <button class="btn btn-primary" onclick="pauseQueue()">Pause</button>
                <button class="btn" onclick="resumeQueue()">Resume</button>
                <button class="btn btn-danger" onclick="clearQueue()">Clear All</button>
                <button class="btn" onclick="refreshQueue()">Refresh</button>
            </div>
            <ul class="queue-list" id="queue-list">
                <li class="queue-item">Loading...</li>
            </ul>
        </div>

        <div class="grid">
            <div class="card">
                <div class="section-title">Throughput (last 60 min)</div>
                <div class="chart-container">
                    <canvas id="throughput-chart" class="chart-canvas"></canvas>
                </div>
            </div>
            <div class="card">
                <div class="section-title">Error Rate</div>
                <div class="chart-container">
                    <canvas id="error-chart" class="chart-canvas"></canvas>
                </div>
            </div>
        </div>

        <div class="card">
            <div class="section-title">Live Logs</div>
            <div class="controls">
                <button class="btn" onclick="clearLogs()">Clear</button>
                <button class="btn" onclick="toggleAutoScroll()">Auto-scroll: ON</button>
                <select class="btn" id="log-level" onchange="setLogLevel()">
                    <option value="DEBUG">DEBUG</option>
                    <option value="INFO" selected>INFO</option>
                    <option value="WARNING">WARNING</option>
                    <option value="ERROR">ERROR</option>
                </select>
            </div>
            <div class="log-viewer" id="log-viewer"></div>
        </div>
    </div>

    <script>
        let ws = null;
        let reconnectInterval = 1000;
        let maxReconnectInterval = 30000;
        let autoScroll = true;
        let logLevel = 'INFO';
        let metrics = {
            queue: [],
            history: [],
            throughput: [],
            errors: []
        };

        function connect() {
            const protocol = window.location.protocol === 'https:' ? 'wss:' : 'ws:';
            ws = new WebSocket(`${protocol}//${window.location.host}/ws`);

            ws.onopen = () => {
                console.log('WebSocket connected');
                document.getElementById('ws-indicator').className = 'websocket-indicator ws-connected';
                document.getElementById('status-badge').className = 'status-badge status-online';
                document.getElementById('status-badge').textContent = 'Online';
                reconnectInterval = 1000;
            };

            ws.onmessage = (event) => {
                const data = JSON.parse(event.data);
                handleMessage(data);
            };

            ws.onclose = () => {
                document.getElementById('ws-indicator').className = 'websocket-indicator ws-disconnected';
                document.getElementById('status-badge').className = 'status-badge status-offline';
                document.getElementById('status-badge').textContent = 'Offline';
                setTimeout(connect, reconnectInterval);
                reconnectInterval = Math.min(reconnectInterval * 2, maxReconnectInterval);
            };

            ws.onerror = (err) => {
                console.error('WebSocket error:', err);
            };
        }

        function handleMessage(data) {
            switch (data.type) {
                case 'metrics':
                    updateMetrics(data.payload);
                    break;
                case 'queue':
                    updateQueue(data.payload);
                    break;
                case 'log':
                    appendLog(data.payload);
                    break;
                case 'history':
                    updateCharts(data.payload);
                    break;
            }
        }

        function updateMetrics(m) {
            document.getElementById('queue-length').textContent = m.queue_length || 0;
            document.getElementById('active-jobs').textContent = m.active_jobs || 0;
            document.getElementById('completed-today').textContent = m.completed_today || 0;
            document.getElementById('avg-time').textContent = (m.avg_generation_time || 0).toFixed(1) + 's';
            document.getElementById('success-rate').textContent = (m.success_rate || 100).toFixed(1) + '%';
            document.getElementById('gpu-util').textContent = (m.gpu_utilization || 0).toFixed(0) + '%';
        }

        function updateQueue(queue) {
            const list = document.getElementById('queue-list');
            const pending = queue.filter(j => j.status === 'pending').length;
            document.getElementById('queue-count').textContent = `${pending} pending`;

            if (queue.length === 0) {
                list.innerHTML = '<li class="queue-item">No jobs in queue</li>';
                return;
            }

            list.innerHTML = queue.map(job => `
                <li class="queue-item">
                    <div>
                        <div class="job-id">${job.id}</div>
                        <div style="font-size: 0.875rem; color: var(--muted);">${job.prompt || 'No prompt'}</div>
                    </div>
                    <span class="job-status status-${job.status}">${job.status}</span>
                </li>
            `).join('');
        }

        function appendLog(log) {
            if (['DEBUG', 'INFO', 'WARNING', 'ERROR'].indexOf(log.level) < ['DEBUG', 'INFO', 'WARNING', 'ERROR'].indexOf(logLevel)) {
                return;
            }
            const viewer = document.getElementById('log-viewer');
            const entry = document.createElement('div');
            entry.className = 'log-entry';
            entry.innerHTML = `<span class="log-time">${log.time}</span> <span class="log-${log.level.toLowerCase()}">[${log.level}]</span> ${log.message}`;
            viewer.appendChild(entry);
            if (autoScroll) viewer.scrollTop = viewer.scrollHeight;
            while (viewer.children.length > 1000) viewer.removeChild(viewer.firstChild);
        }

        function updateCharts(history) {
            drawChart('throughput-chart', history.throughput || [], '#3b82f6');
            drawChart('error-chart', history.errors || [], '#ef4444');
        }

        function drawChart(canvasId, data, color) {
            const canvas = document.getElementById(canvasId);
            const ctx = canvas.getContext('2d');
            const dpr = window.devicePixelRatio || 1;
            const rect = canvas.getBoundingClientRect();
            canvas.width = rect.width * dpr;
            canvas.height = rect.height * dpr;
            ctx.scale(dpr, dpr);

            const w = rect.width, h = rect.height;
            ctx.clearRect(0, 0, w, h);

            if (data.length < 2) return;

            const max = Math.max(...data, 1);
            const step = w / (data.length - 1);

            ctx.beginPath();
            ctx.moveTo(0, h - (data[0] / max) * h);
            for (let i = 1; i < data.length; i++) {
                ctx.lineTo(i * step, h - (data[i] / max) * h);
            }
            ctx.strokeStyle = color;
            ctx.lineWidth = 2;
            ctx.stroke();

            // Fill area
            ctx.lineTo(w, h);
            ctx.lineTo(0, h);
            ctx.closePath();
            ctx.fillStyle = color + '20';
            ctx.fill();
        }

        function pauseQueue() { sendCommand('pause'); }
        function resumeQueue() { sendCommand('resume'); }
        function clearQueue() { if (confirm('Clear all jobs?')) sendCommand('clear'); }
        function refreshQueue() { sendCommand('refresh'); }
        function clearLogs() { document.getElementById('log-viewer').innerHTML = ''; }
        function toggleAutoScroll() { autoScroll = !autoScroll; event.target.textContent = `Auto-scroll: ${autoScroll ? 'ON' : 'OFF'}`; }
        function setLogLevel() { logLevel = document.getElementById('log-level').value; }

        function sendCommand(cmd) {
            if (ws && ws.readyState === WebSocket.OPEN) {
                ws.send(JSON.stringify({ type: 'command', action: cmd }));
            }
        }

        // Initial load
        fetch('/api/metrics').then(r => r.json()).then(m => updateMetrics(m));
        fetch('/api/queue').then(r => r.json()).then(q => updateQueue(q));

        connect();
        window.addEventListener('resize', () => updateCharts(metrics));
    </script>
</body>
</html>
"""


@dataclass
class DashboardMetrics:
    """Current engine metrics for dashboard display."""
    queue_length: int = 0
    active_jobs: int = 0
    completed_today: int = 0
    avg_generation_time: float = 0.0
    success_rate: float = 100.0
    gpu_utilization: float = 0.0
    memory_used: float = 0.0
    memory_total: float = 0.0
    timestamp: float = field(default_factory=time.time)


@dataclass
class JobInfo:
    """Job information for queue display."""
    id: str
    status: str  # pending, running, completed, failed
    prompt: str = ""
    progress: float = 0.0
    start_time: float | None = None
    elapsed: float = 0.0


class DashboardServer:
    """FastAPI-based dashboard server with WebSocket support."""

    def __init__(
        self,
        host: str = "0.0.0.0",
        port: int = 8080,
        engine_client: Any | None = None,
        metrics_endpoint: str | None = None,
    ):
        self.host = host
        self.port = port
        self.engine_client = engine_client
        self.metrics_endpoint = metrics_endpoint

        self.app = FastAPI(title="ComfyUI Engine Dashboard")
        self.connected_clients: set[WebSocket] = set()
        self._metrics_history: list[DashboardMetrics] = []
        self._queue_history: list[JobInfo] = []
        self._log_buffer: list[dict[str, Any]] = []
        self._shutdown_event = asyncio.Event()
        self._broadcast_task: asyncio.Task | None = None

        self._setup_routes()

    def _setup_routes(self) -> None:
        """Configure FastAPI routes."""

        @self.app.get("/", response_class=HTMLResponse)
        async def dashboard():
            return DASHBOARD_HTML

        @self.app.get("/api/metrics")
        async def get_metrics():
            metrics = await self._fetch_current_metrics()
            return JSONResponse(content=asdict(metrics))

        @self.app.get("/api/queue")
        async def get_queue():
            queue = await self._fetch_queue()
            return JSONResponse(content=[asdict(j) for j in queue])

        @self.app.get("/api/history")
        async def get_history(minutes: int = 60):
            cutoff = time.time() - minutes * 60
            history = [m for m in self._metrics_history if m.timestamp > cutoff]
            return JSONResponse(content={
                "throughput": [h.completed_today for h in history],
                "errors": [100 - h.success_rate for h in history],
                "timestamps": [h.timestamp for h in history],
            })

        @self.app.get("/api/logs")
        async def get_logs(level: str = "INFO", limit: int = 100):
            filtered = [l for l in self._log_buffer if l.get("level") == level]
            return JSONResponse(content=filtered[-limit:])

        @self.app.post("/api/control/{action}")
        async def control(action: str):
            result = await self._handle_control(action)
            return JSONResponse(content={"success": result, "action": action})

        @self.app.websocket("/ws")
        async def websocket_endpoint(websocket: WebSocket):
            await websocket.accept()
            self.connected_clients.add(websocket)

            # Send initial state
            await self._send_initial_state(websocket)

            try:
                while not self._shutdown_event.is_set():
                    try:
                        data = await asyncio.wait_for(
                            websocket.receive_json(),
                            timeout=1.0,
                        )
                        await self._handle_websocket_message(websocket, data)
                    except asyncio.TimeoutError:
                        # Send heartbeat
                        await websocket.send_json({"type": "ping"})
                    except WebSocketDisconnect:
                        break
            except Exception as e:
                logger.debug(f"WebSocket error: {e}")
            finally:
                self.connected_clients.discard(websocket)

    async def _fetch_current_metrics(self) -> DashboardMetrics:
        """Fetch current metrics from engine or metrics endpoint."""
        metrics = DashboardMetrics()

        if self.engine_client and hasattr(self.engine_client, 'get_metrics'):
            try:
                engine_metrics = await self.engine_client.get_metrics()
                metrics.queue_length = engine_metrics.get('queue_length', 0)
                metrics.active_jobs = engine_metrics.get('active_jobs', 0)
                metrics.completed_today = engine_metrics.get('completed_today', 0)
                metrics.avg_generation_time = engine_metrics.get('avg_generation_time', 0.0)
                metrics.success_rate = engine_metrics.get('success_rate', 100.0)
            except Exception as e:
                logger.warning(f"Failed to fetch engine metrics: {e}")

        # Try to get GPU metrics
        try:
            metrics.gpu_utilization = await self._get_gpu_utilization()
        except Exception:
            pass

        metrics.timestamp = time.time()
        self._metrics_history.append(metrics)

        # Trim history
        cutoff = time.time() - 24 * 3600
        self._metrics_history = [m for m in self._metrics_history if m.timestamp > cutoff]

        return metrics

    async def _get_gpu_utilization(self) -> float:
        """Get GPU utilization via nvidia-smi or similar."""
        try:
            import subprocess
            result = subprocess.run(
                ["nvidia-smi", "--query-gpu=utilization.gpu", "--format=csv,noheader,nounits"],
                capture_output=True,
                text=True,
                timeout=5,
            )
            if result.returncode == 0:
                return float(result.stdout.strip().split('\n')[0])
        except Exception:
            pass
        return 0.0

    async def _fetch_queue(self) -> list[JobInfo]:
        """Fetch current job queue."""
        if self.engine_client and hasattr(self.engine_client, 'get_queue'):
            try:
                queue = await self.engine_client.get_queue()
                return [
                    JobInfo(
                        id=j.get('id', 'unknown'),
                        status=j.get('status', 'unknown'),
                        prompt=j.get('prompt', '')[:50],
                        progress=j.get('progress', 0.0),
                    )
                    for j in queue
                ]
            except Exception as e:
                logger.warning(f"Failed to fetch queue: {e}")

        return []

    async def _handle_control(self, action: str) -> bool:
        """Handle control actions from dashboard."""
        if not self.engine_client:
            return False

        try:
            if action == "pause":
                if hasattr(self.engine_client, 'pause'):
                    await self.engine_client.pause()
            elif action == "resume":
                if hasattr(self.engine_client, 'resume'):
                    await self.engine_client.resume()
            elif action == "clear":
                if hasattr(self.engine_client, 'clear_queue'):
                    await self.engine_client.clear_queue()
            elif action == "refresh":
                pass  # Just triggers refresh
            else:
                return False

            return True

        except Exception as e:
            logger.error(f"Control action failed: {e}")
            return False

    async def _handle_websocket_message(self, websocket: WebSocket, data: dict[str, Any]) -> None:
        """Handle incoming WebSocket messages."""
        msg_type = data.get("type")

        if msg_type == "command":
            action = data.get("action")
            success = await self._handle_control(action)
            await websocket.send_json({
                "type": "command_result",
                "action": action,
                "success": success,
            })

        elif msg_type == "pong":
            pass  # Heartbeat response

    async def _send_initial_state(self, websocket: WebSocket) -> None:
        """Send initial state to newly connected client."""
        metrics = await self._fetch_current_metrics()
        queue = await self._fetch_queue()

        await websocket.send_json({
            "type": "metrics",
            "payload": asdict(metrics),
        })
        await websocket.send_json({
            "type": "queue",
            "payload": [asdict(j) for j in queue],
        })

    async def _broadcast_metrics(self) -> None:
        """Background task to broadcast metrics to all connected clients."""
        while not self._shutdown_event.is_set():
            try:
                await asyncio.wait_for(
                    self._shutdown_event.wait(),
                    timeout=2.0,
                )
                break
            except asyncio.TimeoutError:
                if not self.connected_clients:
                    continue

                metrics = await self._fetch_current_metrics()
                queue = await self._fetch_queue()

                message = {
                    "type": "metrics",
                    "payload": asdict(metrics),
                }
                queue_message = {
                    "type": "queue",
                    "payload": [asdict(j) for j in queue],
                }

                # Send to all connected clients
                disconnected = set()
                for ws in self.connected_clients:
                    try:
                        await ws.send_json(message)
                        await ws.send_json(queue_message)
                    except Exception:
                        disconnected.add(ws)

                self.connected_clients -= disconnected

    def log(self, level: str, message: str) -> None:
        """Add a log entry to be broadcast to dashboard clients."""
        entry = {
            "time": datetime.now().isoformat(),
            "level": level,
            "message": message,
        }
        self._log_buffer.append(entry)

        # Trim buffer
        if len(self._log_buffer) > 10000:
            self._log_buffer = self._log_buffer[-5000:]

        # Broadcast to connected clients
        if self.connected_clients:
            asyncio.create_task(self._broadcast_log(entry))

    async def _broadcast_log(self, entry: dict[str, Any]) -> None:
        """Broadcast log entry to all clients."""
        message = {
            "type": "log",
            "payload": entry,
        }

        disconnected = set()
        for ws in self.connected_clients:
            try:
                await ws.send_json(message)
            except Exception:
                disconnected.add(ws)

        self.connected_clients -= disconnected

    async def start(self) -> None:
        """Start the dashboard server."""
        import uvicorn

        self._broadcast_task = asyncio.create_task(self._broadcast_metrics())

        config = uvicorn.Config(
            self.app,
            host=self.host,
            port=self.port,
            log_level="warning",
        )
        server = uvicorn.Server(config)

        logger.info(f"Dashboard server starting on http://{self.host}:{self.port}")
        await server.serve()

    async def shutdown(self) -> None:
        """Gracefully shutdown the dashboard server."""
        self._shutdown_event.set()

        if self._broadcast_task:
            self._broadcast_task.cancel()
            try:
                await self._broadcast_task
            except asyncio.CancelledError:
                pass

        # Close all WebSocket connections
        for ws in list(self.connected_clients):
            try:
                await ws.close()
            except Exception:
                pass

        self.connected_clients.clear()
        logger.info("Dashboard server shutdown complete")


# Integration with main engine
class EngineDashboardBridge:
    """Bridge between the engine and dashboard for seamless integration."""

    def __init__(self, engine: Any, dashboard: DashboardServer):
        self.engine = engine
        self.dashboard = dashboard
        self._running = False
        self._monitor_task: asyncio.Task | None = None

    async def start(self) -> None:
        """Start monitoring and forwarding to dashboard."""
        self._running = True
        self._monitor_task = asyncio.create_task(self._monitor_loop())

    async def _monitor_loop(self) -> None:
        """Monitor engine and forward logs/metrics to dashboard."""
        while self._running:
            try:
                # Forward engine logs
                if hasattr(self.engine, 'get_recent_logs'):
                    logs = await self.engine.get_recent_logs()
                    for log in logs:
                        self.dashboard.log(log.get('level', 'INFO'), log.get('message', ''))

                await asyncio.sleep(1)

            except asyncio.CancelledError:
                break
            except Exception as e:
                logger.error(f"Monitor loop error: {e}")
                await asyncio.sleep(5)

    async def shutdown(self) -> None:
        """Stop the bridge."""
        self._running = False
        if self._monitor_task:
            self._monitor_task.cancel()
            try:
                await self._monitor_task
            except asyncio.CancelledError:
                pass


async def create_dashboard(
    engine: Any | None = None,
    host: str = "0.0.0.0",
    port: int = 8080,
) -> DashboardServer:
    """Factory function to create and start a dashboard server."""
    dashboard = DashboardServer(host=host, port=port, engine_client=engine)

    if engine:
        bridge = EngineDashboardBridge(engine, dashboard)
        await bridge.start()

    return dashboard


if __name__ == "__main__":
    # Standalone mode for testing
    dashboard = DashboardServer()
    asyncio.run(dashboard.start())
