
from __future__ import annotations

from copy import deepcopy

from theme_system import DEFAULT_THEME_PACK_LIBRARY, DEFAULT_THEME_PACK_KEY


class ThemeAgent:
    """
    Visual-only theme pack generator.  It never touches workflow logic,
    routing, forms, permissions, calculations, or database behaviour.
    It only returns theme-pack data that can be saved into the theme
    library JSON.
    """

    def __init__(self, approved_assets_only: bool = True):
        self.approved_assets_only = approved_assets_only

    def generate_visual_only_theme_pack(self, prompt: str, existing_library: dict | None = None) -> dict:
        prompt_text = (prompt or "").strip()
        lower = prompt_text.lower()
        base_key = DEFAULT_THEME_PACK_KEY
        if any(word in lower for word in ["green", "emerald", "marble"]):
            base_key = "emerald_marble"
        elif any(word in lower for word in ["ivory", "white", "stone", "linen"]):
            base_key = "ivory_stone"
        elif any(word in lower for word in ["sand", "bronze", "parchment"]):
            base_key = "sandstone_reserve"
        elif any(word in lower for word in ["wood", "heritage", "mahogany"]):
            base_key = "heritage_wood"

        base_pack = deepcopy(DEFAULT_THEME_PACK_LIBRARY[base_key])
        generated_key = "ai_" + "_".join([part for part in prompt_text.lower().replace("-", " ").split()[:6]]) if prompt_text else "ai_new_theme_pack"
        generated_key = generated_key.strip("_") or "ai_new_theme_pack"

        base_pack["label"] = prompt_text[:60] if prompt_text else "AI Generated Theme Pack"
        base_pack["description"] = (
            "AI-generated visual-only theme pack. "
            "Brand lock, functionality, routes, logic, and permissions remain unchanged."
        )
        base_pack["ai_generated"] = True
        base_pack["approved_assets_only"] = bool(self.approved_assets_only)
        return {"key": generated_key, "pack": base_pack}
