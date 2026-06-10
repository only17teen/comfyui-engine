"""ComfyUI Async Generation Engine v2.0 - Genetic Algorithm Prompt Optimizer
Evolutionary optimization of prompts for maximum generation quality.
"""

import asyncio
import copy
import json
import logging
import random
import statistics
import time
from dataclasses import dataclass, field
from enum import Enum, auto
from pathlib import Path
from typing import Any, Callable, Dict, List, Optional, Tuple

from engine.config import EngineConfig
from engine.prompt_manager import PromptManager, GenerationConfig

logger = logging.getLogger(__name__)


class FitnessMetric(Enum):
    """Metrics for evaluating prompt fitness."""

    SUCCESS_RATE = auto()
    GENERATION_SPEED = auto()
    PROMPT_DIVERSITY = auto()
    LORA_COMPATIBILITY = auto()
    USER_RATING = auto()
    COMBINED = auto()


@dataclass
class Chromosome:
    """A single chromosome representing a prompt configuration."""

    genes: dict[str, Any]  # Prompt components: triggers, clothing, poses, etc.
    lora_weights: dict[str, float]
    cfg_scale: float
    steps: int
    sampler: str
    seed: int
    fitness: float = 0.0
    generation_count: int = 0
    success_count: int = 0
    avg_time: float = 0.0
    metadata: dict[str, Any] = field(default_factory=dict)

    def to_generation_config(self, config: EngineConfig) -> GenerationConfig:
        """Convert chromosome to GenerationConfig."""
        return GenerationConfig(
            seed=self.seed,
            positive_prompt=self._build_prompt(),
            negative_prompt=config.prompts.negative_prompt,
            width=512,
            height=768,
            steps=self.steps,
            cfg_scale=self.cfg_scale,
            sampler_name=self.sampler,
            scheduler="Karras",
            lora_stack=[],  # Built from lora_weights
            batch_size=1,
            tags=["ga_optimized"],
        )

    def _build_prompt(self) -> str:
        """Build positive prompt from genes."""
        parts = []
        for _key, value in self.genes.items():
            if isinstance(value, list):
                parts.extend(value)
            elif isinstance(value, str):
                parts.append(value)
        return ", ".join(parts)

    def copy(self) -> "Chromosome":
        """Create deep copy."""
        return Chromosome(
            genes=copy.deepcopy(self.genes),
            lora_weights=copy.deepcopy(self.lora_weights),
            cfg_scale=self.cfg_scale,
            steps=self.steps,
            sampler=self.sampler,
            seed=random.randint(1, 2**32 - 1),
            fitness=0.0,
            generation_count=0,
            success_count=0,
            avg_time=0.0,
            metadata=copy.deepcopy(self.metadata),
        )


@dataclass
class GenerationResult:
    """Result of evaluating a generation."""

    chromosome_id: str
    success: bool
    generation_time: float
    image_quality_score: float | None = None
    user_rating: float | None = None
    error: str | None = None


@dataclass
class Population:
    """A population of chromosomes."""

    chromosomes: list[Chromosome]
    generation: int = 0
    best_fitness: float = 0.0
    avg_fitness: float = 0.0
    diversity_score: float = 0.0

    def to_dict(self) -> dict:
        return {
            "generation": self.generation,
            "population_size": len(self.chromosomes),
            "best_fitness": self.best_fitness,
            "avg_fitness": self.avg_fitness,
            "diversity_score": self.diversity_score,
            "chromosomes": [
                {
                    "genes": c.genes,
                    "lora_weights": c.lora_weights,
                    "cfg_scale": c.cfg_scale,
                    "steps": c.steps,
                    "sampler": c.sampler,
                    "fitness": c.fitness,
                    "success_rate": c.success_count / max(c.generation_count, 1),
                }
                for c in self.chromosomes
            ],
        }


