import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from utils.codex_token_usage import extract_codex_token_budget


def test_extract_codex_token_budget_prefers_last_turn_usage_over_cumulative_total():
    payload = {
        "type": "token_count",
        "info": {
            "total_token_usage": {
                "input_tokens": 147607383,
                "cached_input_tokens": 137063424,
                "output_tokens": 806002,
                "reasoning_output_tokens": 386520,
                "total_tokens": 148413385,
            },
            "last_token_usage": {
                "input_tokens": 175371,
                "cached_input_tokens": 171136,
                "output_tokens": 834,
                "reasoning_output_tokens": 541,
                "total_tokens": 176205,
            },
            "model_context_window": 258400,
        },
    }

    result = extract_codex_token_budget(payload)

    assert result == {
        "used": 176205,
        "total": 258400,
        "inputTokens": 175371,
        "outputTokens": 834,
        "cachedInputTokens": 171136,
        "reasoningOutputTokens": 541,
        "breakdown": {
            "input": 175371,
            "cacheCreation": 0,
            "cacheRead": 171136,
            "output": 834,
            "reasoning": 541,
        },
    }


def test_extract_codex_token_budget_handles_flat_usage_shape():
    payload = {
        "usage": {
            "input_tokens": 9200,
            "output_tokens": 480,
            "cached_input_tokens": 3100,
            "reasoning_output_tokens": 200,
            "total_tokens": 12980,
            "context_window": 258400,
        },
    }

    result = extract_codex_token_budget(payload)

    assert result == {
        "used": 12980,
        "total": 258400,
        "inputTokens": 9200,
        "outputTokens": 480,
        "cachedInputTokens": 3100,
        "reasoningOutputTokens": 200,
        "breakdown": {
            "input": 9200,
            "cacheCreation": 0,
            "cacheRead": 3100,
            "output": 480,
            "reasoning": 200,
        },
    }
