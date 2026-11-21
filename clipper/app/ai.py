"""Talk to whichever AI provider you have a key for.

Set any of these in .env (one is enough):
  ANTHROPIC_API_KEY   Claude          console.anthropic.com      (paid, best picks)
  OPENAI_API_KEY      OpenAI          platform.openai.com        (paid)
  GEMINI_API_KEY      Google Gemini   aistudio.google.com        (free tier)
  GROQ_API_KEY        Groq            console.groq.com           (free tier, best for shorter videos)

AI_ORDER sets which to try first (default: anthropic,openai,gemini,groq). If one fails or hits a
limit, the next one is tried. Each provider's model can be overridden with <NAME>_MODEL.
If a default model name has been retired, the server asks the provider for its current models
and picks a suitable one automatically.
"""
import os
import re

import httpx


def _key(env: str) -> str:
    """Read a key, dropping invisible characters a terminal paste can add."""
    raw = os.environ.get(env, "")
    raw = re.sub(r"(\x1b)?\[20[01]~", "", raw)
    return re.sub(r"[^\x21-\x7e]", "", raw).strip("'\"")

# name: (base url, key env, model env, default model, words to look for when auto-picking a model)
OPENAI_STYLE = {
    "openai": ("https://api.openai.com/v1", "OPENAI_API_KEY", "OPENAI_MODEL", "gpt-5-mini", ["gpt-5-mini", "mini"]),
    "gemini": ("https://generativelanguage.googleapis.com/v1beta/openai", "GEMINI_API_KEY", "GEMINI_MODEL",
               "gemini-flash-latest", ["flash"]),
    "groq": ("https://api.groq.com/openai/v1", "GROQ_API_KEY", "GROQ_MODEL", "openai/gpt-oss-120b",
             ["gpt-oss-120b", "llama-3.3-70b", "70b"]),
}
NAMES = {"anthropic": "Claude", "openai": "OpenAI", "gemini": "Gemini", "groq": "Groq"}
SKIP_WORDS = ("image", "tts", "audio", "embed", "live", "vision", "whisper", "guard", "transcribe", "realtime", "search")
_discovered: dict[str, str] = {}


def configured() -> list[str]:
    order = [p.strip() for p in os.environ.get("AI_ORDER", "anthropic,openai,gemini,groq").split(",") if p.strip()]
    keys = {"anthropic": "ANTHROPIC_API_KEY", **{k: v[1] for k, v in OPENAI_STYLE.items()}}
    return [p for p in order if p in keys and _key(keys[p])]


def ask(prompt: str) -> tuple[str, str]:
    """Send one prompt. Returns (reply text, provider name). Raises if every provider fails."""
    errors = []
    for p in configured():
        try:
            text = _anthropic(prompt) if p == "anthropic" else _openai_style(p, prompt)
            if text.strip():
                return text, NAMES[p]
            errors.append(f"{NAMES[p]}: empty reply")
        except Exception as e:  # try the next provider
            errors.append(f"{NAMES[p]}: {str(e)[:200]}")
    raise RuntimeError("Every AI provider failed. " + " | ".join(errors))


def _anthropic(prompt: str) -> str:
    import anthropic

    os.environ["ANTHROPIC_API_KEY"] = _key("ANTHROPIC_API_KEY")
    model = os.environ.get("CLAUDE_MODEL", "claude-sonnet-5-5")
    msg = anthropic.Anthropic().messages.create(model=model, max_tokens=4000,
                                                messages=[{"role": "user", "content": prompt}])
    return "".join(b.text for b in msg.content if getattr(b, "type", "") == "text")


def _openai_style(name: str, prompt: str) -> str:
    base, key_env, model_env, default, prefer = OPENAI_STYLE[name]
    key = _key(key_env)
    model = os.environ.get(model_env) or _discovered.get(name) or default
    r = _chat(base, key, model, prompt)
    if r.status_code in (400, 404) and "model" in r.text.lower() and not os.environ.get(model_env):
        model = _discover(base, key, prefer)  # default model retired: pick a current one
        _discovered[name] = model
        r = _chat(base, key, model, prompt)
    if r.status_code != 200:
        raise RuntimeError(f"HTTP {r.status_code}: {r.text[:300]}")
    return r.json()["choices"][0]["message"]["content"] or ""


def _chat(base, key, model, prompt):
    return httpx.post(f"{base}/chat/completions", timeout=300,
                      headers={"Authorization": f"Bearer {key}"},
                      json={"model": model, "messages": [{"role": "user", "content": prompt}]})


def _discover(base, key, prefer) -> str:
    r = httpx.get(f"{base}/models", timeout=60, headers={"Authorization": f"Bearer {key}"})
    r.raise_for_status()
    ids = sorted({m["id"].removeprefix("models/") for m in r.json().get("data", [])}, reverse=True)
    usable = [i for i in ids if not any(w in i.lower() for w in SKIP_WORDS)]
    stable = [i for i in usable if "preview" not in i and "exp" not in i] or usable
    for word in prefer:
        for i in stable:
            if word in i:
                return i
    if stable:
        return stable[0]
    raise RuntimeError("Could not find a usable model for this key.")


def test_all() -> list[dict]:
    """Check every provider: not set, working (with the model used), or the error it gave."""
    keys = {"anthropic": "ANTHROPIC_API_KEY", **{k: v[1] for k, v in OPENAI_STYLE.items()}}
    out = []
    for p, env in keys.items():
        if not _key(env):
            out.append({"provider": NAMES[p], "status": "not set", "detail": ""})
            continue
        try:
            text = _anthropic("Reply with the word OK.") if p == "anthropic" else _openai_style(p, "Reply with the word OK.")
            model = os.environ.get("CLAUDE_MODEL", "claude-sonnet-5-5") if p == "anthropic" else (
                os.environ.get(OPENAI_STYLE[p][2]) or _discovered.get(p) or OPENAI_STYLE[p][3])
            out.append({"provider": NAMES[p], "status": "working", "detail": f"model {model}, replied: {text.strip()[:30]}"})
        except Exception as e:
            msg = str(e)
            hint = ("The key looks wrong. Copy it again from the provider's site." if any(c in msg for c in ("401", "403", "invalid", "API key"))
                    else "Out of free quota or credit for now." if "429" in msg or "quota" in msg.lower() or "credit" in msg.lower()
                    else "")
            out.append({"provider": NAMES[p], "status": "error", "detail": f"{hint} {msg[:160]}".strip()})
    return out
