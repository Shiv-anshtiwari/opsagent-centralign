"""Thin Gemini wrapper: tool declarations, retries with backoff, JSON mode."""
import json
import os
import time

from google import genai
from google.genai import types

MODEL = os.getenv("GEMINI_MODEL", "gemini-2.5-flash")


class LLM:
    def __init__(self, model: str = MODEL):
        self.client = genai.Client(api_key=os.environ["GEMINI_API_KEY"])
        self.model = model

    def _call(self, contents, config):
        delay = 2
        for attempt in range(5):
            try:
                return self.client.models.generate_content(model=self.model, contents=contents, config=config)
            except Exception as e:  # rate limits / transient 5xx
                if attempt == 4:
                    raise
                print(f"  [llm] transient error ({type(e).__name__}: {str(e)[:120]}), retrying in {delay}s")
                time.sleep(delay)
                delay *= 2

    def json(self, system: str, prompt: str) -> dict:
        cfg = types.GenerateContentConfig(system_instruction=system, response_mime_type="application/json")
        resp = self._call(prompt, cfg)
        return json.loads(resp.text)

    def step(self, system: str, contents: list, tool_decls: list):
        cfg = types.GenerateContentConfig(
            system_instruction=system,
            tools=[types.Tool(function_declarations=tool_decls)],
        )
        return self._call(contents, cfg)


def decl(name: str, description: str, props: dict | None = None, required: list | None = None):
    """Build a function declaration from a compact JSON-schema-ish dict (types in UPPERCASE)."""
    params = {"type": "OBJECT", "properties": props or {}}
    if required:
        params["required"] = required
    return types.FunctionDeclaration(name=name, description=description, parameters=params if props else None)


def S(desc, **kw):
    return {"type": "STRING", "description": desc, **kw}


def N(desc):
    return {"type": "NUMBER", "description": desc}


def I(desc):
    return {"type": "INTEGER", "description": desc}
