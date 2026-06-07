"""Mobile-responsive web app for monitoring ComfyUI Engine.

PWA (Progressive Web App) that works on mobile and desktop.
Connects to the engine's REST API and WebSocket for real-time updates.
Features touch-optimized UI, offline support, and push notifications.
"""

import asyncio
import json
import logging
import time
from pathlib import Path
from typing import Any, Dict, List, Optional

from fastapi import FastAPI
from fastapi.responses import HTMLResponse, JSONResponse, Response

logger = logging.getLogger(__name__)


MOBILE_APP_HTML = """
<!DOCTYPE html>
<html lang="en">
<head>
    <meta charset="UTF-8">
    <meta name="viewport" content="width=device-width, initial-scale=1.0, maximum-scale=1.0, user-scalable=no">
    <meta name="theme-color" content="#0f172a">
    <meta name="apple-mobile-web-app-capable" content="yes">
    <meta name="apple-mobile-web-app-status-bar-style" content="black-translucent">
    <title>ComfyUI Engine Mobile</title>
    <link rel="manifest" href="/manifest.json">
    <link rel="apple-touch-icon" href="/icon-192.png">
    <style>
        :root {
            --bg: #0f172a;
            --surface: #1e293b;
            --surface-elevated: #334155;
            --text: #f1f5f9;
            --text-secondary: #94a3b8;
            --accent: #3b82f6;
            --accent-light: #60a5fa;
            --success: #22c55e;
            --warning: #f59e0b;
            --danger: #ef4444;
            --border: #334155;
            --shadow: 0 4px 6px -1px rgba(0,0,0,0.3);
        }
        
        * {
            margin: 0;
            padding: 0;
            box-sizing: border-box;
            -webkit-tap-highlight-color: transparent;
            touch-action: manipulation;
        }
        
        body {
            font-family: -apple-system, BlinkMacSystemFont, 'Segoe UI', Roboto, sans-serif;
            background: var(--bg);
            color: var(--text);
            min-height: 100vh;
            overflow-x: hidden;
            -webkit-font-smoothing: antialiased;
        }
        
        /* Header */
        .app-header {
            position: fixed;
            top: 0;
            left: 0;
            right: 0;
            z-index: 1000;
            background: var(--surface);
            backdrop-filter: blur(20px);
            -webkit-backdrop-filter: blur(20px);
            border-bottom: 1px solid var(--border);
            padding: 0.75rem 1rem;
            display: flex;
            align-items: center;
            justify-content: space-between;
            height: 56px;
        }
        
        .app-header h1 {
            font-size: 1.125rem;
            font-weight: 600;
            letter-spacing: -0.025em;
        }
        
        .connection-status {
            display: flex;
            align-items: center;
            gap: 0.5rem;
            font-size: 0.75rem;
            color: var(--text-secondary);
        }
        
        .status-dot {
            width: 8px;
            height: 8px;
            border-radius: 50%;
            background: var(--danger);
            transition: background 0.3s;
        }
        
        .status-dot.connected {
            background: var(--success);
        }
        
        /* Main content */
        .app-content {
            padding: 72px 1rem 80px;
            max-width: 600px;
            margin: 0 auto;
        }
        
        /* Cards */
        .card {
            background: var(--surface);
            border-radius: 16px;
            padding: 1.25rem;
            margin-bottom: 1rem;
            box-shadow: var(--shadow);
            border: 1px solid var(--border);
        }
        
        .card-header {
            display: flex;
            justify-content: space-between;
            align-items: center;
            margin-bottom: 1rem;
        }
        
        .card-title {
            font-size: 0.875rem;
            font-weight: 600;
            text-transform: uppercase;
            letter-spacing: 0.05em;
            color: var(--text-secondary);
        }
        
        .card-action {
            background: none;
            border: none;
            color: var(--accent);
            font-size: 0.875rem;
            cursor: pointer;
            padding: 0.25rem 0.5rem;
        }
        
        /* Metrics grid */
        .metrics-grid {
            display: grid;
            grid-template-columns: repeat(2, 1fr);
            gap: 0.75rem;
        }
        
        .metric-card {
            background: var(--surface-elevated);
            border-radius: 12px;
            padding: 1rem;
            text-align: center;
        }
        
        .metric-value {
            font-size: 1.75rem;
            font-weight: 700;
            color: var(--accent);
            line-height: 1.2;
        }
        
        .metric-label {
            font-size: 0.75rem;
            color: var(--text-secondary);
            margin-top: 0.25rem;
        }
        
        .metric-delta {
            font-size: 0.75rem;
            margin-top: 0.25rem;
        }
        
        .metric-delta.positive { color: var(--success); }
        .metric-delta.negative { color: var(--danger); }
        
        /* Queue list */
        .queue-list {
            list-style: none;
        }
        
        .queue-item {
            display: flex;
            align-items: center;
            padding: 0.875rem;
            background: var(--surface-elevated);
            border-radius: 12px;
            margin-bottom: 0.5rem;
            transition: transform 0.2s;
        }
        
        .queue-item:active {
            transform: scale(0.98);
        }
        
        .queue-item-icon {
            width: 40px;
            height: 40px;
            border-radius: 10px;
            display: flex;
            align-items: center;
            justify-content: center;
            font-size: 1.25rem;
            margin-right: 0.875rem;
            flex-shrink: 0;
        }
        
        .queue-item-icon.pending { background: rgba(245, 158, 11, 0.2); }
        .queue-item-icon.running { background: rgba(59, 130, 246, 0.2); }
        .queue-item-icon.completed { background: rgba(34, 197, 94, 0.2); }
        .queue-item-icon.failed { background: rgba(239, 68, 68, 0.2); }
        
        .queue-item-info {
            flex: 1;
            min-width: 0;
        }
        
        .queue-item-title {
            font-size: 0.875rem;
            font-weight: 500;
            white-space: nowrap;
            overflow: hidden;
            text-overflow: ellipsis;
        }
        
        .queue-item-meta {
            font-size: 0.75rem;
            color: var(--text-secondary);
            margin-top: 0.125rem;
        }
        
        .queue-item-status {
            font-size: 0.75rem;
            font-weight: 600;
            padding: 0.25rem 0.625rem;
            border-radius: 9999px;
            text-transform: uppercase;
        }
        
        .status-pending { background: rgba(245, 158, 11, 0.2); color: var(--warning); }
        .status-running { background: rgba(59, 130, 246, 0.2); color: var(--accent-light); }
        .status-completed { background: rgba(34, 197, 94, 0.2); color: var(--success); }
        .status-failed { background: rgba(239, 68, 68, 0.2); color: var(--danger); }
        
        /* Controls */
        .controls-grid {
            display: grid;
            grid-template-columns: repeat(2, 1fr);
            gap: 0.75rem;
        }
        
        .control-btn {
            background: var(--surface-elevated);
            border: 1px solid var(--border);
            color: var(--text);
            padding: 1rem;
            border-radius: 12px;
            font-size: 0.875rem;
            font-weight: 600;
            cursor: pointer;
            display: flex;
            align-items: center;
            justify-content: center;
            gap: 0.5rem;
            transition: all 0.2s;
        }
        
        .control-btn:active {
            transform: scale(0.96);
            background: var(--accent);
        }
        
        .control-btn.danger {
            border-color: var(--danger);
            color: var(--danger);
        }
        
        .control-btn.danger:active {
            background: var(--danger);
            color: white;
        }
        
        /* Bottom nav */
        .bottom-nav {
            position: fixed;
            bottom: 0;
            left: 0;
            right: 0;
            z-index: 1000;
            background: var(--surface);
            backdrop-filter: blur(20px);
            -webkit-backdrop-filter: blur(20px);
            border-top: 1px solid var(--border);
            display: flex;
            justify-content: space-around;
            padding: 0.5rem 0;
            padding-bottom: max(0.5rem, env(safe-area-inset-bottom));
        }
        
        .nav-item {
            display: flex;
            flex-direction: column;
            align-items: center;
            gap: 0.25rem;
            padding: 0.25rem 1rem;
            color: var(--text-secondary);
            text-decoration: none;
            font-size: 0.625rem;
            transition: color 0.2s;
        }
        
        .nav-item.active {
            color: var(--accent);
        }
        
        .nav-item svg {
            width: 24px;
            height: 24px;
        }
        
        /* Pull to refresh */
        .ptr-indicator {
            text-align: center;
            padding: 1rem;
            color: var(--text-secondary);
            font-size: 0.875rem;
            opacity: 0;
            transition: opacity 0.3s;
        }
        
        .ptr-indicator.visible {
            opacity: 1;
        }
        
        /* Toast notifications */
        .toast-container {
            position: fixed;
            top: 64px;
            left: 1rem;
            right: 1rem;
            z-index: 2000;
            pointer-events: none;
        }
        
        .toast {
            background: var(--surface-elevated);
            border-radius: 12px;
            padding: 1rem;
            margin-bottom: 0.5rem;
            box-shadow: var(--shadow);
            border: 1px solid var(--border);
            transform: translateY(-100%);
            opacity: 0;
            transition: all 0.3s;
        }
        
        .toast.show {
            transform: translateY(0);
            opacity: 1;
        }
        
        .toast-title {
            font-weight: 600;
            font-size: 0.875rem;
        }
        
        .toast-message {
            font-size: 0.75rem;
            color: var(--text-secondary);
            margin-top: 0.25rem;
        }
        
        /* Settings panel */
        .settings-item {
            display: flex;
            justify-content: space-between;
            align-items: center;
            padding: 1rem;
            background: var(--surface-elevated);
            border-radius: 12px;
            margin-bottom: 0.5rem;
        }
        
        .settings-label {
            font-size: 0.875rem;
        }
        
        .settings-value {
            font-size: 0.875rem;
            color: var(--text-secondary);
        }
        
        /* Toggle switch */
        .toggle {
            width: 48px;
            height: 28px;
            background: var(--border);
            border-radius: 14px;
            position: relative;
            cursor: pointer;
            transition: background 0.3s;
        }
        
        .toggle.active {
            background: var(--accent);
        }
        
        .toggle::after {
            content: '';
            position: absolute;
            width: 24px;
            height: 24px;
            background: white;
            border-radius: 50%;
            top: 2px;
            left: 2px;
            transition: transform 0.3s;
            box-shadow: 0 2px 4px rgba(0,0,0,0.2);
        }
        
        .toggle.active::after {
            transform: translateX(20px);
        }
        
        /* Animations */
        @keyframes pulse {
            0%, 100% { opacity: 1; }
            50% { opacity: 0.5; }
        }
        
        .pulse {
            animation: pulse 2s infinite;
        }
        
        @keyframes slideIn {
            from { transform: translateX(100%); opacity: 0; }
            to { transform: translateX(0); opacity: 1; }
        }
        
        .slide-in {
            animation: slideIn 0.3s ease-out;
        }
        
        /* Hide scrollbar */
        ::-webkit-scrollbar {
            display: none;
        }
        
        body {
            -ms-overflow-style: none;
            scrollbar-width: none;
        }
    </style>
</head>
<body>
    <header class="app-header">
        <h1>ComfyUI Engine</h1>
        <div class="connection-status">
            <span class="status-dot" id="status-dot"></span>
            <span id="status-text">Offline</span>
        </div>
    </header>

    <div class="app-content" id="app-content">
        <!-- Dashboard View -->
        <div id="view-dashboard" class="view">
            <div class="ptr-indicator" id="ptr-indicator">
                Pull to refresh
            </div>
            
            <div class="card">
                <div class="metrics-grid">
                    <div class="metric-card">
                        <div class="metric-value" id="metric-queue">0</div>
                        <div class="metric-label">Queue</div>
                    </div>
                    <div class="metric-card">
                        <div class="metric-value" id="metric-active">0</div>
                        <div class="metric-label">Active</div>
                    </div>
                    <div class="metric-card">
                        <div class="metric-value" id="metric-completed">0</div>
                        <div class="metric-label">Done</div>
                    </div>
                    <div class="metric-card">
                        <div class="metric-value" id="metric-gpu">0%</div>
                        <div class="metric-label">GPU</div>
                    </div>
                </div>
            </div>
            
            <div class="card">
                <div class="card-header">
                    <span class="card-title">Queue</span>
                    <button class="card-action" onclick="refreshQueue()">Refresh</button>
                </div>
                <ul class="queue-list" id="queue-list">
                    <li class="queue-item">
                        <div class="queue-item-icon pending"></span></div>
                        <div class="queue-item-info">
                            <div class="queue-item-title">No jobs</div>
                            <div class="queue-item-meta">Queue is empty</div>
                        </div>
                    </li>
                </ul>
            </div>
            
            <div class="card">
                <div class="card-header">
                    <span class="card-title">Controls</span>
                </div>
                <div class="controls-grid">
                    <button class="control-btn" onclick="pauseQueue()">
                        <svg width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2">
                            <rect x="6" y="4" width="4" height="16"></rect>
                            <rect x="14" y="4" width="4" height="16"></rect>
                        </svg>
                        Pause
                    </button>
                    <button class="control-btn" onclick="resumeQueue()">
                        <svg width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2">
                            <polygon points="5 3 19 12 5 21 5 3"></polygon>
                        </svg>
                        Resume
                    </button>
                    <button class="control-btn danger" onclick="clearQueue()">
                        <svg width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2">
                            <polyline points="3 6 5 6 21 6"></polyline>
                            <path d="M19 6v14a2 2 0 0 1-2 2H7a2 2 0 0 1-2-2V6m3 0V4a2 2 0 0 1 2-2h4a2 2 0 0 1 2 2v2"></path>
                        </svg>
                        Clear
                    </button>
                    <button class="control-btn" onclick="submitJob()">
                        <svg width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2">
                            <line x1="12" y1="5" x2="12" y2="19"></line>
                            <line x1="5" y1="12" x2="19" y2="12"></line>
                        </svg>
                        New Job
                    </button>
                </div>
            </div>
        </div>
        
        <!-- Settings View -->
        <div id="view-settings" class="view" style="display:none">
            <div class="card">
                <div class="card-header">
                    <span class="card-title">Connection</span>
                </div>
                <div class="settings-item">
                    <span class="settings-label">Server URL</span>
                    <span class="settings-value" id="settings-url">http://localhost:8000</span>
                </div>
                <div class="settings-item">
                    <span class="settings-label">Auto-connect</span>
                    <div class="toggle active" id="toggle-autoconnect" onclick="toggleSetting(this)"></div>
                </div>
                <div class="settings-item">
                    <span class="settings-label">Notifications</span>
                    <div class="toggle" id="toggle-notifications" onclick="toggleSetting(this)"></div>
                </div>
            </div>
            
            <div class="card">
                <div class="card-header">
                    <span class="card-title">About</span>
                </div>
                <div class="settings-item">
                    <span class="settings-label">Version</span>
                    <span class="settings-value">4.0.0</span>
                </div>
                <div class="settings-item">
                    <span class="settings-label">Build</span>
                    <span class="settings-value">2024.1</span>
                </div>
            </div>
        </div>
    </div>

    <nav class="bottom-nav">
        <a href="#" class="nav-item active" onclick="showView('dashboard')">
            <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2">
                <rect x="3" y="3" width="7" height="7"></rect>
                <rect x="14" y="3" width="7" height="7"></rect>
                <rect x="14" y="14" width="7" height="7"></rect>
                <rect x="3" y="14" width="7" height="7"></rect>
            </svg>
            Dashboard
        </a>
        <a href="#" class="nav-item" onclick="showView('settings')">
            <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2">
                <circle cx="12" cy="12" r="3"></circle>
                <path d="M19.4 15a1.65 1.65 0 0 0 .33 1.82l.06.06a2 2 0 0 1 0 2.83 2 2 0 0 1-2.83 0l-.06-.06a1.65 1.65 0 0 0-1.82-.33 1.65 1.65 0 0 0-1 1.51V21a2 2 0 0 1-2 2 2 2 0 0 1-2-2v-.09A1.65 1.65 0 0 0 9 19.4a1.65 1.65 0 0 0-1.82.33l-.06.06a2 2 0 0 1-2.83 0 2 2 0 0 1 0-2.83l.06-.06a1.65 1.65 0 0 0 .33-1.82 1.65 1.65 0 0 0-1.51-1H3a2 2 0 0 1-2-2 2 2 0 0 1 2-2h.09A1.65 1.65 0 0 0 4.6 9a1.65 1.65 0 0 0-.33-1.82l-.06-.06a2 2 0 0 1 0-2.83 2 2 0 0 1 2.83 0l.06.06a1.65 1.65 0 0 0 1.82.33H9a1.65 1.65 0 0 0 1-1.51V3a2 2 0 0 1 2-2 2 2 0 0 1 2 2v.09a1.65 1.65 0 0 0 1 1.51 1.65 1.65 0 0 0 1.82-.33l.06-.06a2 2 0 0 1 2.83 0 2 2 0 0 1 0 2.83l-.06.06a1.65 1.65 0 0 0-.33 1.82V9a1.65 1.65 0 0 0 1.51 1H21a2 2 0 0 1 2 2 2 2 0 0 1-2 2h-.09a1.65 1.65 0 0 0-1.51 1z"></path>
            </svg>
            Settings
        </a>
    </nav>

    <div class="toast-container" id="toast-container"></div>

    <script>
        // Configuration
        const CONFIG = {
            apiUrl: localStorage.getItem('apiUrl') || 'http://localhost:8000',
            wsUrl: localStorage.getItem('wsUrl') || 'ws://localhost:8000/ws',
            autoConnect: localStorage.getItem('autoConnect') !== 'false',
            notifications: localStorage.getItem('notifications') === 'true',
        };

        // State
        let ws = null;
        let reconnectInterval = 1000;
        let currentView = 'dashboard';
        let pullStartY = 0;
        let isPulling = false;

        // Initialize
        document.addEventListener('DOMContentLoaded', () => {
            if (CONFIG.autoConnect) {
                connect();
            }
            loadQueue();
            setupPullToRefresh();
        });

        // WebSocket connection
        function connect() {
            if (ws) {
                ws.close();
            }

            ws = new WebSocket(CONFIG.wsUrl);

            ws.onopen = () => {
                updateConnectionStatus(true);
                reconnectInterval = 1000;
                showToast('Connected', 'Real-time updates active');
            };

            ws.onmessage = (event) => {
                const data = JSON.parse(event.data);
                handleMessage(data);
            };

            ws.onclose = () => {
                updateConnectionStatus(false);
                if (CONFIG.autoConnect) {
                    setTimeout(connect, reconnectInterval);
                    reconnectInterval = Math.min(reconnectInterval * 2, 30000);
                }
            };

            ws.onerror = (err) => {
                console.error('WebSocket error:', err);
            };
        }

        function updateConnectionStatus(connected) {
            const dot = document.getElementById('status-dot');
            const text = document.getElementById('status-text');
            
            if (connected) {
                dot.classList.add('connected');
                text.textContent = 'Live';
            } else {
                dot.classList.remove('connected');
                text.textContent = 'Offline';
            }
        }

        function handleMessage(data) {
            switch (data.type) {
                case 'metrics':
                    updateMetrics(data.payload);
                    break;
                case 'queue':
                    updateQueue(data.payload);
                    break;
                case 'notification':
                    showToast(data.title, data.message);
                    if (CONFIG.notifications && 'Notification' in window) {
                        new Notification(data.title, { body: data.message });
                    }
                    break;
            }
        }

        function updateMetrics(metrics) {
            document.getElementById('metric-queue').textContent = metrics.queue_length || 0;
            document.getElementById('metric-active').textContent = metrics.active_jobs || 0;
            document.getElementById('metric-completed').textContent = metrics.completed_today || 0;
            document.getElementById('metric-gpu').textContent = (metrics.gpu_utilization || 0) + '%';
        }

        function updateQueue(jobs) {
            const list = document.getElementById('queue-list');
            
            if (!jobs || jobs.length === 0) {
                list.innerHTML = `
                    <li class="queue-item">
                        <div class="queue-item-icon pending"></span></div>
                        <div class="queue-item-info">
                            <div class="queue-item-title">No jobs</div>
                            <div class="queue-item-meta">Queue is empty</div>
                        </div>
                    </li>
                `;
                return;
            }

            list.innerHTML = jobs.map(job => `
                <li class="queue-item slide-in">
                    <div class="queue-item-icon ${job.status}"></span></div>
                    <div class="queue-item-info">
                        <div class="queue-item-title">${job.id || 'Unknown'}</div>
                        <div class="queue-item-meta">${job.status} · ${formatTime(job.elapsed || 0)}</div>
                    </div>
                    <span class="queue-item-status status-${job.status}">${job.status}</span>
                </li>
            `).join('');
        }

        async function loadQueue() {
            try {
                const response = await fetch(`${CONFIG.apiUrl}/api/v1/queue`);
                if (response.ok) {
                    const data = await response.json();
                    updateQueue(data.jobs || []);
                }
            } catch (err) {
                console.error('Failed to load queue:', err);
            }
        }

        async function refreshQueue() {
            showToast('Refreshing', 'Loading latest data...');
            await loadQueue();
            await loadMetrics();
        }

        async function loadMetrics() {
            try {
                const response = await fetch(`${CONFIG.apiUrl}/api/v1/metrics`);
                if (response.ok) {
                    const data = await response.json();
                    updateMetrics(data);
                }
            } catch (err) {
                console.error('Failed to load metrics:', err);
            }
        }

        async function pauseQueue() {
            try {
                const response = await fetch(`${CONFIG.apiUrl}/api/v1/queue/pause`, { method: 'POST' });
                if (response.ok) {
                    showToast('Paused', 'Queue processing paused');
                }
            } catch (err) {
                showToast('Error', 'Failed to pause queue');
            }
        }

        async function resumeQueue() {
            try {
                const response = await fetch(`${CONFIG.apiUrl}/api/v1/queue/resume`, { method: 'POST' });
                if (response.ok) {
                    showToast('Resumed', 'Queue processing resumed');
                }
            } catch (err) {
                showToast('Error', 'Failed to resume queue');
            }
        }

        async function clearQueue() {
            if (!confirm('Clear all jobs?')) return;
            
            try {
                const response = await fetch(`${CONFIG.apiUrl}/api/v1/jobs`, { method: 'DELETE' });
                if (response.ok) {
                    showToast('Cleared', 'All jobs removed');
                    loadQueue();
                }
            } catch (err) {
                showToast('Error', 'Failed to clear queue');
            }
        }

        function submitJob() {
            const prompt = prompt('Enter prompt:');
            if (!prompt) return;
            
            fetch(`${CONFIG.apiUrl}/api/v1/jobs`, {
                method: 'POST',
                headers: { 'Content-Type': 'application/json' },
                body: JSON.stringify({ prompt })
            })
            .then(response => response.json())
            .then(data => {
                showToast('Job Created', `ID: ${data.job_id}`);
                loadQueue();
            })
            .catch(err => {
                showToast('Error', 'Failed to create job');
            });
        }

        // View navigation
        function showView(view) {
            document.querySelectorAll('.view').forEach(v => v.style.display = 'none');
            document.getElementById(`view-${view}`).style.display = 'block';
            
            document.querySelectorAll('.nav-item').forEach(n => n.classList.remove('active'));
            event.target.closest('.nav-item').classList.add('active');
            
            currentView = view;
        }

        // Toast notifications
        function showToast(title, message) {
            const container = document.getElementById('toast-container');
            const toast = document.createElement('div');
            toast.className = 'toast';
            toast.innerHTML = `
                <div class="toast-title">${title}</div>
                <div class="toast-message">${message}</div>
            `;
            container.appendChild(toast);
            
            setTimeout(() => toast.classList.add('show'), 10);
            setTimeout(() => {
                toast.classList.remove('show');
                setTimeout(() => toast.remove(), 300);
            }, 3000);
        }

        // Pull to refresh
        function setupPullToRefresh() {
            const content = document.getElementById('app-content');
            const indicator = document.getElementById('ptr-indicator');
            
            content.addEventListener('touchstart', (e) => {
                if (content.scrollTop === 0) {
                    pullStartY = e.touches[0].clientY;
                    isPulling = true;
                }
            });
            
            content.addEventListener('touchmove', (e) => {
                if (!isPulling) return;
                
                const pull = e.touches[0].clientY - pullStartY;
                if (pull > 0 && pull < 100) {
                    indicator.classList.add('visible');
                    indicator.style.transform = `translateY(${pull * 0.5}px)`;
                }
            });
            
            content.addEventListener('touchend', () => {
                if (!isPulling) return;
                isPulling = false;
                indicator.classList.remove('visible');
                indicator.style.transform = '';
                refreshQueue();
            });
        }

        // Toggle settings
        function toggleSetting(element) {
            element.classList.toggle('active');
            const id = element.id;
            
            if (id === 'toggle-autoconnect') {
                CONFIG.autoConnect = element.classList.contains('active');
                localStorage.setItem('autoConnect', CONFIG.autoConnect);
                if (CONFIG.autoConnect) connect();
            } else if (id === 'toggle-notifications') {
                CONFIG.notifications = element.classList.contains('active');
                localStorage.setItem('notifications', CONFIG.notifications);
                if (CONFIG.notifications) {
                    Notification.requestPermission();
                }
            }
        }

        // Format time
        function formatTime(seconds) {
            if (seconds < 60) return `${seconds}s`;
            if (seconds < 3600) return `${Math.floor(seconds / 60)}m`;
            return `${Math.floor(seconds / 3600)}h ${Math.floor((seconds % 3600) / 60)}m`;
        }

        // Service Worker registration for PWA
        if ('serviceWorker' in navigator) {
            navigator.serviceWorker.register('/sw.js').catch(console.error);
        }

        // Request notification permission
        if ('Notification' in window && CONFIG.notifications) {
            Notification.requestPermission();
        }
    </script>
</body>
</html>
"""


