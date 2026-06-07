"""ComfyUI Async Generation Engine v2.0 - Workflow Validator
JSON schema validation and automatic node ID mapping for ComfyUI workflows.
"""

import json
import logging
from dataclasses import dataclass, field
from enum import Enum, auto
from pathlib import Path
from typing import Any, Dict, List, Optional, Set, Tuple

logger = logging.getLogger(__name__)


class NodeType(Enum):
    """Known ComfyUI node types."""

    KSAMPLER = "KSampler"
    KSAMPLER_ADVANCED = "KSamplerAdvanced"
    EMPTY_LATENT = "EmptyLatentImage"
    CLIP_TEXT_ENCODE = "CLIPTextEncode"
    LORA_LOADER = "LoraLoader"
    LORA_LOADER_MODEL_ONLY = "LoraLoaderModelOnly"
    CHECKPOINT_LOADER = "CheckpointLoaderSimple"
    VAE_DECODE = "VAEDecode"
    SAVE_IMAGE = "SaveImage"
    PREVIEW_IMAGE = "PreviewImage"
    LOAD_IMAGE = "LoadImage"
    IMAGE_SCALE = "ImageScale"
    CONTROL_NET = "ControlNetLoader"
    CONTROL_NET_APPLY = "ControlNetApply"
    CONDITIONING = "ConditioningAverage"
    CONDITIONING_CONCAT = "ConditioningConcat"
    MODEL_MERGE = "ModelMergeSimple"
    T2I_ADAPTER = "T2IAdapterLoader"
    IP_ADAPTER = "IPAdapterApply"
    INSTANT_ID = "InstantIDFaceAnalysis"
    UNKNOWN = "Unknown"


@dataclass
class NodeInfo:
    """Information about a workflow node."""

    node_id: str
    class_type: str
    inputs: dict[str, Any] = field(default_factory=dict)
    outputs: list[str] = field(default_factory=list)
    connections: list[str] = field(default_factory=list)
    node_type: NodeType = NodeType.UNKNOWN


@dataclass
class ValidationResult:
    """Workflow validation result."""

    is_valid: bool
    errors: list[str] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)
    nodes: dict[str, NodeInfo] = field(default_factory=dict)
    required_nodes: dict[str, str | None] = field(default_factory=dict)
    suggested_mappings: dict[str, str] = field(default_factory=dict)