class GeneticAlgorithmConfig:
    """Configuration for genetic algorithm."""

    def __init__(
        self,
        population_size: int = 50,
        generations: int = 20,
        elite_count: int = 5,
        mutation_rate: float = 0.15,
        crossover_rate: float = 0.7,
        tournament_size: int = 5,
        fitness_metric: FitnessMetric = FitnessMetric.COMBINED,
        diversity_weight: float = 0.2,
        convergence_threshold: float = 0.01,
        max_stagnation: int = 5,
        parallel_evaluations: int = 4,
    ):
        self.population_size = population_size
        self.generations = generations
        self.elite_count = elite_count
        self.mutation_rate = mutation_rate
        self.crossover_rate = crossover_rate
        self.tournament_size = tournament_size
        self.fitness_metric = fitness_metric
        self.diversity_weight = diversity_weight
        self.convergence_threshold = convergence_threshold
        self.max_stagnation = max_stagnation
        self.parallel_evaluations = parallel_evaluations


class PromptFitnessEvaluator:
    """Evaluates fitness of prompt chromosomes based on generation results."""

    def __init__(self, metric: FitnessMetric = FitnessMetric.COMBINED):
        self.metric = metric
        self.history: list[GenerationResult] = []

    def evaluate(
        self, chromosome: Chromosome, results: list[GenerationResult]
    ) -> float:
        """Calculate fitness score from generation results."""
        if not results:
            return 0.0

        # Update chromosome statistics
        chromosome.generation_count = len(results)
        chromosome.success_count = sum(1 for r in results if r.success)
        chromosome.avg_time = (
            statistics.mean(r.generation_time for r in results) if results else 0.0
        )

        if self.metric == FitnessMetric.SUCCESS_RATE:
            return chromosome.success_count / max(chromosome.generation_count, 1)

        elif self.metric == FitnessMetric.GENERATION_SPEED:
            if chromosome.avg_time > 0:
                return 1.0 / chromosome.avg_time  # Inverse of time
            return 0.0

        elif self.metric == FitnessMetric.PROMPT_DIVERSITY:
            return self._calculate_diversity(chromosome, results)

        elif self.metric == FitnessMetric.USER_RATING:
            ratings = [r.user_rating for r in results if r.user_rating is not None]
            return statistics.mean(ratings) if ratings else 0.0

        elif self.metric == FitnessMetric.COMBINED:
            success_rate = chromosome.success_count / max(
                chromosome.generation_count, 1
            )
            speed_score = 1.0 / max(chromosome.avg_time, 1.0)
            diversity = self._calculate_diversity(chromosome, results)
            ratings = [r.user_rating for r in results if r.user_rating is not None]
            rating_score = statistics.mean(ratings) if ratings else 0.5

            # Weighted combination
            return (
                success_rate * 0.4
                + speed_score * 0.2
                + diversity * 0.2
                + rating_score * 0.2
            )

        return 0.0

    def _calculate_diversity(
        self,
        chromosome: Chromosome,
        results: list[GenerationResult],
    ) -> float:
        """Calculate prompt diversity score."""
        if not results:
            return 0.0

        # Measure unique words in genes
        all_words = set()
        for _key, value in chromosome.genes.items():
            if isinstance(value, list):
                for item in value:
                    all_words.update(str(item).lower().split())
            elif isinstance(value, str):
                all_words.update(value.lower().split())

        # More unique words = higher diversity
        return min(len(all_words) / 50.0, 1.0)  # Normalize to 0-1


