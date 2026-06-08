from locust import HttpUser, task, between, events
import random
import json
import time
from typing import Dict, Any

class ComfyUIUser(HttpUser):
    """Simulates a user interacting with ComfyUI Engine"""
    
    wait_time = between(1, 5)
    host = "http://localhost:8080"
    
    def on_start(self):
        """Called when a user starts"""
        # Health check
        self.client.get("/health")
        
        # Get available workflows
        response = self.client.get("/api/workflows")
        if response.status_code == 200:
            self.workflows = response.json()
        else:
            self.workflows = ["text_to_image", "image_to_image", "inpainting", "upscaling"]
    
    @task(10)
    def submit_text_to_image(self):
        """Submit a text-to-image generation job"""
        payload = {
            "workflow": "text_to_image",
            "prompt": random.choice([
                "a beautiful landscape with mountains",
                "a futuristic city at night",
                "a serene beach with palm trees",
                "a cozy cabin in the woods",
                "an abstract painting with vibrant colors",
            ]),
            "negative_prompt": "blurry, low quality, distorted",
            "width": random.choice([512, 768, 1024]),
            "height": random.choice([512, 768, 1024]),
            "steps": random.randint(20, 50),
            "cfg_scale": round(random.uniform(5.0, 12.0), 1),
            "seed": random.randint(1, 1000000),
        }
        
        start_time = time.time()
        response = self.client.post(
            "/api/prompt",
            json=payload,
            headers={"Content-Type": "application/json"}
        )
        
        if response.status_code == 200:
            data = response.json()
            prompt_id = data.get("prompt_id")
            
            if prompt_id:
                # Poll for completion
                self._wait_for_completion(prompt_id, start_time)
    
    @task(5)
    def submit_image_to_image(self):
        """Submit an image-to-image generation job"""
        payload = {
            "workflow": "image_to_image",
            "image": "base64_encoded_placeholder",
            "prompt": "enhance quality, add details, improve lighting",
            "strength": round(random.uniform(0.3, 0.8), 2),
            "steps": random.randint(20, 40),
            "cfg_scale": round(random.uniform(5.0, 10.0), 1),
        }
        
        response = self.client.post(
            "/api/prompt",
            json=payload,
            headers={"Content-Type": "application/json"}
        )
        
        if response.status_code == 200:
            data = response.json()
            prompt_id = data.get("prompt_id")
            
            if prompt_id:
                self._wait_for_completion(prompt_id, time.time())
    
    @task(3)
    def submit_inpainting(self):
        """Submit an inpainting job"""
        payload = {
            "workflow": "inpainting",
            "image": "base64_encoded_placeholder",
            "mask": "base64_encoded_placeholder",
            "prompt": "fill the masked area with appropriate content",
            "steps": random.randint(25, 50),
            "denoise": round(random.uniform(0.5, 1.0), 2),
        }
        
        response = self.client.post(
            "/api/prompt",
            json=payload,
            headers={"Content-Type": "application/json"}
        )
        
        if response.status_code == 200:
            data = response.json()
            prompt_id = data.get("prompt_id")
            
            if prompt_id:
                self._wait_for_completion(prompt_id, time.time())
    
    @task(2)
    def submit_upscaling(self):
        """Submit an upscaling job"""
        payload = {
            "workflow": "upscaling",
            "image": "base64_encoded_placeholder",
            "scale": random.choice([2, 4]),
            "denoise": round(random.uniform(0.1, 0.5), 2),
            "model": random.choice(["RealESRGAN_x4plus", "ESRGAN_SRx4", "SwinIR_4x"]),
        }
        
        response = self.client.post(
            "/api/prompt",
            json=payload,
            headers={"Content-Type": "application/json"}
        )
        
        if response.status_code == 200:
            data = response.json()
            prompt_id = data.get("prompt_id")
            
            if prompt_id:
                self._wait_for_completion(prompt_id, time.time())
    
    @task(1)
    def submit_batch_job(self):
        """Submit a batch processing job"""
        batch_size = random.randint(2, 5)
        prompts = [
            "a cat sitting on a windowsill",
            "a dog playing in a park",
            "a bird flying over a forest",
            "a fish swimming in an aquarium",
            "a butterfly landing on a flower",
        ]
        
        payload = {
            "workflow": "text_to_image",
            "batch_size": batch_size,
            "prompts": random.sample(prompts, batch_size),
            "width": 512,
            "height": 512,
            "steps": 20,
            "cfg_scale": 7.5,
        }
        
        response = self.client.post(
            "/api/prompt",
            json=payload,
            headers={"Content-Type": "application/json"}
        )
        
        if response.status_code == 200:
            data = response.json()
            prompt_ids = data.get("prompt_ids", [])
            
            for prompt_id in prompt_ids:
                self._wait_for_completion(prompt_id, time.time())
    
    @task(5)
    def check_queue_status(self):
        """Check queue status"""
        response = self.client.get("/api/queue")
        
        if response.status_code == 200:
            data = response.json()
            queue_remaining = data.get("queue_remaining", 0)
            
            # Record queue depth as a custom metric
            events.request.fire(
                request_type="queue",
                name="queue_depth",
                response_time=0,
                response_length=0,
                context={"queue_remaining": queue_remaining}
            )
    
    @task(3)
    def get_system_info(self):
        """Get system information"""
        self.client.get("/api/system/info")
    
    @task(2)
    def get_gpu_metrics(self):
        """Get GPU metrics"""
        response = self.client.get("/api/system/gpu")
        
        if response.status_code == 200:
            data = response.json()
            if "gpus" in data:
                for gpu in data["gpus"]:
                    gpu_utilization = gpu.get("utilization", 0)
                    gpu_memory = gpu.get("memory_used", 0)
                    
                    # Record GPU metrics
                    events.request.fire(
                        request_type="gpu",
                        name=f"gpu_{gpu.get('id', 0)}_utilization",
                        response_time=0,
                        response_length=0,
                        context={
                            "utilization": gpu_utilization,
                            "memory_used": gpu_memory
                        }
                    )
    
    @task(1)
    def get_model_list(self):
        """Get available models"""
        self.client.get("/api/models")
    
    @task(1)
    def websocket_test(self):
        """Test WebSocket connection"""
        # Note: Locust doesn't natively support WebSockets
        # This is a placeholder for WebSocket testing
        # In practice, you'd use a separate WebSocket client
        pass
    
    def _wait_for_completion(self, prompt_id: str, start_time: float):
        """Wait for a job to complete"""
        max_attempts = 60
        attempts = 0
        
        while attempts < max_attempts:
            response = self.client.get(f"/api/history/{prompt_id}")
            
            if response.status_code == 200:
                data = response.json()
                
                if prompt_id in data and "outputs" in data[prompt_id]:
                    # Job completed
                    completion_time = (time.time() - start_time) * 1000
                    
                    events.request.fire(
                        request_type="inference",
                        name="inference_completion",
                        response_time=completion_time,
                        response_length=0,
                        context={"prompt_id": prompt_id}
                    )
                    return
            
            time.sleep(1)
            attempts += 1
        
        # Job timed out
        events.request.fire(
            request_type="inference",
            name="inference_timeout",
            response_time=(time.time() - start_time) * 1000,
            response_length=0,
            exception=Exception("Inference timeout")
        )

