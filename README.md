# 呼叫中心合规转写与预警服务

面向呼叫中心合规岗的后端服务，接收客服系统推送的通话录音，自动完成语音转写和合规风险检测，返回可质检文本和风险片段。

## 功能特性

- **录音提交接口**：接收录音地址、坐席编号、通话类型，异步返回任务号
- **转写状态查询**：按任务号查询进度，完成后获取带时间戳的对话文本
- **风险片段检测**：自动识别以下合规风险并给出建议等级
  - 🚫 私下加微信 / 引导添加私人联系方式
  - ⚠️ 保证收益 / 承诺保本保息（金融合规）
  - 🚫 辱骂客户 / 服务禁语
  - ⚠️ 遗漏录音告知（首句合规）

## 技术栈

- **框架**：FastAPI 0.104 + Pydantic 2.5
- **运行时**：Python 3.9+
- **ASR**：当前为模拟转写，预留接口可接入阿里云/腾讯云/百度智能云等真实 ASR 服务

## 快速开始

### 方式一：启动脚本（Windows）

双击运行 `start.bat`，脚本会自动创建虚拟环境、安装依赖并启动服务。

### 方式二：手动启动

```bash
# 创建虚拟环境
python -m venv venv
venv\Scripts\activate      # Windows
# source venv/bin/activate  # Linux/Mac

# 安装依赖
pip install -r requirements.txt

# 启动服务
uvicorn app.main:app --host 0.0.0.0 --port 8000 --reload
```

### 访问文档

服务启动后访问：
- Swagger UI：http://localhost:8000/docs （交互式调试）
- ReDoc：http://localhost:8000/redoc

## API 接口

### 1. 提交录音转写任务

```http
POST /api/v1/tasks
Content-Type: application/json

{
  "recording_url": "https://example.com/recordings/20240115_001.wav",
  "agent_id": "AGENT_8823",
  "call_type": "outbound",
  "call_id": "CALL_20240115_001",
  "customer_id": "CUST_5521"
}
```

**响应：**
```json
{
  "task_id": "TASK_20240115_143022_ABC123",
  "status": "pending",
  "submitted_at": "2024-01-15T14:30:22.123456",
  "agent_id": "AGENT_8823"
}
```

### 2. 查询转写状态与文本

```http
GET /api/v1/tasks/{task_id}
```

**响应（完成状态）：**
```json
{
  "task_id": "TASK_20240115_143022_ABC123",
  "status": "completed",
  "agent_id": "AGENT_8823",
  "call_type": "outbound",
  "segments": [
    {"start_time": 0.0, "end_time": 3.5, "speaker": "agent", "text": "您好，这里是XX客服中心，请问有什么可以帮您？"},
    {"start_time": 3.8, "end_time": 8.2, "speaker": "customer", "text": "你好，我想咨询一下理财产品。"}
  ],
  "has_risk": true
}
```

**任务状态流转：**
`pending`（排队中）→ `transcribing`（转写中）→ `analyzing`（合规分析中）→ `completed`（已完成）

### 3. 获取风险检测结果

```http
GET /api/v1/tasks/{task_id}/risks
```

**响应示例：**
```json
{
  "task_id": "TASK_20240115_143022_ABC123",
  "status": "completed",
  "agent_id": "AGENT_8823",
  "total_risks": 2,
  "high_risk_count": 1,
  "medium_risk_count": 1,
  "low_risk_count": 0,
  "risks": [
    {
      "segment_index": 5,
      "original_text": "您加我微信吧，微信号是abc123",
      "speaker": "agent",
      "start_time": 45.2,
      "end_time": 52.8,
      "risk_category": "wechat_solicitation",
      "risk_level": "high",
      "matched_keywords": ["加我微信", "微信号"],
      "suggestion": "坐席违规引导客户添加私人微信，请立即约谈并加强培训。"
    }
  ]
}
```

**风险等级说明：**
| 等级 | 含义 | 建议处理 |
|------|------|----------|
| `critical` | 严重违规 | 立即人工复核，启动合规调查 |
| `high` | 高风险 | 推入人工复核队列，当日处理 |
| `medium` | 中风险 | 批量抽检，周度汇总 |
| `low` | 低风险 | 系统记录，月度统计 |

## 项目结构

```
.
├── app/
│   ├── __init__.py
│   ├── main.py              # FastAPI 应用入口 & API 路由
│   ├── config.py            # 配置管理
│   ├── schemas.py           # Pydantic 数据模型
│   ├── storage.py           # 任务存储层（内存实现，可扩展 Redis/DB）
│   ├── asr_service.py       # ASR 语音转写服务（模拟实现）
│   ├── compliance_engine.py # 合规风险检测引擎
│   └── task_processor.py    # 任务调度（协调转写+检测）
├── requirements.txt
├── start.bat                # Windows 一键启动
└── README.md
```

## 扩展说明

### 接入真实 ASR 服务

编辑 `app/asr_service.py`，实现 `ASRService` 抽象类的 `transcribe` 方法，在 `get_asr_service()` 中返回你的实现。支持的主流服务商：
- 阿里云智能语音交互
- 腾讯云 ASR
- 百度智能云语音识别
- 科大讯飞听写

### 切换持久化存储

编辑 `app/storage.py`，继承 `TaskStorage` 抽象类实现 Redis / MySQL / MongoDB 版本，在 `get_storage()` 中返回。

### 扩展风险规则

在 `app/compliance_engine.py` 的 `RISK_RULES` 列表中添加新的 `RiskRule` 实例即可，支持关键词、说话人过滤、否定词排除等规则配置。

## 注意事项

- 当前版本使用内存存储，服务重启后任务数据会丢失，生产环境请替换为持久化存储
- Mock 模式下转写为随机模拟数据，仅用于接口联调和功能演示
- 生产部署建议使用 Gunicorn + Uvicorn Worker，并配置适当的超时和并发参数
