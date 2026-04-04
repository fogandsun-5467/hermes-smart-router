# Hermes Smart Router

Hermes 智能模型路由插件，实现五层判断逻辑。

## 功能特性

- **Meta 快速模式**：识别"把代码发我看看"等元级别问题，快速返回轻量模型
- **Session 内上下文关系**：检测 continue/downgrade/unrelated 上下文关系
- **经验匹配**：基于 MEMORY.md 的历史经验匹配
- **LLM 评分**：对消息进行多维评分（code/math/roleplay/chat/general）
- **高峰期守门员**：在高峰期自动降级非高优先级任务

## 目录结构

```
smart_router/
├── __init__.py          # 插件入口
├── router_engine.py     # 核心路由引擎（五层判断）
├── context_tracker.py    # Session 内上下文追踪
├── memory_learner.py     # 记忆学习模块
├── config.yaml          # 插件配置
├── plugin.yaml          # 插件清单
└── requirements.txt     # 依赖

hooks/smart_router/
├── handler.py           # Gateway Hook 处理器
└── HOOK.yaml           # Hook 清单
```

## 安装

### 1. 安装插件

```bash
# 复制插件到 Hermes 插件目录
cp -r smart_router ~/.hermes/plugins/
cp -r hooks/smart_router ~/.hermes/hooks/
```

### 2. 配置

编辑 `~/.hermes/plugins/smart_router/config.yaml`：

```yaml
# 高峰期配置
peak_hours:
  enabled: true
  start: 14      # 开始时间（小时）
  end: 18        # 结束时间（小时）
  peak_threshold: 7  # 高于此分数的任务在高峰期也保留专家模型

# 模型配置
models:
  strong: "glm-5.1"      # 专家模型
  cheap: "MiniMax-M2.7"  # 轻量模型
```

### 3. 启用插件

在 Hermes 配置中启用 `smart_router` 插件。

## 使用方式

插件在 `pre_llm_call` 阶段自动判断，不需要手动调用。

## 架构

### 五层判断流程

```
用户消息
    │
    ▼
第一层：Meta快速模式 ── 命中 ──→ cheap ✅
    │ 否
    ▼
第二层：Session上下文关系
    ├─ continue ──→ 上轮分数 + return ✅
    ├─ downgrade ──→ 继续第三层
    └─ unrelated ──→ 继续第三层
    │
    ▼
第三层：经验匹配 ── 命中 ──→ 采纳经验 + return ✅
    │ 否
    ▼
第四层：LLM评分 ──→ record_decision + check_form ✅
    │
    ▼
第五层：高峰期守门员 + update ✅
```

### 评分维度

| Category | 1-3 分 | 4-6 分 | 7-9 分 |
|----------|--------|--------|--------|
| code | 语法问题、简单脚本 | 算法实现、标准API | 分布式系统、微服务 |
| math | 算术、简单公式 | 微积分、线性代数 | 前沿数学问题 |
| chat | 问候、简单确认 | 深度讨论、观点分析 | 复杂决策支持 |
| roleplay | 简单问候 | 复杂剧情、多角色 | 专业级创作 |

## 版本历史

| 版本 | 更新内容 |
|------|----------|
| v3.2 | 修复经验命中时记录暂存区等逻辑漏洞 |

## License

MIT
