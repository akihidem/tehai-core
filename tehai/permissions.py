"""Permission / capability model.

Least privilege by default: an action that is not explicitly granted is denied.
Dangerous, outward-facing, or destructive actions never run directly — they
return NEEDS_APPROVAL and must pass an Approval Gate (with a dry-run option).
A child agent may never hold a capability its parent lacks.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum

from .models import Action, AgentTemplate, DANGEROUS_ACTIONS


class ActionDecision(str, Enum):
    ALLOWED = "allowed"
    NEEDS_APPROVAL = "needs_approval"
    FORBIDDEN = "forbidden"


@dataclass
class ApprovalRequest:
    action: Action
    agent_template_id: str
    context: str = ""
    dry_run_available: bool = True
    approved: bool = False


@dataclass
class CapabilityCheck:
    ok: bool
    violating_actions: list[Action] = field(default_factory=list)
    detail: str = ""


class PermissionModel:
    def __init__(self, dangerous: frozenset[Action] = DANGEROUS_ACTIONS):
        self.dangerous = dangerous
        self.pending_approvals: list[ApprovalRequest] = []

    def check_action(self, agent: AgentTemplate, action: Action) -> ActionDecision:
        if action in agent.forbidden():
            return ActionDecision.FORBIDDEN
        if action not in agent.allowed():
            # Not granted == forbidden (least privilege).
            return ActionDecision.FORBIDDEN
        if action in self.dangerous:
            return ActionDecision.NEEDS_APPROVAL
        return ActionDecision.ALLOWED

    def request_approval(self, agent: AgentTemplate, action: Action, context: str = "") -> ApprovalRequest:
        req = ApprovalRequest(
            action=action,
            agent_template_id=agent.agent_template_id,
            context=context,
            dry_run_available=True,
        )
        self.pending_approvals.append(req)
        return req

    def enforce_child_subset(self, parent: AgentTemplate, child: AgentTemplate) -> CapabilityCheck:
        """A child's granted capabilities must be a subset of the parent's."""
        parent_caps = parent.allowed()
        child_caps = child.allowed()
        violating = sorted(child_caps - parent_caps, key=lambda a: a.value)
        if violating:
            return CapabilityCheck(
                ok=False,
                violating_actions=violating,
                detail=(
                    f"child '{child.agent_template_id}' would hold capabilities its "
                    f"parent '{parent.agent_template_id}' lacks: "
                    f"{[a.value for a in violating]}"
                ),
            )
        return CapabilityCheck(ok=True, detail="child capabilities ⊆ parent")

    def requires_gate(self, action: Action) -> bool:
        return action in self.dangerous
