"""
Smart Router Gateway Hook Handler
==================================
在 agent:start 事件时 patch resolve_turn_route 实现真正的模型切换。
通过文件进行跨进程通信。
"""

import asyncio
import json
import logging
import os
import sys
import tempfile
import threading
from typing import Any, Dict, Optional

logger = logging.getLogger(__name__)

# 确保插件目录在 Python 路径中
_plugin_dir = os.path.join(os.path.dirname(__file__), "..", "..", "plugins", "smart_router")
if _plugin_dir not in sys.path:
    sys.path.insert(0, _plugin_dir)

# 跨进程通信文件路径
_decision_file = os.path.join(tempfile.gettempdir(), "hermes_smart_router_decision.json")
_lock = threading.Lock()

# 是否已经 patch 了 resolve_turn_route
_patch_applied = False


def set_routing_decision(decision: str, details: dict = None):
    """
    设置路由决策（供 Agent Plugin 调用）。
    通过写入临时文件进行跨进程通信。
    """
    with _lock:
        data = {
            "decision": decision,
            "details": details or {},
            "pid": os.getpid()
        }
        with open(_decision_file, "w") as f:
            json.dump(data, f)
    logger.debug(f"Routing decision written to file: {decision}")


def get_routing_decision() -> Optional[Dict[str, Any]]:
    """获取路由决策（从文件读取）"""
    if not os.path.exists(_decision_file):
        return None
    
    with _lock:
        try:
            # 检查文件是否来自当前进程
            with open(_decision_file, "r") as f:
                data = json.load(f)
            
            # 如果文件来自其他进程（旧的决策），忽略
            if data.get("pid") == os.getpid():
                return data
            else:
                return None
        except (json.JSONDecodeError, FileNotFoundError):
            return None


def clear_routing_decision():
    """清除路由决策"""
    with _lock:
        if os.path.exists(_decision_file):
            os.remove(_decision_file)


def _get_model_from_decision(decision: str) -> tuple:
    """根据决策返回 (model, provider)"""
    if decision == "strong":
        return "glm-5.1", "custom"
    else:
        return "MiniMax-M2.7", "minimax-cn"


def _apply_router_patch():
    """
    应用 resolve_turn_route 的 monkey patch。
    这个 patch 会检查 _routing_decision 并据此覆盖模型选择。
    """
    global _patch_applied
    
    if _patch_applied:
        return
    
    try:
        from agent.smart_model_routing import resolve_turn_route as _original_func
        from functools import wraps
        
        @wraps(_original_func)
        async def _patched_resolve_turn_route(
            user_message: str,
            routing_config: Any,
            primary: Dict[str, Any]
        ) -> Dict[str, Any]:
            # 调用原始函数获取基础配置
            result = await _original_func(user_message, routing_config, primary)
            
            # 检查是否有路由决策（从文件读取）
            decision_data = get_routing_decision()
            
            if decision_data is not None:
                decision = decision_data.get("decision", "cheap")
                model, provider = _get_model_from_decision(decision)
                
                logger.info(f"Router patch applied: decision={decision}, model={model}")
                
                # 覆盖模型配置
                result["model"] = model
                result["provider"] = provider
                
                # 清除决策文件（每个回合只用一次）
                clear_routing_decision()
            
            return result
        
        # 应用 patch：替换模块中的函数
        import agent.smart_model_routing as _sr_module
        _sr_module.resolve_turn_route = _patched_resolve_turn_route
        
        _patch_applied = True
        logger.info("Smart Router patch applied to resolve_turn_route")
        
    except Exception as e:
        logger.error(f"Failed to apply router patch: {e}")


def handle(event_type: str, context: dict) -> None:
    """
    Gateway Hook 入口函数。
    
    处理 agent:start 事件，触发模型路由 patch。
    
    Args:
        event_type: 事件类型 (如 "agent:start")
        context: 事件上下文
    """
    if event_type != "agent:start":
        return
    
    # 应用 patch（如果还没应用）
    _apply_router_patch()
    
    # 获取路由决策（如果有）
    decision = get_routing_decision()
    
    if decision is None:
        logger.debug("No routing decision found in hook handle")
    else:
        logger.info(f"Hook handle: decision={decision.get('decision')}")
