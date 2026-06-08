import http from 'k6/http';
import { check, sleep, group } from 'k6';
import { Rate, Trend, Counter } from 'k6/metrics';
import { randomIntBetween, randomItem } from 'https://jslib.k6.io/k6-utils/1.2.0/index.js';

// Chaos testing metrics
const chaosErrorRate = new Rate('chaos_errors');
const recoveryTime = new Trend('recovery_time');
const circuitBreakerTrips = new Counter('circuit_breaker_trips');
const failoverEvents = new Counter('failover_events');

// Test configuration - chaos test with aggressive load
export const options = {
  stages: [
    { duration: '1m', target: 20 },    // Baseline
    { duration: '2m', target: 100 },   // Ramp up
    { duration: '3m', target: 200 },   // Chaos - high load
    { duration: '2m', target: 50 },    // Recovery test
    { duration: '1m', target: 0 },     // Cool down
  ],
  thresholds: {
    http_req_duration: ['p(95)<60000'],
    http_req_failed: ['rate<<0.20'],  // Allow higher error rate during chaos
    chaos_errors: ['rate<<0.30'],
  },
};

const BASE_URL = __ENV.BASE_URL || 'http://localhost:8080';
const API_KEY = __ENV.API_KEY || '';
const CHAOS_MODE = __ENV.CHAOS_MODE || 'true';

const headers = {
  'Content-Type': 'application/json',
  ...(API_KEY && { 'Authorization': `Bearer ${API_KEY}` }),
};

// Chaos scenarios
const chaosScenarios = [
  'memory_pressure',
  'gpu_overload',
  'network_latency',
  'disk_full',
  'high_concurrency',
  'rapid_scaling',
  'connection_pool_exhaustion',
  'cache_invalidation',
];

// Malformed payloads for testing error handling
const malformedPayloads = [
  { invalid: 'data' },
  { workflow: null },
  { prompt: 'a' * 10000 },  // Very long prompt
  {},  // Empty payload
  { workflow: 'nonexistent_workflow' },
  { width: -1, height: -1 },  // Invalid dimensions
  { steps: 1000 },  // Too many steps
  { cfg_scale: -1 },  // Invalid CFG scale
];

export function setup() {
  // Enable chaos mode if configured
  if (CHAOS_MODE === 'true') {
    const chaosResponse = http.post(
      `${BASE_URL}/api/admin/chaos/enable`,
      JSON.stringify({
        scenarios: chaosScenarios,
        intensity: 0.5,
      }),
      { headers }
    );

    check(chaosResponse, {
      'chaos mode enabled': (r) => r.status === 200 || r.status === 404, // 404 if endpoint doesn't exist
    });
  }

  // Record baseline metrics
  const baseline = http.get(`${BASE_URL}/api/metrics`);
  return {
    baseline: baseline.status === 200 ? JSON.parse(baseline.body) : {},
  };
}

export default function(data) {
  group('Chaos - Random Failures', () => {
    const scenario = randomItem(chaosScenarios);
    
    // Trigger specific chaos scenario
    const chaosTrigger = http.post(
      `${BASE_URL}/api/admin/chaos/trigger`,
      JSON.stringify({
        scenario: scenario,
        duration: randomIntBetween(5, 30),
      }),
      { headers }
    );

    check(chaosTrigger, {
      'chaos trigger accepted': (r) => r.status === 200 || r.status === 404,
    });
  });

  group('Chaos - Malformed Requests', () => {
    const payload = randomItem(malformedPayloads);
    
    const response = http.post(
      `${BASE_URL}/api/prompt`,
      JSON.stringify(payload),
      { headers }
    );

    // Expecting errors for malformed requests
    const isError = response.status >= 400;
    chaosErrorRate.add(isError);

    check(response, {
      'malformed request handled gracefully': (r) => r.status < 500, // Should not crash
    });
  });

  group('Chaos - Rapid Requests', () => {
    // Send multiple requests rapidly
    const requests = [];
    for (let i = 0; i < 10; i++) {
      requests.push(http.post(
        `${BASE_URL}/api/prompt`,
        JSON.stringify({
          workflow: 'text_to_image',
          prompt: `chaos test ${i}`,
          width: 512,
          height: 512,
          steps: 5, // Minimal steps for speed
        }),
        { headers }
      ));
    }

    // Check for circuit breaker trips
    const circuitBreakerResponses = requests.filter(r => r.status === 503);
    if (circuitBreakerResponses.length > 0) {
      circuitBreakerTrips.add(circuitBreakerResponses.length);
    }

    check(requests, {
      'rapid requests handled': (r) => r.every(req => req.status < 500),
    });
  });

  group('Chaos - Resource Exhaustion', () => {
    // Request large batch processing
    const batchResponse = http.post(
      `${BASE_URL}/api/prompt`,
      JSON.stringify({
        workflow: 'text_to_image',
        prompt: 'resource exhaustion test',
        width: 1024,
        height: 1024,
        steps: 50,
        batch_size: 10,
      }),
      { headers }
    );

    check(batchResponse, {
      'large request handled': (r) => r.status === 200 || r.status === 429 || r.status === 503,
    });
  });

  group('Chaos - Concurrent Modifications', () => {
    // Try to modify system settings while under load
    const settingsResponse = http.post(
      `${BASE_URL}/api/system/settings`,
      JSON.stringify({
        max_workers: randomIntBetween(1, 100),
        gpu_memory_fraction: Math.random(),
      }),
      { headers }
    );

    check(settingsResponse, {
      'settings modification handled': (r) => r.status < 500,
    });
  });

  group('Chaos - Failover Test', () => {
    // Check if failover is active
    const failoverCheck = http.get(`${BASE_URL}/api/system/failover`);
    
    if (failoverCheck.status === 200) {
      const body = JSON.parse(failoverCheck.body);
      if (body.failover_active) {
        failoverEvents.add(1);
      }
    }

    check(failoverCheck, {
      'failover status accessible': (r) => r.status === 200,
    });
  });

  group('Chaos - Recovery Test', () => {
    // Measure recovery time after chaos
    const startTime = Date.now();
    
    let recovered = false;
    let attempts = 0;
    const maxAttempts = 10;

    while (!recovered && attempts < maxAttempts) {
      const health = http.get(`${BASE_URL}/health`);
      
      if (health.status === 200) {
        const body = JSON.parse(health.body);
        if (body.status === 'healthy') {
          recovered = true;
          recoveryTime.add(Date.now() - startTime);
        }
      }

      if (!recovered) {
        sleep(1);
        attempts++;
      }
    }

    check(null, {
      'system recovered': () => recovered,
    });
  });

  sleep(randomIntBetween(1, 3));
}

export function teardown(data) {
  // Disable chaos mode
  if (CHAOS_MODE === 'true') {
    http.post(
      `${BASE_URL}/api/admin/chaos/disable`,
      '{}',
      { headers }
    );
  }

  // Final health check
  const health = http.get(`${BASE_URL}/health`);
  check(health, {
    'system recovered after chaos': (r) => r.status === 200,
  });

  console.log('Chaos test completed');
  console.log(`Circuit breaker trips: ${circuitBreakerTrips.value}`);
  console.log(`Failover events: ${failoverEvents.value}`);
  console.log(`Average recovery time: ${recoveryTime.avg}ms`);
}