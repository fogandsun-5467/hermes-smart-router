"""
Smart Router Gateway Hook Handler
==================================
在 agent:start 事件时获取路由决策，patch resolve_turn_route 实现真正的模型切换。
"""

import logging
import os
import sys

logger = logging.getLogger(__name__)

# 确保插件目录在 Python 路径中
_plugin_dir = os.path.join(os.path.dirname(__file__), "..", "..", "plugins", "smart_router")
if _plugin_dir not in sys.path:
    sys.path.insert(0, _plugin_dir)

# 全局状态，用于存储来自 Agent Plugin 的路由决策
_routing_decision = None


def set_routing_decision(decision: str, details: dict = None):
    """设置路由决策（由 Agent Plugin 调用）"""
    global _routing_decision
    _routing_decision = {
        "decision": decision,
        "details": details or {}
    }
    logger.info(f"Routing decision set: {decision}")


def get_routing_decision() -> dict:
    """获取路由决策"""
    global _routing_decision
    return _routing_decision


async def on_agent_start(agent_context: dict) -> dict:
    """
    agent:start 事件处理。

    检查是否有缓存的路由决策，如果有则 patch resolve_turn_route。
    """
    global _routing_decision

    decision = _routing_decision

    if decision is None:
        logger.debug("No routing decision found, using default")
        return {}

    final_decision = decision.get("decision", "cheap")
    details = decision.get("details", {})

    # 根据决策返回 patch 指令
    if final_decision == "strong":
        model_override = {
            "model": "glm-5.1",  # 专家模型
            "provider": "custom"
        }
    else:
        model_override = {
            "model": "MiniMax-M2.7",  # 轻量模型
            "provider": "minimax-cn"
        }

    logger.info(f"Applying model override: {model_override}")

    return {
        "model_override": model_override,
        "reason": details.get("source", "unknown"),
        "score": details.get("score", 0),
    }


def patch_resolve_turn_route(original_fn):
    """
    包装 resolve_turn_route 函数的 patcher。
    这个函数返回一个包装器，在原始函数被调用时注入我们的路由决策。
    """
    async def wrapper(*args, **kwargs):
        result = await original_fn(*args, **kwargs)

        global _routing_decision
        if _routing_decision is not None:
            final_decision = _routing_decision.get("decision", "cheap")

            if final_decision == "strong":
                result["model"] = "glm-5.1"
                result["provider"] = "custom"
            else:
                result["model"] = "MiniMax-M2.7"
                result["provider"] = "minimax-cn"

            # 清除决策（每个回合只用一次）
            _routing_decision = None

        return result

    return wrapper
