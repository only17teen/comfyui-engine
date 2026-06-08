#!/bin/bash
set -euo pipefail

# Semantic versioning release script for ComfyUI Engine

REPO="${GITHUB_REPOSITORY:-only17teen/comfyui-engine}"
DEFAULT_BRANCH="${DEFAULT_BRANCH:-main}"
DRY_RUN="${DRY_RUN:-false}"

# Colors for output
RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
NC='\033[0m' # No Color

log_info() {
    echo -e "${GREEN}[INFO]${NC} $1"
}

log_warn() {
    echo -e "${YELLOW}[WARN]${NC} $1"
}

log_error() {
    echo -e "${RED}[ERROR]${NC} $1"
}

# Get the latest tag
get_latest_tag() {
    git describe --tags --abbrev=0 2>/dev/null || echo "v0.0.0"
}

# Parse version from tag
parse_version() {
    local tag="$1"
    echo "${tag#v}"
}

# Determine version bump based on conventional commits
get_version_bump() {
    local latest_tag="$1"
    local commits
    
    if [ "$latest_tag" = "v0.0.0" ]; then
        commits=$(git log --pretty=format:"%s")
    else
        commits=$(git log "${latest_tag}..HEAD" --pretty=format:"%s")
    fi
    
    local major_bump=false
    local minor_bump=false
    local patch_bump=false
    
    while IFS= read -r commit; do
        if [[ $commit =~ ^(feat|feature)(\(.+\))?!: ]] || [[ $commit =~ ^BREAKING[[:space:]]CHANGE: ]]; then
            major_bump=true
        elif [[ $commit =~ ^(feat|feature)(\(.+\))?: ]]; then
            minor_bump=true
        elif [[ $commit =~ ^(fix|perf|refactor|revert)(\(.+\))?: ]]; then
            patch_bump=true
        elif [[ $commit =~ ^(chore|docs|style|test|build|ci)(\(.+\))?!: ]]; then
            patch_bump=true
        fi
    done <<< "$commits"
    
    if $major_bump; then
        echo "major"
    elif $minor_bump; then
        echo "minor"
    elif $patch_bump; then
        echo "patch"
    else
        echo "patch"
    fi
}

# Calculate new version
calculate_new_version() {
    local current_version="$1"
    local bump_type="$2"
    
    IFS='.' read -r major minor patch <<< "$current_version"
    
    case "$bump_type" in
        major)
            echo "$((major + 1)).0.0"
            ;;
        minor)
            echo "${major}.$((minor + 1)).0"
            ;;
        patch)
            echo "${major}.${minor}.$((patch + 1))"
            ;;
        *)
            echo "${major}.${minor}.$((patch + 1))"
            ;;
    esac
}

# Generate changelog
generate_changelog() {
    local latest_tag="$1"
    local new_version="$2"
    
    local changelog_file="CHANGELOG_${new_version}.md"
    
    {
        echo "## What's Changed"
        echo ""
        
        # Features
        echo "### Features"
        if [ "$latest_tag" = "v0.0.0" ]; then
            git log --pretty=format:"- %s (%h)" --grep="^feat" || echo "- No new features"
        else
            git log "${latest_tag}..HEAD" --pretty=format:"- %s (%h)" --grep="^feat" || echo "- No new features"
        fi
        echo ""
        
        # Bug Fixes
        echo "### Bug Fixes"
        if [ "$latest_tag" = "v0.0.0" ]; then
            git log --pretty=format:"- %s (%h)" --grep="^fix" || echo "- No bug fixes"
        else
            git log "${latest_tag}..HEAD" --pretty=format:"- %s (%h)" --grep="^fix" || echo "- No bug fixes"
        fi
        echo ""
        
        # Performance
        echo "### Performance Improvements"
        if [ "$latest_tag" = "v0.0.0" ]; then
            git log --pretty=format:"- %s (%h)" --grep="^perf" || echo "- No performance improvements"
        else
            git log "${latest_tag}..HEAD" --pretty=format:"- %s (%h)" --grep="^perf" || echo "- No performance improvements"
        fi
        echo ""
        
        # Other
        echo "### Other Changes"
        if [ "$latest_tag" = "v0.0.0" ]; then
            git log --pretty=format:"- %s (%h)" --grep="^\(chore\|docs\|style\|test\|refactor\|build\|ci\)" || echo "- No other changes"
        else
            git log "${latest_tag}..HEAD" --pretty=format:"- %s (%h)" --grep="^\(chore\|docs\|style\|test\|refactor\|build\|ci\)" || echo "- No other changes"
        fi
        echo ""
        
        echo "**Full Changelog**: https://github.com/${REPO}/compare/${latest_tag}...v${new_version}"
    } > "$changelog_file"
    
    echo "$changelog_file"
}

