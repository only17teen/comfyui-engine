import http from 'k6/http';
import { check, sleep, group } from 'k6';
import { Rate, Counter } from 'k6/metrics';
import { randomIntBetween } from 'https://jslib.k6.io/k6-utils/1.2.0/index.js';

const chaosErrors = new Rate('chaos_errors');
const recoveryTime = new Counter('recovery_time_ms');
const circuitBreakerTrips = new Counter('circuit_breaker_trips');

const BASE_URL = __ENV.BASE_URL || 'http://localhost:8000';
const API_KEY = __ENV.API_KEY || 'test-api-key';
const CHAOS_TYPE = __ENV.CHAOS_TYPE || 'all';

export const options = {
  scenarios: {
    chaos_injection: {
      executor: 'constant-vus',
      vus: 5,
      duration: '10m',
      tags: { test_type: 'chaos' },
    },
  },
  thresholds: {
    http_req_failed: ['rate<<0.3'],
    chaos_errors: ['rate<<0.5'],
  },
};

const CHAOS_ACTIONS = [
  {
    name: 'memory_pressure',
    description: 'Submit workflows with extremely large batch sizes',
    execute: () => {
      const payload = {
        workflow: {
          '1': { class_type: 'KSampler', inputs: { seed: 42, steps: 50, cfg: 7.5, sampler_name: 'euler', scheduler: 'normal', denoise: 1.0, model: ['2', 0], positive: ['3', 0], negative: ['4', 0], latent_image: ['5', 0] } },
          '2': { class_type: 'CheckpointLoaderSimple', inputs: { ckpt_name: 'v1-5-pruned-emaonly.ckpt' } },
          '3': { class_type: 'CLIPTextEncode', inputs: { text: 'test', clip: ['2', 1] } },
          '4': { class_type: 'CLIPTextEncode', inputs: { text: 'negative', clip: ['2', 1] } },
          '5': { class_type: 'EmptyLatentImage', inputs: { width: 2048, height: 2048, batch_size: 16 } },
        },
        priority: 5,
        metadata: { chaos: 'memory_pressure' },
      };

      return http.post(`${BASE_URL}/api/v1/jobs`, JSON.stringify(payload), {
        headers: { 'Content-Type': 'application/json', 'X-API-Key': API_KEY },
        timeout: '10s',
      });
    },
  },
  {
    name: 'rapid_requests',
    description: 'Send rapid-fire requests to test rate limiting',
    execute: () => {
      const requests = [];
      for (let i = 0; i < 20; i++) {
        const payload = {
          workflow: {
            '1': { class_type: 'KSampler', inputs: { seed: i, steps: 10, cfg: 7.0, sampler_name: 'euler', scheduler: 'normal', denoise: 1.0, model: ['2', 0], positive: ['3', 0], negative: ['4', 0], latent_image: ['5', 0] } },
            '2': { class_type: 'CheckpointLoaderSimple', inputs: { ckpt_name: 'v1-5-pruned-emaonly.ckpt' } },
            '3': { class_type: 'CLIPTextEncode', inputs: { text: `rapid test ${i}`, clip: ['2', 1] } },
            '4': { class_type: 'CLIPTextEncode', inputs: { text: 'negative', clip: ['2', 1] } },
            '5': { class_type: 'EmptyLatentImage', inputs: { width: 512, height: 512, batch_size: 1 } },
          },
          priority: 1,
          metadata: { chaos: 'rapid_requests' },
        };
        requests.push(['POST', `${BASE_URL}/api/v1/jobs`, JSON.stringify(payload), { headers: { 'Content-Type': 'application/json', 'X-API-Key': API_KEY } }]);
      }
      return http.batch(requests);
    },
  },
  {
    name: 'invalid_workflows',
    description: 'Submit malformed workflows to test error handling',
    execute: () => {
      const invalidPayloads = [
        { workflow: 'not-an-object', priority: 1 },
        { workflow: {}, priority: 1 },
        { workflow: { '1': { class_type: 'NonExistentNode' } }, priority: 1 },
        { workflow: { '1': { class_type: 'KSampler', inputs: { model: 'invalid' } } }, priority: 1 },
        { not_workflow: true },
      ];

      const req = invalidPayloads[randomIntBetween(0, invalidPayloads.length - 1)];
      return http.post(`${BASE_URL}/api/v1/jobs`, JSON.stringify(req), {
        headers: { 'Content-Type': 'application/json', 'X-API-Key': API_KEY },
        timeout: '5s',
      });
    },
  },
  {
    name: 'connection_interruption',
    description: 'Test connection resilience with aborted requests',
    execute: () => {
      const payload = {
        workflow: {
          '1': { class_type: 'KSampler', inputs: { seed: 42, steps: 30, cfg: 7.5, sampler_name: 'euler', scheduler: 'normal', denoise: 1.0, model: ['2', 0], positive: ['3', 0], negative: ['4', 0], latent_image: ['5', 0] } },
          '2': { class_type: 'CheckpointLoaderSimple', inputs: { ckpt_name: 'v1-5-pruned-emaonly.ckpt' } },
          '3': { class_type: 'CLIPTextEncode', inputs: { text: 'connection test', clip: ['2', 1] } },
          '4': { class_type: 'CLIPTextEncode', inputs: { text: 'negative', clip: ['2', 1] } },
          '5': { class_type: 'EmptyLatentImage', inputs: { width: 512, height: 512, batch_size: 1 } },
        },
        priority: 3,
        metadata: { chaos: 'connection_interruption' },
      };

      return http.post(`${BASE_URL}/api/v1/jobs`, JSON.stringify(payload), {
        headers: { 'Content-Type': 'application/json', 'X-API-Key': API_KEY },
        timeout: '1s',
      });
    },
  },
  {
    name: 'queue_flooding',
    description: 'Flood queue with many low-priority jobs',
    execute: () => {
      const requests = [];
      for (let i = 0; i < 50; i++) {
        const payload = {
          workflow: {
            '1': { class_type: 'KSampler', inputs: { seed: i, steps: 5, cfg: 7.0, sampler_name: 'euler', scheduler: 'normal', denoise: 1.0, model: ['2', 0], positive: ['3', 0], negative: ['4', 0], latent_image: ['5', 0] } },
            '2': { class_type: 'CheckpointLoaderSimple', inputs: { ckpt_name: 'v1-5-pruned-emaonly.ckpt' } },
            '3': { class_type: 'CLIPTextEncode', inputs: { text: 'flood', clip: ['2', 1] } },
            '4': { class_type: 'CLIPTextEncode', inputs: { text: 'negative', clip: ['2', 1] } },
            '5': { class_type: 'EmptyLatentImage', inputs: { width: 512, height: 512, batch_size: 1 } },
          },
          priority: 5,
          metadata: { chaos: 'queue_flooding' },
        };
        requests.push(['POST', `${BASE_URL}/api/v1/jobs`, JSON.stringify(payload), { headers: { 'Content-Type': 'application/json', 'X-API-Key': API_KEY } }]);
      }
      return http.batch(requests);
    },
  },
];

