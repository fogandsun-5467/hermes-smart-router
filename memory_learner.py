"""
Memory Learner Module
====================
记忆学习模块，负责经验匹配和经验形成。
"""

import asyncio
import logging
import os
import re
from typing import Dict, List, Optional

logger = logging.getLogger(__name__)

# 暂存区：key = "category:segment:decision", value = {count: N, keywords: [...]}
decision_cache: Dict[str, Dict] = {}

# 内存中的经验条目（从 MEMORY.md 加载）
memory_entries: List[str] = []


def get_score_segment(score: int) -> str:
    """获取分数分段"""
    if 1 <= score <= 3:
        return "1-3"
    elif 4 <= score <= 6:
        return "4-6"
    elif 7 <= score <= 9:
        return "7-9"
    return "unknown"


def extract_keywords(message: str) -> List[str]:
    """
    从消息中提取关键词。
    简单实现：分词 + 停用词过滤 + 回退到整个消息。
    """
    # 停用词集合
    stopwords = {
        "的", "了", "在", "是", "我", "你", "他", "她", "它",
        "这", "那", "吗", "呢", "吧", "啊", "哦", "嗯",
        "什么", "怎么", "如何", "为什么", "有没有", "一下",
        "看看", "帮我", "给我", "我想", "我要",
        "请", "能", "可以", "帮忙"
    }

    # 预处理：去除常见标点和空格
    import re
    cleaned = re.sub(r"[\s，。！？、：；\"\"''（）《》【】,!?.:;\"'()\[\]{}]+", " ", message)
    cleaned = cleaned.strip()

    # 简单分词：按空格和常见分隔符
    words = cleaned.split()

    # 过滤停用词和短词
    result = []
    for w in words:
        w_lower = w.lower()
        # 跳过停用词
        if w_lower in stopwords:
            continue
        # 跳过长度小于2的词
        if len(w_lower) < 2:
            continue
        result.append(w_lower)

    # 如果结果为空，使用整个消息作为关键词（转为小写）
    if not result:
        result.append(message.lower())

    return list(set(result))  # 去重


def parse_experience_entry(entry: str) -> Optional[Dict]:
    """
    解析 § 路由经验：[关键词] → 模型 (reason, score=N, category=X, segment=Y)
    """
    pattern = r"§ 路由经验：\[([^\]]+)\]\s*→\s*(strong|cheap)\s*\(([^,]+), score=(\d+), (\w+), segment=(\w+)\)"
    match = re.search(pattern, entry)
    if not match:
        return None

    keywords = [k.strip() for k in match.group(1).split(",")]
    return {
        "keywords": keywords,
        "decision": match.group(2),
        "reason": match.group(3).strip(),
        "score": int(match.group(4)),
        "category": match.group(5),
        "segment": match.group(6),
    }


def search_experience(
    message: str,
    memory_entries: List[str],
    category: str,
    score: int,
) -> Optional[Dict]:
    """
    在 MEMORY.md 历史经验中搜索匹配的经验。

    匹配条件：
    1. 关键词有重叠
    2. category 相同
    3. score 同分段（|score - exp_score| ≤ 1）
    """
    if not memory_entries:
        return None

    msg_keywords = extract_keywords(message)
    if not msg_keywords:
        return None

    current_segment = get_score_segment(score)

    best_match = None
    best_match_count = 0

    for entry in memory_entries:
        if not entry.startswith("§ 路由经验："):
            continue

        exp = parse_experience_entry(entry)
        if not exp:
            continue

        # 检查 category
        if exp["category"] != category:
            continue

        # 检查 score 同分段
        exp_segment = exp.get("segment", get_score_segment(exp["score"]))
        if exp_segment != current_segment:
            continue

        # 检查 |score - exp_score| ≤ 1
        if abs(score - exp["score"]) > 1:
            continue

        # 计算关键词重叠
        overlap = set(msg_keywords) & set(exp["keywords"])
        if len(overlap) == 0:
            continue

        # 选择重叠最多的
        if len(overlap) > best_match_count:
            best_match_count = len(overlap)
            best_match = exp

    if best_match:
        return best_match
    return None


def should_form_experience(category: str, score: int, decision: str) -> bool:
    """检查是否达到形成经验的条件（≥3次）"""
    segment = get_score_segment(score)
    key = f"{category}:{segment}:{decision}"
    record = decision_cache.get(key, {"count": 0})
    return record["count"] >= 3


