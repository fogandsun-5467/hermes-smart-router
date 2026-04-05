"""
Router Engine Module
===================
核心路由引擎，实现五层判断逻辑。
"""

import asyncio
import json
import logging
import re
from datetime import datetime
from typing import Any, Dict, List, Optional, Tuple

from .context_tracker import ContextTracker
from .memory_learner import (
    extract_keywords,
    get_pending_keywords,
    has_experience,
    memory_entries,
    record_decision,
    search_experience,
    should_form_experience,
    load_memory_entries,
    write_experience_async,
    merge_keywords_async,
)

logger = logging.getLogger(__name__)

# ============================================================================
# 第一层：Meta 快速模式
# ============================================================================

META_FAST_PATTERNS = [
    # 粘贴已有内容
    r"把.*(代码|需求|报错|日志|信息).*(贴|发|给|丢|给我)",
    r"(把|将).*(代码|报错|信息).*(发|贴|给).*(看看|我看看|我看下|我看一眼)",
    # 选择问题（语言/技术选择）
    r"(python|java|javascript|node|golang|rust|c\+\+|go|rust)\s*(还是|或|或者)",
    r"(哪个|什么)\s*(语言|框架|技术)",
    # 元级别问题
    r"(帮我|给我|帮我|给我).*(看看|检查一下|看看这个|看看这个代码|看看这个需求)",
    r"(你要|想要|准备).*(写|搞|做).*(哪块|什么|哪个).*(编程|代码|项目)",
]

# ============================================================================
# 复杂问题关键词（直接使用专家模型）
# ============================================================================

STRONG_PATTERNS = [
    # 架构与系统设计
    r"分布式|微服务|集群|负载均衡|容灾|备份",
    r"架构(设计|图|方案|模式)|系统设计|系统架构",
    r"中台|平台化|服务化|解耦",
    # 算法与性能
    r"算法(实现|优化|设计)|排序|搜索|图论",
    r"并发|多线程|锁|同步|异步|队列",
    r"性能(优化|调优)|QPS|TPS|吞吐量",
    r"优化(.*)?(性能|查询|SQL|数据库)",
    # 数据库
    r"(数据库|表)设计|ER图|范式|索引",
    r"分库分表|读写分离|主从复制|数据迁移",
    # 协议与网络
    r"TCP|UDP|HTTP|WebSocket|RPC|gRPC",
    r"API(设计|接口)|RESTful|GraphQL",
    # 安全与运维
    r"安全(漏洞|加固|渗透)|加密|签名|认证",
    r"监控|日志|告警|链路追踪|可观测性",
    # 高级概念
    r"区块链|AI|机器学习|深度学习|大模型",
    r"设计模式|23种|单例|工厂|观察者",
]


def is_meta_fast(message: str) -> bool:
    """检查是否命中 meta 快速模式"""
    for pattern in META_FAST_PATTERNS:
        if re.search(pattern, message, re.IGNORECASE):
            return True
    return False


def has_strong_keywords(message: str) -> bool:
    """检查是否包含复杂问题关键词"""
    for pattern in STRONG_PATTERNS:
        if re.search(pattern, message, re.IGNORECASE):
            return True
    return False


# ============================================================================
# 第五层：高峰期守门员
# ============================================================================

PEAK_HOURS_CONFIG = {
    "enabled": True,
    "start": 14,
    "end": 18,
    "peak_threshold": 7,  # 高于这个分数的任务高峰期也保留专家
}


def is_peak_hours() -> bool:
    """判断当前是否在高峰期"""
    if not PEAK_HOURS_CONFIG["enabled"]:
        return False
    now = datetime.now()
    return PEAK_HOURS_CONFIG["start"] <= now.hour < PEAK_HOURS_CONFIG["end"]


def peak_hours_gate(decision: str, score: int) -> Tuple[str, str]:
    """
    高峰期守门员。
    始终生效，不被经验跳过。

    Returns:
        (final_decision, override_reason)
    """
    if not is_peak_hours():
        return decision, ""

    if decision == "cheap":
        return decision, ""

    # decision == "strong"
    if score >= PEAK_HOURS_CONFIG["peak_threshold"]:
        return decision, ""  # 高优先级保留专家
    else:
        return "cheap", "peak_hours_downgrade"


# ============================================================================
# 第四层：LLM 评分
# ============================================================================