# Main release function
main() {
    log_info "Starting ComfyUI Engine release process"
    
    # Check if we're on the default branch
    current_branch=$(git branch --show-current)
    if [ "$current_branch" != "$DEFAULT_BRANCH" ]; then
        log_warn "Not on $DEFAULT_BRANCH branch (current: $current_branch)"
        read -p "Continue anyway? [y/N] " -n 1 -r
        echo
        if [[ ! $REPLY =~ ^[Yy]$ ]]; then
            exit 1
        fi
    fi
    
    # Fetch latest tags
    log_info "Fetching latest tags..."
    git fetch --tags origin
    
    # Get latest tag and version
    latest_tag=$(get_latest_tag)
    current_version=$(parse_version "$latest_tag")
    
    log_info "Latest tag: $latest_tag"
    log_info "Current version: $current_version"
    
    # Determine version bump
    bump_type=$(get_version_bump "$latest_tag")
    new_version=$(calculate_new_version "$current_version" "$bump_type")
    new_tag="v${new_version}"
    
    log_info "Version bump type: $bump_type"
    log_info "New version: $new_version"
    log_info "New tag: $new_tag"
    
    if [ "$DRY_RUN" = "true" ]; then
        log_warn "Dry run mode - no changes will be made"
        exit 0
    fi
    
    # Confirm release
    read -p "Create release $new_tag? [y/N] " -n 1 -r
    echo
    if [[ ! $REPLY =~ ^[Yy]$ ]]; then
        log_info "Release cancelled"
        exit 0
    fi
    
    # Generate changelog
    log_info "Generating changelog..."
    changelog_file=$(generate_changelog "$latest_tag" "$new_version")
    log_info "Changelog generated: $changelog_file"
    
    # Create and push tag
    log_info "Creating tag $new_tag..."
    git tag -a "$new_tag" -m "Release $new_tag"
    git push origin "$new_tag"
    
    log_info "Tag $new_tag pushed to origin"
    
    # Create GitHub release (if gh CLI is available)
    if command -v gh >/dev/null 2>&1; then
        log_info "Creating GitHub release..."
        gh release create "$new_tag" \
            --title "Release $new_tag" \
            --notes-file "$changelog_file" \
            --repo "$REPO"
        log_info "GitHub release created"
    else
        log_warn "gh CLI not found. GitHub release not created automatically."
        log_info "Please create the release manually at: https://github.com/${REPO}/releases/new?tag=${new_tag}"
    fi
    
    # Update Helm chart version
    log_info "Updating Helm chart version..."
    sed -i "s/^version: .*/version: ${new_version}/" helm/comfyui-engine/Chart.yaml
    sed -i "s/^appVersion: .*/appVersion: \"${new_version}\"/" helm/comfyui-engine/Chart.yaml
    sed -i "s/tag: \".*\"/tag: \"${new_version}\"/" helm/comfyui-engine/values.yaml
    
    git add helm/
    git commit -m "chore(release): update Helm chart to ${new_version} [skip ci]"
    git push origin "$current_branch"
    
    log_info "Helm chart updated"
    
    # Cleanup
    rm -f "$changelog_file"
    
    log_info "Release $new_tag completed successfully!"
    echo
    echo "Next steps:"
    echo "  1. Docker image will be built automatically via GitHub Actions"
    echo "  2. Helm chart will be published to ghcr.io"
    echo "  3. Update your deployments to use the new version"
}

# Show usage
usage() {
    echo "Usage: $0 [OPTIONS]"
    echo
    echo "Options:"
    echo "  -d, --dry-run     Perform a dry run without making changes"
    echo "  -h, --help        Show this help message"
    echo
    echo "Environment variables:"
    echo "  GITHUB_REPOSITORY  GitHub repository (default: only17teen/comfyui-engine)"
    echo "  DEFAULT_BRANCH     Default branch name (default: main)"
    echo "  DRY_RUN            Set to 'true' for dry run mode"
}

# Parse arguments
while [[ $# -gt 0 ]]; do
    case $1 in
        -d|--dry-run)
            DRY_RUN=true
            shift
            ;;
        -h|--help)
            usage
            exit 0
            ;;
        *)
            log_error "Unknown option: $1"
            usage
            exit 1
            ;;
    esac
done

main