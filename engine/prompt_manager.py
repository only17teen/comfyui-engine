"""ComfyUI Async Generation Engine v2.0 - Prompt & LoRA Manager
Pydantic-validated configuration, seed strategies, prompt templates.
"""

import random
import time
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional, Tuple

from engine.config import EngineConfig, LoRAModelConfig


@dataclass
class LoRAStackItem:
    """Single LoRA with resolved weight."""

    name: str
    path: str
    weight: float
    weight_clip: float


@dataclass
class GenerationConfig:
    """Complete generation configuration for a single job."""

    seed: int
    positive_prompt: str
    negative_prompt: str
    width: int
    height: int
    steps: int
    cfg_scale: float
    sampler_name: str
    scheduler: str
    lora_stack: list[LoRAStackItem]
    batch_size: int = 1
    prompt_template: str | None = None
    tags: list[str] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        """Serialize to plain dict for metadata/logging."""
        return {
            "seed": self.seed,
            "positive_prompt": self.positive_prompt,
            "negative_prompt": self.negative_prompt,
            "width": self.width,
            "height": self.height,
            "steps": self.steps,
            "cfg_scale": self.cfg_scale,
            "sampler_name": self.sampler_name,
            "scheduler": self.scheduler,
            "lora_stack": [
                {
                    "name": l.name,
                    "path": l.path,
                    "weight": l.weight,
                    "weight_clip": l.weight_clip,
                }
                for l in self.lora_stack
            ],
            "batch_size": self.batch_size,
            "prompt_template": self.prompt_template,
            "tags": self.tags,
        }


class SeedStrategy:
    """Pluggable seed generation strategies."""

    @staticmethod
    def random() -> int:
        return random.randint(1, 2**32 - 1)

    @staticmethod
    def time_based() -> int:
        return int(time.time() * 1000) % (2**32 - 1)

    @staticmethod
    def sequential(start: int = 1000000) -> int:
        if not hasattr(SeedStrategy.sequential, "_counter"):
            SeedStrategy.sequential._counter = start
        SeedStrategy.sequential._counter += 1
        return SeedStrategy.sequential._counter

    @staticmethod
    def fixed(seed: int) -> int:
        return seed


class PromptTemplate:
    """Template-based prompt construction with variable substitution."""

    # Predefined templates for different styles
    TEMPLATES = {
        "standard": "{triggers}, {clothing}, {pose}, {location}, {lighting}, {expression}",
        "portrait": "{triggers}, {clothing}, close-up portrait, {expression}, {lighting}, {location}",
        "full_body": "{triggers}, {clothing}, full body, {pose}, {location}, {lighting}",
        "cinematic": "{triggers}, {clothing}, cinematic shot, {pose}, {location}, dramatic {lighting}, film grain",
        "fashion": "{triggers}, {clothing}, fashion photography, {pose}, studio lighting, {location}",
    }

    def __init__(self, template_name: str = "standard"):
        self.template = self.TEMPLATES.get(template_name, self.TEMPLATES["standard"])
        self.name = template_name

    def render(self, **kwargs) -> str:
        """Render template with provided variables."""
        try:
            return self.template.format(**kwargs)
        except KeyError as e:
            # Fallback: remove missing keys
            result = self.template
            for key in list(kwargs.keys()):
                result = result.replace(f"{{{key}}}", kwargs.get(key, ""))
            # Remove any remaining unfilled placeholders
            import re

            result = re.sub(r"\{[^}]+\}", "", result)
            # Clean up extra commas and spaces
            result = re.sub(r",\s*,", ",", result)
            result = re.sub(r"\s+", " ", result).strip(", ")
            return result