LLM_JUDGE_PROMPT = """你是一个消息复杂度判断助手。
分析用户输入，判断其所属类别和难度分数。

## 输出格式
输出一个 JSON 对象：
{"score": 1-9, "category": "code|math|roleplay|chat|general", "reasoning": "简短推理"}

## 评分标准

### code（代码/架构）
- 1-2: 语法问题、简单脚本、单函数
- 3-4: 算法实现、调试、标准API调用
- 5-6: 多文件重构、基础架构、标准项目
- 7-8: 分布式系统、微服务、高并发设计
- 9: 百万级并发、金融级系统、事务

### math（数学/推理）
- 1-2: 算术、单位换算、简单公式
- 3-4: 代数、几何证明、概率
- 5-6: 微积分、线性代数、统计分析
- 7-8: 多变量优化、偏微分方程、数论
- 9: 前沿数学问题、复杂证明

### chat（通用对话）
- 1-2: 问候、感谢、简单确认
- 3-4: 知识问答、概念解释
- 5-6: 深度讨论、观点分析
- 7-8: 跨领域综合、专业咨询
- 9: 复杂决策支持、多维分析

### roleplay（角色扮演）
- 1-2: 简单问候、固定回复
- 3-4: 基础对话、单场景交互
- 5-6: 复杂剧情、多角色协调
- 7-8: 深度角色塑造、情感细腻
- 9: 专业级创作、世界观构建

### general（其他）
- 1-3: 简单、单步、标准化
- 4-6: 中等复杂度，需综合
- 7-9: 高复杂度，跨领域

## 关键规则
1. 先确定 category，再按该维度评分
2. 关键词只是辅助，不能单凭关键词定分数
3. "5,3,1排序" 这种简单问题 → 3分左右
4. "设计分布式数据库" → 7分以上

用户输入：{message}

输出 JSON："""


async def llm_judge(message: str, call_provider_func) -> Dict[str, Any]:
    """调用 LLM 进行多维评分"""
    try:
        response = await call_provider_func(
            prompt=LLM_JUDGE_PROMPT.format(message=message),
            system_prompt="你是一个 JSON 助手，只输出 JSON，不要输出任何解释。",
        )

        result = json.loads(response)
        score = int(result.get("score", 5))
        category = result.get("category", "general")
        reasoning = result.get("reasoning", "")

        # decision 根据分数决定
        decision = "strong" if score >= 5 else "cheap"

        return {
            "score": score,
            "category": category,
            "decision": decision,
            "reasoning": reasoning,
        }
    except Exception as e:
        logger.error(f"LLM judge failed: {e}")
        return {"score": 5, "category": "general", "decision": "cheap", "reasoning": "parse_failed"}


# ============================================================================
# 核心路由引擎
# ============================================================================

