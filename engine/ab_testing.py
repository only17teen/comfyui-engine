"""A/B Testing Framework for Workflows.

Addresses Issue #52: A/B testing framework — split traffic between workflow variants.
"""
import random
import logging
from typing import Dict, Any, Tuple

logger = logging.getLogger(__name__)

class ABTestManager:
    """Manages routing of traffic between different workflow variants."""
    
    def __init__(self):
        self.experiments = {}
        
    def register_experiment(self, name: str, variants: Dict[str, Dict[str, Any]], weights: Dict[str, float] = None):
        """Register a new A/B test.
        variants: map of variant_name -> workflow_json
        weights: map of variant_name -> float (0.0 to 1.0). Default is equal distribution.
        """
        if not weights:
            # equal weights
            w = 1.0 / len(variants)
            weights = {k: w for k in variants}
            
        # normalize weights just in case
        total = sum(weights.values())
        normalized_weights = {k: v/total for k, v in weights.items()}
        
        self.experiments[name] = {
            "variants": variants,
            "weights": normalized_weights
        }
        logger.info(f"Registered A/B experiment '{name}' with variants: {list(variants.keys())}")
        
    def get_workflow(self, experiment_name: str, fallback_workflow: Dict[str, Any] = None) -> Tuple[str, Dict[str, Any]]:
        """Get a workflow based on the experiment weights.
        Returns: (variant_name, workflow_json)
        """
        if experiment_name not in self.experiments:
            logger.warning(f"Experiment '{experiment_name}' not found.")
            return "default", fallback_workflow or {}
            
        exp = self.experiments[experiment_name]
        variants = list(exp["weights"].keys())
        weights = list(exp["weights"].values())
        
        # Weighted random choice
        chosen_variant = random.choices(variants, weights=weights, k=1)[0]
        return chosen_variant, exp["variants"][chosen_variant]

# Global singleton
ab_tester = ABTestManager()
