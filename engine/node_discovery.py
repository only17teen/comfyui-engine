"""ComfyUI custom nodes auto-discovery and compatibility checking.

Scans ComfyUI installation for custom nodes, validates their dependencies,
checks version compatibility, and provides installation recommendations.
"""

import asyncio
import importlib
import importlib.util
import inspect
import json
import logging
import os
import re
import subprocess
import sys
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any, Dict, List, Optional, Set, Tuple
from urllib.parse import urlparse

import aiohttp

logger = logging.getLogger(__name__)


@dataclass
class NodeInfo:
    """Information about a discovered custom node."""

    name: str
    display_name: str
    category: str
    description: str = ""
    author: str = ""
    version: str = "unknown"
    repository_url: str = ""
    # Dependency info
    python_dependencies: list[str] = field(default_factory=list)
    custom_node_dependencies: list[str] = field(default_factory=list)
    # Status
    installed: bool = False
    enabled: bool = False
    has_errors: bool = False
    error_message: str = ""
    # ComfyUI version requirements
    comfyui_min_version: str | None = None
    comfyui_max_version: str | None = None
    # Node class mappings
    node_classes: dict[str, str] = field(default_factory=dict)
    # File paths
    module_path: Path | None = None
    config_path: Path | None = None


@dataclass
class CompatibilityReport:
    """Full compatibility report for a ComfyUI installation."""

    comfyui_version: str = "unknown"
    python_version: str = "unknown"
    pytorch_version: str = "unknown"
    cuda_available: bool = False
    cuda_version: str = "unknown"

    nodes: list[NodeInfo] = field(default_factory=list)
    missing_dependencies: list[str] = field(default_factory=list)
    version_conflicts: list[dict[str, Any]] = field(default_factory=list)

    # Summary stats
    total_nodes: int = 0
    installed_nodes: int = 0
    enabled_nodes: int = 0
    error_nodes: int = 0

    def to_dict(self) -> dict[str, Any]:
        return {
            **asdict(self),
            "nodes": [asdict(n) for n in self.nodes],
        }


class NodeRegistry:
    """Registry of known custom nodes with metadata."""

    # Known popular nodes with their repository URLs
    KNOWN_NODES = {
        "ComfyUI-Manager": {
            "repo": "https://github.com/ltdrdata/ComfyUI-Manager",
            "description": "Node manager for ComfyUI",
            "dependencies": [],
        },
        "ComfyUI-Impact-Pack": {
            "repo": "https://github.com/ltdrdata/ComfyUI-Impact-Pack",
            "description": "Custom nodes for detailers and detectors",
            "dependencies": ["ComfyUI-Manager"],
        },
        "ComfyUI-ControlNet-Aux": {
            "repo": "https://github.com/Fannovel16/comfyui_controlnet_aux",
            "description": "ControlNet preprocessors",
            "dependencies": [],
        },
        "ComfyUI-VideoHelperSuite": {
            "repo": "https://github.com/Kosinkadink/ComfyUI-VideoHelperSuite",
            "description": "Video processing utilities",
            "dependencies": [],
        },
        "ComfyUI-AnimateDiff-Evolved": {
            "repo": "https://github.com/Kosinkadink/ComfyUI-AnimateDiff-Evolved",
            "description": "Advanced AnimateDiff implementation",
            "dependencies": ["ComfyUI-VideoHelperSuite"],
        },
        "ComfyUI-IPAdapter-Plus": {
            "repo": "https://github.com/cubiq/ComfyUI_IPAdapter_plus",
            "description": "Enhanced IP-Adapter nodes",
            "dependencies": [],
        },
        "ComfyUI-Efficiency-Nodes": {
            "repo": "https://github.com/jags111/efficiency-nodes-comfyui",
            "description": "Workflow efficiency nodes",
            "dependencies": [],
        },
        "ComfyUI-Comfyroll-Studio": {
            "repo": "https://github.com/Suzie1/ComfyUI_Comfyroll_CustomNodes",
            "description": "Custom nodes collection",
            "dependencies": [],
        },
        "ComfyUI-FaceAnalysis": {
            "repo": "https://github.com/cubiq/ComfyUI_FaceAnalysis",
            "description": "Face analysis and detection nodes",
            "dependencies": [],
        },
        "ComfyUI-LogicUtils": {
            "repo": "https://github.com/aria1th/ComfyUI-LogicUtils",
            "description": "Logic and utility nodes",
            "dependencies": [],
        },
    }

    @classmethod
    def get_node_info(cls, name: str) -> dict[str, Any] | None:
        """Get known node information by name."""
        return cls.KNOWN_NODES.get(name)

    @classmethod
    def search_nodes(cls, query: str) -> list[dict[str, Any]]:
        """Search known nodes by name or description."""
        results = []
        query_lower = query.lower()
        for name, info in cls.KNOWN_NODES.items():
            if (
                query_lower in name.lower()
                or query_lower in info.get("description", "").lower()
            ):
                results.append({"name": name, **info})
        return results


