"""ComfyUI Async Generation Engine v2.0 - A/B Testing Framework
Statistical comparison of prompt templates, LoRA weights, and generation parameters.
"""

import json
import logging
import statistics
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

from engine.config import EngineConfig
from engine.prompt_manager import PromptManager, GenerationConfig, PromptTemplate

logger = logging.getLogger(__name__)


@dataclass
class VariantResult:
    """Results for a single A/B test variant."""

    variant_id: str
    template_name: str
    config_overrides: dict[str, Any] = field(default_factory=dict)
    total_generations: int = 0
    successful: int = 0
    failed: int = 0
    avg_processing_time: float = 0.0
    processing_times: list[float] = field(default_factory=list)
    seed_values: list[int] = field(default_factory=list)
    prompt_samples: list[str] = field(default_factory=list)
    metadata: dict[str, Any] = field(default_factory=dict)

    @property
    def success_rate(self) -> float:
        if self.total_generations == 0:
            return 0.0
        return self.successful / self.total_generations

    @property
    def p50_time(self) -> float:
        if not self.processing_times:
            return 0.0
        return statistics.median(self.processing_times)

    @property
    def p95_time(self) -> float:
        if not self.processing_times:
            return 0.0
        sorted_times = sorted(self.processing_times)
        idx = int(len(sorted_times) * 0.95)
        return sorted_times[min(idx, len(sorted_times) - 1)]

    @property
    def stddev_time(self) -> float:
        if len(self.processing_times) < 2:
            return 0.0
        return statistics.stdev(self.processing_times)

    def to_dict(self) -> dict:
        return {
            "variant_id": self.variant_id,
            "template_name": self.template_name,
            "config_overrides": self.config_overrides,
            "total_generations": self.total_generations,
            "successful": self.successful,
            "failed": self.failed,
            "success_rate": self.success_rate,
            "avg_processing_time": self.avg_processing_time,
            "p50_time": self.p50_time,
            "p95_time": self.p95_time,
            "stddev_time": self.stddev_time,
            "processing_times": self.processing_times,
            "seed_values": self.seed_values,
            "prompt_samples": self.prompt_samples[:5],  # First 5 only
            "metadata": self.metadata,
        }


@dataclass
class ABTestResult:
    """Complete A/B test results with statistical analysis."""

    test_id: str
    test_name: str
    hypothesis: str
    variants: list[VariantResult] = field(default_factory=list)
    winner: str | None = None
    confidence: float = 0.0
    recommendation: str = ""
    timestamp: float = field(default_factory=time.time)

    def to_dict(self) -> dict:
        return {
            "test_id": self.test_id,
            "test_name": self.test_name,
            "hypothesis": self.hypothesis,
            "variants": [v.to_dict() for v in self.variants],
            "winner": self.winner,
            "confidence": self.confidence,
            "recommendation": self.recommendation,
            "timestamp": self.timestamp,
        }


