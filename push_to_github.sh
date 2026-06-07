#!/bin/bash
# Push ComfyUI Engine to GitHub
# Usage: ./push_to_github.sh YOUR_GITHUB_TOKEN

TOKEN=$1
USERNAME="only17teen"
REPO="comfyui-engine"

if [ -z "$TOKEN" ]; then
    echo "Usage: $0 YOUR_GITHUB_TOKEN"
    echo "Get token at: https://github.com/settings/tokens"
    exit 1
fi

cd /workspace/comfyui_engine

# Set remote with token
git remote set-url origin "https://${USERNAME}:${TOKEN}@github.com/${USERNAME}/${REPO}.git"

# Push
git push -u origin main

# Remove token from remote URL for security
git remote set-url origin "https://github.com/${USERNAME}/${REPO}.git"

echo "Done! Repository: https://github.com/${USERNAME}/${REPO}"