class WorkflowValidator:
    """Validates ComfyUI workflow JSON and provides automatic node mapping.

    Features:
    - Schema validation (required nodes, connections)
    - Node type detection and classification
    - Automatic node ID mapping for common workflows
    - Connection graph analysis
    - Missing node detection
    """

    # Required nodes for a complete generation workflow
    REQUIRED_NODE_TYPES = {
        NodeType.KSAMPLER,
        NodeType.EMPTY_LATENT,
        NodeType.CLIP_TEXT_ENCODE,
        NodeType.CHECKPOINT_LOADER,
        NodeType.VAE_DECODE,
    }

    # Node type aliases (class_type -> NodeType)
    NODE_TYPE_MAP = {
        "KSampler": NodeType.KSAMPLER,
        "KSamplerAdvanced": NodeType.KSAMPLER_ADVANCED,
        "EmptyLatentImage": NodeType.EMPTY_LATENT,
        "CLIPTextEncode": NodeType.CLIP_TEXT_ENCODE,
        "LoraLoader": NodeType.LORA_LOADER,
        "LoraLoaderModelOnly": NodeType.LORA_LOADER_MODEL_ONLY,
        "CheckpointLoaderSimple": NodeType.CHECKPOINT_LOADER,
        "VAEDecode": NodeType.VAE_DECODE,
        "SaveImage": NodeType.SAVE_IMAGE,
        "PreviewImage": NodeType.PREVIEW_IMAGE,
        "LoadImage": NodeType.LOAD_IMAGE,
        "ImageScale": NodeType.IMAGE_SCALE,
        "ControlNetLoader": NodeType.CONTROL_NET,
        "ControlNetApply": NodeType.CONTROL_NET_APPLY,
        "ConditioningAverage": NodeType.CONDITIONING,
        "ConditioningConcat": NodeType.CONDITIONING_CONCAT,
        "ModelMergeSimple": NodeType.MODEL_MERGE,
        "T2IAdapterLoader": NodeType.T2I_ADAPTER,
        "IPAdapterApply": NodeType.IP_ADAPTER,
        "InstantIDFaceAnalysis": NodeType.INSTANT_ID,
    }

    # Standard node ID mappings for common workflows
    STANDARD_MAPPINGS = {
        "positive_prompt": ["6", "clip_text_positive", "text_positive"],
        "negative_prompt": ["7", "clip_text_negative", "text_negative"],
        "ksampler": ["3", "sampler", "ksampler"],
        "checkpoint": ["4", "checkpoint_loader", "model_loader"],
        "empty_latent": ["5", "latent_empty", "empty_latent"],
        "vae_decode": ["8", "vae_decoder", "decode"],
        "save_image": ["9", "image_save", "output"],
        "lora_1": ["10", "lora_loader_1"],
        "lora_2": ["11", "lora_loader_2"],
        "lora_3": ["12", "lora_loader_3"],
    }

    def __init__(self):
        self.logger = logging.getLogger(__name__)

    def _detect_node_type(self, class_type: str) -> NodeType:
        """Detect node type from class_type string."""
        return self.NODE_TYPE_MAP.get(class_type, NodeType.UNKNOWN)

    def _extract_connections(self, node: dict) -> list[str]:
        """Extract connected node IDs from inputs."""
        connections = []
        inputs = node.get("inputs", {})

        for _key, value in inputs.items():
            if isinstance(value, list) and len(value) == 2:
                # ComfyUI connections are [node_id, output_index]
                connections.append(str(value[0]))

        return connections

    def validate(self, workflow: dict) -> ValidationResult:
        """Validate ComfyUI workflow JSON.

        Args:
            workflow: ComfyUI workflow dict (node_id -> node_data).

        Returns:
            ValidationResult with errors, warnings, and node mapping.
        """
        result = ValidationResult(is_valid=True)

        # 1. Basic structure check
        if not isinstance(workflow, dict):
            result.is_valid = False
            result.errors.append("Workflow must be a dictionary")
            return result

        if not workflow:
            result.is_valid = False
            result.errors.append("Workflow is empty")
            return result

        # 2. Parse all nodes
        node_types_found: set[NodeType] = set()

        for node_id, node_data in workflow.items():
            if not isinstance(node_data, dict):
                result.warnings.append(f"Node {node_id}: invalid data type")
                continue

            class_type = node_data.get("class_type", "")
            if not class_type:
                result.warnings.append(f"Node {node_id}: missing class_type")
                continue

            node_type = self._detect_node_type(class_type)
            node_types_found.add(node_type)

            node_info = NodeInfo(
                node_id=node_id,
                class_type=class_type,
                inputs=node_data.get("inputs", {}),
                outputs=node_data.get("outputs", []),
                connections=self._extract_connections(node_data),
                node_type=node_type,
            )
            result.nodes[node_id] = node_info

        # 3. Check required nodes
        missing_required = self.REQUIRED_NODE_TYPES - node_types_found
        if missing_required:
            for node_type in missing_required:
                result.errors.append(f"Missing required node type: {node_type.value}")
            result.is_valid = False

        # 4. Check for positive/negative prompt nodes
        clip_nodes = [n for n in result.nodes.values() if n.node_type == NodeType.CLIP_TEXT_ENCODE]
        if len(clip_nodes) < 2:
            result.warnings.append(f"Found {len(clip_nodes)} CLIPTextEncode nodes, expected 2 (positive + negative)")

        # 5. Check for save/preview output
        output_nodes = [
            n for n in result.nodes.values() if n.node_type in (NodeType.SAVE_IMAGE, NodeType.PREVIEW_IMAGE)
        ]
        if not output_nodes:
            result.warnings.append("No SaveImage or PreviewImage node found")

        # 6. Check for LoRA loaders (optional but common)
        lora_nodes = [n for n in result.nodes.values() if "Lora" in n.class_type]
        if not lora_nodes:
            result.warnings.append("No LoRA loader nodes found")

        # 7. Generate automatic node mappings
        result.suggested_mappings = self._generate_mappings(result.nodes)

        # 8. Validate connections
        self._validate_connections(result)

        return result

    def _generate_mappings(self, nodes: dict[str, NodeInfo]) -> dict[str, str]:
        """Generate automatic node ID mappings."""
        mappings = {}

        # Find nodes by type and suggest IDs
        for purpose, _candidate_ids in self.STANDARD_MAPPINGS.items():
            for node_id, node_info in nodes.items():
                if purpose == "positive_prompt" and node_info.node_type == NodeType.CLIP_TEXT_ENCODE:
                    # Check if connected to positive side of sampler
                    if self._is_positive_clip(node_info, nodes):
                        mappings[purpose] = node_id
                        break
                elif purpose == "negative_prompt" and node_info.node_type == NodeType.CLIP_TEXT_ENCODE:
                    if self._is_negative_clip(node_info, nodes):
                        mappings[purpose] = node_id
                        break
                elif purpose == "ksampler" and node_info.node_type in (
                    NodeType.KSAMPLER,
                    NodeType.KSAMPLER_ADVANCED,
                ):
                    mappings[purpose] = node_id
                    break
                elif purpose == "checkpoint" and node_info.node_type == NodeType.CHECKPOINT_LOADER:
                    mappings[purpose] = node_id
                    break
                elif purpose == "empty_latent" and node_info.node_type == NodeType.EMPTY_LATENT:
                    mappings[purpose] = node_id
                    break
                elif purpose == "vae_decode" and node_info.node_type == NodeType.VAE_DECODE:
                    mappings[purpose] = node_id
                    break
                elif purpose == "save_image" and node_info.node_type == NodeType.SAVE_IMAGE:
                    mappings[purpose] = node_id
                    break
                elif purpose.startswith("lora_") and "Lora" in node_info.class_type:
                    lora_idx = int(purpose.split("_")[1]) - 1
                    lora_nodes = [n for n in nodes.values() if "Lora" in n.class_type]
                    if lora_idx < len(lora_nodes):
                        mappings[purpose] = lora_nodes[lora_idx].node_id

        return mappings

    def _is_positive_clip(self, node: NodeInfo, all_nodes: dict[str, NodeInfo]) -> bool:
        """Check if CLIPTextEncode node is connected to positive input."""
        # In standard workflows, positive is often connected first or to specific inputs
        for conn_id in node.connections:
            if conn_id in all_nodes:
                target = all_nodes[conn_id]
                if target.node_type in (NodeType.KSAMPLER, NodeType.KSAMPLER_ADVANCED):
                    # Check if connected to positive input
                    inputs = target.inputs
                    if "positive" in inputs:
                        pos_input = inputs["positive"]
                        if isinstance(pos_input, list) and str(pos_input[0]) == node.node_id:
                            return True
        return False

    def _is_negative_clip(self, node: NodeInfo, all_nodes: dict[str, NodeInfo]) -> bool:
        """Check if CLIPTextEncode node is connected to negative input."""
        for conn_id in node.connections:
            if conn_id in all_nodes:
                target = all_nodes[conn_id]
                if target.node_type in (NodeType.KSAMPLER, NodeType.KSAMPLER_ADVANCED):
                    inputs = target.inputs
                    if "negative" in inputs:
                        neg_input = inputs["negative"]
                        if isinstance(neg_input, list) and str(neg_input[0]) == node.node_id:
                            return True
        return False

    def _validate_connections(self, result: ValidationResult) -> None:
        """Validate node connections form a complete graph."""
        # Check for orphaned nodes (no connections)
        for node_id, node_info in result.nodes.items():
            if not node_info.connections and node_info.node_type not in (
                NodeType.SAVE_IMAGE,
                NodeType.PREVIEW_IMAGE,
            ):
                # Some nodes like checkpoint loaders may have no inputs
                if node_info.node_type not in (
                    NodeType.CHECKPOINT_LOADER,
                    NodeType.CONTROL_NET,
                ):
                    result.warnings.append(f"Node {node_id} ({node_info.class_type}) has no connections")

        # Check for disconnected outputs
        ksampler_nodes = [
            n for n in result.nodes.values() if n.node_type in (NodeType.KSAMPLER, NodeType.KSAMPLER_ADVANCED)
        ]
        for ksampler in ksampler_nodes:
            # KSampler should output to VAE decode or image save
            has_output = False
            for _node_id, node_info in result.nodes.items():
                if ksampler.node_id in node_info.connections:
                    has_output = True
                    break
            if not has_output:
                result.warnings.append(f"KSampler {ksampler.node_id} has no output connections")

    def get_node_summary(self, workflow: dict) -> dict[str, any]:
        """Get human-readable summary of workflow nodes."""
        result = self.validate(workflow)

        type_counts = {}
        for node in result.nodes.values():
            type_counts[node.class_type] = type_counts.get(node.class_type, 0) + 1

        return {
            "valid": result.is_valid,
            "total_nodes": len(result.nodes),
            "node_types": type_counts,
            "errors": result.errors,
            "warnings": result.warnings,
            "suggested_mappings": result.suggested_mappings,
        }

    def apply_mappings(self, workflow: dict, mappings: dict[str, str]) -> dict:
        """Apply node mappings to workflow for consistent injection.

        Args:
            workflow: Original workflow dict.
            mappings: Dict of purpose -> node_id.

        Returns:
            Modified workflow with standardized node IDs.
        """
        # Create reverse mapping (old_id -> new_id)
        # This is a simplified version - real implementation would need
        # to handle all connections
        modified = {}
        id_mapping = {}

        for purpose, node_id in mappings.items():
            if node_id in workflow:
                # Generate standard ID
                standard_id = self.STANDARD_MAPPINGS.get(purpose, [purpose])[0]
                id_mapping[node_id] = standard_id

        # Apply mapping
        for old_id, node_data in workflow.items():
            new_id = id_mapping.get(old_id, old_id)
            modified[new_id] = node_data

            # Update connections in inputs
            inputs = node_data.get("inputs", {})
            for _key, value in inputs.items():
                if isinstance(value, list) and len(value) == 2:
                    old_conn_id = str(value[0])
                    if old_conn_id in id_mapping:
                        value[0] = id_mapping[old_conn_id]

        return modified

    def validate_file(self, path: str) -> ValidationResult:
        """Validate workflow from file path."""
        p = Path(path)
        if not p.exists():
            result = ValidationResult(is_valid=False)
            result.errors.append(f"File not found: {path}")
            return result

        try:
            with open(p, encoding="utf-8") as f:
                workflow = json.load(f)
            return self.validate(workflow)
        except json.JSONDecodeError as e:
            result = ValidationResult(is_valid=False)
            result.errors.append(f"Invalid JSON: {e}")
            return result
        except Exception as e:
            result = ValidationResult(is_valid=False)
            result.errors.append(f"Read error: {e}")
            return result
