"""Key management + feed sanitization (PART 10)."""
import os, re, html
from pathlib import Path
from functools import lru_cache

ROOT = Path(__file__).resolve().parent.parent


@lru_cache(maxsize=1)
def load_env() -> dict:
    env_file = ROOT / ".env"
    env = {}
    if env_file.exists():
        for line in env_file.read_text().splitlines():
            line = line.strip()
            if line and not line.startswith("#") and "=" in line:
                k, v = line.split("=", 1)
                env[k.strip()] = v.strip().strip('"').strip("'")
    for k, v in env.items():
        os.environ.setdefault(k, v)
    return env


def get_key(name: str) -> str:
    """Retrieve a specific API key by name (e.g., NOUS_API_KEY, GROQ_API_KEY)."""
    env = load_env()
    return env.get(name, "") or os.getenv(name, "")


def clean_text(s, maxlen=300) -> str:
    """Sanitize feed content before storage/render (XSS prevention)."""
    s = html.unescape(str(s))
    s = re.sub(r"<[^>]*>", "", s)
    s = re.sub(r"(javascript|data):", "", s, flags=re.I)
    return s[:maxlen]