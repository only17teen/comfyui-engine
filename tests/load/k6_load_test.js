import http from 'k6/http';
import { check, sleep, group } from 'k6';
import { Rate, Trend, Counter } from 'k6/metrics';
import { randomIntBetween } from 'https://jslib.k6.io/k6-utils/1.2.0/index.js';

// Custom metrics
const errorRate = new Rate('errors');
const inferenceLatency = new Trend('inference_latency');
const queueWaitTime = new Trend('queue_wait_time');
const gpuUtilization = new Trend('gpu_utilization');
const requestCounter = new Counter('requests');

// Test configuration
export const options = {
  stages: [
    { duration: '2m', target: 10 },   // Ramp up
    { duration: '5m', target: 50 },   // Steady state
    { duration: '2m', target: 100 },  // Stress test
    { duration: '5m', target: 100 },  // Sustained load
    { duration: '2m', target: 200 },  // Spike test
    { duration: '3m', target: 50 },   // Recovery
    { duration: '2m', target: 0 },     // Ramp down
  ],
  thresholds: {
    http_req_duration: ['p(95)<30000'],  // 95% of requests under 30s
    http_req_failed: ['rate<<0.05'],      // Less than 5% errors
    errors: ['rate<<0.05'],
    inference_latency: ['p(95)<60000'],  // 95% inference under 60s
  },
};

const BASE_URL = __ENV.BASE_URL || 'http://localhost:8080';
const API_KEY = __ENV.API_KEY || '';

const headers = {
  'Content-Type': 'application/json',
  ...(API_KEY && { 'Authorization': `Bearer ${API_KEY}` }),
};

// Sample workflows for testing
const workflows = [
  {
    name: 'text_to_image',
    payload: {
      workflow: 'text_to_image',
      prompt: 'a beautiful landscape with mountains and a lake',
      negative_prompt: 'blurry, low quality',
      width: 512,
      height: 512,
      steps: 20,
      cfg_scale: 7.5,
    }
  },
  {
    name: 'image_to_image',
    payload: {
      workflow: 'image_to_image',
      image: 'base64_encoded_image_data',
      prompt: 'enhance quality, add details',
      strength: 0.75,
      steps: 30,
    }
  },
  {
    name: 'inpainting',
    payload: {
      workflow: 'inpainting',
      image: 'base64_encoded_image_data',
      mask: 'base64_encoded_mask_data',
      prompt: 'fill the masked area with appropriate content',
      steps: 25,
    }
  },
  {
    name: 'upscaling',
    payload: {
      workflow: 'upscaling',
      image: 'base64_encoded_image_data',
      scale: 2,
      denoise: 0.1,
    }
  }
];

export function setup() {
  // Health check
  const healthCheck = http.get(`${BASE_URL}/health`);
  check(healthCheck, {
    'health check status is 200': (r) => r.status === 200,
    'health check response is healthy': (r) => {
      const body = JSON.parse(r.body);
      return body.status === 'healthy';
    },
  });

  // Get system info
  const systemInfo = http.get(`${BASE_URL}/api/system/info`);
  check(systemInfo, {
    'system info retrieved': (r) => r.status === 200,
  });

  return {
    systemInfo: JSON.parse(systemInfo.body),
  };
}

export default function(data) {
  group('Health Check', () => {
    const health = http.get(`${BASE_URL}/health`);
    check(health, {
      'health check passes': (r) => r.status === 200,
    });
  });

  group('Queue Status', () => {
    const queueStatus = http.get(`${BASE_URL}/api/queue`);
    check(queueStatus, {
      'queue status retrieved': (r) => r.status === 200,
    });

    if (queueStatus.status === 200) {
      const body = JSON.parse(queueStatus.body);
      queueWaitTime.add(body.queue_remaining || 0);
    }
  });

  group('Submit Inference Job', () => {
    const workflow = workflows[randomIntBetween(0, workflows.length - 1)];
    
    const startTime = Date.now();
    const response = http.post(
      `${BASE_URL}/api/prompt`,
      JSON.stringify(workflow.payload),
      { headers }
    );
    const endTime = Date.now();

    const success = check(response, {
      'inference job submitted': (r) => r.status === 200,
      'inference job has prompt_id': (r) => {
        const body = JSON.parse(r.body);
        return body.prompt_id !== undefined;
      },
    });

    errorRate.add(!success);
    requestCounter.add(1);

    if (success) {
      const body = JSON.parse(response.body);
      const promptId = body.prompt_id;

      // Poll for completion
      let completed = false;
      let attempts = 0;
      const maxAttempts = 60;

      while (!completed && attempts < maxAttempts) {
        sleep(1);
        attempts++;

        const history = http.get(`${BASE_URL}/api/history/${promptId}`);
        
        if (history.status === 200) {
          const historyBody = JSON.parse(history.body);
          
          if (historyBody[promptId] && historyBody[promptId].outputs) {
            completed = true;
            inferenceLatency.add(endTime - startTime);
          }
        }
      }

      if (!completed) {
        errorRate.add(1);
        console.log(`Job ${promptId} did not complete within timeout`);
      }
    }
  });

  group('GPU Metrics', () => {
    const metrics = http.get(`${BASE_URL}/api/system/gpu`);
    check(metrics, {
      'GPU metrics retrieved': (r) => r.status === 200,
    });

    if (metrics.status === 200) {
      const body = JSON.parse(metrics.body);
      if (body.gpus && body.gpus.length > 0) {
        body.gpus.forEach(gpu => {
          gpuUtilization.add(gpu.utilization || 0);
        });
      }
    }
  });

  group('Model List', () => {
    const models = http.get(`${BASE_URL}/api/models`);
    check(models, {
      'models retrieved': (r) => r.status === 200,
    });
  });

  sleep(randomIntBetween(1, 5));
}

export function teardown(data) {
  // Final health check
  const health = http.get(`${BASE_URL}/health`);
  check(health, {
    'system still healthy after test': (r) => r.status === 200,
  });

  console.log('Load test completed');
  console.log(`System info: ${JSON.stringify(data.systemInfo)}`);
}