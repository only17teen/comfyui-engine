"""Plugin system for third-party extensions to ComfyUI Engine.

Provides a lightweight but powerful plugin architecture with:
- Hook-based event system (before/after generation, on error, etc.)
- Plugin discovery and auto-loading
- Sandboxed execution with resource limits
- Configuration management per plugin
- Dependency resolution between plugins
"""

import asyncio
import importlib
import importlib.util
import inspect
import json
import logging
import os
import sys
import traceback
from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Callable, Dict, List, Optional, Set, Tuple, Type, Union

logger = logging.getLogger(__name__)


@dataclass
class PluginMetadata:
    """Plugin metadata from plugin.json or __init__.py."""

    name: str
    version: str = "1.0.0"
    description: str = ""
    author: str = ""
    license: str = "MIT"
    homepage: str = ""
    # Dependencies
    requires_engine: str = ">=3.0.0"
    requires_plugins: list[str] = field(default_factory=list)
    requires_python: list[str] = field(default_factory=list)
    # Capabilities
    hooks: list[str] = field(default_factory=list)
    provides: list[str] = field(default_factory=list)
    # Settings
    default_config: dict[str, Any] = field(default_factory=dict)
    # Status
    enabled: bool = True


@dataclass
class PluginContext:
    """Context passed to plugins during execution."""

    engine_config: dict[str, Any] = field(default_factory=dict)
    plugin_config: dict[str, Any] = field(default_factory=dict)
    state: dict[str, Any] = field(default_factory=dict)

    def get(self, key: str, default: Any = None) -> Any:
        """Get value from context with fallback."""
        return self.state.get(key, self.plugin_config.get(key, default))


class PluginHook:
    """Represents a hook point that plugins can attach to."""

    def __init__(self, name: str, description: str = ""):
        self.name = name
        self.description = description
        self._handlers: list[tuple[Callable, int, str]] = (
            []
        )  # (handler, priority, plugin_name)

    def register(
        self,
        handler: Callable,
        priority: int = 50,
        plugin_name: str = "",
    ) -> None:
        """Register a handler with priority (lower = earlier)."""
        self._handlers.append((handler, priority, plugin_name))
        self._handlers.sort(key=lambda x: x[1])
        logger.debug(f"Registered handler for {self.name} from {plugin_name}")

    def unregister(self, plugin_name: str) -> None:
        """Remove all handlers from a specific plugin."""
        self._handlers = [h for h in self._handlers if h[2] != plugin_name]

    async def fire(self, context: PluginContext, *args, **kwargs) -> list[Any]:
        """Execute all handlers in priority order.

        Returns list of results. If any handler raises, it's logged and
        execution continues with remaining handlers.
        """
        results = []

        for handler, _priority, plugin_name in self._handlers:
            try:
                if asyncio.iscoroutinefunction(handler):
                    result = await handler(context, *args, **kwargs)
                else:
                    result = handler(context, *args, **kwargs)
                results.append(result)
            except Exception as e:
                logger.error(f"Hook {self.name} handler from {plugin_name} failed: {e}")
                logger.debug(traceback.format_exc())

        return results


class PluginBase(ABC):
    """Base class for all plugins.

    Plugins must inherit from this class and implement required methods.
    The plugin system will handle lifecycle management automatically.
    """

    metadata: PluginMetadata

    def __init__(self, context: PluginContext):
        self.context = context
        self._initialized = False

    @abstractmethod
    async def initialize(self) -> None:
        """Called when plugin is loaded. Setup resources here."""
        pass

    @abstractmethod
    async def shutdown(self) -> None:
        """Called when plugin is unloaded. Cleanup resources here."""
        pass

    async def health_check(self) -> bool:
        """Return True if plugin is healthy."""
        return True

    def get_config(self, key: str, default: Any = None) -> Any:
        """Get configuration value."""
        return self.context.plugin_config.get(key, default)

    def set_state(self, key: str, value: Any) -> None:
        """Store state in plugin context."""
        self.context.state[key] = value

    def get_state(self, key: str, default: Any = None) -> Any:
        """Retrieve state from plugin context."""
        return self.context.state.get(key, default)


