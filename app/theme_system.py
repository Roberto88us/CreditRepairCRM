from __future__ import annotations

import json
from copy import deepcopy
from pathlib import Path

DEFAULT_THEME_PACK_KEY = "royal_blue_executive"

LEGACY_THEME_KEY_MAP = {
    "royal_blue": "royal_blue_executive",
    "green": "emerald_marble",
    "off_white": "ivory_stone",
    "sand": "sandstone_reserve",
    "brown": "heritage_wood",
    "black": "royal_blue_executive",
    "red": "sandstone_reserve",
    "white": "executive_white",
    "executive": "executive_white",
}



BACKGROUND_LABELS = {
    "cosmic_01_royal_blue_beauty": "Royal blue nebula",
    "cosmic_02_royal_blue_stars_nebulae": "Starfield nebula",
    "cosmic_03_royal_blue_elegance": "Cosmic executive blue",
    "green_01_marble_elegant_gold_veins": "Emerald marble",
    "green_02_marble_golden_veins": "Green marble glow",
    "green_03_marble_rich_gold_veins": "Rich emerald marble",
    "light_01_offwhite_linen_soft_accents": "Off-white linen",
    "light_02_marble_white_soft_veins": "White marble",
    "sand_01_weathered_stone_fragments": "Weathered stone",
    "wood_01_plank_texture_detail": "Heritage planks",
    "wood_02_weathered_planks": "Weathered wood",
    "wood_03_weathered_planks_lighter": "Light aged planks",
}

