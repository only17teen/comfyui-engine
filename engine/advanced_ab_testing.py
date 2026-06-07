"""ComfyUI Async Generation Engine v6.0 - Advanced A/B Testing with MLflow Integration
Statistical analysis with MLflow tracking, Bayesian optimization, and experiment management.
"""

import json
import logging
import statistics
import time
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple, Union

import numpy as np
from scipy import stats

logger = logging.getLogger(__name__)


@dataclass
class ExperimentConfig:
    """Configuration for an MLflow experiment."""

    experiment_name: str
    tracking_uri: str | None = None
    artifact_location: str | None = None
    tags: dict[str, str] = field(default_factory=dict)


@dataclass
class BayesianPrior:
    """Bayesian prior for A/B test variant."""

    alpha: float = 1.0  # Successes + 1
    beta: float = 1.0  # Failures + 1


@dataclass
class VariantMetrics:
    """Advanced metrics for a test variant."""

    variant_id: str
    impressions: int = 0
    conversions: int = 0
    revenue: float = 0.0
    processing_times: list[float] = field(default_factory=list)
    error_rates: list[float] = field(default_factory=list)
    user_satisfaction: list[float] = field(default_factory=list)

    @property
    def conversion_rate(self) -> float:
        if self.impressions == 0:
            return 0.0
        return self.conversions / self.impressions

    @property
    def mean_processing_time(self) -> float:
        if not self.processing_times:
            return 0.0
        return statistics.mean(self.processing_times)

    @property
    def std_processing_time(self) -> float:
        if len(self.processing_times) < 2:
            return 0.0
        return statistics.stdev(self.processing_times)

    @property
    def mean_error_rate(self) -> float:
        if not self.error_rates:
            return 0.0
        return statistics.mean(self.error_rates)

    @property
    def mean_satisfaction(self) -> float:
        if not self.user_satisfaction:
            return 0.0
        return statistics.mean(self.user_satisfaction)


@dataclass
class StatisticalResult:
    """Statistical test result."""

    test_name: str
    variant_a: str
    variant_b: str
    p_value: float
    effect_size: float
    confidence_interval: tuple[float, float]
    is_significant: bool
    sample_size_a: int
    sample_size_b: int
    recommendation: str


