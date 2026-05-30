"""
Lightweight prompt template renderer.

Adapted from the Anthropic Skilljar PromptEvaluator notebook
(002_prompting_completed.ipynb). A tiny `{placeholder}` substitution helper that
is safer than str.format() for prompt strings:

  - Only substitutes placeholders that are actually provided in `variables`.
    Unknown `{...}` are left untouched (str.format would raise KeyError).
  - Supports `{{` / `}}` as literal braces (e.g. for embedded JSON examples).

This is useful in eval/dataset-generation prompts that mix template variables
with literal JSON braces — exactly the case in generate_dataset.py.
"""
import re

_PLACEHOLDER_RE = re.compile(r"{([^{}]+)}")


def render(template_string: str, variables: dict) -> str:
    """Substitute {key} placeholders from `variables`; leave unknowns intact.

    >>> render("Hi {name}, score {score}", {"name": "Ada"})
    'Hi Ada, score {score}'
    >>> render("JSON: {{\\"k\\": {v}}}", {"v": 1})
    'JSON: {"k": 1}'
    """
    result = template_string
    for placeholder in _PLACEHOLDER_RE.findall(template_string):
        if placeholder in variables:
            result = result.replace("{" + placeholder + "}", str(variables[placeholder]))
    # Collapse escaped braces last, so a literal {{x}} never gets treated as a
    # placeholder above.
    return result.replace("{{", "{").replace("}}", "}")
