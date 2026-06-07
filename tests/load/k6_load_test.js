import http from 'k6/http';
import { check, sleep, group } from 'k6';
import { Rate, Trend, Counter } from 'k6/metrics';
import { randomIntBetween } from 'https://jslib.k6.io/k6-utils/1.2.0/index.js';

const errorRate = new Rate('errors');
const requestDuration = new Trend('request_duration');
const jobsSubmitted = new Counter('jobs_submitted');
const jobsCompleted = new Counter('jobs_completed');

const BASE_URL = __ENV.BASE_URL || 'http://localhost:8000';
const API_KEY = __ENV.API_KEY || 'test-api-key';
const WS_URL = __ENV.WS_URL || 'ws://localhost:8000/ws';

export const options = {
  scenarios: {
    smoke: {
      executor: 'constant-vus',
      vus: 1,
      duration: '1m',
      tags: { test_type: 'smoke' },
    },
    load: {
      executor: 'ramping-vus',
      startVUs: 0,
      stages: [
        { duration: '2m', target: 10 },
        { duration: '5m', target: 10 },
        { duration: '2m', target: 20 },
        { duration: '5m', target: 20 },
        { duration: '2m', target: 0 },
      ],
      gracefulRampDown: '30s',
      tags: { test_type: 'load' },
    },
    stress: {
      executor: 'ramping-vus',
      startVUs: 0,
      stages: [
        { duration: '2m', target: 50 },
        { duration: '5m', target: 50 },
        { duration: '2m', target: 100 },
        { duration: '5m', target: 100 },
        { duration: '2m', target: 150 },
        { duration: '5m', target: 150 },
        { duration: '2m', target: 0 },
      ],
      gracefulRampDown: '1m',
      tags: { test_type: 'stress' },
    },
    spike: {
      executor: 'ramping-vus',
      startVUs: 0,
      stages: [
        { duration: '30s', target: 200 },
        { duration: '2m', target: 200 },
        { duration: '30s', target: 0 },
      ],
      gracefulRampDown: '30s',
      tags: { test_type: 'spike' },
    },
    soak: {
      executor: 'constant-vus',
      vus: 30,
      duration: '30m',
      tags: { test_type: 'soak' },
    },
  },
  thresholds: {
    http_req_duration: ['p(95)<5000'],
    http_req_failed: ['rate<<0.1'],
    errors: ['rate<<0.05'],
    request_duration: ['p(95)<3000'],
  },
};

const WORKFLOW_TEMPLATES = [
  {
    name: 'simple_txt2img',
    nodes: {
      '1': { class_type: 'KSampler', inputs: { seed: 42, steps: 20, cfg: 7.5, sampler_name: 'euler', scheduler: 'normal', denoise: 1.0, model: ['2', 0], positive: ['3', 0], negative: ['4', 0], latent_image: ['5', 0] } },
      '2': { class_type: 'CheckpointLoaderSimple', inputs: { ckpt_name: 'v1-5-pruned-emaonly.ckpt' } },
      '3': { class_type: 'CLIPTextEncode', inputs: { text: 'beautiful landscape', clip: ['2', 1] } },
      '4': { class_type: 'CLIPTextEncode', inputs: { text: 'blurry, low quality', clip: ['2', 1] } },
      '5': { class_type: 'EmptyLatentImage', inputs: { width: 512, height: 512, batch_size: 1 } },
    },
  },
  {
    name: 'batch_processing',
    nodes: {
      '1': { class_type: 'KSampler', inputs: { seed: 123, steps: 15, cfg: 8.0, sampler_name: 'dpmpp_2m', scheduler: 'karras', denoise: 1.0, model: ['2', 0], positive: ['3', 0], negative: ['4', 0], latent_image: ['5', 0] } },
      '2': { class_type: 'CheckpointLoaderSimple', inputs: { ckpt_name: 'sd-xl-base_1.0.safetensors' } },
      '3': { class_type: 'CLIPTextEncode', inputs: { text: 'professional portrait', clip: ['2', 1] } },
      '4': { class_type: 'CLIPTextEncode', inputs: { text: 'cartoon, anime', clip: ['2', 1] } },
      '5': { class_type: 'EmptyLatentImage', inputs: { width: 1024, height: 1024, batch_size: 4 } },
    },
  },
];

function getAuthHeaders() {
  return {
    'Content-Type': 'application/json',
    'X-API-Key': API_KEY,
  };
}

