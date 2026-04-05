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
import threading
from typing import Any, Dict, Optional

logger = logging.getLogger(__name__)

# 全局路由引擎实例
_router_engine: Optional[Any] = None

# 跨进程通信文件路径（与 hook 共享）
_decision_file = os.path.join(tempfile.gettempdir(), "hermes_smart_router_decision.json")


# 全局变量，用于在 pre_llm_call 和 post_llm_call 之间传递决策
_last_decision: Optional[str] = None
_last_decision_lock = threading.Lock()


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
    # 注册 post_llm_call 钩子（用于直接插入模型标识）
    ctx.register_hook("post_llm_call", post_llm_call)
    
    logger.info(f"Smart Router plugin registered, memory_file={memory_file}")


def pre_llm_call(
    session_id: str,
    user_message: str,
    conversation_history: list = None,
    is_first_turn: bool = False,
    model: str = None,
    platform: str = None,
    **kwargs
) -> Dict[str, Any]:
    """
    pre_llm_call 钩子。

    在 LLM 调用前执行五层判断，返回路由决策和提示。
    
    参数（Hermes 传入）：
        session_id: 会话ID
        user_message: 用户消息
        conversation_history: 对话历史
        is_first_turn: 是否是第一轮
        model: 当前模型
        platform: 平台
    
    Returns:
        dict: 必须包含 'context' 键，其值会被添加到 ephemeral system prompt
    """
    global _router_engine
    import asyncio

    # 如果还没有初始化，先初始化
    if _router_engine is None:
        from .router_engine import RouterEngine
        memory_dir = os.path.join(os.path.dirname(__file__), "..", "memories")
        memory_file = os.path.join(memory_dir, "MEMORY.md")
        os.makedirs(memory_dir, exist_ok=True)
        
        # 获取配置（从 kwargs 或默认）
        config = kwargs.get('config', {}) or {}
        
        _router_engine = RouterEngine(
            config=config,
            memory_file=memory_file,
            call_provider_func=None  # LLM调用在Agent进程中不可用，使用默认判断
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

    # 保存决策到全局变量，供 post_llm_call 使用
    global _last_decision
    with _last_decision_lock:
        _last_decision = final_decision

    # 返回结果，context 键会被添加到 system prompt
    return {
        "context": hint,  # 必须用 context 键
        "decision": final_decision,
        "reason": reason,
        "details": details,
        "_smart_router_decision": final_decision,
        "_smart_router_details": details,
    }


def post_llm_call(
    session_id: str,
    user_message: str,
    assistant_response: str,
    conversation_history: list = None,
    model: str = None,
    platform: str = None,
    **kwargs
) -> Dict[str, Any]:
    """
    post_llm_call 钩子。

    在 LLM 调用完成后执行，直接在响应开头插入模型标识。
    不再依赖 LLM 添加标识（不稳定）。
    
    处理逻辑：
    1. 如果响应已有正确的标识 → 无需修改
    2. 如果响应有错误的标识（LLM 误加）→ 移除错误标识，添加正确标识
    3. 如果响应没有标识 → 直接添加正确标识

    参数（Hermes 传入）：
        session_id: 会话ID
        user_message: 用户消息
        assistant_response: LLM 生成的响应
        conversation_history: 对话历史
        model: 当前模型
        platform: 平台

    Returns:
        dict: 包含 'modified_response' 键来替换响应
    """
    global _last_decision

    # 获取决策
    decision = None
    with _last_decision_lock:
        decision = _last_decision
        # 清除决策，避免影响下一轮
        _last_decision = None

    if decision is None:
        # 如果没有决策，使用默认值
        decision = "cheap"

    # 确定模型标识
    if decision == "strong":
        correct_prefix = "[🧠] "
    else:
        correct_prefix = "[⚡] "

    # 如果响应为空或无效，直接返回
    if not assistant_response:
        return {}

    # 检查是否已有任何模型标识
    known_prefixes = ["[⚡] ", "[🧠] "]
    has_any_prefix = any(assistant_response.startswith(p) for p in known_prefixes)
    
    if has_any_prefix:
        # LLM 已经添加了标识（可能正确也可能错误）
        # 移除已有的标识（移除第一个已知前缀）
        response_content = assistant_response
        for p in known_prefixes:
            if response_content.startswith(p):
                response_content = response_content[len(p):]
                break
        
        # 添加正确的标识
        modified_response = correct_prefix + response_content
        logger.info(f"Replaced model prefix with '{correct_prefix}'")
        return {"modified_response": modified_response}
    else:
        # 没有标识，直接添加
        modified_response = correct_prefix + assistant_response
        logger.info(f"Inserted model prefix '{correct_prefix}' into response")
        return {"modified_response": modified_response}


def get_last_decision() -> Optional[Dict[str, Any]]:
    """获取最后一次路由决策（供 Gateway Hook 使用）"""
    global _router_engine
    if _router_engine is None:
        return None
    return {
        "decision": "cheap",
        "details": {}
    }
