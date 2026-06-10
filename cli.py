#!/usr/bin/env python3
"""ComfyUI Engine CLI.

Addresses Issue #50: CLI tool — `comfyui-engine` command line interface.
"""
import argparse
import json
import sys
import os

from sdk import ComfyUIEngineClient

def main():
    parser = argparse.ArgumentParser(description="ComfyUI Engine CLI")
    parser.add_argument("--url", default=os.getenv("COMFYUI_ENGINE_URL", "http://localhost:8000"), help="Engine API URL")
    parser.add_argument("--api-key", default=os.getenv("COMFYUI_ENGINE_API_KEY", ""), help="API Key")
    
    subparsers = parser.add_subparsers(dest="command", required=True)
    
    # status
    subparsers.add_parser("status", help="Get engine status")
    
    # submit
    submit_p = subparsers.add_parser("submit", help="Submit a job")
    submit_p.add_argument("workflow_file", help="Path to workflow JSON file")
    submit_p.add_argument("--priority", type=int, default=1, help="Job priority")
    
    # get
    get_p = subparsers.add_parser("get", help="Get job by ID")
    get_p.add_argument("job_id", help="Job ID")
    
    # cancel
    cancel_p = subparsers.add_parser("cancel", help="Cancel job by ID")
    cancel_p.add_argument("job_id", help="Job ID")
    
    # list
    list_p = subparsers.add_parser("list", help="List recent jobs")
    list_p.add_argument("--limit", type=int, default=50, help="Number of jobs to retrieve")
    list_p.add_argument("--status", help="Filter by status")

    args = parser.parse_args()
    
    try:
        with ComfyUIEngineClient(base_url=args.url, api_key=args.api_key) as client:
            if args.command == "status":
                result = client.get_status()
                print(json.dumps(result, indent=2))
            
            elif args.command == "submit":
                with open(args.workflow_file, "r") as f:
                    workflow = json.load(f)
                result = client.submit_job(workflow, priority=args.priority)
                print(f"Job submitted successfully. ID: {result.get('job_id', result.get('id', 'unknown'))}")
                print(json.dumps(result, indent=2))
                
            elif args.command == "get":
                result = client.get_job(args.job_id)
                print(json.dumps(result, indent=2))
                
            elif args.command == "cancel":
                result = client.cancel_job(args.job_id)
                print(f"Job {args.job_id} cancelled.")
                print(json.dumps(result, indent=2))
                
            elif args.command == "list":
                result = client.list_jobs(limit=args.limit, status=args.status)
                print(json.dumps(result, indent=2))
                
    except Exception as e:
        print(f"Error: {e}", file=sys.stderr)
        sys.exit(1)

if __name__ == "__main__":
    main()
