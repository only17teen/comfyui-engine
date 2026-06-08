#!/bin/bash
set -euo pipefail

# Pre-commit hook for conventional commits
# Install: ln -s ../../scripts/pre-commit.sh .git/hooks/pre-commit

# Colors
RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
NC='\033[0m'

# Conventional commit types
VALID_TYPES="feat|fix|docs|style|refactor|perf|test|build|ci|chore|revert|release"

# Regex pattern for conventional commits
CONVENTIONAL_COMMIT_REGEX="^(${VALID_TYPES})(\(.+\))?!?: .+"

# Get the commit message
commit_msg_file="$1"
commit_msg=$(cat "$commit_msg_file")

# Check if commit message follows conventional commits
if ! echo "$commit_msg" | grep -qE "$CONVENTIONAL_COMMIT_REGEX"; then
    echo -e "${RED}Error: Commit message does not follow conventional commits format.${NC}"
    echo
    echo "Expected format: <type>(<<scope>): <description>"
    echo
    echo "Valid types:"
    echo "  feat:     A new feature"
    echo "  fix:      A bug fix"
    echo "  docs:     Documentation only changes"
    echo "  style:    Changes that do not affect the meaning of the code"
    echo "  refactor: A code change that neither fixes a bug nor adds a feature"
    echo "  perf:     A code change that improves performance"
    echo "  test:     Adding missing tests or correcting existing tests"
    echo "  build:    Changes that affect the build system or external dependencies"
    echo "  ci:       Changes to CI configuration files and scripts"
    echo "  chore:    Other changes that don't modify src or test files"
    echo "  revert:   Reverts a previous commit"
    echo "  release:  Release commit"
    echo
    echo "Examples:"
    echo "  feat: add new GPU optimization"
    echo "  fix(api): resolve memory leak in inference"
    echo "  feat!: breaking change to API"
    echo "  chore(deps): update dependencies"
    echo
    echo "Your commit message:"
    echo "  $commit_msg"
    exit 1
fi

# Check commit message length
if [ ${#commit_msg} -gt 72 ]; then
    echo -e "${YELLOW}Warning: Commit message is longer than 72 characters.${NC}"
    echo "Consider keeping the subject line under 72 characters for better readability."
fi

echo -e "${GREEN}Commit message follows conventional commits format.${NC}"
exit 0