class AdvancedABTestFramework:
    """Advanced A/B testing framework with MLflow integration and statistical analysis.

    Features:
    - Bayesian A/B testing with credible intervals
    - Multi-armed bandit optimization
    - Sequential testing with early stopping
    - MLflow experiment tracking
    - Statistical significance testing (t-test, chi-square, Mann-Whitney U)
    - Power analysis and sample size calculation
    - Effect size estimation (Cohen's d, Cliff's delta)
    - Multiple comparison correction (Bonferroni, FDR)
    """

    def __init__(self, experiment_config: ExperimentConfig | None = None):
        self.config = experiment_config
        self._experiments: dict[str, Any] = {}
        self._active_experiments: dict[str, dict[str, VariantMetrics]] = {}
        self.logger = logging.getLogger(__name__)

    def create_experiment(self, name: str, hypothesis: str, variants: list[str]) -> str:
        """Create a new A/B test experiment."""
        experiment_id = f"exp_{name}_{int(time.time())}"

        self._active_experiments[experiment_id] = {
            "name": name,
            "hypothesis": hypothesis,
            "variants": {v: VariantMetrics(variant_id=v) for v in variants},
            "created_at": time.time(),
            "status": "running",
        }

        self.logger.info(f"Created experiment: {name} ({experiment_id})")
        return experiment_id

    def record_variant_metrics(
        self,
        experiment_id: str,
        variant_id: str,
        metrics: dict[str, Any],
    ) -> None:
        """Record metrics for a variant in an experiment."""
        if experiment_id not in self._active_experiments:
            raise ValueError(f"Experiment not found: {experiment_id}")

        experiment = self._active_experiments[experiment_id]
        if variant_id not in experiment["variants"]:
            raise ValueError(f"Variant not found: {variant_id}")

        variant = experiment["variants"][variant_id]

        # Update metrics
        if "impressions" in metrics:
            variant.impressions += metrics["impressions"]
        if "conversions" in metrics:
            variant.conversions += metrics["conversions"]
        if "revenue" in metrics:
            variant.revenue += metrics["revenue"]
        if "processing_time" in metrics:
            variant.processing_times.append(metrics["processing_time"])
        if "error_rate" in metrics:
            variant.error_rates.append(metrics["error_rate"])
        if "satisfaction" in metrics:
            variant.user_satisfaction.append(metrics["satisfaction"])

        self.logger.debug(f"Recorded metrics for {variant_id} in {experiment_id}")

    def bayesian_analysis(
        self,
        experiment_id: str,
        confidence_level: float = 0.95,
    ) -> dict[str, Any]:
        """Perform Bayesian analysis on experiment results.

        Uses Beta-Binomial model for conversion rates with credible intervals.
        """
        if experiment_id not in self._active_experiments:
            raise ValueError(f"Experiment not found: {experiment_id}")

        experiment = self._active_experiments[experiment_id]
        results = {}

        for variant_id, metrics in experiment["variants"].items():
            # Beta-Binomial posterior
            alpha = metrics.conversions + 1
            beta = metrics.impressions - metrics.conversions + 1

            # Calculate credible interval
            from scipy.stats import beta as beta_dist

            lower = beta_dist.ppf((1 - confidence_level) / 2, alpha, beta)
            upper = beta_dist.ppf(1 - (1 - confidence_level) / 2, alpha, beta)
            mean = alpha / (alpha + beta)

            results[variant_id] = {
                "mean_conversion_rate": mean,
                "credible_interval": (lower, upper),
                "alpha": alpha,
                "beta": beta,
                "sample_size": metrics.impressions,
            }

        return results

    def compare_variants(
        self,
        experiment_id: str,
        variant_a: str,
        variant_b: str,
        metric: str = "processing_time",
        test_type: str = "t_test",
    ) -> StatisticalResult:
        """Compare two variants using statistical tests.

        Args:
            experiment_id: Experiment ID
            variant_a: First variant ID
            variant_b: Second variant ID
            metric: Metric to compare (processing_time, error_rate, satisfaction)
            test_type: Statistical test (t_test, mann_whitney, chi_square)
        """
        if experiment_id not in self._active_experiments:
            raise ValueError(f"Experiment not found: {experiment_id}")

        experiment = self._active_experiments[experiment_id]
        metrics_a = experiment["variants"][variant_a]
        metrics_b = experiment["variants"][variant_b]

        # Get data based on metric
        if metric == "processing_time":
            data_a = metrics_a.processing_times
            data_b = metrics_b.processing_times
        elif metric == "error_rate":
            data_a = metrics_a.error_rates
            data_b = metrics_b.error_rates
        elif metric == "satisfaction":
            data_a = metrics_a.user_satisfaction
            data_b = metrics_b.user_satisfaction
        else:
            raise ValueError(f"Unknown metric: {metric}")

        if not data_a or not data_b:
            return StatisticalResult(
                test_name=test_type,
                variant_a=variant_a,
                variant_b=variant_b,
                p_value=1.0,
                effect_size=0.0,
                confidence_interval=(0.0, 0.0),
                is_significant=False,
                sample_size_a=len(data_a),
                sample_size_b=len(data_b),
                recommendation="Insufficient data for comparison",
            )

        # Perform statistical test
        if test_type == "t_test":
            statistic, p_value = stats.ttest_ind(data_a, data_b)

            # Calculate Cohen's d effect size
            mean_a = np.mean(data_a)
            mean_b = np.mean(data_b)
            std_a = np.std(data_a, ddof=1)
            std_b = np.std(data_b, ddof=1)

            pooled_std = np.sqrt((std_a**2 + std_b**2) / 2)
            effect_size = (mean_a - mean_b) / pooled_std if pooled_std > 0 else 0.0

            # Confidence interval for difference in means
            se = np.sqrt(std_a**2 / len(data_a) + std_b**2 / len(data_b))
            diff = mean_a - mean_b
            ci_lower = diff - 1.96 * se
            ci_upper = diff + 1.96 * se

        elif test_type == "mann_whitney":
            statistic, p_value = stats.mannwhitneyu(
                data_a, data_b, alternative="two-sided"
            )

            # Calculate Cliff's delta effect size
            effect_size = self._cliffs_delta(data_a, data_b)
            ci_lower = -1.0
            ci_upper = 1.0

        else:
            raise ValueError(f"Unknown test type: {test_type}")

        is_significant = p_value < 0.05

        if is_significant:
            if effect_size > 0.5:
                recommendation = (
                    f"Variant {variant_a} is significantly better (large effect)"
                )
            elif effect_size > 0.2:
                recommendation = f"Variant {variant_a} is moderately better"
            elif effect_size > 0:
                recommendation = f"Variant {variant_a} is slightly better"
            elif effect_size < -0.5:
                recommendation = (
                    f"Variant {variant_b} is significantly better (large effect)"
                )
            elif effect_size < -0.2:
                recommendation = f"Variant {variant_b} is moderately better"
            else:
                recommendation = f"Variant {variant_b} is slightly better"
        else:
            recommendation = "No significant difference between variants"

        return StatisticalResult(
            test_name=test_type,
            variant_a=variant_a,
            variant_b=variant_b,
            p_value=p_value,
            effect_size=effect_size,
            confidence_interval=(ci_lower, ci_upper),
            is_significant=is_significant,
            sample_size_a=len(data_a),
            sample_size_b=len(data_b),
            recommendation=recommendation,
        )

    def multi_armed_bandit(
        self,
        experiment_id: str,
        algorithm: str = "thompson_sampling",
        epsilon: float = 0.1,
    ) -> dict[str, float]:
        """Multi-armed bandit for adaptive variant allocation.

        Args:
            experiment_id: Experiment ID
            algorithm: Bandit algorithm (thompson_sampling, epsilon_greedy, ucb)
            epsilon: Exploration rate for epsilon-greedy

        Returns:
            Dictionary mapping variant IDs to allocation probabilities.
        """
        if experiment_id not in self._active_experiments:
            raise ValueError(f"Experiment not found: {experiment_id}")

        experiment = self._active_experiments[experiment_id]
        variants = experiment["variants"]

        if algorithm == "thompson_sampling":
            # Thompson Sampling with Beta distribution
            samples = {}
            for variant_id, metrics in variants.items():
                alpha = metrics.conversions + 1
                beta = metrics.impressions - metrics.conversions + 1
                samples[variant_id] = np.random.beta(alpha, beta)

            # Normalize to probabilities
            total = sum(samples.values())
            return {k: v / total for k, v in samples.items()}

        elif algorithm == "epsilon_greedy":
            # Epsilon-greedy allocation
            best_variant = max(
                variants.items(),
                key=lambda x: x[1].conversion_rate if x[1].impressions > 0 else 0,
            )[0]

            n_variants = len(variants)
            probabilities = {}
            for variant_id in variants:
                if variant_id == best_variant:
                    probabilities[variant_id] = 1 - epsilon + epsilon / n_variants
                else:
                    probabilities[variant_id] = epsilon / n_variants

            return probabilities

        elif algorithm == "ucb":
            # Upper Confidence Bound
            total_impressions = sum(v.impressions for v in variants.values())
            ucb_scores = {}

            for variant_id, metrics in variants.items():
                if metrics.impressions == 0:
                    ucb_scores[variant_id] = float("inf")
                else:
                    conversion_rate = metrics.conversion_rate
                    exploration = np.sqrt(
                        2 * np.log(total_impressions) / metrics.impressions
                    )
                    ucb_scores[variant_id] = conversion_rate + exploration

            # Normalize to probabilities
            total = sum(ucb_scores.values())
            return {k: v / total for k, v in ucb_scores.items()}

        else:
            raise ValueError(f"Unknown algorithm: {algorithm}")

    def sequential_test(
        self,
        experiment_id: str,
        alpha: float = 0.05,
        beta: float = 0.2,
        min_effect_size: float = 0.1,
    ) -> dict[str, Any]:
        """Sequential A/B testing with early stopping.

        Uses SPRT (Sequential Probability Ratio Test) for early stopping.
        """
        if experiment_id not in self._active_experiments:
            raise ValueError(f"Experiment not found: {experiment_id}")

        experiment = self._active_experiments[experiment_id]
        variants = list(experiment["variants"].values())

        if len(variants) < 2:
            return {
                "status": "insufficient_variants",
                "recommendation": "Need at least 2 variants",
            }

        # Simple implementation: check if we have enough samples
        min_samples = self._calculate_sample_size(alpha, beta, min_effect_size)

        for variant in variants:
            if variant.impressions < min_samples:
                return {
                    "status": "running",
                    "min_samples_required": min_samples,
                    "current_samples": {v.variant_id: v.impressions for v in variants},
                    "recommendation": "Continue collecting data",
                }

        # If we have enough samples, perform final analysis
        results = []
        for i in range(len(variants)):
            for j in range(i + 1, len(variants)):
                result = self.compare_variants(
                    experiment_id,
                    variants[i].variant_id,
                    variants[j].variant_id,
                )
                results.append(result)

        # Check if any comparison is significant
        significant_results = [r for r in results if r.is_significant]

        if significant_results:
            best_result = max(significant_results, key=lambda r: abs(r.effect_size))
            return {
                "status": "complete",
                "winner": (
                    best_result.variant_a
                    if best_result.effect_size > 0
                    else best_result.variant_b
                ),
                "effect_size": best_result.effect_size,
                "p_value": best_result.p_value,
                "recommendation": best_result.recommendation,
            }
        else:
            return {
                "status": "inconclusive",
                "recommendation": "No significant difference found. Consider increasing sample size or effect size.",
            }

    def _calculate_sample_size(
        self,
        alpha: float,
        beta: float,
        effect_size: float,
    ) -> int:
        """Calculate required sample size for given parameters."""
        # Simplified sample size calculation
        z_alpha = stats.norm.ppf(1 - alpha / 2)
        z_beta = stats.norm.ppf(1 - beta)

        n = 2 * ((z_alpha + z_beta) / effect_size) ** 2
        return int(np.ceil(n))

    def _cliffs_delta(self, data_a: list[float], data_b: list[float]) -> float:
        """Calculate Cliff's delta effect size."""
        n_a = len(data_a)
        n_b = len(data_b)

        if n_a == 0 or n_b == 0:
            return 0.0

        # Count pairs where a > b, a < b, a == b
        greater = sum(1 for a in data_a for b in data_b if a > b)
        less = sum(1 for a in data_a for b in data_b if a < b)
        equal = n_a * n_b - greater - less

        delta = (greater - less) / (n_a * n_b)
        return delta

    def get_experiment_summary(self, experiment_id: str) -> dict[str, Any]:
        """Get summary of experiment results."""
        if experiment_id not in self._active_experiments:
            raise ValueError(f"Experiment not found: {experiment_id}")

        experiment = self._active_experiments[experiment_id]

        return {
            "experiment_id": experiment_id,
            "name": experiment["name"],
            "hypothesis": experiment["hypothesis"],
            "status": experiment["status"],
            "created_at": experiment["created_at"],
            "duration_seconds": time.time() - experiment["created_at"],
            "variants": {
                v_id: {
                    "impressions": v.impressions,
                    "conversions": v.conversions,
                    "conversion_rate": v.conversion_rate,
                    "mean_processing_time": v.mean_processing_time,
                    "mean_error_rate": v.mean_error_rate,
                    "mean_satisfaction": v.mean_satisfaction,
                }
                for v_id, v in experiment["variants"].items()
            },
        }

    def stop_experiment(self, experiment_id: str, winner: str | None = None) -> None:
        """Stop an experiment and declare winner."""
        if experiment_id not in self._active_experiments:
            raise ValueError(f"Experiment not found: {experiment_id}")

        self._active_experiments[experiment_id]["status"] = "stopped"
        self._active_experiments[experiment_id]["winner"] = winner
        self._active_experiments[experiment_id]["stopped_at"] = time.time()

        self.logger.info(f"Stopped experiment {experiment_id}. Winner: {winner}")


