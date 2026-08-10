from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

ExperimentalMode = str  # "shadow" | "live"


@dataclass(frozen=True)
class ModuleVerdict:
    module: str
    keep: bool
    reason: str
    detail: str = ""

    @property
    def would_filter(self) -> bool:
        return not self.keep


@dataclass
class ExperimentalEvaluation:
    modules_checked: int = 0
    would_filter: bool = False
    reasons: list[str] = field(default_factory=list)
    module_verdicts: list[ModuleVerdict] = field(default_factory=list)

    def add(self, verdict: ModuleVerdict) -> None:
        self.modules_checked += 1
        self.module_verdicts.append(verdict)
        if verdict.would_filter:
            self.would_filter = True
            if verdict.reason:
                self.reasons.append(verdict.reason)

    def as_dict(self) -> dict[str, Any]:
        return {
            "modules_checked": self.modules_checked,
            "would_filter": self.would_filter,
            "reasons": list(self.reasons),
            "modules": [
                {
                    "module": item.module,
                    "keep": item.keep,
                    "reason": item.reason,
                    "detail": item.detail,
                }
                for item in self.module_verdicts
            ],
        }
