import http from 'k6/http';
import { check, sleep } from 'k6';

// Addresses Issue #48: Load testing suite with k6 scripts

export const options = {
  stages: [
    { duration: '30s', target: 20 }, // simulate ramp-up of traffic from 1 to 20 users over 30 seconds.
    { duration: '1m', target: 20 },  // stay at 20 users for 1 minute
    { duration: '30s', target: 0 },  // ramp-down to 0 users
  ],
  thresholds: {
    http_req_duration: ['p(99)<2000'], // 99% of requests must complete below 2s
  },
};

const BASE_URL = __ENV.BASE_URL || 'http://localhost:8000';

export default function () {
  // 1. Check system status
  let res = http.get(`${BASE_URL}/api/v1/system/status`);
  check(res, { 'status is 200': (r) => r.status === 200 });

  // 2. Submit a dummy job
  let payload = JSON.stringify({
    workflow: { "3": { "class_type": "KSampler", "inputs": { "seed": Math.floor(Math.random() * 10000) } } },
    priority: 1
  });
  let params = { headers: { 'Content-Type': 'application/json' } };
  
  let jobRes = http.post(`${BASE_URL}/api/v1/jobs`, payload, params);
  check(jobRes, { 'job created successfully': (r) => r.status === 200 || r.status === 201 });
  
  sleep(1);
}