class PromptManager:
    """Advanced prompt manager with:
    - Pydantic-validated configuration
    - Multiple seed strategies
    - Template-based prompt construction
    - Weighted random selection
    - Prompt history and deduplication
    """

    def __init__(self, config: EngineConfig):
        self.config = config
        self.prompt_history: list[str] = []
        self.max_history = 1000
        self._rng = random.Random()

    def set_seed(self, seed: int) -> None:
        """Set deterministic RNG seed for reproducible generations."""
        self._rng.seed(seed)

    def _pick_weighted(
        self, items: list[str], weights: list[float] | None = None
    ) -> str:
        """Weighted random selection from a list."""
        if not items:
            return ""
        if weights and len(weights) == len(items):
            return self._rng.choices(items, weights=weights, k=1)[0]
        return self._rng.choice(items)

    def _pick_multiple(
        self, items: list[str], count: int, weights: list[float] | None = None
    ) -> list[str]:
        """Pick multiple unique items."""
        if not items or count <= 0:
            return []
        count = min(count, len(items))
        if weights and len(weights) == len(items):
            # Weighted sampling without replacement (approximate)
            selected = []
            available = (
                list(zip(items, weights)) if weights else [(i, 1.0) for i in items]
            )
            for _ in range(count):
                if not available:
                    break
                total = sum(w for _, w in available)
                r = self._rng.uniform(0, total)
                cumsum = 0.0
                for idx, (item, weight) in enumerate(available):
                    cumsum += weight
                    if r <= cumsum:
                        selected.append(item)
                        available.pop(idx)
                        break
            return selected
        return self._rng.sample(items, count)

    def _build_clothing(self) -> str:
        """Build clothing string from randomized components."""
        clothing = self.config.prompts.clothing
        parts = []

        # 80% chance to include top
        if clothing.tops and self._rng.random() < 0.8:
            parts.append(self._pick_weighted(clothing.tops))

        # 70% chance to include bottom
        if clothing.bottoms and self._rng.random() < 0.7:
            parts.append(self._pick_weighted(clothing.bottoms))

        # 40% chance to include accessory
        if clothing.accessories and self._rng.random() < 0.4:
            parts.append(self._pick_weighted(clothing.accessories))

        # 20% chance for full-body outfit (replaces top+bottom)
        if clothing.full_body and self._rng.random() < 0.2:
            parts = [self._pick_weighted(clothing.full_body)]

        return ", ".join(filter(None, parts))

    def _build_triggers(self, count: int = 4) -> str:
        """Select trigger words with quality bias."""
        triggers = self.config.prompts.trigger_words
        if not triggers:
            return ""

        # Quality tags have higher weight
        quality_tags = [
            "masterpiece",
            "best quality",
            "highly detailed",
            "ultra-detailed",
        ]
        weights = [2.0 if t in quality_tags else 1.0 for t in triggers]

        selected = self._pick_multiple(triggers, min(count, len(triggers)), weights)
        return ", ".join(selected)

    def generate_prompt(self, template_name: str | None = None) -> tuple[str, str]:
        """Generate positive and negative prompts.

        Returns:
            (positive_prompt, template_name_used)
        """
        template = PromptTemplate(template_name or "standard")

        # Build components
        triggers = self._build_triggers()
        clothing = self._build_clothing()
        pose = (
            self._pick_weighted(self.config.prompts.poses)
            if self.config.prompts.poses
            else ""
        )
        location = (
            self._pick_weighted(self.config.prompts.locations)
            if self.config.prompts.locations
            else ""
        )
        expression = (
            self._pick_weighted(self.config.prompts.expressions)
            if self.config.prompts.expressions
            else ""
        )
        lighting = (
            self._pick_weighted(self.config.prompts.lighting)
            if self.config.prompts.lighting
            else ""
        )

        positive = template.render(
            triggers=triggers,
            clothing=clothing,
            pose=pose,
            location=location,
            expression=expression,
            lighting=lighting,
        )

        # Deduplication check
        if positive in self.prompt_history:
            # Retry with different template
            alt_template = self._rng.choice(list(PromptTemplate.TEMPLATES.keys()))
            if alt_template != template.name:
                return self.generate_prompt(alt_template)

        self.prompt_history.append(positive)
        if len(self.prompt_history) > self.max_history:
            self.prompt_history.pop(0)

        return positive, template.name

    def build_lora_stack(self, num_lora: int | None = None) -> list[LoRAStackItem]:
        """Build randomized LoRA stack with validated weights.

        Args:
            num_lora: Override number of LoRAs. Defaults to config.

        Returns:
            List of LoRAStackItem with resolved weights.
        """
        models = self.config.lora.models
        if not models:
            return []

        count = num_lora or min(2, len(models))
        count = min(count, len(models))

        selected = self._rng.sample(models, count)
        stack = []

        for model in selected:
            weight = round(self._rng.uniform(*model.weight_range), 2)
            # Clip strength typically slightly lower than model strength
            weight_clip = max(0.1, round(weight - 0.2, 2))
            stack.append(
                LoRAStackItem(
                    name=model.name,
                    path=model.path,
                    weight=weight,
                    weight_clip=weight_clip,
                )
            )

        return stack

    def get_resolution(self) -> tuple[int, int]:
        """Pick random resolution from config presets."""
        resolutions = self.config.lora.resolutions
        if not resolutions:
            return (512, 768)
        return self._rng.choice(resolutions)

    def get_sampling_params(self) -> dict[str, Any]:
        """Randomize sampling parameters within validated ranges."""
        sampling = self.config.lora.sampling
        return {
            "steps": self._rng.randint(*sampling.steps_range),
            "cfg_scale": round(self._rng.uniform(*sampling.cfg_scale_range), 1),
            "sampler_name": self._rng.choice(sampling.sampler_names),
            "scheduler": sampling.scheduler,
        }

    def generate_config(
        self,
        seed: int | None = None,
        seed_strategy: str = "random",
        num_lora: int | None = None,
        template: str | None = None,
        tags: list[str] | None = None,
    ) -> GenerationConfig:
        """Generate complete configuration with full control.

        Args:
            seed: Fixed seed (overrides strategy).
            seed_strategy: "random", "time_based", "sequential", "fixed".
            num_lora: Number of LoRA models.
            template: Prompt template name.
            tags: Optional tags for organization.

        Returns:
            GenerationConfig with all parameters.
        """
        # Resolve seed
        if seed is not None:
            resolved_seed = seed
        else:
            strategy = getattr(SeedStrategy, seed_strategy, SeedStrategy.random)
            resolved_seed = strategy()

        self.set_seed(resolved_seed)

        # Generate prompts
        positive, template_name = self.generate_prompt(template)
        negative = self.config.prompts.negative_prompt

        # Resolution and sampling
        width, height = self.get_resolution()
        sampling = self.get_sampling_params()

        # LoRA stack
        lora_stack = self.build_lora_stack(num_lora)

        return GenerationConfig(
            seed=resolved_seed,
            positive_prompt=positive,
            negative_prompt=negative,
            width=width,
            height=height,
            steps=sampling["steps"],
            cfg_scale=sampling["cfg_scale"],
            sampler_name=sampling["sampler_name"],
            scheduler=sampling["scheduler"],
            lora_stack=lora_stack,
            batch_size=self.config.lora.batch_size,
            prompt_template=template_name,
            tags=tags or [],
        )

    def generate_batch(
        self,
        count: int,
        seed_strategy: str = "random",
        num_lora: int | None = None,
        template: str | None = None,
    ) -> list[GenerationConfig]:
        """Generate a batch of unique configurations."""
        configs = []
        for _ in range(count):
            cfg = self.generate_config(
                seed_strategy=seed_strategy,
                num_lora=num_lora,
                template=template,
            )
            configs.append(cfg)
        return configs

    def to_comfy_payload(
        self, config: GenerationConfig, workflow_template: dict
    ) -> dict:
        """Inject GenerationConfig into ComfyUI workflow template.
        Supports both node-ID-based and class_type-based injection.
        """
        import copy

        payload = copy.deepcopy(workflow_template)

        # Build LoRA prompt suffix
        lora_prompt = ""
        if config.lora_stack:
            lora_parts = []
            for lora in config.lora_stack:
                lora_parts.append(f"\u003clora:{lora.path}:{lora.weight}\u003e")
            lora_prompt = ", " + ", ".join(lora_parts)

        full_positive = config.positive_prompt + lora_prompt

        # Node injection strategies
        for node_id, node in payload.items():
            if not isinstance(node, dict):
                continue

            class_type = node.get("class_type", "")
            inputs = node.get("inputs", {})

            # KSampler / KSamplerAdvanced
            if class_type in ("KSampler", "KSamplerAdvanced"):
                inputs["seed"] = config.seed
                inputs["steps"] = config.steps
                inputs["cfg"] = config.cfg_scale
                inputs["sampler_name"] = config.sampler_name
                inputs["scheduler"] = config.scheduler

            # Empty Latent Image
            elif class_type == "EmptyLatentImage":
                inputs["width"] = config.width
                inputs["height"] = config.height
                inputs["batch_size"] = config.batch_size

            # CLIP Text Encode - Positive
            elif class_type == "CLIPTextEncode":
                # Detect positive vs negative by node connections or ID
                node_inputs = node.get("inputs", {})
                text = node_inputs.get("text", "")
                if isinstance(text, str):
                    # Heuristic: if connected to positive sampler path or node ID 6
                    if node_id == "6" or "positive" in text.lower()[:20]:
                        inputs["text"] = full_positive
                    elif node_id == "7" or "negative" in text.lower()[:20]:
                        inputs["text"] = config.negative_prompt

            # LoRA Loaders
            elif "LoraLoader" in class_type:
                # Match by index in stack
                lora_idx = 0
                try:
                    # Try to extract index from node ID (e.g., "10", "11")
                    lora_idx = int(node_id) - 10 if node_id.isdigit() else 0
                except (ValueError, TypeError):
                    pass

                if lora_idx < len(config.lora_stack):
                    lora = config.lora_stack[lora_idx]
                    inputs["lora_name"] = lora.path
                    inputs["strength_model"] = lora.weight
                    inputs["strength_clip"] = lora.weight_clip

        return payload
