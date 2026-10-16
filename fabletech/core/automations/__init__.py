"""Recipe 注册表。"""

from .base import Recipe
from .health_check import HealthCheckRecipe
from .outlook_register import OutlookRegisterRecipe
from .tiktok_post import TikTokPostRecipe
from .x_post import XPostRecipe

RECIPES: dict[str, type[Recipe]] = {
    r.name: r for r in (
        OutlookRegisterRecipe, XPostRecipe, TikTokPostRecipe, HealthCheckRecipe,
    )
}


def get_recipe(name: str) -> Recipe:
    cls = RECIPES.get(name)
    if cls is None:
        raise KeyError(f"未知配方「{name}」。可用：{'、'.join(RECIPES)}")
    return cls()


def recipe_titles() -> list[tuple[str, str]]:
    """[(name, title)]，供 GUI/CLI 展示。"""
    return [(name, cls.title) for name, cls in RECIPES.items()]


__all__ = ["RECIPES", "get_recipe", "recipe_titles", "Recipe"]