class NodeDiscovery:
    """Discover and analyze custom nodes in a ComfyUI installation."""

    def __init__(self, comfyui_path: Path | None = None):
        """Initialize with path to ComfyUI installation.

        Args:
            comfyui_path: Path to ComfyUI root. Auto-detected if None.
        """
        self.comfyui_path = comfyui_path or self._find_comfyui()
        self.custom_nodes_path = (
            self.comfyui_path / "custom_nodes" if self.comfyui_path else None
        )
        self._session: aiohttp.ClientSession | None = None

    def _find_comfyui(self) -> Path | None:
        """Auto-detect ComfyUI installation path."""
        # Common installation paths
        search_paths = [
            Path.home() / "ComfyUI",
            Path.home() / "comfyui",
            Path.home() / "ComfyUI_windows_portable" / "ComfyUI",
            Path("/opt/ComfyUI"),
            Path("/usr/share/ComfyUI"),
            Path.cwd() / "ComfyUI",
        ]

        # Check environment variable
        if "COMFYUI_PATH" in os.environ:
            env_path = Path(os.environ["COMFYUI_PATH"])
            if env_path.exists():
                return env_path

        for path in search_paths:
            if (path / "main.py").exists() and (path / "nodes.py").exists():
                return path

        logger.warning("Could not auto-detect ComfyUI installation")
        return None

    async def _get_session(self) -> aiohttp.ClientSession:
        """Get or create aiohttp session."""
        if self._session is None or self._session.closed:
            self._session = aiohttp.ClientSession(
                timeout=aiohttp.ClientTimeout(total=30),
            )
        return self._session

    async def scan_nodes(self) -> list[NodeInfo]:
        """Scan for all custom nodes in the installation."""
        if not self.custom_nodes_path or not self.custom_nodes_path.exists():
            logger.error("Custom nodes path not found")
            return []

        nodes = []

        for item in self.custom_nodes_path.iterdir():
            if item.name.startswith(".") or item.name == "__pycache__":
                continue

            try:
                if item.is_dir():
                    node_info = await self._analyze_node_directory(item)
                    if node_info:
                        nodes.append(node_info)
                elif item.suffix == ".py":
                    # Single-file node
                    node_info = await self._analyze_node_file(item)
                    if node_info:
                        nodes.append(node_info)

            except Exception as e:
                logger.warning(f"Error analyzing {item.name}: {e}")
                nodes.append(
                    NodeInfo(
                        name=item.name,
                        display_name=item.name,
                        category="unknown",
                        has_errors=True,
                        error_message=str(e),
                    )
                )

        return nodes

    async def _analyze_node_directory(self, path: Path) -> NodeInfo | None:
        """Analyze a custom node directory."""
        name = path.name

        # Check for __init__.py
        init_file = path / "__init__.py"
        if not init_file.exists():
            # Check for .py files directly
            py_files = list(path.glob("*.py"))
            if not py_files:
                return None

        info = NodeInfo(
            name=name,
            display_name=name,
            category=self._detect_category(name),
            installed=True,
            enabled=True,
            module_path=path,
        )

        # Try to extract metadata from __init__.py or main module
        if init_file.exists():
            await self._extract_metadata(init_file, info)

        # Check for requirements.txt
        req_file = path / "requirements.txt"
        if req_file.exists():
            info.python_dependencies = self._parse_requirements(req_file)

        # Check for node config
        config_file = path / "node_config.json"
        if config_file.exists():
            info.config_path = config_file
            try:
                with open(config_file) as f:
                    config = json.load(f)
                    info.version = config.get("version", "unknown")
                    info.author = config.get("author", "")
                    info.description = config.get("description", "")
                    info.repository_url = config.get("repository", "")
            except Exception:
                pass

        # Check for known node info
        known = NodeRegistry.get_node_info(name)
        if known:
            if not info.description:
                info.description = known.get("description", "")
            if not info.repository_url:
                info.repository_url = known.get("repo", "")
            info.custom_node_dependencies = known.get("dependencies", [])

        # Scan for node class definitions
        info.node_classes = await self._find_node_classes(path)

        return info

    async def _analyze_node_file(self, path: Path) -> NodeInfo | None:
        """Analyze a single-file custom node."""
        name = path.stem

        info = NodeInfo(
            name=name,
            display_name=name,
            category=self._detect_category(name),
            installed=True,
            enabled=True,
            module_path=path,
        )

        await self._extract_metadata(path, info)
        info.node_classes = await self._find_node_classes_in_file(path)

        return info

    async def _extract_metadata(self, file_path: Path, info: NodeInfo) -> None:
        """Extract metadata from a Python file."""
        try:
            content = file_path.read_text(encoding="utf-8", errors="ignore")

            # Look for docstring
            doc_match = re.search(r'"""(.*?)"""', content, re.DOTALL)
            if doc_match and not info.description:
                info.description = doc_match.group(1).strip()[:200]

            # Look for version
            version_match = re.search(r'VERSION\s*=\s*["\'](.+?)["\']', content)
            if version_match:
                info.version = version_match.group(1)

            # Look for author
            author_match = re.search(r'AUTHOR\s*=\s*["\'](.+?)["\']', content)
            if author_match:
                info.author = author_match.group(1)

            # Look for repository URL
            url_match = re.search(r'URL\s*=\s*["\'](https?://.+?)["\']', content)
            if url_match and not info.repository_url:
                info.repository_url = url_match.group(1)

        except Exception as e:
            logger.debug(f"Metadata extraction failed for {file_path}: {e}")

    def _parse_requirements(self, req_file: Path) -> list[str]:
        """Parse requirements.txt file."""
        deps = []
        try:
            with open(req_file) as f:
                for line in f:
                    line = line.strip()
                    if line and not line.startswith("#"):
                        # Remove version specifiers for basic checking
                        dep = re.split(r"[>=<<~=!]", line)[0].strip()
                        if dep:
                            deps.append(dep)
        except Exception as e:
            logger.warning(f"Failed to parse requirements {req_file}: {e}")
        return deps

    async def _find_node_classes(self, path: Path) -> dict[str, str]:
        """Find ComfyUI node class definitions in a directory."""
        classes = {}

        for py_file in path.rglob("*.py"):
            if py_file.name.startswith("."):
                continue
            file_classes = await self._find_node_classes_in_file(py_file)
            classes.update(file_classes)

        return classes

    async def _find_node_classes_in_file(self, path: Path) -> dict[str, str]:
        """Find node class definitions in a single file."""
        classes = {}

        try:
            content = path.read_text(encoding="utf-8", errors="ignore")

            # Look for class definitions that inherit from typical node bases
            # Pattern: class ClassName(...): with NODE_CLASS_MAPPINGS nearby
            class_pattern = re.compile(
                r"class\s+(\w+)\s*\([^)]*(?:NODE|BaseNode|ComfyNode)[^)]*\):",
                re.IGNORECASE,
            )

            for match in class_pattern.finditer(content):
                class_name = match.group(1)
                # Try to find the display name mapping
                mapping_pattern = re.compile(
                    rf'["\']{class_name}["\']\s*:\s*["\']([^"\']+)["\']',
                )
                mapping_match = mapping_pattern.search(content)
                display_name = mapping_match.group(1) if mapping_match else class_name

                classes[class_name] = display_name

        except Exception as e:
            logger.debug(f"Class scanning failed for {path}: {e}")

        return classes

    def _detect_category(self, name: str) -> str:
        """Detect node category from name."""
        name_lower = name.lower()

        categories = {
            "control": "controlnet",
            "ipadapter": "ipadapter",
            "face": "face_processing",
            "video": "video",
            "animate": "animation",
            "efficiency": "utils",
            "logic": "utils",
            "manager": "management",
            "impact": "detection",
            "detector": "detection",
            "segment": "segmentation",
            "upscale": "upscaling",
            "model": "model_management",
        }

        for keyword, category in categories.items():
            if keyword in name_lower:
                return category

        return "custom"

    async def check_dependencies(
        self, nodes: list[NodeInfo]
    ) -> tuple[list[str], list[dict]]:
        """Check Python dependencies for installed nodes.

        Returns:
            Tuple of (missing_packages, version_conflicts)
        """
        missing = []
        conflicts = []

        all_deps: dict[str, set[str]] = {}

        for node in nodes:
            for dep in node.python_dependencies:
                # Normalize package name
                pkg = dep.lower().replace("-", "_").replace(".", "_")
                if pkg not in all_deps:
                    all_deps[pkg] = set()
                all_deps[pkg].add(node.name)

        # Check each dependency
        for pkg, node_sources in all_deps.items():
            try:
                # Try importing the module
                importlib.import_module(pkg)
            except ImportError:
                missing.append(pkg)
                logger.info(
                    f"Missing dependency: {pkg} (required by: {', '.join(node_sources)})"
                )

        return missing, conflicts

    async def check_comfyui_version(self) -> str:
        """Get installed ComfyUI version."""
        if not self.comfyui_path:
            return "unknown"

        # Try git describe
        git_dir = self.comfyui_path / ".git"
        if git_dir.exists():
            try:
                result = subprocess.run(
                    ["git", "describe", "--tags", "--always"],
                    cwd=self.comfyui_path,
                    capture_output=True,
                    text=True,
                    timeout=5,
                )
                if result.returncode == 0:
                    return result.stdout.strip()
            except Exception:
                pass

        # Try version file
        version_file = self.comfyui_path / "comfyui_version.txt"
        if version_file.exists():
            return version_file.read_text().strip()

        return "unknown"

    async def generate_report(self) -> CompatibilityReport:
        """Generate full compatibility report."""
        report = CompatibilityReport()

        # System info
        report.python_version = f"{sys.version_info.major}.{sys.version_info.minor}.{sys.version_info.micro}"

        try:
            import torch

            report.pytorch_version = torch.__version__
            report.cuda_available = torch.cuda.is_available()
            if report.cuda_available:
                report.cuda_version = torch.version.cuda or "unknown"
        except ImportError:
            pass

        # ComfyUI version
        report.comfyui_version = await self.check_comfyui_version()

        # Scan nodes
        report.nodes = await self.scan_nodes()
        report.total_nodes = len(report.nodes)
        report.installed_nodes = sum(1 for n in report.nodes if n.installed)
        report.enabled_nodes = sum(1 for n in report.nodes if n.enabled)
        report.error_nodes = sum(1 for n in report.nodes if n.has_errors)

        # Check dependencies
        report.missing_dependencies, report.version_conflicts = (
            await self.check_dependencies(report.nodes)
        )

        return report

    async def install_node(self, node_name: str, method: str = "git") -> bool:
        """Install a custom node from the registry.

        Args:
            node_name: Name of the node to install
            method: Installation method (git, pip, or manual)

        Returns:
            True if installation succeeded
        """
        known = NodeRegistry.get_node_info(node_name)
        if not known:
            logger.error(f"Unknown node: {node_name}")
            return False

        if not self.custom_nodes_path:
            logger.error("Custom nodes path not available")
            return False

        repo_url = known.get("repo", "")
        if not repo_url:
            logger.error(f"No repository URL for {node_name}")
            return False

        target_path = self.custom_nodes_path / node_name

        if target_path.exists():
            logger.warning(f"Node {node_name} already exists at {target_path}")
            return False

        try:
            if method == "git":
                result = subprocess.run(
                    ["git", "clone", repo_url, str(target_path)],
                    capture_output=True,
                    text=True,
                    timeout=120,
                )
                if result.returncode != 0:
                    logger.error(f"Git clone failed: {result.stderr}")
                    return False

            # Install Python dependencies
            req_file = target_path / "requirements.txt"
            if req_file.exists():
                result = subprocess.run(
                    [sys.executable, "-m", "pip", "install", "-r", str(req_file)],
                    capture_output=True,
                    text=True,
                    timeout=300,
                )
                if result.returncode != 0:
                    logger.warning(f"Dependency install had issues: {result.stderr}")

            logger.info(f"Successfully installed {node_name}")
            return True

        except Exception as e:
            logger.error(f"Installation failed: {e}")
            return False

    async def update_node(self, node_name: str) -> bool:
        """Update an installed custom node via git pull."""
        if not self.custom_nodes_path:
            return False

        node_path = self.custom_nodes_path / node_name
        if not node_path.exists():
            logger.error(f"Node not found: {node_name}")
            return False

        git_dir = node_path / ".git"
        if not git_dir.exists():
            logger.error(f"Node {node_name} is not a git repository")
            return False

        try:
            result = subprocess.run(
                ["git", "pull"],
                cwd=node_path,
                capture_output=True,
                text=True,
                timeout=60,
            )
            if result.returncode == 0:
                logger.info(f"Updated {node_name}: {result.stdout.strip()}")
                return True
            else:
                logger.error(f"Update failed: {result.stderr}")
                return False

        except Exception as e:
            logger.error(f"Update error: {e}")
            return False

    async def shutdown(self) -> None:
        """Cleanup resources."""
        if self._session and not self._session.closed:
            await self._session.close()