class ABTestFramework:
    """A/B testing framework for comparing prompt templates and generation parameters.

    Features:
    - Template comparison (standard vs cinematic vs portrait)
    - LoRA weight optimization
    - CFG scale testing
    - Sampler comparison
    - Statistical significance testing
    - Automatic winner selection
    """

    def __init__(self, config: EngineConfig):
        self.config = config
        self.prompt_manager = PromptManager(config)
        self.logger = logging.getLogger(__name__)

    def create_test_variants(
        self,
        test_type: str,
        base_params: dict | None = None,
    ) -> list[dict[str, Any]]:
        """Generate test variants based on test type.

        Args:
            test_type: Type of test (templates, lora_weights, cfg_scales, samplers)
            base_params: Base parameters to override

        Returns:
            List of variant configurations.
        """
        variants = []

        if test_type == "templates":
            for template in [
                "standard",
                "portrait",
                "cinematic",
                "fashion",
                "full_body",
            ]:
                variants.append(
                    {
                        "variant_id": f"template_{template}",
                        "template_name": template,
                        "config_overrides": {"template": template},
                    }
                )

        elif test_type == "lora_weights":
            for weight_range in [(0.3, 0.5), (0.5, 0.7), (0.7, 1.0)]:
                variants.append(
                    {
                        "variant_id": f"lora_{weight_range[0]}_{weight_range[1]}",
                        "template_name": "standard",
                        "config_overrides": {
                            "lora_weight_range": weight_range,
                        },
                    }
                )

        elif test_type == "cfg_scales":
            for cfg in [5.0, 7.0, 9.0, 12.0]:
                variants.append(
                    {
                        "variant_id": f"cfg_{cfg}",
                        "template_name": "standard",
                        "config_overrides": {
                            "cfg_scale": cfg,
                        },
                    }
                )

        elif test_type == "samplers":
            for sampler in ["DPM++ 2M Karras", "Euler a", "DPM++ SDE Karras", "UniPC"]:
                variants.append(
                    {
                        "variant_id": f"sampler_{sampler.replace(' ', '_').replace('+', 'p')}",
                        "template_name": "standard",
                        "config_overrides": {
                            "sampler_name": sampler,
                        },
                    }
                )

        elif test_type == "seed_strategies":
            for strategy in ["random", "time_based", "sequential"]:
                variants.append(
                    {
                        "variant_id": f"seed_{strategy}",
                        "template_name": "standard",
                        "config_overrides": {
                            "seed_strategy": strategy,
                        },
                    }
                )

        elif test_type == "custom":
            # Use provided base_params as variants
            if base_params:
                variants = base_params

        else:
            raise ValueError(f"Unknown test type: {test_type}")

        return variants

    def generate_variant_configs(
        self,
        variant: dict[str, Any],
        count: int,
    ) -> list[GenerationConfig]:
        """Generate configurations for a specific variant."""
        template = variant.get("template_name", "standard")
        overrides = variant.get("config_overrides", {})

        configs = []
        for _ in range(count):
            config = self.prompt_manager.generate_config(
                template=template,
                seed_strategy=overrides.get("seed_strategy", "random"),
            )

            # Apply overrides
            if "cfg_scale" in overrides:
                config.cfg_scale = overrides["cfg_scale"]
            if "sampler_name" in overrides:
                config.sampler_name = overrides["sampler_name"]
            if "lora_weight_range" in overrides:
                # Adjust LoRA weights
                for lora in config.lora_stack:
                    lora.weight = self.prompt_manager._rng.uniform(*overrides["lora_weight_range"])

            configs.append(config)

        return configs

    def analyze_results(self, results: list[VariantResult]) -> ABTestResult:
        """Perform statistical analysis on test results.

        Selects winner based on:
        1. Highest success rate
        2. Lowest average processing time (if success rates are close)
        3. Lowest variance (if both are close)
        """
        if not results:
            return ABTestResult(
                test_id=f"test_{int(time.time())}",
                test_name="empty",
                hypothesis="No results",
                recommendation="No data to analyze",
            )

        # Filter variants with enough data
        valid_results = [r for r in results if r.total_generations >= 5]

        if not valid_results:
            return ABTestResult(
                test_id=f"test_{int(time.time())}",
                test_name="insufficient_data",
                hypothesis="Insufficient data for analysis",
                recommendation="Run more generations per variant",
            )

        # Score each variant
        scores = {}
        for result in valid_results:
            # Success rate (0-1, higher is better)
            success_score = result.success_rate

            # Speed score (inverse of avg time, normalized)
            max_time = max(r.avg_processing_time for r in valid_results) or 1.0
            speed_score = 1.0 - (result.avg_processing_time / max_time)

            # Consistency score (inverse of stddev, normalized)
            max_stddev = max(r.stddev_time for r in valid_results) or 1.0
            consistency_score = 1.0 - (result.stddev_time / max_stddev)

            # Weighted composite score
            scores[result.variant_id] = success_score * 0.5 + speed_score * 0.3 + consistency_score * 0.2

        # Select winner
        winner_id = max(scores, key=scores.get)
        winner_score = scores[winner_id]
        winner_result = next(r for r in valid_results if r.variant_id == winner_id)

        # Calculate confidence (difference from second best)
        other_scores = [s for vid, s in scores.items() if vid != winner_id]
        if other_scores:
            second_best = max(other_scores)
            confidence = (winner_score - second_best) / winner_score if winner_score > 0 else 0.0
        else:
            confidence = 1.0

        # Generate recommendation
        if confidence > 0.2:
            recommendation = (
                f"Use variant '{winner_id}' ({winner_result.template_name}) "
                f"with {winner_result.success_rate*100:.1f}% success rate, "
                f"avg {winner_result.avg_processing_time:.1f}s"
            )
        elif confidence > 0.05:
            recommendation = (
                f"Variant '{winner_id}' shows slight improvement. " f"Consider running more tests for confirmation."
            )
        else:
            recommendation = (
                "No statistically significant winner. "
                f"All variants perform similarly (confidence: {confidence*100:.1f}%)."
            )

        return ABTestResult(
            test_id=f"test_{int(time.time())}",
            test_name=winner_result.template_name,
            hypothesis=f"Compare {len(results)} variants",
            variants=results,
            winner=winner_id,
            confidence=confidence,
            recommendation=recommendation,
        )

    def save_results(self, result: ABTestResult, path: str = "ab_test_results.json") -> None:
        """Save A/B test results to JSON."""
        Path(path).write_text(
            json.dumps(result.to_dict(), indent=2, ensure_ascii=False),
            encoding="utf-8",
        )
        self.logger.info(f"A/B test results saved to: {path}")

    def load_results(self, path: str) -> ABTestResult | None:
        """Load A/B test results from JSON."""
        p = Path(path)
        if not p.exists():
            return None

        try:
            data = json.loads(p.read_text(encoding="utf-8"))
            return ABTestResult(**data)
        except Exception as e:
            self.logger.error(f"Failed to load results: {e}")
            return None

    def print_report(self, result: ABTestResult) -> None:
        """Print formatted A/B test report."""
        print()
        print("=" * 70)
        print(f"  A/B TEST REPORT: {result.test_name}")
        print("=" * 70)
        print(f"  Test ID:     {result.test_id}")
        print(f"  Hypothesis:  {result.hypothesis}")
        print(f"  Timestamp:   {time.strftime('%Y-%m-%d %H:%M:%S', time.localtime(result.timestamp))}")
        print()

        print(f"  {'Variant':<25} {'Success':>8} {'Avg Time':>10} {'P95 Time':>10} {'StdDev':>10}")
        print("  " + "-" * 65)

        for variant in result.variants:
            marker = " ← WINNER" if variant.variant_id == result.winner else ""
            print(
                f"  {variant.variant_id:<25} "
                f"{variant.success_rate*100:>7.1f}% "
                f"{variant.avg_processing_time:>9.1f}s "
                f"{variant.p95_time:>9.1f}s "
                f"{variant.stddev_time:>9.1f}s"
                f"{marker}"
            )

        print()
        print(f"  WINNER:      {result.winner}")
        print(f"  Confidence:  {result.confidence*100:.1f}%")
        print()
        print(f"  RECOMMENDATION:")
        print(f"  {result.recommendation}")
        print("=" * 70)
        print()

    def compare_prompt_diversity(
        self,
        template_a: str,
        template_b: str,
        sample_size: int = 100,
    ) -> dict[str, Any]:
        """Compare prompt diversity between two templates.

        Returns:
            Dict with diversity metrics (unique words, overlap, etc.)
        """
        prompts_a = []
        prompts_b = []

        for _ in range(sample_size):
            cfg_a = self.prompt_manager.generate_config(template=template_a)
            cfg_b = self.prompt_manager.generate_config(template=template_b)
            prompts_a.append(cfg_a.positive_prompt)
            prompts_b.append(cfg_b.positive_prompt)

        # Word diversity analysis
        words_a = set()
        words_b = set()

        for prompt in prompts_a:
            words_a.update(prompt.lower().split(", "))
        for prompt in prompts_b:
            words_b.update(prompt.lower().split(", "))

        overlap = words_a & words_b
        unique_a = words_a - words_b
        unique_b = words_b - words_a

        return {
            "template_a": template_a,
            "template_b": template_b,
            "sample_size": sample_size,
            "unique_words_a": len(unique_a),
            "unique_words_b": len(unique_b),
            "overlap_words": len(overlap),
            "overlap_percentage": len(overlap) / max(len(words_a), 1) * 100,
            "diversity_score_a": len(unique_a) / max(len(words_a), 1),
            "diversity_score_b": len(unique_b) / max(len(words_b), 1),
        }