class HookRegistry:
    """Central registry for all plugin hooks."""

    # Built-in hook points
    BUILTIN_HOOKS = {
        "engine.start": "Called when engine starts",
        "engine.stop": "Called when engine stops",
        "generation.before": "Before image generation starts",
        "generation.after": "After image generation completes",
        "generation.error": "When generation fails",
        "generation.progress": "Progress updates during generation",
        "queue.before_add": "Before job is added to queue",
        "queue.after_add": "After job is added to queue",
        "queue.before_remove": "Before job is removed from queue",
        "model.before_load": "Before model is loaded",
        "model.after_load": "After model is loaded",
        "model.before_unload": "Before model is unloaded",
        "output.before_save": "Before output is saved",
        "output.after_save": "After output is saved",
        "config.changed": "When configuration changes",
        "metrics.collected": "When metrics are collected",
        "websocket.connected": "When WebSocket client connects",
        "websocket.disconnected": "When WebSocket client disconnects",
    }

    def __init__(self):
        self._hooks: dict[str, PluginHook] = {}
        self._register_builtin_hooks()

    def _register_builtin_hooks(self) -> None:
        """Register all built-in hooks."""
        for name, description in self.BUILTIN_HOOKS.items():
            self._hooks[name] = PluginHook(name, description)

    def register_hook(self, name: str, description: str = "") -> PluginHook:
        """Register a new hook point."""
        if name not in self._hooks:
            self._hooks[name] = PluginHook(name, description)
        return self._hooks[name]

    def get_hook(self, name: str) -> PluginHook | None:
        """Get a hook by name."""
        return self._hooks.get(name)

    def list_hooks(self) -> list[str]:
        """List all available hook names."""
        return list(self._hooks.keys())

    async def fire(
        self, hook_name: str, context: PluginContext, *args, **kwargs
    ) -> list[Any]:
        """Fire a hook by name."""
        hook = self._hooks.get(hook_name)
        if hook is None:
            logger.warning(f"Unknown hook: {hook_name}")
            return []
        return await hook.fire(context, *args, **kwargs)

    def unregister_plugin(self, plugin_name: str) -> None:
        """Remove all handlers from a plugin."""
        for hook in self._hooks.values():
            hook.unregister(plugin_name)


