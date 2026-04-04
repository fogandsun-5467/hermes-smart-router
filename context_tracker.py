"""
Context Tracker Module
====================
session 内上下文追踪器，用于检测当前问题与上一轮的关系。
"""

from dataclasses import dataclass
from typing import Optional


@dataclass
class TurnContext:
    """单轮对话的上下文"""
    message: str
    category: str
    score: int
    decision: str  # strong/cheap
    context_relation: str  # continue/downgrade/unrelated


class ContextTracker:
    """session 内上下文追踪器"""

    def __init__(self):
        self.history: list[TurnContext] = []
        self.continue_signals = [
            "继续", "接着", "然后", "下一步",
            "go on", "continue", "next"
        ]
        self.downgrade_signals = [
            "好了", "就这样", "可以了", "结束", "完成",
            "done", "finish", "ok", "okay"
        ]

    def detect_relation(self, current_message: str) -> tuple[str, Optional[TurnContext]]:
        """
        检测当前问题与上一轮的关系。

        Returns:
            (relation, last_context)
            relation: "continue" | "downgrade" | "unrelated"
            last_context: 上一轮上下文（无则为None）
        """
        if not self.history:
            return "unrelated", None

        last = self.history[-1]

        # 检查 continue
        for signal in self.continue_signals:
            if signal in current_message:
                return "continue", last

        # 检查 downgrade
        for signal in self.downgrade_signals:
            if signal in current_message:
                return "downgrade", last

        return "unrelated", last

    def update(self, message: str, category: str, score: int, decision: str, relation: str):
        """更新上下文"""
        ctx = TurnContext(
            message=message,
            category=category,
            score=score,
            decision=decision,
            context_relation=relation,
        )
        self.history.append(ctx)

    def get_last_context(self) -> Optional[TurnContext]:
        """获取上一轮上下文"""
        return self.history[-1] if self.history else None