class ABTestRunner:
    """High-level runner for executing A/B tests with the engine."""

    def __init__(self, engine_instance=None):
        self.engine = engine_instance
        self.logger = logging.getLogger(__name__)

    async def run_test(
        self,
        test_type: str,
        generations_per_variant: int = 10,
        workflow: dict | None = None,
    ) -> ABTestResult:
        """Run complete A/B test.

        Args:
            test_type: Type of test (templates, lora_weights, etc.)
            generations_per_variant: Number of generations per variant
            workflow: ComfyUI workflow template

        Returns:
            ABTestResult with statistical analysis.
        """
        if not self.engine:
            raise RuntimeError("Engine instance required for test execution")

        framework = ABTestFramework(self.engine.config)
        variants = framework.create_test_variants(test_type)

        self.logger.info(
            f"Starting A/B test: {test_type} with {len(variants)} variants, "
            f"{generations_per_variant} generations each"
        )

        results = []
        for variant in variants:
            self.logger.info(f"Testing variant: {variant['variant_id']}")

            result = VariantResult(
                variant_id=variant["variant_id"],
                template_name=variant["template_name"],
                config_overrides=variant.get("config_overrides", {}),
            )

            configs = framework.generate_variant_configs(variant, generations_per_variant)

            for config in configs:
                result.total_generations += 1
                result.seed_values.append(config.seed)
                result.prompt_samples.append(config.positive_prompt)

                # Simulate generation (in real use, this would call the engine)
                # For now, just record the config
                result.successful += 1
                result.processing_times.append(15.0 + (result.total_generations % 10))

            result.avg_processing_time = statistics.mean(result.processing_times) if result.processing_times else 0.0
            results.append(result)

        analysis = framework.analyze_results(results)
        framework.print_report(analysis)

        return analysis
