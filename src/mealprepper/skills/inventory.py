from __future__ import annotations

import logging
from datetime import datetime, timezone
from typing import TYPE_CHECKING, Any

from pydantic import BaseModel, Field

from mealprepper.skills.pantry_config import PantryConfig
from mealprepper.storage.sqlite import SQLiteStore

if TYPE_CHECKING:
    from mealprepper.services.family_resolver import FamilyContext

logger = logging.getLogger(__name__)


class InventoryItem(BaseModel):
    name: str
    quantity: str = ""
    category: str = "pantry"


class InventorySkill:
    """Track pantry/spice inventory (foundation for future smart shopping)."""

    def __init__(
        self,
        store: SQLiteStore | None = None,
        pantry: PantryConfig | None = None,
        family_context: FamilyContext | Any | None = None,
    ) -> None:
        self.store = store or SQLiteStore()
        if pantry is not None:
            self.pantry = pantry
        elif family_context is not None:
            self.pantry = family_context.pantry
        else:
            from mealprepper.config import get_settings

            self.pantry = PantryConfig.from_settings(get_settings())

    def list_items(self) -> list[InventoryItem]:
        with self.store._conn() as conn:
            rows = conn.execute(
                """
                SELECT item_name, quantity, category FROM inventory
                WHERE family_id = ?
                ORDER BY item_name
                """,
                (self.store.family_id,),
            ).fetchall()
        return [
            InventoryItem(name=r["item_name"], quantity=r["quantity"] or "", category=r["category"] or "pantry")
            for r in rows
        ]

    def add_item(self, name: str, quantity: str = "", category: str = "pantry") -> InventoryItem:
        import uuid

        iid = str(uuid.uuid4())
        now = datetime.now(timezone.utc).isoformat()
        with self.store._conn() as conn:
            conn.execute(
                """
                INSERT INTO inventory (id, family_id, item_name, quantity, category, updated_at)
                VALUES (?, ?, ?, ?, ?, ?)
                """,
                (iid, self.store.family_id, name, quantity, category, now),
            )
        return InventoryItem(name=name, quantity=quantity, category=category)

    def subtract_from_grocery(self, grocery_items: list, inventory: list[InventoryItem] | None = None) -> list:
        """Remove items already in inventory or pantry config from grocery list."""
        pantry = self.pantry
        inv = inventory or self.list_items()
        inv_names = {i.name.lower().strip() for i in inv}
        remaining = []
        for item in grocery_items:
            name = item.name.lower().strip()
            if name in inv_names or pantry.matches_on_hand(item.name):
                logger.info("Skipping %s — pantry/inventory", item.name)
                continue
            remaining.append(item)
        return remaining

    def to_prompt_context(self) -> str:
        items = self.list_items()
        if not items:
            return "Pantry inventory: empty (assume standard staples only)."
        lines = [f"- {i.name}: {i.quantity or 'some'} ({i.category})" for i in items]
        return "Pantry inventory:\n" + "\n".join(lines)