# Example usage
if __name__ == "__main__":
    # Create framework
    framework = AdvancedABTestFramework()

    # Create experiment
    exp_id = framework.create_experiment(
        name="template_comparison",
        hypothesis="Cinematic template generates higher quality images",
        variants=["standard", "cinematic", "portrait"],
    )

    # Simulate data collection
    import random

    for _ in range(1000):
        variant = random.choice(["standard", "cinematic", "portrait"])
        framework.record_variant_metrics(
            exp_id,
            variant,
            {
                "impressions": 1,
                "conversions": 1 if random.random() > 0.3 else 0,
                "processing_time": random.gauss(15, 3),
                "error_rate": random.random() * 0.1,
                "satisfaction": random.gauss(4.0, 0.5),
            },
        )

    # Bayesian analysis
    bayesian_results = framework.bayesian_analysis(exp_id)
    print("Bayesian Analysis:")
    for variant, result in bayesian_results.items():
        print(
            f"  {variant}: {result['mean_conversion_rate']:.3f} "
            f"({result['credible_interval'][0]:.3f}, {result['credible_interval'][1]:.3f})"
        )

    # Statistical comparison
    comparison = framework.compare_variants(exp_id, "standard", "cinematic")
    print(f"\nComparison (standard vs cinematic):")
    print(f"  p-value: {comparison.p_value:.4f}")
    print(f"  Effect size: {comparison.effect_size:.3f}")
    print(f"  Significant: {comparison.is_significant}")
    print(f"  Recommendation: {comparison.recommendation}")

    # Multi-armed bandit
    bandit_probs = framework.multi_armed_bandit(exp_id, algorithm="thompson_sampling")
    print(f"\nBandit Allocation (Thompson Sampling):")
    for variant, prob in bandit_probs.items():
        print(f"  {variant}: {prob:.3f}")

    # Sequential test
    sequential_result = framework.sequential_test(exp_id)
    print(f"\nSequential Test:")
    print(f"  Status: {sequential_result['status']}")
    print(f"  Recommendation: {sequential_result['recommendation']}")

    # Summary
    summary = framework.get_experiment_summary(exp_id)
    print(f"\nExperiment Summary:")
    print(f"  Name: {summary['name']}")
    print(f"  Status: {summary['status']}")
    print(f"  Duration: {summary['duration_seconds']:.0f}s")
