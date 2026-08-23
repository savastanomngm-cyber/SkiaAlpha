"""Multi-provider LLM layer with tier-based routing.
FIX: explicit task='deep' now ALWAYS gets DEEP_CHAIN regardless of UI mode.
The UI mode only affects calls that don't specify a task preference."""
import json, re, time
from openai import OpenAI
from ..security import get_key

DEEP_CHAIN = [
    ("nous", "stealth/ox-alpha"),
    ("groq", "openai/gpt-oss-120b"),
    ("groq", "openai/gpt-oss-20b"),
    ("groq", "qwen/qwen3.6-27b"),
]
FAST_CHAIN = [
    ("groq", "openai/gpt-oss-20b"),
    ("groq", "qwen/qwen3.6-27b"),
    ("groq", "openai/gpt-oss-120b"),
]
NOUS_BASE = "https://inference-api.nousresearch.com/v1"
GROQ_BASE = "https://api.groq.com/openai/v1"
MAX_RETRIES = 2
RETRY_DELAY = 3
_MODE = "auto"


def set_mode(m):
    global _MODE
    _MODE = m if m in ("deep", "instant", "auto") else "auto"


def resolve_chain(task="deep"):
    """Route to the correct provider chain.
    
    FIXED: explicit task='deep' ALWAYS gets DEEP_CHAIN (Nous first),
    regardless of UI mode. The UI mode only downgrades calls that
    don't explicitly request deep reasoning.
    """
    # Explicit deep request ALWAYS gets Nous — this is the fix
    if task == "deep":
        return DEEP_CHAIN
    # For non-deep tasks, respect the UI mode
    if _MODE == "deep":
        return DEEP_CHAIN
    if _MODE == "instant":
        return FAST_CHAIN
    # Auto mode: fast tasks go to fast chain
    return FAST_CHAIN if task == "fast" else DEEP_CHAIN


def _client(provider):
    if provider == "nous":
        key = get_key("NOUS_API_KEY")
        if not key:
            return None
        return OpenAI(api_key=key, base_url=NOUS_BASE)
    key = get_key("GROQ_API_KEY")
    if not key:
        return None
    return OpenAI(api_key=key, base_url=GROQ_BASE)


def _strip_wrappers(text):
    """Remove think tags and markdown fences that reasoning models add."""
    if not text:
        return text
    text = re.sub(r"", "", text, flags=re.DOTALL | re.IGNORECASE)
    text = re.sub(r"```(?:json)?\s*", "", text)
    text = text.replace("```", "")
    return text.strip()


def complete(system, user, temperature=0.7, max_tokens=4096,
             task="deep", force_provider=None, force_model=None, timeout=240):
    chain = [(force_provider, force_model)] if (force_provider and force_model) else resolve_chain(task)
    last_err = "no provider attempted"
    for provider, model in chain:
        client = _client(provider)
        if not client:
            print(f"[llm] ⚠️  {provider} SKIPPED — no API key set in .env")
            last_err = f"no key for {provider}"
            continue
        for attempt in range(1, MAX_RETRIES + 1):
            try:
                resp = client.chat.completions.create(
                    model=model,
                    messages=[{"role": "system", "content": system},
                              {"role": "user", "content": user}],
                    temperature=temperature, max_tokens=max_tokens, timeout=timeout,
                )
                txt = (resp.choices[0].message.content or "").strip()
                txt = _strip_wrappers(txt)
                if txt:
                    print(f"[llm] ✅ {provider}:{model} responded ({len(txt)} chars)")
                    return txt, {"provider": provider, "model": model}
                last_err = "empty response"
            except Exception as e:
                err = str(e)
                last_err = err[:200]
                if "429" in err or "rate" in err.lower() or "TPD" in err or "tokens per day" in err.lower():
                    print(f"[llm] 🚫 {provider}:{model} rate limited -> next model")
                    break
                if "404" in err or "not_found" in err or "decommissioned" in err:
                    print(f"[llm] 🚫 {provider}:{model} not found -> next model")
                    break
                if "413" in err or "too large" in err.lower():
                    print(f"[llm] 🚫 {provider}:{model} request too large -> next model")
                    break
                if "timeout" in err.lower() or "timed out" in err.lower():
                    print(f"[llm] ⏱️  {provider}:{model} timeout (attempt {attempt}/{MAX_RETRIES})")
                    if attempt < MAX_RETRIES:
                        time.sleep(2)
                        continue
                    break
                print(f"[llm] ❌ {provider}:{model} error: {err[:100]}")
                if attempt < MAX_RETRIES:
                    time.sleep(RETRY_DELAY)
                    continue
                break
    print(f"[llm] 💀 ALL providers exhausted. Last error: {last_err}")
    return "", {"error": last_err}


