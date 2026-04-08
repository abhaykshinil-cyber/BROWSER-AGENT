"""
BrowserAgent — LLM Helper (Google Gemini)

Single entry-point for all LLM calls.  Wraps google-generativeai so
the rest of the codebase stays clean and provider-swappable.
"""

from __future__ import annotations

import asyncio
import logging
from typing import Any

import google.generativeai as genai

logger = logging.getLogger("browseragent.llm")

# Free-tier Gemini models
GEMINI_MAIN  = "gemini-2.0-flash"       # planning, teaching, learning
GEMINI_FAST  = "gemini-2.0-flash-lite"  # verification, quick checks


def call_gemini(
    api_key: str,
    model_name: str,
    system_prompt: str,
    user_content: Any,          # str  OR  list for multimodal
    max_tokens: int = 4096,
) -> str:
    """Synchronous Gemini call.  Returns the response text.

    Args:
        api_key:      Google Gemini API key (AIza…).
        model_name:   Model string, e.g. "gemini-2.0-flash".
        system_prompt: System-level instruction for the model.
        user_content: Either a plain string or a list of parts
                      (text + inline image dicts) for multimodal.
        max_tokens:   Maximum tokens the model may generate.

    Returns:
        The model's text response, stripped of leading/trailing whitespace.

    Raises:
        Exception: Re-raises any API or network error so callers can
                   decide whether to fall back or propagate.
    """
    genai.configure(api_key=api_key)
    model = genai.GenerativeModel(
        model_name=model_name,
        system_instruction=system_prompt,
    )
    response = model.generate_content(
        user_content,
        generation_config=genai.GenerationConfig(max_output_tokens=max_tokens),
    )
    return response.text.strip()


async def call_gemini_async(
    api_key: str,
    model_name: str,
    system_prompt: str,
    user_content: Any,
    max_tokens: int = 4096,
) -> str:
    """Async wrapper around call_gemini using asyncio.to_thread.

    Allows the FastAPI async event loop to remain unblocked while the
    synchronous Gemini SDK performs its HTTP call.
    """
    return await asyncio.to_thread(
        call_gemini,
        api_key,
        model_name,
        system_prompt,
        user_content,
        max_tokens,
    )