function submitWorkflow() {
  const template = WORKFLOW_TEMPLATES[randomIntBetween(0, WORKFLOW_TEMPLATES.length - 1)];
  const payload = {
    workflow: template.nodes,
    priority: randomIntBetween(1, 5),
    callback_url: null,
    metadata: { test: true, template: template.name },
  };

  const start = Date.now();
  const res = http.post(`${BASE_URL}/api/v1/jobs`, JSON.stringify(payload), {
    headers: getAuthHeaders(),
    timeout: '30s',
  });
  const duration = Date.now() - start;
  requestDuration.add(duration);

  const success = check(res, {
    'job submitted successfully': (r) => r.status === 202,
    'response has job_id': (r) => r.json('job_id') !== undefined,
    'response time < 5s': (r) => duration < 5000,
  });

  if (!success) {
    errorRate.add(1);
  } else {
    jobsSubmitted.add(1);
    return res.json('job_id');
  }
  return null;
}

function checkJobStatus(jobId) {
  const res = http.get(`${BASE_URL}/api/v1/jobs/${jobId}`, {
    headers: getAuthHeaders(),
  });

  check(res, {
    'job status retrieved': (r) => r.status === 200,
    'job has valid status': (r) => ['pending', 'running', 'completed', 'failed'].includes(r.json('status')),
  });

  return res.json('status');
}

function pollJobCompletion(jobId, maxAttempts = 30) {
  for (let i = 0; i < maxAttempts; i++) {
    const status = checkJobStatus(jobId);
    if (status === 'completed' || status === 'failed') {
      if (status === 'completed') {
        jobsCompleted.add(1);
      }
      return status;
    }
    sleep(2);
  }
  return 'timeout';
}

function getMetrics() {
  const res = http.get(`${BASE_URL}/metrics`, {
    headers: getAuthHeaders(),
  });

  check(res, {
    'metrics endpoint available': (r) => r.status === 200,
    'metrics contain prometheus format': (r) => r.body.includes('comfyui_engine'),
  });
}

function getHealth() {
  const res = http.get(`${BASE_URL}/health`);

  check(res, {
    'health check passes': (r) => r.status === 200,
    'health status is healthy': (r) => r.json('status') === 'healthy',
  });
}

export default function () {
  group('Health Check', () => {
    getHealth();
  });

  group('Submit Workflow', () => {
    const jobId = submitWorkflow();
    if (jobId) {
      group('Poll Job Status', () => {
        pollJobCompletion(jobId);
      });
    }
  });

  group('Metrics', () => {
    getMetrics();
  });

  sleep(randomIntBetween(1, 3));
}

export function handleSummary(data) {
  return {
    'stdout': textSummary(data, { indent: ' ', enableColors: true }),
    'load-test-results.json': JSON.stringify(data, null, 2),
    'load-test-report.html': generateHtmlReport(data),
  };
}

function textSummary(data, options) {
  const indent = options.indent || '';
  const colors = options.enableColors ? {
    green: '\x1b[32m',
    red: '\x1b[31m',
    yellow: '\x1b[33m',
    reset: '\x1b[0m',
  } : { green: '', red: '', yellow: '', reset: '' };

  let summary = [];
  summary.push(`${indent}${colors.green}Load Test Results${colors.reset}`);
  summary.push(`${indent}=================`);
  summary.push(`${indent}Total Requests: ${data.metrics.http_reqs?.count || 0}`);
  summary.push(`${indent}Failed Requests: ${data.metrics.http_req_failed?.passes || 0}`);
  summary.push(`${indent}Avg Request Duration: ${data.metrics.http_req_duration?.avg?.toFixed(2) || 0}ms`);
  summary.push(`${indent}P95 Duration: ${data.metrics.http_req_duration?.['p(95)']?.toFixed(2) || 0}ms`);
  summary.push(`${indent}Jobs Submitted: ${data.metrics.jobs_submitted?.count || 0}`);
  summary.push(`${indent}Jobs Completed: ${data.metrics.jobs_completed?.count || 0}`);
  summary.push(`${indent}Error Rate: ${((data.metrics.errors?.rate || 0) * 100).toFixed(2)}%`);
  summary.push('');

  for (const [scenario, metrics] of Object.entries(data.metrics || {})) {
    if (metrics.thresholds) {
      summary.push(`${indent}Scenario: ${scenario}`);
      for (const [threshold, passed] of Object.entries(metrics.thresholds)) {
        const color = passed ? colors.green : colors.red;
        summary.push(`${indent}  ${threshold}: ${color}${passed ? 'PASS' : 'FAIL'}${colors.reset}`);
      }
    }
  }

  return summary.join('\n');
}

