"""
Smart Router Gateway Hook Handler
==================================
在 agent:start 事件时进行路由判断，并通过 patch resolve_turn_route 实现真正的模型切换。
不再依赖 Agent Plugin 的 pre_llm_call（因为时序问题）。
"""

import json
import logging
import os
import re
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

# 复杂问题关键词
STRONG_PATTERNS = [
    r"分布式|微服务|集群|负载均衡|容灾|备份",
    r"架构(设计|图|方案|模式)|系统设计|系统架构",
    r"中台|平台化|服务化|解耦",
    r"算法(实现|优化|设计)|排序|搜索|图论",
    r"并发|多线程|锁|同步|异步|队列",
    r"性能(优化|调优)|QPS|TPS|吞吐量",
    r"优化(.*)?(性能|查询|SQL|数据库)",
    r"(数据库|表)设计|ER图|范式|索引",
    r"分库分表|读写分离|主从复制|数据迁移",
    r"TCP|UDP|HTTP|WebSocket|RPC|gRPC",
    r"API(设计|接口)|RESTful|GraphQL",
    r"安全(漏洞|加固|渗透)|加密|签名|认证",
    r"监控|日志|告警|链路追踪|可观测性",
    r"区块链|AI|机器学习|深度学习|大模型",
    r"设计模式|23种|单例|工厂|观察者",
]

# Meta 快速模式
META_FAST_PATTERNS = [
    r"把.*(代码|需求|报错|日志|信息).*(贴|发|给|丢|给我)",
    r"(把|将).*(代码|报错|信息).*(发|贴|给).*(看看|我看看|我看下|我看一眼)",
    r"(python|java|javascript|node|golang|rust|c\+\+|go|rust)\s*(还是|或|或者)",
    r"(哪个|什么)\s*(语言|框架|技术)",
    r"(帮我|给我|帮我|给我).*(看看|检查一下|看看这个|看看这个代码|看看这个需求)",
    r"(你要|想要|准备).*(写|搞|做).*(哪块|什么|哪个).*(编程|代码|项目)",
]


def _has_strong_keywords(message: str) -> bool:
    """检查是否包含复杂问题关键词"""
    for pattern in STRONG_PATTERNS:
        if re.search(pattern, message, re.IGNORECASE):
            return True
    return False


def _is_meta_fast(message: str) -> bool:
    """检查是否命中 meta 快速模式"""
    for pattern in META_FAST_PATTERNS:
        if re.search(pattern, message, re.IGNORECASE):
            return True
    return False


def set_routing_decision(decision: str, details: dict = None):
    """写入路由决策到文件"""
    with _lock:
        data = {
            "decision": decision,
            "details": details or {},
            "pid": os.getpid()
        }
        with open(_decision_file, "w") as f:
            json.dump(data, f)
    logger.debug(f"Routing decision written: {decision}")


def get_routing_decision() -> Optional[Dict[str, Any]]:
    """获取路由决策（从文件读取）"""
    if not os.path.exists(_decision_file):
        return None
    
    with _lock:
        try:
            with open(_decision_file, "r") as f:
                data = json.load(f)
            if data.get("decision"):
                return data
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
    这个 patch 会检查决策文件并据此覆盖模型选择。
    """
    global _patch_applied
    
    if _patch_applied:
        return
    
    try:
        from agent.smart_model_routing import resolve_turn_route as _original_func
        from functools import wraps
        
        @wraps(_original_func)
        def _patched_resolve_turn_route(
            user_message: str,
            routing_config: Any,
            primary: Dict[str, Any]
        ) -> Dict[str, Any]:
            # 调用原始函数获取基础配置
            result = _original_func(user_message, routing_config, primary)
            
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
        
        # 应用 patch
        import agent.smart_model_routing as _sr_module
        _sr_module.resolve_turn_route = _patched_resolve_turn_route
        
        _patch_applied = True
        logger.info("Smart Router patch applied to resolve_turn_route")
        
    except Exception as e:
        logger.error(f"Failed to apply router patch: {e}")


def handle(event_type: str, context: dict) -> None:
    """
    Gateway Hook 入口函数。
    
    在 agent:start 事件时进行路由判断，写入决策文件。
    这个时序是正确的：handle 在 resolve_turn_route 之前执行。
    
    Args:
        event_type: 事件类型 (如 "agent:start")
        context: 事件上下文，包含 message 字段
    """
    if event_type != "agent:start":
        return
    
    # 应用 patch（如果还没应用）
    _apply_router_patch()
    
    # 获取用户消息
    message = context.get("message", "")
    if not message:
        logger.debug("No message in hook context")
        return
    
    # 进行路由判断
    if _is_meta_fast(message):
        decision = "cheap"
        reason = "meta_fast"
    elif _has_strong_keywords(message):
        decision = "strong"
        reason = "strong_keyword"
    else:
        decision = "cheap"
        reason = "default"
    
    # 写入决策文件
    set_routing_decision(decision, {"reason": reason, "message_preview": message[:50]})
    logger.info(f"Hook handle: decision={decision}, reason={reason}, message={message[:50]}...")