function getFilteredActions() {
  if (CHAOS_TYPE === 'all') return CHAOS_ACTIONS;
  return CHAOS_ACTIONS.filter(a => a.name === CHAOS_TYPE || CHAOS_TYPE.includes(a.name));
}

function checkSystemHealth() {
  const health = http.get(`${BASE_URL}/health`);
  const metrics = http.get(`${BASE_URL}/metrics`);

  const healthOk = check(health, {
    'health check passes': (r) => r.status === 200,
    'system is healthy': (r) => r.json('status') === 'healthy',
  });

  const metricsOk = check(metrics, {
    'metrics available': (r) => r.status === 200,
  });

  return healthOk && metricsOk;
}

function measureRecovery() {
  const start = Date.now();
  let attempts = 0;
  const maxAttempts = 30;

  while (attempts < maxAttempts) {
    const health = http.get(`${BASE_URL}/health`);
    if (health.status === 200 && health.json('status') === 'healthy') {
      recoveryTime.add(Date.now() - start);
      return true;
    }
    sleep(1);
    attempts++;
  }
  return false;
}

export default function () {
  group('System Health Check', () => {
    const healthy = checkSystemHealth();
    if (!healthy) {
      chaosErrors.add(1);
      console.log('System not healthy before chaos injection');
    }
  });

  const actions = getFilteredActions();
  const action = actions[randomIntBetween(0, actions.length - 1)];

  group(`Chaos: ${action.name}`, () => {
    console.log(`Executing chaos action: ${action.name}`);
    const start = Date.now();
    const res = action.execute();
    const duration = Date.now() - start;

    if (Array.isArray(res)) {
      const allOk = res.every(r => r.status < 500);
      if (!allOk) {
        chaosErrors.add(1);
        console.log(`Chaos action ${action.name} caused errors`);
      }
    } else {
      const success = check(res, {
        'request handled': (r) => r.status < 500,
        'response time reasonable': (r) => duration < 10000,
      });

      if (!success) {
        chaosErrors.add(1);
      }
    }
  });

  group('Recovery Check', () => {
    const recovered = measureRecovery();
    if (!recovered) {
      chaosErrors.add(1);
      console.log('System failed to recover from chaos');
    } else {
      console.log('System recovered successfully');
    }
  });

  sleep(randomIntBetween(2, 5));
}

export function handleSummary(data) {
  const totalChaos = data.metrics.http_reqs?.count || 0;
  const chaosErrorRate = ((data.metrics.chaos_errors?.rate || 0) * 100).toFixed(2);
  const avgRecovery = data.metrics.recovery_time_ms?.avg?.toFixed(0) || 0;

  return {
    'stdout': `Chaos Engineering Results
========================
Total Chaos Actions: ${totalChaos}
Chaos Error Rate: ${chaosErrorRate}%
Avg Recovery Time: ${avgRecovery}ms
Circuit Breaker Trips: ${data.metrics.circuit_breaker_trips?.count || 0}
System Resilience: ${parseFloat(chaosErrorRate) < 30 ? 'ACCEPTABLE' : 'POOR'}
`,
    'chaos-test-results.json': JSON.stringify(data, null, 2),
  };
}