def record_decision(category: str, score: int, decision: str, keywords: List[str]):
    """记录判断到暂存区，同时累积关键词"""
    segment = get_score_segment(score)
    key = f"{category}:{segment}:{decision}"
    record = decision_cache.get(key, {"count": 0, "keywords": []})
    record["count"] += 1
    if keywords:
        record["keywords"] = list(set(record["keywords"]) | set(keywords))
    decision_cache[key] = record


def get_pending_keywords(category: str, score: int, decision: str) -> List[str]:
    """获取暂存区累积的所有关键词"""
    segment = get_score_segment(score)
    key = f"{category}:{segment}:{decision}"
    record = decision_cache.get(key, {"count": 0, "keywords": []})
    return record.get("keywords", [])


def has_experience(category: str, score: int, decision: str, memory_entries: List[str]) -> bool:
    """检查是否已有同类经验"""
    segment = get_score_segment(score)
    for entry in memory_entries:
        if not entry.startswith("§ 路由经验："):
            continue
        exp = parse_experience_entry(entry)
        if exp and exp["category"] == category and exp["segment"] == segment and exp["decision"] == decision:
            return True
    return False


def load_memory_entries(memory_file: str) -> List[str]:
    """从 MEMORY.md 加载经验条目"""
    global memory_entries
    try:
        if os.path.exists(memory_file):
            with open(memory_file, "r", encoding="utf-8") as f:
                content = f.read()
                # 提取所有 § 路由经验 开头的行
                memory_entries = [line.strip() for line in content.split("\n") if line.strip().startswith("§ 路由经验：")]
                logger.info(f"Loaded {len(memory_entries)} memory entries")
        else:
            memory_entries = []
            logger.info("No memory file found, starting fresh")
    except Exception as e:
        logger.error(f"Failed to load memory entries: {e}")
        memory_entries = []
    return memory_entries


async def write_experience_async(
    keywords: List[str],
    decision: str,
    reason: str,
    score: int,
    category: str,
    memory_file: str
):
    """异步写入经验到 MEMORY.md"""
    segment = get_score_segment(score)
    entry = f"§ 路由经验：[{', '.join(keywords)}] → {decision} ({reason}, score={score}, {category}, segment={segment})"

    try:
        # 读取现有内容
        existing_content = ""
        if os.path.exists(memory_file):
            with open(memory_file, "r", encoding="utf-8") as f:
                existing_content = f.read()

        # 追加新经验
        with open(memory_file, "a", encoding="utf-8") as f:
            if existing_content and not existing_content.endswith("\n"):
                f.write("\n")
            f.write(entry + "\n")

        # 更新内存中的条目
        global memory_entries
        memory_entries.append(entry)

        logger.info(f"Wrote new experience: {entry[:100]}...")
    except Exception as e:
        logger.error(f"Failed to write experience: {e}")


async def merge_keywords_async(
    keywords: List[str],
    category: str,
    score: int,
    decision: str,
    memory_file: str
):
    """合并关键词到已有经验"""
    segment = get_score_segment(score)

    try:
        if not os.path.exists(memory_file):
            return

        with open(memory_file, "r", encoding="utf-8") as f:
            lines = f.readlines()

        # 找到并更新对应的经验行
        updated_lines = []
        for line in lines:
            exp = parse_experience_entry(line.strip())
            if exp and exp["category"] == category and exp["segment"] == segment and exp["decision"] == decision:
                # 合并关键词
                all_keywords = list(set(exp["keywords"]) | set(keywords))
                new_entry = f"§ 路由经验：[{', '.join(all_keywords)}] → {decision} ({exp['reason']}, score={exp['score']}, {category}, segment={segment})"
                updated_lines.append(new_entry + "\n")
                logger.info(f"Merged keywords into existing experience")
            else:
                updated_lines.append(line)

        # 写回文件
        with open(memory_file, "w", encoding="utf-8") as f:
            f.writelines(updated_lines)

        # 更新内存
        global memory_entries
        memory_entries = [parse_experience_entry(l.strip()) for l in updated_lines if l.strip().startswith("§ 路由经验：")]

    except Exception as e:
        logger.error(f"Failed to merge keywords: {e}")