DEFAULT_THEME_PACK_LIBRARY = {
    "royal_blue_executive": {
        "label": "Royal Blue Executive",
        "summary": "Dark executive cosmic theme",
        "description": "Deep royal-blue executive theme with cosmic depth, premium gold accents, and dark polished panels.",
        "accent": "#d9a441",
        "mode": "dark",
        "backgrounds": {
            "login": "cosmic_03_royal_blue_elegance",
            "entry": "cosmic_03_royal_blue_elegance",
            "lobby": "cosmic_01_royal_blue_beauty",
            "module": "cosmic_02_royal_blue_stars_nebulae",
            "dashboard": "cosmic_02_royal_blue_stars_nebulae",
            "settings": "cosmic_03_royal_blue_elegance"
        },
        "palette": {
            "primary": "#1D3E73",
            "secondary": "#B78C26",
            "surface": "rgba(8,18,33,0.86)",
            "surface_soft": "rgba(11,24,43,0.72)",
            "border": "rgba(212,171,82,0.45)",
            "text": "#f3e5bc",
            "muted": "#cab889"
        }
    },
    "emerald_marble": {
        "label": "Emerald Marble",
        "summary": "Executive marble with gold",
        "description": "Emerald marble theme with antique gold accents and dark green executive surfaces.",
        "accent": "#c9a34a",
        "mode": "dark",
        "backgrounds": {
            "login": "green_01_marble_elegant_gold_veins",
            "entry": "green_01_marble_elegant_gold_veins",
            "lobby": "green_02_marble_golden_veins",
            "module": "green_03_marble_rich_gold_veins",
            "dashboard": "green_03_marble_rich_gold_veins",
            "settings": "green_02_marble_golden_veins"
        },
        "palette": {
            "primary": "#123C34",
            "secondary": "#C7A24D",
            "surface": "rgba(10,23,19,0.84)",
            "surface_soft": "rgba(16,35,28,0.72)",
            "border": "rgba(199,162,77,0.44)",
            "text": "#efe5c3",
            "muted": "#c9bb91"
        }
    },
    "ivory_stone": {
        "label": "Ivory Stone",
        "summary": "Light stone and ivory",
        "description": "Light ivory and stone theme with restrained bronze accents and softer surfaces.",
        "accent": "#b38a4f",
        "mode": "light",
        "backgrounds": {
            "login": "light_02_marble_white_soft_veins",
            "entry": "light_01_offwhite_linen_soft_accents",
            "lobby": "light_02_marble_white_soft_veins",
            "module": "sand_01_weathered_stone_fragments",
            "dashboard": "light_02_marble_white_soft_veins",
            "settings": "light_01_offwhite_linen_soft_accents"
        },
        "palette": {
            "primary": "#7A6145",
            "secondary": "#B38A4F",
            "surface": "rgba(255,251,243,0.88)",
            "surface_soft": "rgba(248,242,233,0.78)",
            "border": "rgba(179,138,79,0.28)",
            "text": "#2e241a",
            "muted": "#6f5f4b"
        }
    },
    "sandstone_reserve": {
        "label": "Sandstone Reserve",
        "summary": "Warm parchment executive light",
        "description": "Warm parchment and sandstone theme with burnished bronze contrast.",
        "accent": "#b8894d",
        "mode": "light",
        "backgrounds": {
            "login": "sand_01_weathered_stone_fragments",
            "entry": "sand_01_weathered_stone_fragments",
            "lobby": "light_01_offwhite_linen_soft_accents",
            "module": "sand_01_weathered_stone_fragments",
            "dashboard": "sand_01_weathered_stone_fragments",
            "settings": "light_02_marble_white_soft_veins"
        },
        "palette": {
            "primary": "#8A6233",
            "secondary": "#C69955",
            "surface": "rgba(255,248,239,0.88)",
            "surface_soft": "rgba(249,240,227,0.80)",
            "border": "rgba(198,153,85,0.28)",
            "text": "#312113",
            "muted": "#73563b"
        }
    },
    "heritage_wood": {
        "label": "Heritage Wood",
        "summary": "Warm wood and aged brass",
        "description": "Dark heritage wood theme with aged brass accents and warm executive depth.",
        "accent": "#b67d3c",
        "mode": "dark",
        "backgrounds": {
            "login": "wood_03_weathered_planks_lighter",
            "entry": "wood_03_weathered_planks_lighter",
            "lobby": "wood_01_plank_texture_detail",
            "module": "wood_02_weathered_planks",
            "dashboard": "wood_02_weathered_planks",
            "settings": "wood_03_weathered_planks_lighter"
        },
        "palette": {
            "primary": "#5A3B21",
            "secondary": "#B67D3C",
            "surface": "rgba(24,16,10,0.82)",
            "surface_soft": "rgba(42,28,18,0.68)",
            "border": "rgba(182,125,60,0.42)",
            "text": "#f2e0bf",
            "muted": "#c8aa82"
        }
    },
    "executive_white": {
        "label": "Executive White",
        "summary": "Refined light corporate theme",
        "description": "Professional white executive theme with restrained gold accents, soft stone backgrounds, and high readability for long operational work sessions.",
        "accent": "#a8823f",
        "mode": "light",
        "backgrounds": {
            "login": "light_01_offwhite_linen_soft_accents",
            "entry": "light_01_offwhite_linen_soft_accents",
            "lobby": "light_02_marble_white_soft_veins",
            "module": "light_01_offwhite_linen_soft_accents",
            "dashboard": "light_02_marble_white_soft_veins",
            "settings": "light_01_offwhite_linen_soft_accents"
        },
        "palette": {
            "primary": "#546273",
            "secondary": "#A8823F",
            "surface": "rgba(255,255,255,0.90)",
            "surface_soft": "rgba(247,249,252,0.84)",
            "border": "rgba(168,130,63,0.24)",
            "text": "#1e2936",
            "muted": "#607080"
        }
    }
}


def normalize_theme_key(theme_key: str | None, library: dict | None = None) -> str:
    raw = (theme_key or "").strip()
    if not raw:
        return DEFAULT_THEME_PACK_KEY
    mapped = LEGACY_THEME_KEY_MAP.get(raw, raw)
    if library and mapped in library:
        return mapped
    if mapped in DEFAULT_THEME_PACK_LIBRARY:
        return mapped
    return DEFAULT_THEME_PACK_KEY