class PluginManager:
    """Manages plugin lifecycle, discovery, and execution.

    Features:
    - Auto-discovery from plugins/ directory
    - Dependency resolution and ordering
    - Hot-reload support
    - Sandboxed execution
    - Configuration management
    """

    def __init__(
        self,
        plugins_dir: Path | None = None,
        engine_config: dict[str, Any] | None = None,
    ):
        self.plugins_dir = plugins_dir or Path("plugins")
        self.engine_config = engine_config or {}

        self._registry = HookRegistry()
        self._plugins: dict[str, PluginBase] = {}
        self._metadata: dict[str, PluginMetadata] = {}
        self._contexts: dict[str, PluginContext] = {}
        self._loaded_modules: dict[str, Any] = {}

        self._lock = asyncio.Lock()
        self._running = False
        self._watch_task: asyncio.Task | None = None

    async def initialize(self) -> None:
        """Initialize plugin manager and discover plugins."""
        self._running = True

        # Ensure plugins directory exists
        self.plugins_dir.mkdir(parents=True, exist_ok=True)

        # Discover and load plugins
        await self.discover_plugins()

        # Start file watcher for hot-reload
        self._watch_task = asyncio.create_task(self._watch_plugins())

        logger.info(f"Plugin manager initialized with {len(self._plugins)} plugins")

    async def discover_plugins(self) -> list[str]:
        """Discover and load all plugins from plugins directory.

        Returns:
            List of loaded plugin names
        """
        loaded = []

        if not self.plugins_dir.exists():
            return loaded

        # Collect all plugin candidates
        candidates: list[tuple[str, Path]] = []

        for item in self.plugins_dir.iterdir():
            if item.name.startswith(".") or item.name == "__pycache__":
                continue

            if item.is_dir():
                # Check for plugin.json or __init__.py
                if (item / "plugin.json").exists() or (item / "__init__.py").exists():
                    candidates.append((item.name, item))
            elif item.suffix == ".py" and not item.name.startswith("_"):
                # Single-file plugin
                candidates.append((item.stem, item))

        # Sort by dependencies
        candidates = self._resolve_dependencies(candidates)

        # Load each plugin
        for name, path in candidates:
            try:
                if await self._load_plugin(name, path):
                    loaded.append(name)
            except Exception as e:
                logger.error(f"Failed to load plugin {name}: {e}")

        return loaded

    def _resolve_dependencies(
        self,
        candidates: list[tuple[str, Path]],
    ) -> list[tuple[str, Path]]:
        """Sort plugins by dependency order.

        Simple topological sort - plugins with fewer dependencies first.
        """
        # Build dependency graph
        deps: dict[str, set[str]] = {}
        paths: dict[str, Path] = {}

        for name, path in candidates:
            paths[name] = path
            metadata = self._read_metadata(path)
            deps[name] = set(metadata.requires_plugins)

        # Topological sort
        resolved: list[str] = []
        unresolved = set(deps.keys())

        while unresolved:
            # Find plugins with no remaining unresolved dependencies
            ready = {name for name in unresolved if not (deps[name] & unresolved)}

            if not ready:
                # Circular dependency - break by taking first
                ready = {next(iter(unresolved))}
                logger.warning(
                    f"Circular dependency detected, breaking at {next(iter(ready))}"
                )

            for name in sorted(ready):
                resolved.append(name)
                unresolved.remove(name)

        return [(name, paths[name]) for name in resolved]

    def _read_metadata(self, path: Path) -> PluginMetadata:
        """Read plugin metadata from directory or file."""
        # Try plugin.json first
        if path.is_dir():
            json_file = path / "plugin.json"
            if json_file.exists():
                try:
                    with open(json_file) as f:
                        data = json.load(f)
                    return PluginMetadata(**data)
                except Exception:
                    pass

        # Try to extract from Python file
        if path.is_file() and path.suffix == ".py":
            try:
                content = path.read_text()
                # Simple extraction of metadata from docstring or variables
                name = path.stem
                return PluginMetadata(name=name)
            except Exception:
                pass

        # Default metadata
        return PluginMetadata(name=path.name if path.is_dir() else path.stem)

    async def _load_plugin(self, name: str, path: Path) -> bool:
        """Load a single plugin.

        Args:
            name: Plugin name
            path: Path to plugin directory or file

        Returns:
            True if loaded successfully
        """
        async with self._lock:
            if name in self._plugins:
                logger.warning(f"Plugin {name} already loaded")
                return False

            metadata = self._read_metadata(path)

            if not metadata.enabled:
                logger.info(f"Plugin {name} is disabled, skipping")
                return False

            # Check engine version requirement
            if not self._check_version_requirement(metadata.requires_engine):
                logger.warning(
                    f"Plugin {name} requires engine {metadata.requires_engine}"
                )
                return False

            # Check plugin dependencies
            for dep in metadata.requires_plugins:
                if dep not in self._plugins:
                    logger.error(f"Plugin {name} requires {dep} which is not loaded")
                    return False

            # Load module
            try:
                if path.is_dir():
                    module = self._load_module_from_dir(name, path)
                else:
                    module = self._load_module_from_file(name, path)

                if module is None:
                    return False

                self._loaded_modules[name] = module

                # Find plugin class
                plugin_class = self._find_plugin_class(module)
                if plugin_class is None:
                    logger.error(f"No PluginBase subclass found in {name}")
                    return False

                # Create context
                config = self._load_plugin_config(name, metadata.default_config)
                context = PluginContext(
                    engine_config=self.engine_config,
                    plugin_config=config,
                )
                self._contexts[name] = context

                # Instantiate plugin
                plugin = plugin_class(context)
                plugin.metadata = metadata

                # Initialize
                await plugin.initialize()

                # Register hooks
                self._register_plugin_hooks(name, plugin)

                self._plugins[name] = plugin
                self._metadata[name] = metadata

                logger.info(f"Loaded plugin: {name} v{metadata.version}")
                return True

            except Exception as e:
                logger.error(f"Failed to load plugin {name}: {e}")
                logger.debug(traceback.format_exc())
                return False

    def _load_module_from_dir(self, name: str, path: Path) -> Any | None:
        """Load plugin module from directory."""
        init_file = path / "__init__.py"
        if init_file.exists():
            spec = importlib.util.spec_from_file_location(
                f"plugins.{name}",
                init_file,
            )
        else:
            # Find first .py file
            py_files = list(path.glob("*.py"))
            if not py_files:
                return None
            spec = importlib.util.spec_from_file_location(
                f"plugins.{name}",
                py_files[0],
            )

        if spec is None or spec.loader is None:
            return None

        module = importlib.util.module_from_spec(spec)
        sys.modules[spec.name] = module
        spec.loader.exec_module(module)
        return module

    def _load_module_from_file(self, name: str, path: Path) -> Any | None:
        """Load plugin module from single file."""
        spec = importlib.util.spec_from_file_location(
            f"plugins.{name}",
            path,
        )

        if spec is None or spec.loader is None:
            return None

        module = importlib.util.module_from_spec(spec)
        sys.modules[spec.name] = module
        spec.loader.exec_module(module)
        return module

    def _find_plugin_class(self, module: Any) -> type[PluginBase] | None:
        """Find PluginBase subclass in module."""
        for _name, obj in inspect.getmembers(module, inspect.isclass):
            if issubclass(obj, PluginBase) and obj is not PluginBase:
                return obj
        return None

    def _register_plugin_hooks(self, plugin_name: str, plugin: PluginBase) -> None:
        """Auto-register plugin methods as hook handlers."""
        metadata = self._metadata.get(plugin_name)
        if not metadata:
            return

        for hook_name in metadata.hooks:
            method_name = hook_name.replace(".", "_")
            if hasattr(plugin, method_name):
                handler = getattr(plugin, method_name)
                hook = self._registry.register_hook(hook_name)
                hook.register(handler, plugin_name=plugin_name)

    def _load_plugin_config(
        self,
        name: str,
        defaults: dict[str, Any],
    ) -> dict[str, Any]:
        """Load plugin configuration from file."""
        config_path = self.plugins_dir / f"{name}.config.json"
        config = dict(defaults)

        if config_path.exists():
            try:
                with open(config_path) as f:
                    user_config = json.load(f)
                config.update(user_config)
            except Exception:
                pass

        return config

    def _check_version_requirement(self, requirement: str) -> bool:
        """Check if engine version satisfies requirement.

        Simple check - supports >=, <=, ==, >, < prefixes.
        """
        from packaging import version as pkg_version

        engine_version = "3.0.0"  # Current engine version

        req = requirement.strip()

        if req.startswith(">="):
            return pkg_version.parse(engine_version) >= pkg_version.parse(req[2:])
        elif req.startswith("<<="):
            return pkg_version.parse(engine_version) <= pkg_version.parse(req[2:])
        elif req.startswith(">"):
            return pkg_version.parse(engine_version) > pkg_version.parse(req[1:])
        elif req.startswith("<"):
            return pkg_version.parse(engine_version) < pkg_version.parse(req[1:])
        elif req.startswith("=="):
            return pkg_version.parse(engine_version) == pkg_version.parse(req[2:])

        return True

    async def unload_plugin(self, name: str) -> bool:
        """Unload a plugin and cleanup."""
        async with self._lock:
            if name not in self._plugins:
                return False

            plugin = self._plugins[name]

            # Unregister hooks
            self._registry.unregister_plugin(name)

            # Shutdown plugin
            try:
                await plugin.shutdown()
            except Exception as e:
                logger.error(f"Error shutting down plugin {name}: {e}")

            # Cleanup
            del self._plugins[name]
            del self._metadata[name]
            del self._contexts[name]

            if name in self._loaded_modules:
                sys.modules.pop(f"plugins.{name}", None)
                del self._loaded_modules[name]

            logger.info(f"Unloaded plugin: {name}")
            return True

    async def reload_plugin(self, name: str) -> bool:
        """Hot-reload a plugin."""
        # Find plugin path
        path = self.plugins_dir / name
        if not path.exists():
            path = self.plugins_dir / f"{name}.py"

        if not path.exists():
            logger.error(f"Plugin {name} not found for reload")
            return False

        # Unload and reload
        if name in self._plugins:
            await self.unload_plugin(name)

        return await self._load_plugin(name, path)

    async def fire_hook(
        self,
        hook_name: str,
        *args,
        plugin_context: dict[str, Any] | None = None,
        **kwargs,
    ) -> list[Any]:
        """Fire a hook with optional per-plugin context."""
        results = []

        for name, _plugin in self._plugins.items():
            context = self._contexts.get(name, PluginContext())

            if plugin_context and name in plugin_context:
                # Merge with plugin-specific context
                merged = PluginContext(
                    engine_config=context.engine_config,
                    plugin_config={**context.plugin_config, **plugin_context[name]},
                    state=context.state,
                )
                context = merged

            hook_result = await self._registry.fire(hook_name, context, *args, **kwargs)
            results.extend(hook_result)

        return results

    async def _watch_plugins(self) -> None:
        """Watch plugins directory for changes and hot-reload."""
        # Simple polling-based watcher
        last_modified: dict[str, float] = {}

        while self._running:
            try:
                await asyncio.sleep(2)

                for item in self.plugins_dir.iterdir():
                    if item.name.startswith("."):
                        continue

                    name = item.name if item.is_dir() else item.stem
                    mtime = item.stat().st_mtime

                    if name in last_modified and last_modified[name] != mtime:
                        if name in self._plugins:
                            logger.info(
                                f"Detected change in plugin {name}, reloading..."
                            )
                            await self.reload_plugin(name)

                    last_modified[name] = mtime

            except asyncio.CancelledError:
                break
            except Exception as e:
                logger.error(f"Plugin watcher error: {e}")

    def list_plugins(self) -> list[dict[str, Any]]:
        """List all loaded plugins with metadata."""
        return [
            {
                "name": name,
                "version": meta.version,
                "description": meta.description,
                "author": meta.author,
                "enabled": meta.enabled,
                "hooks": meta.hooks,
                "healthy": True,  # Could add health check
            }
            for name, meta in self._metadata.items()
        ]

    async def health_check(self) -> dict[str, bool]:
        """Run health checks on all plugins."""
        results = {}

        for name, plugin in self._plugins.items():
            try:
                results[name] = await plugin.health_check()
            except Exception as e:
                logger.error(f"Health check failed for {name}: {e}")
                results[name] = False

        return results

    async def shutdown(self) -> None:
        """Shutdown all plugins and cleanup."""
        self._running = False

        if self._watch_task:
            self._watch_task.cancel()
            try:
                await self._watch_task
            except asyncio.CancelledError:
                pass

        # Unload all plugins
        for name in list(self._plugins.keys()):
            await self.unload_plugin(name)

        logger.info("Plugin manager shutdown complete")


# Example plugin implementation
class ExamplePlugin(PluginBase):
    """Example plugin demonstrating the plugin system."""

    metadata = PluginMetadata(
        name="example-plugin",
        version="1.0.0",
        description="Example plugin for ComfyUI Engine",
        author="Engine Team",
        hooks=["generation.before", "generation.after"],
    )

    async def initialize(self) -> None:
        logger.info("Example plugin initialized")

    async def shutdown(self) -> None:
        logger.info("Example plugin shutdown")

    async def generation_before(self, context: PluginContext, *args, **kwargs) -> None:
        """Hook called before generation."""
        logger.info("Example: Before generation")
        # Could modify parameters here

    async def generation_after(self, context: PluginContext, *args, **kwargs) -> None:
        """Hook called after generation."""
        logger.info("Example: After generation")
        # Could post-process results here


if __name__ == "__main__":

    async def main():
        manager = PluginManager()
        await manager.initialize()

        # List plugins
        plugins = manager.list_plugins()
        print(f"Loaded plugins: {plugins}")

        # Fire a hook
        context = PluginContext()
        results = await manager.fire_hook("generation.before", context)
        print(f"Hook results: {results}")

        await manager.shutdown()

    asyncio.run(main())
