"""Moltbook API client — async HTTP wrapper for www.moltbook.com/api/v1."""

import json
import logging
import os
import re
from pathlib import Path
from typing import Any, Optional

import httpx

from .sanitize import sanitize_response

logger = logging.getLogger(__name__)

BASE_URL = "https://www.moltbook.com/api/v1"
CREDENTIALS_PATH = Path.home() / ".config" / "moltbook" / "credentials.json"


def _load_api_key() -> str:
    """Load API key from env var or credentials file."""
    key = os.environ.get("MOLTBOOK_API_KEY")
    if key:
        return key
    if CREDENTIALS_PATH.exists():
        data = json.loads(CREDENTIALS_PATH.read_text())
        return data["api_key"]
    raise RuntimeError(
        f"No API key found. Set MOLTBOOK_API_KEY env var or create {CREDENTIALS_PATH}"
    )


_WORD_TO_NUM = {
    "zero": 0, "one": 1, "two": 2, "three": 3, "four": 4, "five": 5,
    "six": 6, "seven": 7, "eight": 8, "nine": 9, "ten": 10,
    "eleven": 11, "twelve": 12, "thirteen": 13, "fourteen": 14, "fifteen": 15,
    "sixteen": 16, "seventeen": 17, "eighteen": 18, "nineteen": 19,
    "twenty": 20, "thirty": 30, "forty": 40, "fifty": 50, "sixty": 60,
    "seventy": 70, "eighty": 80, "ninety": 90, "hundred": 100,
}

# Build a collapsed-key version for matching after dedup
# e.g. "three" -> "thre", "fifteen" -> "fiften", "eighteen" -> "eighten"
_COLLAPSED = {re.sub(r'(.)\1+', r'\1', k): v for k, v in _WORD_TO_NUM.items()}


def _collapse_repeats(text: str) -> str:
    """Collapse repeated chars case-insensitively: 'eEe' -> 'e', 'OoO' -> 'o'."""
    return re.sub(r'(?i)(.)\1+', r'\1', text)


def _normalize_challenge(text: str) -> str:
    """Strip ALL non-alphanumeric/space characters and collapse repeated letters.

    Handles obfuscation like 'ThIrTy{tW}o' -> 'thirty two'
    and 'eEeIghT eEn' -> 'eight en', 'NeWwToOnSs' -> 'newtons'.
    """
    # Strip everything that isn't a letter, digit, or space
    cleaned = re.sub(r'[^a-zA-Z0-9\s]', '', text)
    # Collapse repeated letters: 'eee' -> 'e', 'OoO' -> 'o'
    cleaned = _collapse_repeats(cleaned)
    return cleaned.lower()


def _match_word(word: str) -> int | None:
    """Match a word against the number dictionary, trying exact then collapsed."""
    if word in _WORD_TO_NUM:
        return _WORD_TO_NUM[word]
    if word in _COLLAPSED:
        return _COLLAPSED[word]
    return None


def _extract_word_numbers(text: str) -> list[float]:
    """Extract numbers from text, supporting both digits and spelled-out words.

    Handles: 'thirty two' -> 32, 'forty five' -> 45, 'eighteen' -> 18,
    'eight en' -> 18 (split by obfuscation), '32' -> 32.
    """
    # First try digit extraction
    digit_nums = re.findall(r'-?\d+\.?\d*', text)

    # Try word-based extraction with lookahead for split words
    words = text.split()
    word_nums = []
    i = 0
    while i < len(words):
        # Try joining current + next word(s) to catch split numbers like "eight en" = "eighteen"
        matched = False
        for join_len in (3, 2):  # try joining 3, then 2 words
            if i + join_len <= len(words):
                joined = ''.join(words[i:i + join_len])
                val = _match_word(joined)
                if val is not None:
                    word_nums.append(float(val))
                    i += join_len
                    matched = True
                    break
        if matched:
            continue

        val = _match_word(words[i])
        if val is not None:
            # Check for compound: "thirty two" = 30 + 2, "forty five" = 40 + 5
            if val >= 20 and val < 100 and i + 1 < len(words):
                next_val = _match_word(words[i + 1])
                if next_val is not None and next_val < 10:
                    val += next_val
                    i += 1
                else:
                    # Try joining next two words: "tw o" -> "two"
                    if i + 2 <= len(words):
                        joined_next = ''.join(words[i + 1:i + 3]) if i + 2 < len(words) else words[i + 1]
                        next_val = _match_word(joined_next)
                        if next_val is not None and next_val < 10:
                            val += next_val
                            i += (2 if i + 2 < len(words) else 1)
            # Check for "hundred" multiplier
            if i + 1 < len(words) and _match_word(words[i + 1]) == 100:
                val *= 100
                i += 1
                if i + 1 < len(words):
                    next_val = _match_word(words[i + 1])
                    if next_val is not None and next_val < 100:
                        val += next_val
                        i += 1
            word_nums.append(float(val))
        i += 1

    # Prefer word numbers if found (challenges use spelled-out numbers)
    # Fall back to digit numbers
    if word_nums:
        return word_nums
    if digit_nums:
        return [float(n) for n in digit_nums]
    return []


