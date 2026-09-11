"""Minimal wrapper around Groq's OpenAI-compatible chat completions endpoint."""

import json
import logging

import requests

import config

log = logging.getLogger("groq_client")


class GroqError(RuntimeError):
    pass


def chat(messages: list, temperature: float = 0.4, response_format_json: bool = False) -> str:
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
    }
    if response_format_json:
        body["response_format"] = {"type": "json_object"}

    resp = requests.post(config.GROQ_API_URL, headers=headers, json=body, timeout=60)

    if resp.status_code != 200:
        raise GroqError(f"Groq API error {resp.status_code}: {resp.text[:500]}")

    data = resp.json()
    try:
        return data["choices"][0]["message"]["content"]
    except (KeyError, IndexError) as exc:
        raise GroqError(f"Unexpected Groq response shape: {data}") from exc


def chat_json(messages: list, temperature: float = 0.3) -> dict:
    """Like chat(), but parses the response as JSON, stripping code fences if present."""
    raw = chat(messages, temperature=temperature, response_format_json=True)
    cleaned = raw.strip()
    if cleaned.startswith("```"):
        cleaned = cleaned.strip("`")
        if cleaned.lower().startswith("json"):
            cleaned = cleaned[4:]
    cleaned = cleaned.strip()
    try:
        return json.loads(cleaned)
    except json.JSONDecodeError as exc:
        raise GroqError(f"Could not parse JSON from Groq response: {raw[:500]}") from exc
