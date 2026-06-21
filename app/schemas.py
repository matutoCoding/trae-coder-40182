from datetime import datetime
from enum import Enum
from typing import List, Optional
from pydantic import BaseModel, Field


class TaskStatus(str, Enum):
    PENDING = "pending"
    TRANSCRIBING = "transcribing"
    ANALYZING = "analyzing"
    COMPLETED = "completed"
    FAILED = "failed"


class Speaker(str, Enum):
    AGENT = "agent"
    CUSTOMER = "customer"
    UNKNOWN = "unknown"


class CallType(str, Enum):
    OUTBOUND = "outbound"
    INBOUND = "inbound"
    CALLBACK = "callback"


class RiskLevel(str, Enum):
    LOW = "low"
    MEDIUM = "medium"
    HIGH = "high"
    CRITICAL = "critical"


class RiskCategory(str, Enum):
    WECHAT_SOLICITATION = "wechat_solicitation"
    PROFIT_GUARANTEE = "profit_guarantee"
    VERBAL_ABUSE = "verbal_abuse"
    MISSING_RECORDING_NOTICE = "missing_recording_notice"
    OTHER_SENSITIVE = "other_sensitive"


class TranscriptSegment(BaseModel):
    start_time: float = Field(..., description="片段开始时间（秒）")
    end_time: float = Field(..., description="片段结束时间（秒）")
    speaker: Speaker = Field(..., description="说话人")
    text: str = Field(..., description="转写文本内容")

    class Config:
        json_schema_extra = {
            "example": {
                "start_time": 0.0,
                "end_time": 3.5,
                "speaker": "agent",
                "text": "您好，这里是XX客服中心，请问有什么可以帮您？"
            }
        }