def _solve_challenge(challenge_text: str) -> str:
    """Solve a Moltbook verification math challenge.

    Extracts numbers and operations from the obfuscated word problem,
    evaluates, and returns answer with 2 decimal places.
    """
    # Normalize: strip all obfuscation, collapse repeated chars
    text_lower = _normalize_challenge(challenge_text)

    # Extract numbers (word-based first, digit fallback)
    nums = _extract_word_numbers(text_lower)
    if not nums:
        # Also try digit extraction from raw text as last resort
        raw_nums = re.findall(r'-?\d+\.?\d*', challenge_text)
        if not raw_nums:
            raise ValueError(
                f"Could not extract numbers from challenge: {challenge_text}"
            )
        nums = [float(n) for n in raw_nums]

    # Detect operation from normalized text
    # Check multiply/divide BEFORE add/total — "what's total force?" appears in
    # multiplication challenges, and "total" would wrongly trigger addition.
    if any(w in text_lower for w in ["multiply", "multipli", "product", "times"]):
        result = 1
        for n in nums:
            result *= n
    elif any(w in text_lower for w in ["divide", "quotient", "split", "ratio", "presure"]):
        result = nums[0]
        for n in nums[1:]:
            if n != 0:
                result /= n
    elif any(w in text_lower for w in ["subtract", "minus", "difference", "take away", "less"]):
        result = nums[0] - sum(nums[1:]) if len(nums) > 1 else nums[0]
    elif any(w in text_lower for w in ["sum", "add", "plus", "total", "combine", "together", "gains"]):
        result = sum(nums)
    elif any(w in text_lower for w in ["square root", "sqrt"]):
        result = nums[0] ** 0.5
    elif any(w in text_lower for w in ["power", "exponent", "raised"]):
        result = nums[0] ** nums[1] if len(nums) > 1 else nums[0]
    else:
        # Default: try to evaluate as expression, fall back to sum
        expr_match = re.search(r'[\d\.\s\+\-\*\/\(\)]+', challenge_text)
        if expr_match:
            try:
                # Safe: input is server-generated challenge text, and the regex
                # above restricts to digits, whitespace, and arithmetic operators.
                result = eval(expr_match.group().strip())  # noqa: S307
            except Exception:
                result = sum(nums)
        else:
            result = sum(nums)

    return f"{result:.2f}"


