import json
import os

from dotenv import load_dotenv
from openai import OpenAI

load_dotenv()


class MyAI:
    """Small OpenAI Responses API wrapper for structured metabolite summaries."""

    RESPONSE_SCHEMA = {
        "type": "object",
        "properties": {
            "status": {
                "type": "string",
                "enum": ["complete", "no_relevant_results"],
            },
            "summary": {"type": "string"},
        },
        "required": ["status", "summary"],
        "additionalProperties": False,
    }

    def __init__(self, model_name: str, system_prompt: str):
        self.api_key = os.environ.get("OPENAI_API_KEY")
        self.model_name = model_name
        self.system_prompt = system_prompt
        self.client = None

    def _get_client(self) -> OpenAI:
        api_key = os.environ.get("OPENAI_API_KEY") or self.api_key
        if not api_key or not api_key.strip():
            raise RuntimeError(
                "OpenAI API key is missing. Set OPENAI_API_KEY in your environment or .env file."
            )

        if self.client is None:
            self.client = OpenAI(api_key=api_key, max_retries=4, timeout=60.0)
        return self.client

    def generate_response(self, input_text: str) -> dict:
        response = self._get_client().responses.create(
            model=self.model_name,
            instructions=self.system_prompt,
            input=input_text,
            text={
                "format": {
                    "type": "json_schema",
                    "name": "metabolite_analysis",
                    "strict": True,
                    "schema": self.RESPONSE_SCHEMA,
                }
            },
        )

        response_status = getattr(response, "status", None)
        if response_status and response_status != "completed":
            details = getattr(response, "incomplete_details", None)
            reason = getattr(details, "reason", None) if details else None
            raise ValueError(
                f"OpenAI response did not complete (status={response_status}, reason={reason})."
            )

        if self._contains_refusal(response):
            raise ValueError("OpenAI refused the metabolite analysis request.")

        output_text = getattr(response, "output_text", None)
        if not isinstance(output_text, str) or not output_text.strip():
            raise ValueError("OpenAI returned an empty response.")

        try:
            result = json.loads(output_text)
        except json.JSONDecodeError as exc:
            raise ValueError("OpenAI returned invalid JSON for the metabolite analysis.") from exc

        if not isinstance(result, dict):
            raise ValueError("OpenAI response must be a JSON object.")
        status = result.get("status")
        summary = result.get("summary")
        if status not in {"complete", "no_relevant_results"}:
            raise ValueError(f"OpenAI returned an unsupported status: {status!r}.")
        if not isinstance(summary, str) or not summary.strip():
            raise ValueError("OpenAI response did not include a nonempty summary.")

        usage = getattr(response, "usage", None)
        input_tokens = self._token_count(usage, "input_tokens")
        output_tokens = self._token_count(usage, "output_tokens")
        total_tokens = self._token_count(usage, "total_tokens")
        if total_tokens is None and input_tokens is not None and output_tokens is not None:
            total_tokens = input_tokens + output_tokens

        return {
            "status": status,
            "summary": summary.strip(),
            "usage": {
                "input_tokens": input_tokens,
                "output_tokens": output_tokens,
                "total_tokens": total_tokens,
            },
            "response_id": getattr(response, "id", None),
        }

    @staticmethod
    def _token_count(usage, field: str) -> int | None:
        value = getattr(usage, field, None) if usage is not None else None
        return value if isinstance(value, int) and value >= 0 else None

    @staticmethod
    def _contains_refusal(response) -> bool:
        for item in getattr(response, "output", []) or []:
            for content in getattr(item, "content", []) or []:
                if getattr(content, "type", None) == "refusal":
                    return True
        return False