class RouterEngine:
    """核心路由引擎，实现五层判断"""

    def __init__(
        self,
        config: Dict[str, Any],
        memory_file: str,
        call_provider_func=None
    ):
        self.config = config
        self.memory_file = memory_file
        self.call_provider_func = call_provider_func
        self.context_tracker = ContextTracker()

        # 更新高峰期配置
        peak_config = config.get("peak_hours", {})
        if peak_config:
            PEAK_HOURS_CONFIG.update(peak_config)

        # 加载已有经验
        load_memory_entries(memory_file)

        # 模型切换提醒状态
        self._is_first_call = True  # 首次调用标记
        self._last_decision = None  # 上一次的决策

    async def judge(self, message: str) -> Tuple[str, str, Dict[str, Any]]:
        """
        执行五层判断。

        Returns:
            (final_decision, reason, details)
        """
        # ── 第一层：meta 快速模式 ─────
        if is_meta_fast(message):
            logger.info(f"Meta fast mode matched: {message[:50]}...")
            return "cheap", "meta_fast", {
                "source": "meta_fast",
                "decision": "cheap",
                "score": 0,
                "category": "meta",
            }

        # ── 1.5层：复杂问题关键词（无需LLM，直接判断）─────
        if has_strong_keywords(message):
            logger.info(f"Strong keywords matched: {message[:50]}...")
            # 复杂问题直接使用专家模型
            decision = "strong"
            category = "code"
            score = 7  # 高复杂度默认分数
            
            # 记录到暂存区（用于形成经验）
            keywords = extract_keywords(message)
            record_decision(category, score, decision, keywords)
            
            # 异步写入经验
            if should_form_experience(category, score, decision):
                asyncio.create_task(write_experience_async(
                    keywords, decision, "strong_keyword",
                    score, category, self.memory_file
                ))
            
            final_decision, override = peak_hours_gate(decision, score)
            
            # 更新上下文追踪
            self.context_tracker.update(message, category, score, decision, "unrelated")
            
            return final_decision, "strong_keyword", {
                "source": "strong_keyword",
                "decision": decision,
                "score": score,
                "category": category,
                "final_decision": final_decision,
                "peak_override": override,
            }

        # ── 第二层：session 内上下文关系 ─────
        relation, last_ctx = self.context_tracker.detect_relation(message)
        logger.debug(f"Context relation: {relation}")

        # continue 需要强制用上轮分数/decision
        if relation == "continue" and last_ctx:
            final_decision, override = peak_hours_gate(last_ctx.decision, last_ctx.score)
            # 更新上下文追踪
            self.context_tracker.update(
                message, last_ctx.category, last_ctx.score, last_ctx.decision, relation
            )
            logger.info(f"Continue mode: using last ctx score={last_ctx.score}, decision={last_ctx.decision}")
            return final_decision, f"context_continue", {
                "source": "context_continue",
                "decision": last_ctx.decision,
                "score": last_ctx.score,
                "category": last_ctx.category,
                "final_decision": final_decision,
                "peak_override": override,
            }

        # ── 第三层：经验匹配 + 第四层：LLM 评分 ─────
        # 经验匹配需要 category 作为参数，所以先调用 LLM
        if self.call_provider_func:
            llm_result = await llm_judge(message, self.call_provider_func)
        else:
            # 如果没有 LLM 调用函数，使用默认判断
            llm_result = {
                "score": 5,
                "category": "general",
                "decision": "cheap",
                "reasoning": "no_llm_available"
            }

        category = llm_result["category"]
        score = llm_result["score"]

        # 经验匹配（关键词 + category + score同分段±1 + decision）
        matched_exp = search_experience(message, memory_entries, category, score)

        if matched_exp:
            # 经验匹配成功，直接采纳
            decision = matched_exp["decision"]
            logger.info(f"Experience matched: {matched_exp['keywords'][:5]}... -> {decision}")

            # 经验命中也要记录到暂存区（累积计数，形成经验）
            keywords = extract_keywords(message)
            record_decision(category, matched_exp["score"], decision, keywords)

            # 检查是否形成经验
            if should_form_experience(category, matched_exp["score"], decision):
                all_keywords = get_pending_keywords(category, matched_exp["score"], decision)
                if has_experience(category, matched_exp["score"], decision, memory_entries):
                    asyncio.create_task(merge_keywords_async(
                        all_keywords, category, matched_exp["score"], decision, self.memory_file
                    ))
                else:
                    asyncio.create_task(write_experience_async(
                        all_keywords, decision, matched_exp["reason"],
                        matched_exp["score"], category, self.memory_file
                    ))

            final_decision, override = peak_hours_gate(decision, matched_exp["score"])

            # 更新上下文追踪
            self.context_tracker.update(message, category, matched_exp["score"], decision, relation)

            return final_decision, f"experience:{matched_exp['reason']}", {
                "source": "experience",
                "decision": decision,
                "score": matched_exp["score"],
                "category": category,
                "final_decision": final_decision,
                "peak_override": override,
            }

        # ── 第四层：LLM 评分（无经验时）─────
        decision = llm_result["decision"]
        reasoning = llm_result["reasoning"]
        logger.info(f"LLM judged: score={score}, category={category}, decision={decision}")

        # 记录到暂存区（用于形成经验）
        keywords = extract_keywords(message)
        record_decision(category, score, decision, keywords)

        # 检查是否形成经验
        if should_form_experience(category, score, decision):
            all_keywords = get_pending_keywords(category, score, decision)
            if has_experience(category, score, decision, memory_entries):
                asyncio.create_task(merge_keywords_async(
                    all_keywords, category, score, decision, self.memory_file
                ))
            else:
                asyncio.create_task(write_experience_async(
                    all_keywords, decision, reasoning, score, category, self.memory_file
                ))

        # ── 第五层：高峰期守门员 ─────
        final_decision, override = peak_hours_gate(decision, score)

        # 更新上下文追踪
        self.context_tracker.update(message, category, score, decision, relation)

        return final_decision, f"llm_judge:{reasoning}", {
            "source": "llm",
            "decision": decision,
            "score": score,
            "category": category,
            "final_decision": final_decision,
            "peak_override": override,
        }

    def get_routing_hint(self, final_decision: str, details: Dict[str, Any]) -> str:
        """
        生成路由提示，用于注入到 system prompt。

        注意：模型标识现在由 post_llm_call 钩子直接插入，不再依赖 LLM。
        此提示仅用于告知当前使用的模型，不要求 LLM 添加标识。
        """
        model_names = {
            "strong": "专家模型(glm-5.1)",
            "cheap": "轻量模型(MiniMax-M2.7)"
        }

        current_model = model_names.get(final_decision, final_decision)
        emoji = "🧠" if final_decision == "strong" else "⚡"

        if self._is_first_call:
            # 首次调用，显示完整提示
            self._is_first_call = False
            self._last_decision = final_decision
            return f"【路由提示】当前使用{current_model}处理（{emoji}标识已由系统自动添加）。"
        
        if final_decision == self._last_decision:
            # 同一模型，只显示模型名称
            return f"【路由提示】当前使用{current_model}处理。"
        else:
            # 切换模型
            prev_model = model_names.get(self._last_decision, self._last_decision)
            self._last_decision = final_decision
            return f"【路由提示】当前任务已从{prev_model}切换至{current_model}。"
