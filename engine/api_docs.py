"""ComfyUI Async Generation Engine v4.0 - API Documentation Generator
Auto-generates OpenAPI/Swagger docs and Markdown API reference.
"""

import json
import logging
from pathlib import Path
from typing import Any, Dict, List, Optional

logger = logging.getLogger(__name__)


class APIDocGenerator:
    """Generates API documentation from FastAPI app introspection.

    Features:
    - OpenAPI JSON export
    - Markdown API reference
    - Postman collection generation
    - Example request/response generation
    """

    def __init__(self, output_dir: str = "docs/api"):
        self.output_dir = Path(output_dir).resolve()
        self.output_dir.mkdir(parents=True, exist_ok=True)

    def generate_openapi_json(self, app: Any) -> str:
        """Generate OpenAPI JSON from FastAPI app."""
        openapi_schema = app.openapi()
        output_path = self.output_dir / "openapi.json"
        output_path.write_text(
            json.dumps(openapi_schema, indent=2, ensure_ascii=False),
            encoding="utf-8",
        )
        logger.info(f"OpenAPI schema saved: {output_path}")
        return str(output_path)

    def generate_markdown_docs(self, app: Any) -> str:
        """Generate Markdown API reference from FastAPI app."""
        openapi_schema = app.openapi()

        md_lines = [
            "# ComfyUI Engine API Reference",
            "",
            f"Version: {openapi_schema.get('info', {}).get('version', '1.0.0')}",
            "",
            "## Table of Contents",
            "",
        ]

        # Group paths by tags
        paths = openapi_schema.get("paths", {})
        tags = {}

        for path, methods in paths.items():
            for method, spec in methods.items():
                if method == "parameters":
                    continue

                tag = spec.get("tags", ["General"])[0]
                if tag not in tags:
                    tags[tag] = []
                tags[tag].append((method.upper(), path, spec))

        # Generate TOC
        for tag in sorted(tags.keys()):
            md_lines.append(f"- [{tag}](#{tag.lower().replace(' ', '-')})")
        md_lines.append("")

        # Generate endpoints
        for tag in sorted(tags.keys()):
            md_lines.append(f"## {tag}")
            md_lines.append("")

            for method, path, spec in tags[tag]:
                md_lines.append(f"### `{method}` {path}")
                md_lines.append("")

                if "summary" in spec:
                    md_lines.append(f"**Summary:** {spec['summary']}")
                    md_lines.append("")

                if "description" in spec:
                    md_lines.append(spec["description"])
                    md_lines.append("")

                # Parameters
                if "parameters" in spec:
                    md_lines.append("#### Parameters")
                    md_lines.append("")
                    md_lines.append("| Name | Type | Required | Description |")
                    md_lines.append("|------|------|----------|-------------|")

                    for param in spec["parameters"]:
                        name = param.get("name", "")
                        param_type = param.get("schema", {}).get("type", "any")
                        required = "Yes" if param.get("required", False) else "No"
                        description = param.get("description", "")
                        md_lines.append(
                            f"| {name} | {param_type} | {required} | {description} |"
                        )

                    md_lines.append("")

                # Request body
                if "requestBody" in spec:
                    md_lines.append("#### Request Body")
                    md_lines.append("")
                    content = spec["requestBody"].get("content", {})
                    for content_type, content_spec in content.items():
                        md_lines.append(f"Content-Type: `{content_type}`")
                        md_lines.append("")

                        schema = content_spec.get("schema", {})
                        if "example" in content_spec:
                            md_lines.append("```json")
                            md_lines.append(
                                json.dumps(content_spec["example"], indent=2)
                            )
                            md_lines.append("```")
                            md_lines.append("")

                # Responses
                if "responses" in spec:
                    md_lines.append("#### Responses")
                    md_lines.append("")

                    for status_code, response in spec["responses"].items():
                        description = response.get("description", "")
                        md_lines.append(f"- **{status_code}**: {description}")

                    md_lines.append("")

        output_path = self.output_dir / "api_reference.md"
        output_path.write_text("\n".join(md_lines), encoding="utf-8")
        logger.info(f"Markdown docs saved: {output_path}")
        return str(output_path)

    def generate_postman_collection(
        self, app: Any, base_url: str = "http://localhost:8000"
    ) -> str:
        """Generate Postman collection from FastAPI app."""
        openapi_schema = app.openapi()

        collection = {
            "info": {
                "name": "ComfyUI Engine API",
                "description": openapi_schema.get("info", {}).get("description", ""),
                "version": openapi_schema.get("info", {}).get("version", "1.0.0"),
                "schema": "https://schema.getpostman.com/json/collection/v2.1.0/collection.json",
            },
            "item": [],
        }

        paths = openapi_schema.get("paths", {})

        for path, methods in paths.items():
            for method, spec in methods.items():
                if method == "parameters":
                    continue

                item = {
                    "name": spec.get("summary", f"{method.upper()} {path}"),
                    "request": {
                        "method": method.upper(),
                        "header": [
                            {
                                "key": "Authorization",
                                "value": "Bearer {{api_key}}",
                                "type": "text",
                            },
                            {
                                "key": "Content-Type",
                                "value": "application/json",
                                "type": "text",
                            },
                        ],
                        "url": {
                            "raw": f"{{{{base_url}}}}{path}",
                            "host": ["{{base_url}}"],
                            "path": path.strip("/").split("/"),
                        },
                        "description": spec.get("description", ""),
                    },
                }

                # Add request body if present
                if "requestBody" in spec:
                    content = spec["requestBody"].get("content", {})
                    for _content_type, content_spec in content.items():
                        if "example" in content_spec:
                            item["request"]["body"] = {
                                "mode": "raw",
                                "raw": json.dumps(content_spec["example"], indent=2),
                                "options": {
                                    "raw": {
                                        "language": "json",
                                    }
                                },
                            }

                collection["item"].append(item)

        output_path = self.output_dir / "postman_collection.json"
        output_path.write_text(
            json.dumps(collection, indent=2),
            encoding="utf-8",
        )
        logger.info(f"Postman collection saved: {output_path}")
        return str(output_path)

    def generate_all(
        self, app: Any, base_url: str = "http://localhost:8000"
    ) -> dict[str, str]:
        """Generate all documentation formats."""
        return {
            "openapi": self.generate_openapi_json(app),
            "markdown": self.generate_markdown_docs(app),
            "postman": self.generate_postman_collection(app, base_url),
        }