def _surface_labels(pack: dict) -> dict:
    backgrounds = (pack or {}).get("backgrounds", {}) or {}
    return {
        "login": BACKGROUND_LABELS.get(backgrounds.get("login", ""), "Styled login"),
        "lobby": BACKGROUND_LABELS.get(backgrounds.get("lobby", ""), "Styled lobby"),
        "settings": BACKGROUND_LABELS.get(backgrounds.get("settings", ""), "Styled settings"),
        "dashboard": BACKGROUND_LABELS.get(backgrounds.get("dashboard", ""), "Styled dashboard"),
        "module": BACKGROUND_LABELS.get(backgrounds.get("module", ""), "Styled workspace"),
    }

def ensure_theme_files(library_path: Path, settings_path: Path) -> None:
    library_path = Path(library_path)
    settings_path = Path(settings_path)
    library_path.parent.mkdir(parents=True, exist_ok=True)
    settings_path.parent.mkdir(parents=True, exist_ok=True)

    if not library_path.exists():
        library_path.write_text(json.dumps(DEFAULT_THEME_PACK_LIBRARY, indent=2), encoding="utf-8")
    else:
        try:
            current = json.loads(library_path.read_text(encoding="utf-8"))
        except Exception:
            current = {}
        merged = deepcopy(DEFAULT_THEME_PACK_LIBRARY)
        if isinstance(current, dict):
            for key, value in current.items():
                if isinstance(value, dict):
                    if "mode" not in value and key in DEFAULT_THEME_PACK_LIBRARY:
                        value["mode"] = DEFAULT_THEME_PACK_LIBRARY[key].get("mode", "dark")
                    if "summary" not in value and key in DEFAULT_THEME_PACK_LIBRARY:
                        value["summary"] = DEFAULT_THEME_PACK_LIBRARY[key].get("summary", "")
                    merged[key] = value
        library_path.write_text(json.dumps(merged, indent=2), encoding="utf-8")

    if not settings_path.exists():
        settings_path.write_text(json.dumps({
            "active_theme_key": DEFAULT_THEME_PACK_KEY,
            "brand_lock": {
                "logo_file": "creditsapientia_lockup_primary_transparent.png",
                "crest_file": "creditsapientia_crest_clean.png",
                "wordmark_file": "creditsapientia_wordmark_tagline_clean.png",
                "vault_entry_file": "Golden vault emblem with padlock.png",
                "vault_credentials_file": "Ornate gold login panel design(1).png"
            }
        }, indent=2), encoding="utf-8")


def load_theme_library(library_path: Path) -> dict:
    ensure_theme_files(library_path, library_path.parent / "theme_system_settings.json")
    try:
        data = json.loads(Path(library_path).read_text(encoding="utf-8"))
    except Exception:
        data = deepcopy(DEFAULT_THEME_PACK_LIBRARY)
    if not isinstance(data, dict):
        data = deepcopy(DEFAULT_THEME_PACK_LIBRARY)
    return data


def load_theme_settings(settings_path: Path) -> dict:
    ensure_theme_files(settings_path.parent / "theme_pack_library.json", settings_path)
    try:
        data = json.loads(Path(settings_path).read_text(encoding="utf-8"))
    except Exception:
        data = {}
    if not isinstance(data, dict):
        data = {}
    if "active_theme_key" not in data:
        data["active_theme_key"] = DEFAULT_THEME_PACK_KEY
    return data


def save_theme_settings(settings_path: Path, settings: dict) -> None:
    Path(settings_path).write_text(json.dumps(settings, indent=2), encoding="utf-8")


def save_theme_library(library_path: Path, library: dict) -> None:
    Path(library_path).write_text(json.dumps(library, indent=2), encoding="utf-8")


def get_theme_catalog(library: dict) -> dict:
    catalog = {}
    for key, value in (library or {}).items():
        pack = value or {}
        catalog[key] = {
            "label": pack.get("label", key.replace("_", " ").title()),
            "accent": pack.get("accent", "#d9a441"),
            "description": pack.get("description", ""),
            "summary": pack.get("summary") or pack.get("description", ""),
            "mode": pack.get("mode", "dark"),
            "ai_generated": bool(pack.get("ai_generated")),
            "surface_labels": _surface_labels(pack),
        }
    return catalog