# Service Worker for PWA offline support
SERVICE_WORKER_JS = """
const CACHE_NAME = 'comfyui-engine-v1';
const urlsToCache = [
    '/',
    '/manifest.json',
    '/icon-192.png',
    '/icon-512.png'
];

self.addEventListener('install', (event) => {
    event.waitUntil(
        caches.open(CACHE_NAME)
            .then((cache) => cache.addAll(urlsToCache))
    );
});

self.addEventListener('fetch', (event) => {
    event.respondWith(
        caches.match(event.request)
            .then((response) => {
                if (response) {
                    return response;
                }
                return fetch(event.request);
            })
    );
});

self.addEventListener('push', (event) => {
    const data = event.data.json();
    event.waitUntil(
        self.registration.showNotification(data.title, {
            body: data.message,
            icon: '/icon-192.png',
            badge: '/icon-192.png'
        })
    );
});
"""


# Web Manifest for PWA
WEB_MANIFEST = {
    "name": "ComfyUI Engine Mobile",
    "short_name": "ComfyUI Engine",
    "description": "Mobile monitoring and control for ComfyUI Engine",
    "start_url": "/",
    "display": "standalone",
    "background_color": "#0f172a",
    "theme_color": "#0f172a",
    "orientation": "portrait",
    "icons": [
        {
            "src": "/icon-192.png",
            "sizes": "192x192",
            "type": "image/png"
        },
        {
            "src": "/icon-512.png",
            "sizes": "512x512",
            "type": "image/png"
        }
    ]
}


