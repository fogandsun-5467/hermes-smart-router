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
import json
import tempfile
from typing import Any, Dict, Optional

logger = logging.getLogger(__name__)

# 全局路由引擎实例
_router_engine: Optional[Any] = None

# 跨进程通信文件路径（与 hook 共享）
_decision_file = os.path.join(tempfile.gettempdir(), "hermes_smart_router_decision.json")


def register(ctx: Any) -> None:
    """
    插件注册函数。
    
    Args:
        ctx: Hermes 插件上下文
    """
    global _router_engine
    
    # 获取配置
    config = {}
    if hasattr(ctx, 'config'):
        config = ctx.config or {}
    elif hasattr(ctx, 'get_config'):
        config = ctx.get_config() or {}
    
    # 获取 memory 文件路径
    memory_dir = os.path.join(os.path.dirname(__file__), "..", "memories")
    memory_file = os.path.join(memory_dir, "MEMORY.md")
    os.makedirs(memory_dir, exist_ok=True)
    
    # 延迟导入避免循环依赖
    from .router_engine import RouterEngine
    
    _router_engine = RouterEngine(
        config=config,
        memory_file=memory_file,
        call_provider_func=None
    )
    
    # 注册 pre_llm_call 钩子
    ctx.register_hook("pre_llm_call", pre_llm_call)
    
    logger.info(f"Smart Router plugin registered, memory_file={memory_file}")


def pre_llm_call(
    user_message: str,
    session_id: str,
    config: Dict[str, Any],
    **kwargs
) -> Dict[str, Any]:
    """
    pre_llm_call 钩子。

    在 LLM 调用前执行五层判断，返回路由决策和提示。
    
    Returns:
        dict: 必须包含 'context' 键，其值会被添加到 ephemeral system prompt
    """
    global _router_engine
    import asyncio

    # 如果还没有初始化（不应该发生，因为 register 已初始化），使用默认配置
    if _router_engine is None:
        from .router_engine import RouterEngine
        memory_dir = os.path.join(os.path.dirname(__file__), "..", "memories")
        memory_file = os.path.join(memory_dir, "MEMORY.md")
        os.makedirs(memory_dir, exist_ok=True)
        _router_engine = RouterEngine(
            config=config,
            memory_file=memory_file,
            call_provider_func=None
        )

    # 执行五层判断（同步封装）
    final_decision, reason, details = asyncio.run(
        _router_engine.judge(user_message)
    )

    # 生成路由提示
    hint = _router_engine.get_routing_hint(final_decision, details)

    # 写入决策文件（供 Gateway Hook 跨进程读取）
    try:
        with open(_decision_file, "w") as f:
            json.dump({
                "decision": final_decision,
                "details": details,
                "pid": os.getpid()
            }, f)
    except Exception as e:
        logger.warning(f"Failed to write routing decision to file: {e}")

    logger.info(
        f"Routing decision: {final_decision} (reason={reason}, "
        f"score={details.get('score')}, category={details.get('category')})"
    )

    # 返回结果，context 键会被添加到 system prompt
    return {
        "context": hint,  # 必须用 context 键
        "decision": final_decision,
        "reason": reason,
        "details": details,
        "_smart_router_decision": final_decision,
        "_smart_router_details": details,
    }


def get_last_decision() -> Optional[Dict[str, Any]]:
    """获取最后一次路由决策（供 Gateway Hook 使用）"""
    global _router_engine
    if _router_engine is None:
        return None
    return {
        "decision": "cheap",
        "details": {}
    }
