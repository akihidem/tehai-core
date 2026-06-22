"""Agent Registry: load + validate + select agent templates.

Selection is registry-first. The Agent Designer never invents an agent at
runtime; at most it adjusts parameters of a registered template, and genuinely
novel needs are *recorded as proposals* (see propose_new_template), never
auto-instantiated.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Optional

from .models import AgentTemplate, ModelTier, TaskType


DATA_DIR = Path(__file__).parent / "data" / "agents"


class RegistryError(Exception):
    pass


class AgentRegistry:
    def __init__(self, templates: dict[str, AgentTemplate]):
        self._templates = templates
        # Proposals for genuinely new agents — recorded, never auto-created.
        self.proposals: list[dict] = []

    # ----- loading ----- #
    @classmethod
    def load(cls, data_dir: Path | str = DATA_DIR) -> "AgentRegistry":
        data_dir = Path(data_dir)
        templates: dict[str, AgentTemplate] = {}
        files = sorted(data_dir.glob("*.json"))
        if not files:
            raise RegistryError(f"no agent templates found under {data_dir}")
        for fp in files:
            raw = json.loads(fp.read_text(encoding="utf-8"))
            entries = raw if isinstance(raw, list) else [raw]
            for entry in entries:
                tmpl = AgentTemplate.from_dict(entry)
                errs = tmpl.errors()
                if errs:
                    raise RegistryError(f"{fp.name}:{tmpl.agent_template_id}: {errs}")
                if tmpl.agent_template_id in templates:
                    raise RegistryError(f"duplicate template id: {tmpl.agent_template_id}")
                templates[tmpl.agent_template_id] = tmpl
        return cls(templates)

    # ----- access ----- #
    def get(self, template_id: str) -> AgentTemplate:
        if template_id not in self._templates:
            raise RegistryError(f"unknown agent template: {template_id}")
        return self._templates[template_id]

    def __contains__(self, template_id: str) -> bool:
        return template_id in self._templates

    def all(self) -> list[AgentTemplate]:
        return list(self._templates.values())

    def ids(self) -> list[str]:
        return list(self._templates.keys())

    # ----- selection (Agent Designer core) ----- #
    def select_for_task(
        self,
        task_type: TaskType,
        preferred_id: Optional[str] = None,
    ) -> AgentTemplate:
        """Pick the best registered template for a task type.

        Priority: explicit preferred_id (if it handles the type) > a template that
        declares the type > a sensible generic fallback. Raises if nothing fits,
        so the caller can record a proposal rather than fabricate an agent.
        """
        if preferred_id and preferred_id in self._templates:
            t = self._templates[preferred_id]
            if not t.handles_task_types or task_type.value in t.handles_task_types:
                return t

        candidates = [
            t for t in self._templates.values()
            if task_type.value in t.handles_task_types
        ]
        if candidates:
            # Prefer the cheapest capable tier (least-privilege / least-cost bias).
            candidates.sort(key=lambda t: t.recommended_model_tier.rank)
            return candidates[0]

        fallback = {
            TaskType.GENERIC: "Implementer",
            TaskType.SPEC_DESIGN: "ProductManager",
            TaskType.INTEGRATION: "Synthesizer",
        }.get(task_type, "Implementer")
        if fallback in self._templates:
            return self._templates[fallback]
        raise RegistryError(f"no registered template handles {task_type.value}")

    def propose_new_template(self, role: str, reason: str, task_type: TaskType) -> dict:
        """Record (do not create) a request for a new agent template."""
        proposal = {"role": role, "reason": reason, "task_type": task_type.value}
        self.proposals.append(proposal)
        return proposal
