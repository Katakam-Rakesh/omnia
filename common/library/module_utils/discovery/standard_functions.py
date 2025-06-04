# Copyright 2025 Dell Inc. or its subsidiaries. All Rights Reserved.
#
#  Licensed under the Apache License, Version 2.0 (the "License");
#  you may not use this file except in compliance with the License.
#  You may obtain a copy of the License at
#
#      http://www.apache.org/licenses/LICENSE-2.0
#
#  Unless required by applicable law or agreed to in writing, software
#  distributed under the License is distributed on an "AS IS" BASIS,
#  WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied.
#  See the License for the specific language governing permissions and
#  limitations under the License.

# pylint: disable=import-error,no-name-in-module,line-too-long

"""Standard functions for discovery and other modules."""

import os
import yaml
from jinja2 import Template


def create_directory(path: str, mode: int) -> None:
    """Create a directory if it does not exist, with given permissions."""
    if not os.path.exists(path):
        os.makedirs(path, mode)
    else:
        os.chmod(path, mode)

def render_template(src: str, dest: str, context: dict) -> None:
    """Render a Jinja2 template from src to dest using context."""
    try:
        with open(src, 'r', encoding='utf-8', encoding='utf-8') as f:
            template_content = f.read()
        template = Template(template_content)
        rendered = template.render(context)

        with open(dest, 'w', encoding='utf-8') as f:
            f.write(rendered)
    except Exception as e:
        raise RuntimeError(f"Template render error ({src} → {dest}): {e}") from e

def load_vars_file(path: str) -> dict:
    """Load YAML variables from a file and return as a dict."""
    if not path:
        return {}
    try:
        with open(path, 'r', encoding='utf-8', encoding='utf-8') as f:
            return yaml.safe_load(f) or {}
    except Exception as e:
        raise RuntimeError(f"Failed to read vars file '{path}': {str(e)}") from e

def render_template_multi_pass(src: str, dest: str, context: dict, passes: int = 5) -> None:
    """Render a Jinja2 template from src to dest using context."""
    try:
        # Load the template
        with open(src, 'r', encoding='utf-8') as f:
            template_content = f.read()

        # Perform multiple rendering passes
        for _ in range(passes):
            rendered = Template(template_content).render(context)

        # Save the final rendered result
        with open(dest, 'w', encoding='utf-8') as f:
            f.write(rendered)
    except Exception as e:
        raise RuntimeError(f"Template render error ({src} → {dest}): {e}") from e
