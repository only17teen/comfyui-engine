"""Workflow templates with variable substitution.

Addresses Issue #35: Workflow templates with variable substitution.
"""
import json
import re
from typing import Dict, Any

class TemplateEngine:
    def __init__(self):
        self.templates = {}

    def load_template(self, name: str, template_json: str):
        self.templates[name] = template_json

    def render(self, template_name: str, variables: Dict[str, Any]) -> Dict[str, Any]:
        """Render a template by replacing {{ var_name }} with actual values."""
        if template_name not in self.templates:
            raise ValueError(f"Template {template_name} not found")
            
        template_str = self.templates[template_name]
        
        # Simple string replacement for variables
        for key, value in variables.items():
            # For JSON safety, if value is a string, keep it safe
            # If value is numeric, regex will replace the bare token
            pattern = re.compile(r'{{\s*' + key + r'\s*}}')
            # If it's being placed inside a string (already quotes around it)
            if isinstance(value, str):
                template_str = pattern.sub(value, template_str)
            else:
                template_str = pattern.sub(json.dumps(value), template_str)
                
        return json.loads(template_str)

template_engine = TemplateEngine()
