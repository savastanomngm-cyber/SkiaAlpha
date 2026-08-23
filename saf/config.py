"""Config loading with validation. Merges universe.yaml with SQLite custom holdings."""
import yaml
from pathlib import Path
from functools import lru_cache

# Point directly to the saf/ folder where you placed universe.yaml
SAF_DIR = Path(__file__).resolve().parent


class ConfigError(Exception):
    pass


@lru_cache(maxsize=1)
def load() -> dict:
    path = SAF_DIR / "universe.yaml"
    if not path.exists():
        raise ConfigError(f"Missing {path}")
    cfg = yaml.safe_load(path.read_text())
    _validate(cfg)
    return cfg


def _validate(cfg):
    s = cfg.get("settings", {})
    if "benchmark" not in s:
        raise ConfigError("settings.benchmark required")
    th = s.get("score_thresholds", {})
    if not (0 < th.get("watch", 45) < th.get("candidate", 60) <= 100):
        raise ConfigError("thresholds must satisfy 0 < watch < candidate <= 100")
    
    baskets = cfg.get("baskets", [])
    if not isinstance(baskets, list):
        raise ConfigError("baskets must be a YAML list (use '- name:' for each)")
        
    seen = set()
    for b in baskets:
        name = b.get("name")
        if not name or not b.get("holdings"):
            raise ConfigError(f"basket needs name+holdings: {b}")
        
        # Cast keys to string to avoid bool vs str comparison issues later
        holdings = {str(k): v for k, v in b["holdings"].items()}
        dupes = [t for t in holdings if t in seen]
        if dupes:
            print(f"⚠️  WARNING: {dupes} appear in multiple baskets (check intent)")
        wsum = sum(holdings.values())
        if not 0 < wsum < 100:
            raise ConfigError(f"basket '{name}' weight sum suspicious: {wsum}")
        seen.update(holdings)


def baskets(cfg=None) -> dict:
    """Returns merged basket holdings: universe.yaml + SQLite custom_holdings."""
    cfg = cfg or load()
    merged = {}
    for b in cfg.get("baskets", []):
        name = b["name"]
        # CRITICAL FIX: YAML parses unquoted ON, OFF, YES, NO as booleans (True/False).
        # Force all ticker symbols to strings so they don't crash sorted() later.
        merged[name] = {str(k): float(v) for k, v in b.get("holdings", {}).items()}

    from . import store
    try:
        custom = store.get_custom_holdings()
        for basket_name, holdings in custom.items():
            if basket_name not in merged:
                merged[basket_name] = {}
            merged[basket_name].update({str(k): float(v) for k, v in holdings.items()})
    except Exception:
        pass  # table might not exist yet on first run

    return merged


def basket_sections(cfg=None) -> dict:
    cfg = cfg or load()
    sections = {b["name"]: b.get("section", "OTHER") for b in cfg.get("baskets", [])}
    merged = baskets(cfg)
    for name in merged:
        if name not in sections:
            sections[name] = "🆕 AI-ADDED"
    return sections


def all_tickers(cfg=None) -> list:
    merged = baskets(cfg)
    ts = set()
    for holdings in merged.values():
        ts.update(str(t) for t in holdings.keys())
    cfg = cfg or load()
    for group in cfg.get("screening_universe", {}).values():
        # Force string cast here too in case ON/OFF/YES/NO is in the screening universe
        ts.update(str(t) for t in group)
    ts.add(str(cfg["settings"]["benchmark"]))
    return sorted(list(ts))


def sector_of(ticker, cfg=None):
    cfg = cfg or load()
    ticker = str(ticker)
    for sector, tickers in cfg.get("screening_universe", {}).items():
        if ticker in [str(t) for t in tickers]:
            return sector
    merged = baskets(cfg)
    for basket_name, holdings in merged.items():
        if ticker in holdings:
            sections = basket_sections(cfg)
            return sections.get(basket_name, "BASKET")
    return None


def basket_names(cfg=None) -> list:
    """All basket names (YAML + custom) for UI dropdowns."""
    return sorted(baskets(cfg).keys())