class NodeCompatibilityChecker:
    """Check compatibility between nodes and ComfyUI versions."""

    def __init__(self, discovery: NodeDiscovery):
        self.discovery = discovery

    def check_version_requirements(
        self,
        node: NodeInfo,
        comfyui_version: str,
    ) -> tuple[bool, str | None]:
        """Check if a node is compatible with the ComfyUI version.

        Returns:
            Tuple of (is_compatible, reason_if_not)
        """
        if node.comfyui_min_version:
            if self._compare_versions(comfyui_version, node.comfyui_min_version) < 0:
                return False, f"Requires ComfyUI >= {node.comfyui_min_version}"

        if node.comfyui_max_version:
            if self._compare_versions(comfyui_version, node.comfyui_max_version) > 0:
                return False, f"Requires ComfyUI <= {node.comfyui_max_version}"

        return True, None

    @staticmethod
    def _compare_versions(v1: str, v2: str) -> int:
        """Compare two version strings. Returns -1, 0, or 1."""

        def normalize(v: str) -> list[int]:
            # Remove 'v' prefix and split
            v = v.lstrip("vV")
            parts = re.split(r"[.-]", v)
            result = []
            for p in parts:
                try:
                    result.append(int(p))
                except ValueError:
                    # Non-numeric parts sort after numeric
                    result.append(0)
            return result

        p1 = normalize(v1)
        p2 = normalize(v2)

        for a, b in zip(p1, p2):
            if a < b:
                return -1
            if a > b:
                return 1

        return len(p1) - len(p2)

    async def get_install_recommendations(
        self,
        workflow_nodes: list[str],
    ) -> list[dict[str, Any]]:
        """Get installation recommendations for nodes required by a workflow.

        Args:
            workflow_nodes: List of node names/class names used in a workflow

        Returns:
            List of recommended installations with metadata
        """
        recommendations = []

        # Scan current installation
        installed = await self.discovery.scan_nodes()
        installed_names = {n.name for n in installed}

        for node_name in workflow_nodes:
            if node_name in installed_names:
                continue

            # Check if it's a known node
            known = NodeRegistry.get_node_info(node_name)
            if known:
                recommendations.append(
                    {
                        "name": node_name,
                        "description": known.get("description", ""),
                        "repository": known.get("repo", ""),
                        "dependencies": known.get("dependencies", []),
                        "install_command": f"python -m comfyui_engine.nodes install {node_name}",
                    }
                )
            else:
                recommendations.append(
                    {
                        "name": node_name,
                        "description": "Unknown node - manual installation required",
                        "repository": "",
                        "dependencies": [],
                        "install_command": "",
                    }
                )

        return recommendations


