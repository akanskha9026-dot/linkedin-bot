"""Minimal wrapper around Groq's OpenAI-compatible chat completions endpoint."""

import json
import logging

import requests

import config

log = logging.getLogger("groq_client")


class GroqError(RuntimeError):
    pass


def chat(messages: list, temperature: float = 0.4, response_format_json: bool = False, max_tokens: int = 1024, reasoning_effort: str = None) -> str:
    """Send a chat completion request to Groq and return the text content."""
    if not config.GROQ_API_KEY:
        raise GroqError("GROQ_API_KEY is not set. Add it as a GitHub Secret / env var.")

    headers = {
        "Authorization": f"Bearer {config.GROQ_API_KEY}",
        "Content-Type": "application/json",
    }
    body = {
        "model": config.GROQ_MODEL,
        "messages": messages,
        "temperature": temperature,
        "max_tokens": max_tokens,
    }
    if response_format_json:
        body["response_format"] = {"type": "json_object"}
    if reasoning_effort:
        body["reasoning_effort"] = reasoning_effort

    resp = requests.post(config.GROQ_API_URL, headers=headers, json=body, timeout=60)

    if resp.status_code != 200:
        raise GroqError(f"Groq API error {resp.status_code}: {resp.text[:500]}")

    data = resp.json()
    try:
        return data["choices"][0]["message"]["content"]
    except (KeyError, IndexError) as exc:
        raise GroqError(f"Unexpected Groq response shape: {data}") from exc


def chat_json(messages: list, temperature: float = 0.3, max_tokens: int = 1024, reasoning_effort: str = None) -> dict:
    """Like chat(), but parses the response as JSON, stripping code fences if present.
    Always returns a dict — normalizes the case where the model wraps the
    object in a list, and raises GroqError (so callers' existing retry loops
    catch it) for any shape that still isn't a usable dict."""
    raw = chat(messages, temperature=temperature, response_format_json=True, max_tokens=max_tokens, reasoning_effort=reasoning_effort)
    cleaned = raw.strip()
    if cleaned.startswith("```"):
        cleaned = cleaned.strip("`")
        if cleaned.lower().startswith("json"):
            cleaned = cleaned[4:]
    cleaned = cleaned.strip()
    try:
        data = json.loads(cleaned)
    except json.JSONDecodeError as exc:
        raise GroqError(f"Could not parse JSON from Groq response: {raw[:500]}") from exc

    if isinstance(data, list):
        data = next((item for item in data if isinstance(item, dict)), None)

    if not isinstance(data, dict):
        raise GroqError(f"Groq response was not a JSON object (got {type(data).__name__}): {raw[:500]}")

    return data