class GeneticPromptOptimizer:
    """Genetic algorithm for optimizing prompt configurations.

    Features:
    - Tournament selection
    - Multi-point crossover
    - Adaptive mutation rates
    - Elitism preservation
    - Diversity maintenance
    - Convergence detection
    - Parallel fitness evaluation
    """

    def __init__(
        self,
        config: EngineConfig,
        ga_config: GeneticAlgorithmConfig | None = None,
    ):
        self.engine_config = config
        self.prompt_manager = PromptManager(config)
        self.ga_config = ga_config or GeneticAlgorithmConfig()
        self.evaluator = PromptFitnessEvaluator(self.ga_config.fitness_metric)

        self._population: Population | None = None
        self._best_chromosome: Chromosome | None = None
        self._stagnation_count: int = 0
        self._last_best_fitness: float = 0.0

        self.logger = logging.getLogger(__name__)

    def _create_random_chromosome(self) -> Chromosome:
        """Create a random chromosome from prompt manager."""
        gen_config = self.prompt_manager.generate_config()

        # Extract genes from generated prompt
        genes = {
            "triggers": self._extract_triggers(gen_config.positive_prompt),
            "clothing": self._extract_clothing(gen_config.positive_prompt),
            "pose": self._extract_pose(gen_config.positive_prompt),
            "location": self._extract_location(gen_config.positive_prompt),
            "lighting": self._extract_lighting(gen_config.positive_prompt),
            "expression": self._extract_expression(gen_config.positive_prompt),
        }

        # Random LoRA weights
        lora_weights = {}
        for lora in self.engine_config.lora.models:
            lora_weights[lora.name] = random.uniform(
                lora.weight_range[0], lora.weight_range[1]
            )

        return Chromosome(
            genes=genes,
            lora_weights=lora_weights,
            cfg_scale=random.uniform(
                self.engine_config.lora.sampling.cfg_scale_range[0],
                self.engine_config.lora.sampling.cfg_scale_range[1],
            ),
            steps=random.randint(
                self.engine_config.lora.sampling.steps_range[0],
                self.engine_config.lora.sampling.steps_range[1],
            ),
            sampler=random.choice(self.engine_config.lora.sampling.sampler_names),
            seed=gen_config.seed,
        )

    def _extract_triggers(self, prompt: str) -> list[str]:
        """Extract trigger words from prompt."""
        triggers = []
        for word in self.engine_config.prompts.trigger_words:
            if word.lower() in prompt.lower():
                triggers.append(word)
        return triggers

    def _extract_clothing(self, prompt: str) -> list[str]:
        """Extract clothing items from prompt."""
        clothing = []
        for category in ["tops", "bottoms", "accessories", "full_body"]:
            items = getattr(self.engine_config.prompts.clothing, category, [])
            for item in items:
                if item.lower() in prompt.lower():
                    clothing.append(item)
        return clothing

    def _extract_pose(self, prompt: str) -> str:
        """Extract pose from prompt."""
        for pose in self.engine_config.prompts.poses:
            if pose.lower() in prompt.lower():
                return pose
        return "standing"

    def _extract_location(self, prompt: str) -> str:
        """Extract location from prompt."""
        for location in self.engine_config.prompts.locations:
            if location.lower() in prompt.lower():
                return location
        return "city"

    def _extract_lighting(self, prompt: str) -> str:
        """Extract lighting from prompt."""
        for lighting in self.engine_config.prompts.lighting:
            if lighting.lower() in prompt.lower():
                return lighting
        return "daylight"

    def _extract_expression(self, prompt: str) -> str:
        """Extract expression from prompt."""
        for expression in self.engine_config.prompts.expressions:
            if expression.lower() in prompt.lower():
                return expression
        return "smile"

    def initialize_population(self) -> Population:
        """Create initial random population."""
        chromosomes = [
            self._create_random_chromosome()
            for _ in range(self.ga_config.population_size)
        ]

        self._population = Population(
            chromosomes=chromosomes,
            generation=0,
        )

        self.logger.info(f"Initialized population with {len(chromosomes)} chromosomes")
        return self._population

    def _tournament_select(self) -> Chromosome:
        """Select chromosome using tournament selection."""
        tournament = random.sample(
            self._population.chromosomes,
            min(self.ga_config.tournament_size, len(self._population.chromosomes)),
        )
        return max(tournament, key=lambda c: c.fitness)

    def _crossover(
        self,
        parent1: Chromosome,
        parent2: Chromosome,
    ) -> tuple[Chromosome, Chromosome]:
        """Perform multi-point crossover."""
        if random.random() > self.ga_config.crossover_rate:
            return parent1.copy(), parent2.copy()

        child1 = parent1.copy()
        child2 = parent2.copy()

        # Crossover genes
        gene_keys = list(parent1.genes.keys())
        crossover_points = sorted(random.sample(range(len(gene_keys)), 2))

        for i in range(crossover_points[0], crossover_points[1]):
            key = gene_keys[i]
            child1.genes[key], child2.genes[key] = (
                copy.deepcopy(parent2.genes[key]),
                copy.deepcopy(parent1.genes[key]),
            )

        # Crossover parameters
        if random.random() < 0.5:
            child1.cfg_scale, child2.cfg_scale = parent2.cfg_scale, parent1.cfg_scale
        if random.random() < 0.5:
            child1.steps, child2.steps = parent2.steps, parent1.steps
        if random.random() < 0.5:
            child1.sampler, child2.sampler = parent2.sampler, parent1.sampler

        # Crossover LoRA weights
        lora_keys = list(parent1.lora_weights.keys())
        for key in lora_keys:
            if random.random() < 0.5:
                child1.lora_weights[key], child2.lora_weights[key] = (
                    parent2.lora_weights[key],
                    parent1.lora_weights[key],
                )

        return child1, child2

    def _mutate(self, chromosome: Chromosome) -> None:
        """Apply mutation to chromosome."""
        # Adaptive mutation rate based on stagnation
        mutation_rate = self.ga_config.mutation_rate
        if self._stagnation_count > 2:
            mutation_rate = min(
                mutation_rate * 1.5, 0.5
            )  # Increase mutation when stuck

        # Mutate genes
        for key in chromosome.genes:
            if random.random() < mutation_rate:
                if key == "triggers":
                    chromosome.genes[key] = random.sample(
                        self.engine_config.prompts.trigger_words,
                        k=random.randint(3, 5),
                    )
                elif key == "clothing":
                    chromosome.genes[key] = self._random_clothing()
                elif key in ["pose", "location", "lighting", "expression"]:
                    chromosome.genes[key] = self._random_from_category(key)

        # Mutate parameters
        if random.random() < mutation_rate:
            chromosome.cfg_scale = random.uniform(
                self.engine_config.lora.sampling.cfg_scale_range[0],
                self.engine_config.lora.sampling.cfg_scale_range[1],
            )
        if random.random() < mutation_rate:
            chromosome.steps = random.randint(
                self.engine_config.lora.sampling.steps_range[0],
                self.engine_config.lora.sampling.steps_range[1],
            )
        if random.random() < mutation_rate:
            chromosome.sampler = random.choice(
                self.engine_config.lora.sampling.sampler_names
            )

        # Mutate LoRA weights
        for key in chromosome.lora_weights:
            if random.random() < mutation_rate:
                lora_config = next(
                    (l for l in self.engine_config.lora.models if l.name == key),
                    None,
                )
                if lora_config:
                    chromosome.lora_weights[key] = random.uniform(
                        lora_config.weight_range[0],
                        lora_config.weight_range[1],
                    )

        # Always new seed
        chromosome.seed = random.randint(1, 2**32 - 1)

    def _random_clothing(self) -> list[str]:
        """Generate random clothing combination."""
        clothing = []
        categories = ["tops", "bottoms", "accessories"]
        for category in categories:
            items = getattr(self.engine_config.prompts.clothing, category, [])
            if items and random.random() < 0.7:
                clothing.append(random.choice(items))
        return clothing

    def _random_from_category(self, category: str) -> str:
        """Get random item from category."""
        mapping = {
            "pose": self.engine_config.prompts.poses,
            "location": self.engine_config.prompts.locations,
            "lighting": self.engine_config.prompts.lighting,
            "expression": self.engine_config.prompts.expressions,
        }
        items = mapping.get(category, [])
        return random.choice(items) if items else ""

    def _calculate_diversity(self, population: Population) -> float:
        """Calculate population diversity score."""
        if len(population.chromosomes) < 2:
            return 0.0

        # Measure genetic distance between chromosomes
        distances = []
        for i in range(len(population.chromosomes)):
            for j in range(i + 1, len(population.chromosomes)):
                dist = self._genetic_distance(
                    population.chromosomes[i],
                    population.chromosomes[j],
                )
                distances.append(dist)

        return statistics.mean(distances) if distances else 0.0

    def _genetic_distance(self, c1: Chromosome, c2: Chromosome) -> float:
        """Calculate genetic distance between two chromosomes."""
        distance = 0.0

        # Gene difference
        for key in c1.genes:
            if c1.genes[key] != c2.genes[key]:
                distance += 1.0

        # Parameter difference
        distance += abs(c1.cfg_scale - c2.cfg_scale) / 10.0
        distance += abs(c1.steps - c2.steps) / 40.0
        if c1.sampler != c2.sampler:
            distance += 1.0

        # LoRA weight difference
        for key in c1.lora_weights:
            if key in c2.lora_weights:
                distance += abs(c1.lora_weights[key] - c2.lora_weights[key])

        return distance

    def _check_convergence(self, population: Population) -> bool:
        """Check if population has converged."""
        if (
            population.best_fitness - self._last_best_fitness
            < self.ga_config.convergence_threshold
        ):
            self._stagnation_count += 1
        else:
            self._stagnation_count = 0

        self._last_best_fitness = population.best_fitness

        return self._stagnation_count >= self.ga_config.max_stagnation

    async def evaluate_population(
        self,
        population: Population,
        evaluation_func: Callable[[Chromosome], list[GenerationResult]] | None = None,
    ) -> None:
        """Evaluate fitness of all chromosomes in population."""
        if evaluation_func is None:
            # Default: random fitness for testing
            for chromosome in population.chromosomes:
                chromosome.fitness = random.random()
            return

        # Parallel evaluation
        semaphore = asyncio.Semaphore(self.ga_config.parallel_evaluations)

        async def evaluate_single(chromosome: Chromosome) -> None:
            async with semaphore:
                results = await evaluation_func(chromosome)
                chromosome.fitness = self.evaluator.evaluate(chromosome, results)

        await asyncio.gather(*[evaluate_single(c) for c in population.chromosomes])

        # Update population statistics
        fitnesses = [c.fitness for c in population.chromosomes]
        population.best_fitness = max(fitnesses)
        population.avg_fitness = statistics.mean(fitnesses)
        population.diversity_score = self._calculate_diversity(population)

    def evolve_generation(self) -> Population:
        """Evolve one generation."""
        if not self._population:
            raise RuntimeError("Population not initialized")

        # Sort by fitness (descending)
        sorted_chromosomes = sorted(
            self._population.chromosomes,
            key=lambda c: c.fitness,
            reverse=True,
        )

        # Elitism: keep best chromosomes
        new_chromosomes = [
            c.copy() for c in sorted_chromosomes[: self.ga_config.elite_count]
        ]

        # Generate offspring
        while len(new_chromosomes) < self.ga_config.population_size:
            parent1 = self._tournament_select()
            parent2 = self._tournament_select()

            child1, child2 = self._crossover(parent1, parent2)

            self._mutate(child1)
            self._mutate(child2)

            new_chromosomes.extend([child1, child2])

        # Trim to population size
        new_chromosomes = new_chromosomes[: self.ga_config.population_size]

        self._population = Population(
            chromosomes=new_chromosomes,
            generation=self._population.generation + 1,
        )

        return self._population

    async def run(
        self,
        evaluation_func: Callable[[Chromosome], list[GenerationResult]] | None = None,
        progress_callback: Callable[[int, Population], None] | None = None,
    ) -> tuple[Chromosome, Population]:
        """Run complete genetic algorithm optimization.

        Args:
            evaluation_func: Function to evaluate chromosome fitness.
            progress_callback: Callback(generation, population) for progress updates.

        Returns:
            (best_chromosome, final_population)
        """
        self.logger.info(
            f"Starting GA optimization: {self.ga_config.population_size} chromosomes, "
            f"{self.ga_config.generations} generations"
        )

        # Initialize
        population = self.initialize_population()

        # Evaluate initial population
        await self.evaluate_population(population, evaluation_func)

        best_chromosome = max(population.chromosomes, key=lambda c: c.fitness)
        self._best_chromosome = best_chromosome

        self.logger.info(
            f"Generation 0: best={population.best_fitness:.4f}, "
            f"avg={population.avg_fitness:.4f}, "
            f"diversity={population.diversity_score:.4f}"
        )

        # Evolution loop
        for generation in range(1, self.ga_config.generations + 1):
            # Evolve
            population = self.evolve_generation()

            # Evaluate
            await self.evaluate_population(population, evaluation_func)

            # Update best
            current_best = max(population.chromosomes, key=lambda c: c.fitness)
            if current_best.fitness > best_chromosome.fitness:
                best_chromosome = current_best.copy()
                self._best_chromosome = best_chromosome

            # Progress callback
            if progress_callback:
                progress_callback(generation, population)

            self.logger.info(
                f"Generation {generation}: best={population.best_fitness:.4f}, "
                f"avg={population.avg_fitness:.4f}, "
                f"diversity={population.diversity_score:.4f}"
            )

            # Check convergence
            if self._check_convergence(population):
                self.logger.info(
                    f"Converged at generation {generation} "
                    f"(stagnation: {self._stagnation_count})"
                )
                break

        self.logger.info(f"GA complete. Best fitness: {best_chromosome.fitness:.4f}")

        return best_chromosome, population

    def save_state(self, path: str = "ga_state.json") -> None:
        """Save current GA state."""
        if not self._population:
            return

        state = {
            "ga_config": {
                "population_size": self.ga_config.population_size,
                "generations": self.ga_config.generations,
                "mutation_rate": self.ga_config.mutation_rate,
                "crossover_rate": self.ga_config.crossover_rate,
            },
            "population": self._population.to_dict(),
            "best_chromosome": {
                "genes": self._best_chromosome.genes if self._best_chromosome else {},
                "lora_weights": (
                    self._best_chromosome.lora_weights if self._best_chromosome else {}
                ),
                "cfg_scale": (
                    self._best_chromosome.cfg_scale if self._best_chromosome else 0
                ),
                "steps": self._best_chromosome.steps if self._best_chromosome else 0,
                "sampler": (
                    self._best_chromosome.sampler if self._best_chromosome else ""
                ),
                "fitness": (
                    self._best_chromosome.fitness if self._best_chromosome else 0
                ),
            },
            "stagnation_count": self._stagnation_count,
        }

        Path(path).write_text(json.dumps(state, indent=2), encoding="utf-8")
        self.logger.info(f"GA state saved to {path}")

    def load_state(self, path: str = "ga_state.json") -> bool:
        """Load GA state from file."""
        p = Path(path)
        if not p.exists():
            return False

        try:
            state = json.loads(p.read_text(encoding="utf-8"))

            # Reconstruct population
            chromosomes = []
            for c_data in state["population"]["chromosomes"]:
                chromosome = Chromosome(
                    genes=c_data["genes"],
                    lora_weights=c_data["lora_weights"],
                    cfg_scale=c_data["cfg_scale"],
                    steps=c_data["steps"],
                    sampler=c_data["sampler"],
                    seed=random.randint(1, 2**32 - 1),
                    fitness=c_data.get("fitness", 0.0),
                )
                chromosomes.append(chromosome)

            self._population = Population(
                chromosomes=chromosomes,
                generation=state["population"]["generation"],
                best_fitness=state["population"]["best_fitness"],
                avg_fitness=state["population"]["avg_fitness"],
                diversity_score=state["population"]["diversity_score"],
            )

            self._stagnation_count = state.get("stagnation_count", 0)

            self.logger.info(f"GA state loaded from {path}")
            return True

        except Exception as e:
            self.logger.error(f"Failed to load GA state: {e}")
            return False

    def get_best_prompt(self) -> GenerationConfig | None:
        """Get best prompt as GenerationConfig."""
        if not self._best_chromosome:
            return None
        return self._best_chromosome.to_generation_config(self.engine_config)