function generateHtmlReport(data) {
  const totalRequests = data.metrics.http_reqs?.count || 0;
  const failedRequests = data.metrics.http_req_failed?.passes || 0;
  const avgDuration = data.metrics.http_req_duration?.avg?.toFixed(2) || 0;
  const p95Duration = data.metrics.http_req_duration?.['p(95)']?.toFixed(2) || 0;
  const errorRate = ((data.metrics.errors?.rate || 0) * 100).toFixed(2);

  return `<!DOCTYPE html>
<html>
<head>
  <title>ComfyUI Engine Load Test Report</title>
  <style>
    body { font-family: Arial, sans-serif; margin: 40px; background: #f5f5f5; }
    .container { max-width: 1200px; margin: 0 auto; background: white; padding: 30px; border-radius: 8px; box-shadow: 0 2px 10px rgba(0,0,0,0.1); }
    h1 { color: #333; border-bottom: 3px solid #4CAF50; padding-bottom: 10px; }
    .metrics { display: grid; grid-template-columns: repeat(auto-fit, minmax(250px, 1fr)); gap: 20px; margin: 20px 0; }
    .metric-card { background: #f8f9fa; padding: 20px; border-radius: 6px; border-left: 4px solid #4CAF50; }
    .metric-card.error { border-left-color: #f44336; }
    .metric-card.warning { border-left-color: #ff9800; }
    .metric-value { font-size: 2em; font-weight: bold; color: #333; }
    .metric-label { color: #666; margin-top: 5px; }
    .pass { color: #4CAF50; }
    .fail { color: #f44336; }
    table { width: 100%; border-collapse: collapse; margin: 20px 0; }
    th, td { padding: 12px; text-align: left; border-bottom: 1px solid #ddd; }
    th { background: #f8f9fa; font-weight: bold; }
    .status-pass { background: #e8f5e8; color: #2e7d32; padding: 4px 8px; border-radius: 4px; }
    .status-fail { background: #ffebee; color: #c62828; padding: 4px 8px; border-radius: 4px; }
  </style>
</head>
<body>
  <div class="container">
    <h1>ComfyUI Engine Load Test Report</h1>
    <p>Generated: ${new Date().toISOString()}</p>

    <div class="metrics">
      <div class="metric-card">
        <div class="metric-value">${totalRequests}</div>
        <div class="metric-label">Total Requests</div>
      </div>
      <div class="metric-card ${failedRequests > 0 ? 'error' : ''}">
        <div class="metric-value">${failedRequests}</div>
        <div class="metric-label">Failed Requests</div>
      </div>
      <div class="metric-card">
        <div class="metric-value">${avgDuration}ms</div>
        <div class="metric-label">Avg Duration</div>
      </div>
      <div class="metric-card">
        <div class="metric-value">${p95Duration}ms</div>
        <div class="metric-label">P95 Duration</div>
      </div>
      <div class="metric-card ${parseFloat(errorRate) > 5 ? 'error' : ''}">
        <div class="metric-value">${errorRate}%</div>
        <div class="metric-label">Error Rate</div>
      </div>
    </div>

    <h2>Threshold Results</h2>
    <table>
      <tr><th>Metric</th><th>Threshold</th><th>Status</th></tr>
      <tr>
        <td>HTTP Request Duration (P95)</td>
        <td>&lt; 5000ms</td>
        <td><span class="status-${data.metrics.http_req_duration?.thresholds?.['p(95)<5000'] ? 'pass' : 'fail'}">${data.metrics.http_req_duration?.thresholds?.['p(95)<5000'] ? 'PASS' : 'FAIL'}</span></td>
      </tr>
      <tr>
        <td>HTTP Request Failed Rate</td>
        <td>&lt; 10%</td>
        <td><span class="status-${data.metrics.http_req_failed?.thresholds?.['rate<<0.1'] ? 'pass' : 'fail'}">${data.metrics.http_req_failed?.thresholds?.['rate<<0.1'] ? 'PASS' : 'FAIL'}</span></td>
      </tr>
      <tr>
        <td>Error Rate</td>
        <td>&lt; 5%</td>
        <td><span class="status-${data.metrics.errors?.thresholds?.['rate<<0.05'] ? 'pass' : 'fail'}">${data.metrics.errors?.thresholds?.['rate<<0.05'] ? 'PASS' : 'FAIL'}</span></td>
      </tr>
    </table>
  </div>
</body>
</html>`;
}