# CLI interface
async def main():
    """CLI entry point for node discovery."""
    import argparse

    parser = argparse.ArgumentParser(description="ComfyUI Custom Node Discovery")
    parser.add_argument("--comfyui-path", help="Path to ComfyUI installation")
    parser.add_argument("--scan", action="store_true", help="Scan and list all nodes")
    parser.add_argument("--check", action="store_true", help="Check compatibility")
    parser.add_argument("--install", help="Install a node by name")
    parser.add_argument("--update", help="Update a node by name")
    parser.add_argument("--update-all", action="store_true", help="Update all nodes")
    parser.add_argument("--search", help="Search for nodes by keyword")
    parser.add_argument("--json", action="store_true", help="Output as JSON")

    args = parser.parse_args()

    discovery = NodeDiscovery(
        comfyui_path=Path(args.comfyui_path) if args.comfyui_path else None
    )
    checker = NodeCompatibilityChecker(discovery)

    if args.scan or args.check:
        report = await discovery.generate_report()

        if args.json:
            print(json.dumps(report.to_dict(), indent=2))
        else:
            print(f"ComfyUI Version: {report.comfyui_version}")
            print(f"Python: {report.python_version}")
            print(f"PyTorch: {report.pytorch_version}")
            print(f"CUDA: {'Available' if report.cuda_available else 'Not available'}")
            print(
                f"\nNodes: {report.total_nodes} total, {report.installed_nodes} installed, {report.enabled_nodes} enabled"
            )

            if report.error_nodes > 0:
                print(f"Errors: {report.error_nodes} nodes have issues")

            if report.missing_dependencies:
                print(
                    f"\nMissing dependencies: {', '.join(report.missing_dependencies)}"
                )

            print("\nInstalled Nodes:")
            for node in report.nodes:
                status = "✓" if node.enabled else "✗"
                error = " [ERROR]" if node.has_errors else ""
                print(
                    f"  {status} {node.name} ({node.version}) - {node.category}{error}"
                )
                if node.python_dependencies:
                    print(f"    Dependencies: {', '.join(node.python_dependencies)}")

    elif args.install:
        success = await discovery.install_node(args.install)
        sys.exit(0 if success else 1)

    elif args.update:
        success = await discovery.update_node(args.update)
        sys.exit(0 if success else 1)

    elif args.update_all:
        nodes = await discovery.scan_nodes()
        for node in nodes:
            if node.installed and (node.module_path / ".git").exists():
                await discovery.update_node(node.name)

    elif args.search:
        results = NodeRegistry.search_nodes(args.search)
        for r in results:
            print(f"{r['name']}: {r['description']}")
            print(f"  Repo: {r['repo']}")
            print()

    else:
        parser.print_help()

    await discovery.shutdown()


if __name__ == "__main__":
    asyncio.run(main())
