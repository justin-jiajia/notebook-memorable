from __future__ import annotations

import base64
import json
import os
from typing import Any

import requests


class ExtractionError(RuntimeError):
    pass


def _parse_entries(value: Any) -> list[dict[str, str]]:
    if isinstance(value, dict):
        value = value.get("entries", value.get("items"))
    if not isinstance(value, list):
        raise ExtractionError("模型返回的不是 JSON 数组")
    entries = []
    for item in value:
        if not isinstance(item, dict) or not isinstance(item.get("zh"), str) or not isinstance(item.get("en"), str):
            raise ExtractionError("每个条目必须包含字符串 zh 和 en")
        entries.append({"zh": item["zh"].strip(), "en": item["en"].strip()})
    return entries


def extract_entries(image: bytes, content_type: str) -> list[dict[str, str]]:
    """Extract bilingual rows through an OpenAI-compatible vision endpoint.

    The request is retried once when the model response cannot be parsed.
    """
    api_key = os.getenv("OPENAI_API_KEY")
    if not api_key:
        raise ExtractionError("未配置 OPENAI_API_KEY，无法进行图片识别")
    endpoint = os.getenv("OPENAI_BASE_URL", "https://api.openai.com/v1").rstrip("/") + "/chat/completions"
    model = os.getenv("OPENAI_VISION_MODEL", "gpt-4o-mini")
    data_url = f"data:{content_type or 'image/jpeg'};base64,{base64.b64encode(image).decode()}"
    payload = {
        "model": model,
        "temperature": 0,
        "response_format": {"type": "json_object"},
        "messages": [{
            "role": "user",
            "content": [
                {"type": "text", "text": os.getenv("OPENAI_PROMPT", "读取图片中每一行的中英词条，做成flashcard，只返回 JSON：{\"entries\":[{\"zh\":\"中文\",\"en\":\"English\"}]}。不要解释。")},
                {"type": "image_url", "image_url": {"url": data_url}},
            ],
        }],
        "response_format": {
            "type": "json_object",
        }
    }
    last_error: Exception | None = None
    for _ in range(2):
        try:
            response = requests.post(endpoint, json=payload, headers={"Authorization": f"Bearer {api_key}"}, timeout=90)
            response.raise_for_status()
            content = response.json()["choices"][0]["message"]["content"]
            return _parse_entries(json.loads(content))
        except (requests.RequestException, KeyError, IndexError, TypeError, json.JSONDecodeError, ExtractionError) as exc:
            last_error = exc
    raise ExtractionError(f"图片识别失败：{last_error}")