class RiskFragment(BaseModel):
    segment_index: int = Field(..., description="对应转写片段的索引")
    original_text: str = Field(..., description="原始文本")
    speaker: Speaker = Field(..., description="说话人")
    start_time: float = Field(..., description="开始时间（秒）")
    end_time: float = Field(..., description="结束时间（秒）")
    risk_category: RiskCategory = Field(..., description="风险类别")
    risk_level: RiskLevel = Field(..., description="建议风险等级")
    matched_keywords: List[str] = Field(default_factory=list, description="匹配到的关键词")
    suggestion: str = Field(..., description="合规建议")

    class Config:
        json_schema_extra = {
            "example": {
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
        }


class RecordingSubmitRequest(BaseModel):
    recording_url: str = Field(..., description="录音文件访问地址（HTTP/HTTPS）", min_length=1)
    agent_id: str = Field(..., description="坐席编号", min_length=1)
    call_type: CallType = Field(..., description="通话类型")
    call_id: Optional[str] = Field(None, description="业务系统通话ID（可选，用于关联）")
    customer_id: Optional[str] = Field(None, description="客户编号（可选）")
    call_start_time: Optional[datetime] = Field(None, description="通话开始时间（可选）")
    mock_text: Optional[str] = Field(None, description="模拟转写文本（联调专用，传则优先使用此文本生成转写结果，录音地址可任意")

    class Config:
        json_schema_extra = {
            "example": {
                "recording_url": "https://example.com/recordings/20240115_001.wav",
                "agent_id": "AGENT_8823",
                "call_type": "outbound",
                "call_id": "CALL_20240115_001",
                "customer_id": "CUST_5521"
            }
        }


class RecordingSubmitResponse(BaseModel):
    task_id: str = Field(..., description="转写任务ID")
    status: TaskStatus = Field(..., description="当前任务状态")
    submitted_at: datetime = Field(..., description="提交时间")
    agent_id: str = Field(..., description="坐席编号")

    class Config:
        json_schema_extra = {
            "example": {
                "task_id": "TASK_20240115_143022_ABC123",
                "status": "pending",
                "submitted_at": "2024-01-15T14:30:22.123456",
                "agent_id": "AGENT_8823"
            }
        }


class TranscriptResult(BaseModel):
    task_id: str = Field(..., description="任务ID")
    status: TaskStatus = Field(..., description="任务状态")
    agent_id: str = Field(..., description="坐席编号")
    call_type: CallType = Field(..., description="通话类型")
    call_id: Optional[str] = Field(None, description="业务系统通话ID")
    submitted_at: datetime = Field(..., description="提交时间")
    started_at: Optional[datetime] = Field(None, description="转写开始时间")
    completed_at: Optional[datetime] = Field(None, description="转写完成时间")
    duration_seconds: Optional[float] = Field(None, description="通话总时长（秒）")
    segments: List[TranscriptSegment] = Field(default_factory=list, description="带时间戳的对话文本")
    has_risk: bool = Field(False, description="是否检测到风险")
    error_message: Optional[str] = Field(None, description="错误信息（任务失败时）")

    class Config:
        json_schema_extra = {
            "example": {
                "task_id": "TASK_20240115_143022_ABC123",
                "status": "completed",
                "agent_id": "AGENT_8823",
                "call_type": "outbound",
                "call_id": "CALL_20240115_001",
                "submitted_at": "2024-01-15T14:30:22.123456",
                "started_at": "2024-01-15T14:30:23.456789",
                "completed_at": "2024-01-15T14:30:45.789012",
                "duration_seconds": 185.5,
                "segments": [
                    {"start_time": 0.0, "end_time": 3.5, "speaker": "agent", "text": "您好，这里是XX客服中心。"},
                    {"start_time": 3.8, "end_time": 8.2, "speaker": "customer", "text": "你好，我想咨询一下理财产品。"}
                ],
                "has_risk": True,
                "error_message": None
            }
        }


class RiskAnalysisResponse(BaseModel):
    task_id: str = Field(..., description="任务ID")
    status: TaskStatus = Field(..., description="任务状态")
    agent_id: str = Field(..., description="坐席编号")
    total_risks: int = Field(0, description="风险片段总数")
    high_risk_count: int = Field(0, description="高风险片段数")
    medium_risk_count: int = Field(0, description="中风险片段数")
    low_risk_count: int = Field(0, description="低风险片段数")
    risks: List[RiskFragment] = Field(default_factory=list, description="风险片段列表")

    class Config:
        json_schema_extra = {
            "example": {
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
        }


class TaskSummary(BaseModel):
    task_id: str = Field(..., description="任务ID")
    status: TaskStatus = Field(..., description="任务状态")
    agent_id: str = Field(..., description="坐席编号")
    call_type: CallType = Field(..., description="通话类型")
    call_id: Optional[str] = Field(None, description="业务系统通话ID")
    submitted_at: datetime = Field(..., description="提交时间")
    completed_at: Optional[datetime] = Field(None, description="完成时间")
    duration_seconds: Optional[float] = Field(None, description="通话时长（秒）")
    has_risk: bool = Field(False, description="是否有风险")
    risk_count: int = Field(0, description="风险片段总数")
    high_risk_count: int = Field(0, description="高风险及以上数量")
    error_message: Optional[str] = Field(None, description="错误信息")

    class Config:
        json_schema_extra = {
            "example": {
                "task_id": "TASK_20240115_143022_ABC123",
                "status": "completed",
                "agent_id": "AGENT_8823",
                "call_type": "outbound",
                "call_id": "CALL_20240115_001",
                "submitted_at": "2024-01-15T14:30:22.123456",
                "completed_at": "2024-01-15T14:30:45.789012",
                "duration_seconds": 185.5,
                "has_risk": True,
                "risk_count": 3,
                "high_risk_count": 2,
                "error_message": None
            }
        }


class TaskListResponse(BaseModel):
    total: int = Field(0, description="总任务数")
    page: int = Field(1, description="当前页")
    page_size: int = Field(20, description="每页数量")
    items: List[TaskSummary] = Field(default_factory=list, description="任务列表")

    class Config:
        json_schema_extra = {
            "example": {
                "total": 42,
                "page": 1,
                "page_size": 20,
                "items": [
                    {
                        "task_id": "TASK_20240115_143022_ABC123",
                        "status": "completed",
                        "agent_id": "AGENT_8823",
                        "call_type": "outbound",
                        "submitted_at": "2024-01-15T14:30:22.123456",
                        "has_risk": True,
                        "risk_count": 3,
                        "high_risk_count": 2
                    }
                ]
            }
        }