def complete_json(system, user, temperature=0.3, max_tokens=4096,
                  task="deep", force_provider=None, force_model=None, timeout=240):
    chain = [(force_provider, force_model)] if (force_provider and force_model) else resolve_chain(task)
    last_err = "no provider attempted"
    for provider, model in chain:
        client = _client(provider)
        if not client:
            print(f"[llm] ⚠️  {provider} SKIPPED — no API key set in .env")
            last_err = f"no key for {provider}"
            continue
        use_json = True
        for attempt in range(1, MAX_RETRIES + 1):
            try:
                kwargs = dict(model=model,
                              messages=[{"role": "system", "content": system},
                                        {"role": "user", "content": user}],
                              temperature=temperature, max_tokens=max_tokens, timeout=timeout)
                if use_json and provider == "groq":
                    kwargs["response_format"] = {"type": "json_object"}
                resp = client.chat.completions.create(**kwargs)
                txt = (resp.choices[0].message.content or "").strip()
                parsed = extract_json(txt)
                if parsed is not None:
                    print(f"[llm] ✅ {provider}:{model} JSON ok ({len(txt)} chars)")
                    return parsed, {"provider": provider, "model": model}
                print(f"[llm] ⚠️  {provider}:{model} JSON parse failed. Raw: {txt[:120]}...")
                last_err = f"unparseable: {txt[:120]}"
            except Exception as e:
                err = str(e)
                last_err = err[:200]
                if "response_format" in err or ("json" in err.lower() and "400" in err):
                    use_json = False
                    continue
                if "429" in err or "rate" in err.lower() or "TPD" in err or "tokens per day" in err.lower():
                    print(f"[llm] 🚫 {provider}:{model} rate limited -> next model")
                    break
                if "404" in err or "not_found" in err or "decommissioned" in err:
                    print(f"[llm] 🚫 {provider}:{model} not found -> next model")
                    break
                if "413" in err or "too large" in err.lower():
                    print(f"[llm] 🚫 {provider}:{model} request too large -> next model")
                    break
                if "timeout" in err.lower() or "timed out" in err.lower():
                    print(f"[llm] ⏱️  {provider}:{model} timeout (attempt {attempt}/{MAX_RETRIES})")
                    if attempt < MAX_RETRIES:
                        time.sleep(2)
                        continue
                    break
                print(f"[llm] ❌ {provider}:{model} error: {err[:100]}")
                if attempt < MAX_RETRIES:
                    time.sleep(RETRY_DELAY)
                    continue
                break
    print(f"[llm] 💀 ALL providers exhausted. Last error: {last_err}")
    return None, {"error": last_err}


def extract_json(text):
    """Robust JSON extraction — the same approach that was working before."""
    if not text:
        return None
    # 1. Direct parse
    try:
        return json.loads(text)
    except Exception:
        pass
    # 2. Strip wrappers then parse
    cleaned = _strip_wrappers(text)
    try:
        return json.loads(cleaned)
    except Exception:
        pass
    # 3. Greedy regex: first '{' to LAST '}' (this is what worked in v3)
    m = re.search(r"\{.*\}", cleaned, re.DOTALL)
    if m:
        try:
            return json.loads(m.group(0))
        except Exception:
            pass
    # 4. Greedy regex on original text
    m = re.search(r"\{.*\}", text, re.DOTALL)
    if m:
        try:
            return json.loads(m.group(0))
        except Exception:
            pass
    return None