class MoltbookClient:
    """Async HTTP client for the Moltbook API."""

    def __init__(self) -> None:
        self._api_key = _load_api_key()
        self._client: Optional[httpx.AsyncClient] = None

    def _get_client(self) -> httpx.AsyncClient:
        if self._client is None or self._client.is_closed:
            self._client = httpx.AsyncClient(
                base_url=BASE_URL,
                headers={
                    "Authorization": f"Bearer {self._api_key}",
                    "Content-Type": "application/json",
                },
                timeout=30.0,
            )
        return self._client

    def _parse_rate_limits(self, response: httpx.Response) -> dict[str, Any]:
        """Extract rate limit info from response headers."""
        info = {}
        remaining = response.headers.get("X-RateLimit-Remaining")
        if remaining is not None:
            info["rate_limit_remaining"] = int(remaining)
        reset = response.headers.get("X-RateLimit-Reset")
        if reset is not None:
            info["rate_limit_reset"] = int(reset)
        return info

    async def request(
        self,
        method: str,
        endpoint: str,
        params: Optional[dict] = None,
        json_body: Optional[dict] = None,
        sanitize: bool = True,
    ) -> dict[str, Any]:
        """Make an API request and return parsed response."""
        client = self._get_client()
        try:
            response = await client.request(
                method,
                endpoint,
                params=params,
                json=json_body,
            )

            rate_info = self._parse_rate_limits(response)

            if response.status_code == 429:
                # Server-generated error — no user content to sanitize
                data = response.json()
                return {
                    "success": False,
                    "error": "Rate limit exceeded",
                    "retry_after_seconds": data.get("retry_after_seconds"),
                    **rate_info,
                }

            response.raise_for_status()
            data = response.json()
            if sanitize:
                data = sanitize_response(data)

            if rate_info:
                data["_rate_limit"] = rate_info

            return data

        except httpx.HTTPStatusError as e:
            try:
                error_body = e.response.json()
                error_msg = error_body.get("error", str(e))
                hint = error_body.get("hint", "")
            except Exception:
                error_msg = str(e)
                hint = ""
            return {
                "success": False,
                "error": f"HTTP {e.response.status_code}: {error_msg}",
                "hint": hint,
            }
        except httpx.TimeoutException:
            return {"success": False, "error": "Request timed out. Try again."}
        except Exception as e:
            return {"success": False, "error": f"Unexpected error: {e}"}

    async def request_with_verification(
        self,
        method: str,
        endpoint: str,
        json_body: dict,
    ) -> dict[str, Any]:
        """Make a request that may require verification (posts, comments).

        Automatically solves the math challenge and submits verification.
        """
        # Skip sanitization for internal verification flow — challenge text is server-generated
        result = await self.request(method, endpoint, json_body=json_body, sanitize=False)

        # Extract verification object from all known response shapes:
        # Shape 1: {"verification_required": true, "verification": {...}}
        # Shape 2: {"post": {"verification_status": "pending", "verification": {...}}}
        # Shape 3: {"comment": {"verification_status": "pending", "verification": {...}}}
        verification = result.get("verification")
        if not verification:
            for key in ("post", "comment", "data"):
                nested = result.get(key, {})
                if isinstance(nested, dict) and nested.get("verification"):
                    verification = nested["verification"]
                    break

        needs_verification = result.get("verification_required") or verification is not None

        if not needs_verification:
            return sanitize_response(result)
        if not verification:
            return sanitize_response(result)

        challenge_text = verification.get("challenge_text", "")
        verification_code = verification.get("verification_code", "")

        if not challenge_text or not verification_code:
            return {
                "success": False,
                "error": "Verification required but challenge data missing",
                "verification": verification,
            }

        try:
            answer = _solve_challenge(challenge_text)
            logger.info(f"Solving verification: {challenge_text[:80]}... -> {answer}")
        except Exception as e:
            return {
                "success": False,
                "error": f"Could not solve verification challenge: {e}",
                "challenge_text": challenge_text,
            }

        verify_result = await self.request(
            "POST",
            "/verify",
            json_body={
                "verification_code": verification_code,
                "answer": answer,
            },
            sanitize=False,
        )

        if not verify_result.get("success", True):
            return {
                "success": False,
                "error": f"Verification failed: {verify_result.get('error', 'unknown')}",
                "challenge_text": challenge_text,
                "answer_given": answer,
                "hint": verify_result.get("hint", "Check math and try again"),
            }

        return sanitize_response(verify_result)

    async def get(self, endpoint: str, params: Optional[dict] = None) -> dict:
        return await self.request("GET", endpoint, params=params)

    async def post(self, endpoint: str, json_body: Optional[dict] = None) -> dict:
        return await self.request("POST", endpoint, json_body=json_body)

    async def delete(self, endpoint: str) -> dict:
        return await self.request("DELETE", endpoint)

    async def patch(self, endpoint: str, json_body: Optional[dict] = None) -> dict:
        return await self.request("PATCH", endpoint, json_body=json_body)

    async def close(self) -> None:
        if self._client and not self._client.is_closed:
            await self._client.aclose()