class ComfyUIAdminUser(HttpUser):
    """Simulates an admin user performing management tasks"""
    
    wait_time = between(10, 30)
    host = "http://localhost:8080"
    
    @task(5)
    def get_system_metrics(self):
        """Get system metrics"""
        self.client.get("/api/metrics")
    
    @task(3)
    def get_queue_details(self):
        """Get detailed queue information"""
        self.client.get("/api/queue/details")
    
    @task(2)
    def get_worker_status(self):
        """Get worker status"""
        self.client.get("/api/workers")
    
    @task(1)
    def trigger_gc(self):
        """Trigger garbage collection"""
        self.client.post("/api/admin/gc", json={})
    
    @task(1)
    def clear_cache(self):
        """Clear cache"""
        self.client.post("/api/admin/cache/clear", json={})
    
    @task(1)
    def reload_models(self):
        """Reload models"""
        self.client.post("/api/admin/models/reload", json={})

class ComfyUIStressUser(HttpUser):
    """Simulates a stress test user with rapid requests"""
    
    wait_time = between(0.1, 0.5)
    host = "http://localhost:8080"
    
    @task(10)
    def rapid_health_checks(self):
        """Rapid health checks"""
        self.client.get("/health")
    
    @task(5)
    def rapid_queue_checks(self):
        """Rapid queue checks"""
        self.client.get("/api/queue")
    
    @task(1)
    def rapid_inference(self):
        """Rapid inference requests"""
        payload = {
            "workflow": "text_to_image",
            "prompt": "stress test",
            "width": 512,
            "height": 512,
            "steps": 5,
        }
        
        self.client.post(
            "/api/prompt",
            json=payload,
            headers={"Content-Type": "application/json"}
        )

# Event handlers
@events.request.add_listener
def on_request(request_type, name, response_time, response_length, response, context, exception, **kwargs):
    """Handle request events"""
    if exception:
        print(f"Request failed: {request_type} {name} - {exception}")

@events.test_start.add_listener
def on_test_start(environment, **kwargs):
    """Called when the test starts"""
    print(f"Starting load test with {environment.runner.target_user_count} users")

@events.test_stop.add_listener
def on_test_stop(environment, **kwargs):
    """Called when the test stops"""
    print("Load test completed")
    print(f"Total requests: {environment.runner.stats.total.num_requests}")
    print(f"Failed requests: {environment.runner.stats.total.num_failures}")
    print(f"Average response time: {environment.runner.stats.total.avg_response_time:.2f}ms")