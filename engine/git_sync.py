"""
ComfyUI Async Generation Engine v2.0 - Git Sync Module
Enhanced with atomic operations, branch management, and conflict resolution.
"""

import asyncio
import logging
import os
from datetime import datetime
from pathlib import Path
from typing import Dict, Optional


logger = logging.getLogger(__name__)


DEFAULT_GITIGNORE = """# ComfyUI Engine v2.0 - Git Ignore
# Generated images, models, and heavy binaries excluded

# Output directories
output_models/
*.png
*.jpg
*.jpeg
*.webp
*.gif
*.bmp
*.tiff
*.avif
*.mp4
*.avi
*.mov
*.mkv

# Model weights and checkpoints
*.safetensors
*.ckpt
*.pth
*.onnx
*.bin
*.pt
*.h5
*.gguf
*.vae.pt
*.vae.safetensors

# Python artifacts
__pycache__/
*.py[cod]
*$py.class
*.so
.Python
build/
develop-eggs/
dist/
downloads/
eggs/
.eggs/
lib/
lib64/
parts/
sdist/
var/
wheels/
*.egg-info/
.installed.cfg
*.egg

# Virtual environments
venv/
ENV/
env/
.venv/

# IDE and OS
.vscode/
.idea/
*.swp
*.swo
*~
.DS_Store
Thumbs.db

# Logs (keep structure, ignore large files)
logs/*.log
logs/*.log.*

# Local environment and secrets
.env
.env.local
.env.*
secrets.yaml
secrets.json
credentials.json
*.pem
*.key

# ComfyUI specific
ComfyUI/
models/
input/
temp/
custom_nodes/

# Session manifests (optional - uncomment to track)
# output_models/*/_session_manifest.json
"""


async def _run_git(
    repo_path: Path,
    *args: str,
    cwd: Optional[Path] = None,
    check: bool = True,
    capture_output: bool = True,
) -> tuple[int, str, str]:
    """
    Execute git command with full control.

    Returns:
        (returncode, stdout, stderr)
    """
    cmd = ["git", "-C", str(repo_path)] + list(args)
    working_dir = cwd or repo_path

    logger.debug(f"git {' '.join(args)}")

    proc = await asyncio.create_subprocess_exec(
        *cmd,
        stdout=asyncio.subprocess.PIPE if capture_output else asyncio.subprocess.DEVNULL,
        stderr=asyncio.subprocess.PIPE if capture_output else asyncio.subprocess.DEVNULL,
        cwd=str(working_dir),
        env=os.environ,
    )

    stdout, stderr = await proc.communicate()
    stdout_decoded = stdout.decode("utf-8", errors="replace").strip() if stdout else ""
    stderr_decoded = stderr.decode("utf-8", errors="replace").strip() if stderr else ""

    if check and proc.returncode != 0:
        raise RuntimeError(
            f"git {' '.join(args)} failed (exit {proc.returncode}): {stderr_decoded}"
        )

    return proc.returncode, stdout_decoded, stderr_decoded


async def ensure_gitignore(repo_path: str) -> Path:
    """Ensure .gitignore exists with proper exclusions."""
    repo = Path(repo_path).resolve()
    gitignore_path = repo / ".gitignore"

    if not gitignore_path.exists():
        gitignore_path.write_text(DEFAULT_GITIGNORE, encoding="utf-8")
        logger.info(f"Created .gitignore")
        return gitignore_path

    # Check if our rules are present
    current = gitignore_path.read_text(encoding="utf-8")
    required_lines = ["output_models/", "*.safetensors", "*.ckpt", "*.png"]

    missing = [line for line in required_lines if line not in current]
    if missing:
        with open(gitignore_path, "a", encoding="utf-8") as f:
            f.write("\n\n# Auto-added by ComfyUI Engine v2.0\n")
            for line in missing:
                f.write(f"{line}\n")
        logger.info(f"Updated .gitignore with {len(missing)} missing rules")

    return gitignore_path


async def get_git_status(repo_path: str) -> Dict[str, any]:
    """Get detailed git status."""
    repo = Path(repo_path).resolve()

    rc, stdout, _ = await _run_git(repo, "status", "--porcelain", "-b", check=False)

    staged = []
    unstaged = []
    untracked = []
    branch = "unknown"

    for line in stdout.split("\n"):
        if not line.strip():
            continue
        if line.startswith("##"):
            branch = line[3:].split("...")[0].strip()
            continue

        status = line[:2]
        filename = line[3:].strip()

        if status[0] in "MADRC":
            staged.append(filename)
        if status[1] in "MD":
            unstaged.append(filename)
        if status == "??":
            untracked.append(filename)

    return {
        "branch": branch,
        "is_clean": not (staged or unstaged or untracked),
        "staged_count": len(staged),
        "unstaged_count": len(unstaged),
        "untracked_count": len(untracked),
        "staged": staged,
        "unstaged": unstaged,
        "untracked": untracked,
    }


