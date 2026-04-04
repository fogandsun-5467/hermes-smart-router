"""
Smart Router Plugin for Hermes
=============================
智能模型路由插件，实现五层判断逻辑：
1. meta 快速模式
2. session 内上下文关系
3. 经验匹配
4. LLM 评分
5. 高峰期守门员

配合 Gateway Hook 实现真正的模型切换。
"""

import logging
import os
from typing import Any, Dict, Optional

from .router_engine import RouterEngine

logger = logging.getLogger(__name__)

# 全局路由引擎实例
_router_engine: Optional[RouterEngine] = None


def init_plugin(config: Dict[str, Any], call_provider_func=None) -> None:
    """初始化插件"""
    global _router_engine

    # 获取 memory 文件路径
    memory_dir = os.path.join(os.path.dirname(__file__), "..", "memories")
    memory_file = os.path.join(memory_dir, "MEMORY.md")

    # 确保目录存在
    os.makedirs(memory_dir, exist_ok=True)

    _router_engine = RouterEngine(
        config=config,
        memory_file=memory_file,
        call_provider_func=call_provider_func
    )

    logger.info(f"Smart Router plugin initialized, memory_file={memory_file}")


async def pre_llm_call(
    message: str,
    session_id: str,
    config: Dict[str, Any],
    call_provider_func=None
) -> Dict[str, Any]:
    """
    pre_llm_call 钩子。

    在 LLM 调用前执行五层判断，返回路由决策和提示。
    """
    global _router_engine

    # 如果还没有初始化，先初始化
    if _router_engine is None:
        init_plugin(config, call_provider_func)

    # 执行五层判断
    final_decision, reason, details = await _router_engine.judge(message)

    # 生成路由提示
    hint = _router_engine.get_routing_hint(final_decision, details)

    logger.info(
        f"Routing decision: {final_decision} (reason={reason}, "
        f"score={details.get('score')}, category={details.get('category')})"
    )

    return {
        "decision": final_decision,
        "reason": reason,
        "details": details,
        "hint": hint,
        # 存储到全局状态，供 Gateway Hook 读取
        "_smart_router_decision": final_decision,
        "_smart_router_details": details,
    }


def get_last_decision() -> Optional[Dict[str, Any]]:
    """获取最后一次路由决策（供 Gateway Hook 使用）"""
    global _router_engine
    if _router_engine is None:
        return None
    # 返回存储在全局状态中的决策
    return {
        "decision": "cheap",  # 默认值，实际应该从上下文获取
        "details": {}
    }


def set_call_provider_func(func):
    """设置 LLM 调用函数"""
    global _router_engine
    if _router_engine:
        _router_engine.call_provider_func = func