def get_theme_pack(library: dict, theme_key: str | None) -> dict:
    key = normalize_theme_key(theme_key, library)
    pack = deepcopy((library or {}).get(key) or DEFAULT_THEME_PACK_LIBRARY[DEFAULT_THEME_PACK_KEY])
    pack["key"] = key
    if "mode" not in pack:
        pack["mode"] = DEFAULT_THEME_PACK_LIBRARY.get(key, {}).get("mode", "dark")
    if "summary" not in pack:
        pack["summary"] = DEFAULT_THEME_PACK_LIBRARY.get(key, {}).get("summary", pack.get("description", ""))
    return pack


def resolve_background_for_screen(theme_pack: dict, screen: str, fallback: str = "login") -> str:
    backgrounds = (theme_pack or {}).get("backgrounds") or {}
    stem = (backgrounds.get(screen) or backgrounds.get(fallback) or "").strip()
    return stem or DEFAULT_THEME_PACK_LIBRARY[DEFAULT_THEME_PACK_KEY]["backgrounds"]["login"]


def build_theme_runtime(theme_pack: dict, screen: str) -> dict:
    palette = deepcopy((theme_pack or {}).get("palette") or {})
    mode = (theme_pack or {}).get("mode", "dark")
    if mode == "light":
        ui = {
            "page_bg": "#f3f5f8",
            "page_tint": "linear-gradient(180deg, rgba(255,255,255,.38), rgba(241,244,248,.82))",
            "rail_bg": "linear-gradient(180deg, rgba(255,255,255,.95), rgba(245,247,250,.92))",
            "rail_border": "rgba(168,130,63,.18)",
            "button_dark_bg": "rgba(255,255,255,.85)",
            "button_dark_text": palette.get("text", "#1e2936"),
            "button_dark_border": palette.get("border", "rgba(168,130,63,.24)"),
            "active_bg": "linear-gradient(180deg, rgba(168,130,63,.12), rgba(168,130,63,.04))",
            "active_text": palette.get("text", "#1e2936"),
            "flash_success": "#14532d",
            "flash_error": "#7f1d1d",
            "shadow": "0 18px 42px rgba(15,23,42,.12)",
        }
    else:
        ui = {
            "page_bg": "#071220",
            "page_tint": "radial-gradient(circle at 78% 15%, rgba(245,208,119,.16), transparent 20%),linear-gradient(180deg, rgba(2,7,16,.38), rgba(2,7,16,.82))",
            "rail_bg": "linear-gradient(180deg, rgba(6,12,22,.95), rgba(6,12,22,.72))",
            "rail_border": "rgba(215,175,87,.16)",
            "button_dark_bg": "rgba(8,18,33,.72)",
            "button_dark_text": palette.get("text", "#f3e5bc"),
            "button_dark_border": palette.get("border", "rgba(212,171,82,.45)"),
            "active_bg": "linear-gradient(180deg, rgba(216,180,92,.18), rgba(216,180,92,.08))",
            "active_text": palette.get("text", "#f3e5bc"),
            "flash_success": "#d7ffdb",
            "flash_error": "#ffd8d8",
            "shadow": "0 18px 42px rgba(0,0,0,.34)",
        }

    return {
        "key": theme_pack.get("key", DEFAULT_THEME_PACK_KEY),
        "label": theme_pack.get("label", "Royal Blue Executive"),
        "description": theme_pack.get("description", ""),
        "accent": theme_pack.get("accent", "#d9a441"),
        "mode": mode,
        "background_stem": resolve_background_for_screen(theme_pack, screen),
        "palette": {
            "primary": palette.get("primary", "#1D3E73"),
            "secondary": palette.get("secondary", "#B78C26"),
            "surface": palette.get("surface", "rgba(8,18,33,0.86)"),
            "surface_soft": palette.get("surface_soft", "rgba(11,24,43,0.72)"),
            "border": palette.get("border", "rgba(212,171,82,0.45)"),
            "text": palette.get("text", "#f3e5bc"),
            "muted": palette.get("muted", "#cab889"),
        },
        "ui": ui,
    }