class MobileAppServer:
    """FastAPI server that serves the mobile PWA app."""
    
    def __init__(self, api_url: str = "http://localhost:8000"):
        self.api_url = api_url
        self.app = FastAPI(title="ComfyUI Engine Mobile")
        self._setup_routes()
    
    def _setup_routes(self) -> None:
        """Configure mobile app routes."""
        
        @self.app.get("/", response_class=HTMLResponse)
        async def mobile_app():
            """Serve the mobile app HTML."""
            return MOBILE_APP_HTML
        
        @self.app.get("/manifest.json")
        async def manifest():
            """Serve web manifest."""
            return JSONResponse(content=WEB_MANIFEST)
        
        @self.app.get("/sw.js", response_class=HTMLResponse)
        async def service_worker():
            """Serve service worker."""
            return Response(
                content=SERVICE_WORKER_JS,
                media_type="application/javascript",
            )
        
        @self.app.get("/icon-192.png")
        async def icon_192():
            """Serve 192x192 icon."""
            # Return a simple colored square as PNG
            return Response(
                content=b"",  # Would be actual PNG bytes
                media_type="image/png",
            )
        
        @self.app.get("/icon-512.png")
        async def icon_512():
            """Serve 512x512 icon."""
            return Response(
                content=b"",  # Would be actual PNG bytes
                media_type="image/png",
            )
    
    async def start(self, host: str = "0.0.0.0", port: int = 8080) -> None:
        """Start the mobile app server."""
        import uvicorn
        
        config = uvicorn.Config(
            self.app,
            host=host,
            port=port,
            log_level="info",
        )
        server = uvicorn.Server(config)
        
        logger.info(f"Mobile app server starting on http://{host}:{port}")
        await server.serve()


# Write mobile app files to disk
def write_mobile_app_files(output_dir: Path = Path("mobile_app")) -> None:
    """Write mobile app files to disk."""
    output_dir.mkdir(parents=True, exist_ok=True)
    
    # Write HTML
    (output_dir / "index.html").write_text(MOBILE_APP_HTML)
    
    # Write service worker
    (output_dir / "sw.js").write_text(SERVICE_WORKER_JS)
    
    # Write manifest
    (output_dir / "manifest.json").write_text(json.dumps(WEB_MANIFEST, indent=2))
    
    logger.info(f"Mobile app files written to {output_dir}")


if __name__ == "__main__":
    # Write files
    write_mobile_app_files()
    
    # Start server
    server = MobileAppServer()
    asyncio.run(server.start())