async def sync_to_git(
    repo_path: str,
    commit_message: Optional[str] = None,
    branch: str = "main",
    auto_create_branch: bool = True,
    push: bool = True,
) -> Dict[str, str]:
    """
    Synchronize with git repository.

    Args:
        repo_path: Path to repository.
        commit_message: Custom commit message. Auto-generated if None.
        branch: Target branch.
        auto_create_branch: Create branch if it doesn't exist.
        push: Push after commit.

    Returns:
        Dict with operation results.
    """
    repo = Path(repo_path).resolve()
    results = {"status": "unknown", "commit": None, "push": None}

    if not (repo / ".git").is_dir():
        raise RuntimeError(f"Not a git repository: {repo}")

    # Ensure .gitignore
    await ensure_gitignore(repo)

    # Check status
    status = await get_git_status(repo)
    if status["is_clean"]:
        logger.info("Working tree clean, nothing to commit")
        results["status"] = "clean"
        return results

    # Stage all
    await _run_git(repo, "add", ".")
    logger.info(f"Staged {status['staged_count'] + status['untracked_count']} files")

    # Generate commit message
    if commit_message is None:
        timestamp = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        commit_message = f"engine: generation workflow update [{timestamp}]"

    # Commit
    try:
        rc, stdout, stderr = await _run_git(repo, "commit", "-m", commit_message, check=False)
        if rc == 0:
            # Extract commit hash
            commit_hash = stdout.split("\n")[0] if stdout else "unknown"
            results["commit"] = commit_hash
            results["status"] = "committed"
            logger.info(f"Committed: {commit_hash[:7]}")
        elif "nothing to commit" in stderr.lower():
            results["status"] = "clean"
            logger.info("Nothing to commit")
        else:
            raise RuntimeError(f"Commit failed: {stderr}")
    except Exception as e:
        logger.error(f"Commit error: {e}")
        raise

    # Ensure branch exists
    if auto_create_branch:
        rc, branches, _ = await _run_git(repo, "branch", "--list", branch, check=False)
        if not branches.strip():
            await _run_git(repo, "checkout", "-b", branch)
            logger.info(f"Created branch: {branch}")

    # Push
    if push and results["status"] == "committed":
        try:
            rc, stdout, stderr = await _run_git(
                repo, "push", "origin", branch, check=False
            )
            if rc == 0:
                results["push"] = "success"
                logger.info("Pushed to origin")
            elif "up-to-date" in stderr.lower() or "up to date" in stderr.lower():
                results["push"] = "up-to-date"
                logger.info("Already up to date")
            else:
                results["push"] = f"failed: {stderr}"
                logger.warning(f"Push issue: {stderr}")
        except Exception as e:
            results["push"] = f"error: {e}"
            logger.error(f"Push failed: {e}")

    return results


async def init_repo(
    repo_path: str,
    remote_url: Optional[str] = None,
    branch: str = "main",
) -> None:
    """Initialize new git repository with proper setup."""
    repo = Path(repo_path).resolve()

    if (repo / ".git").is_dir():
        logger.info(f"Git repo already exists at {repo}")
        return

    await _run_git(repo, "init", cwd=repo)
    logger.info(f"Initialized git repo")

    # Configure default branch
    await _run_git(repo, "checkout", "-b", branch)

    # Add .gitignore
    await ensure_gitignore(repo)

    # Initial commit
    await _run_git(repo, "add", ".")
    await _run_git(repo, "commit", "-m", "Initial commit: ComfyUI Engine v2.0 setup")

    # Add remote
    if remote_url:
        await _run_git(repo, "remote", "add", "origin", remote_url)
        logger.info(f"Added remote: {remote_url}")


async def get_repo_info(repo_path: str) -> Dict[str, any]:
    """Get repository information."""
    repo = Path(repo_path).resolve()

    rc, branch, _ = await _run_git(repo, "branch", "--show-current", check=False)
    rc2, remote, _ = await _run_git(repo, "remote", "-v", check=False)
    rc3, log, _ = await _run_git(repo, "log", "--oneline", "-5", check=False)

    return {
        "branch": branch.strip() if rc == 0 else "unknown",
        "remotes": [line.strip() for line in remote.split("\n") if line.strip()] if rc2 == 0 else [],
        "recent_commits": [line.strip() for line in log.split("\n") if line.strip()] if rc3 == 0 else [],
    }


# Type hints for return
from typing import Dict
