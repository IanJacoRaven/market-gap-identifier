"""Local LLM analyst layer via Ollama — token-free, runs entirely on this machine.

This replaces the cloud Claude analyst with a local model (default Qwen2.5 14B
served by Ollama at http://localhost:11434). It reasons over the REAL price +
news signals the deterministic scan already collected (which work locally), and
produces a decision-first brief.

Stdlib only (urllib) to keep the project dependency-free. If Ollama is not
running or the model is missing, it fails gracefully and the scan still produces
its mechanical report.
"""

from __future__ import annotations

import json
import urllib.error
import urllib.request
from dataclasses import dataclass

SYSTEM_PROMPT = (
    "You are a sharp, skeptical supply-chain market analyst. You hunt for market "
    "gaps: sectors or products where demand is currently outrunning supply, "
    "creating openings a small operator could exploit (alternative sourcing, "
    "reselling/brokering, substitution). You are given today's mechanical scan, "
    "which already contains real commodity price moves and real recent news "
    "headlines. Reason ONLY from that evidence plus well-established background "
    "knowledge — do not invent specific facts, numbers, or sources that are not "
    "supported by the provided data. Prefer 'nothing compelling today' over "
    "manufacturing an opportunity. Be concise and decision-oriented."
)

USER_TEMPLATE = """Here is today's mechanical market-gap scan (real data collected locally):

<scan>
{report}
</scan>
{web_block}
Write a tight analyst brief in Markdown with this exact structure:

## Analyst brief — {date}

**Today's single best opportunity:** <one sentence — or "Nothing compelling today">

### Top gaps
For each of the 3 highest-signal sectors (use the ranked scores and the headlines/price moves as evidence):
- **<Sector>** — VERDICT: REAL GAP / WATCH / NOISE
  - Reasoning: 2-4 sentences. Is the price move a genuine shortage or just cost inflation/demand drop? Are the headlines real and current or noise?
  - Opportunity: the specific material/product/service that is short, who needs it, and how hard it would be for a SMALL operator to step in (say so plainly if it's real but inaccessible).
  - Key risks: 1-2 bullets.
  - Evidence: cite the specific headlines or price figures FROM THE SCAN ABOVE that support your verdict.

Do not pad. Do not fabricate sources or statistics beyond what the scan AND the web results below provide. When you cite a fact or figure, it must come from the scan or the web search results; prefer linking the web result URLs as your sources."""


@dataclass
class LocalLLMResult:
    ok: bool
    text: str = ""
    error: str = ""
    model: str = ""


def generate_brief(
    report_md: str,
    date_str: str,
    model: str = "qwen2.5:14b",
    host: str = "http://localhost:11434",
    temperature: float = 0.3,
    num_ctx: int = 8192,
    timeout: int = 600,
    web_context: str = "",
    focus_note: str = "",
) -> LocalLLMResult:
    """Call a local Ollama model to turn the mechanical scan into an analyst brief."""
    web_block = (
        f"\nRecent web search results (free DuckDuckGo / news search, current as of today):\n\n"
        f"<web_results>\n{web_context}\n</web_results>\n"
        if web_context.strip()
        else ""
    )
    if focus_note.strip():
        web_block += f"\n{focus_note}\n"
    payload = {
        "model": model,
        "messages": [
            {"role": "system", "content": SYSTEM_PROMPT},
            {
                "role": "user",
                "content": USER_TEMPLATE.format(
                    report=report_md, date=date_str, web_block=web_block
                ),
            },
        ],
        "stream": False,
        "options": {"temperature": temperature, "num_ctx": num_ctx},
    }
    data = json.dumps(payload).encode("utf-8")
    req = urllib.request.Request(
        f"{host.rstrip('/')}/api/chat",
        data=data,
        headers={"Content-Type": "application/json"},
        method="POST",
    )
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            body = json.loads(resp.read().decode("utf-8"))
    except urllib.error.HTTPError as e:
        detail = _read_err(e)
        if e.code == 404:
            return LocalLLMResult(
                ok=False, model=model,
                error=f"model '{model}' not found in Ollama (pull it: `ollama pull {model}`)",
            )
        return LocalLLMResult(ok=False, model=model, error=f"HTTP {e.code}: {detail}")
    except (urllib.error.URLError, TimeoutError, OSError) as e:
        return LocalLLMResult(
            ok=False, model=model,
            error=f"could not reach Ollama at {host} ({e}). Is it running? `ollama serve`",
        )
    except (ValueError, json.JSONDecodeError) as e:
        return LocalLLMResult(ok=False, model=model, error=f"bad response from Ollama: {e}")

    text = (body.get("message") or {}).get("content", "").strip()
    if not text:
        return LocalLLMResult(ok=False, model=model, error="empty completion from model")
    return LocalLLMResult(ok=True, text=text, model=model)


def _read_err(e: urllib.error.HTTPError) -> str:
    try:
        return e.read().decode("utf-8", errors="replace")[:300]
    except Exception:
        return str(e)


def check_available(
    model: str = "qwen2.5:14b", host: str = "http://localhost:11434", timeout: int = 10
) -> tuple[bool, str]:
    """Return (ready, message): is Ollama up and is the model present?"""
    req = urllib.request.Request(f"{host.rstrip('/')}/api/tags", method="GET")
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            body = json.loads(resp.read().decode("utf-8"))
    except Exception as e:  # noqa: BLE001 - any failure means "not ready"
        return False, f"Ollama not reachable at {host}: {e}"
    names = {m.get("name", "") for m in body.get("models", [])}
    # Ollama may store as 'qwen2.5:14b' or with a digest; match on prefix too.
    if model in names or any(n.split(":")[0] == model.split(":")[0] for n in names):
        return True, f"Ollama ready, model '{model}' available"
    return False, f"Ollama up but model '{model}' not pulled. Run: ollama pull {model}"
