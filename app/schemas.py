from datetime import datetime
from enum import Enum
from typing import List, Optional, Dict, Any
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


class RiskConclusion(str, Enum):
    UNHANDLED = "unhandled"
    CONFIRMED_VIOLATION = "confirmed_violation"
    FALSE_ALARM = "false_alarm"
    REVIEWED_NO_ISSUE = "reviewed_no_issue"
    PENDING_REVIEW = "pending_review"


class CallbackStatus(str, Enum):
    PENDING = "pending"
    SUCCESS = "success"
    FAILED = "failed"


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
    conclusion: RiskConclusion = Field(RiskConclusion.UNHANDLED, description="处理结论")
    reviewer: Optional[str] = Field(None, description="处理人")
    reviewed_at: Optional[datetime] = Field(None, description="处理时间")
    review_note: Optional[str] = Field(None, description="处理备注")

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
                "suggestion": "坐席违规引导客户添加私人微信，请立即约谈并加强培训。",
                "conclusion": "unhandled",
                "reviewer": None,
                "reviewed_at": None,
                "review_note": None,
            }
        }


class RecordingSubmitRequest(BaseModel):
    recording_url: str = Field(..., description="录音文件访问地址（HTTP/HTTPS）", min_length=1)
    agent_id: str = Field(..., description="坐席编号", min_length=1)
    call_type: CallType = Field(..., description="通话类型")
    call_id: Optional[str] = Field(None, description="业务系统通话ID（可选，用于关联）")
    customer_id: Optional[str] = Field(None, description="客户编号（可选）")
    call_start_time: Optional[datetime] = Field(None, description="通话开始时间（可选）")
    mock_text: Optional[str] = Field(None, description="模拟转写文本（联调专用，传则优先使用）")
    callback_url: Optional[str] = Field(None, description="任务完成/失败时的回调通知地址（HTTP/HTTPS）")

    class Config:
        json_schema_extra = {
            "example": {
                "recording_url": "https://example.com/recordings/20240115_001.wav",
                "agent_id": "AGENT_8823",
                "call_type": "outbound",
                "call_id": "CALL_20240115_001",
                "customer_id": "CUST_5521",
                "callback_url": "https://crm.example.com/api/callbacks/compliance",
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
    callback_url: Optional[str] = Field(None, description="回调地址")
    error_message: Optional[str] = Field(None, description="错误信息（任务失败时）")


class RiskAnalysisResponse(BaseModel):
    task_id: str = Field(..., description="任务ID")
    status: TaskStatus = Field(..., description="任务状态")
    agent_id: str = Field(..., description="坐席编号")
    total_risks: int = Field(0, description="风险片段总数")
    high_risk_count: int = Field(0, description="高风险及以上数量")
    medium_risk_count: int = Field(0, description="中风险数量")
    low_risk_count: int = Field(0, description="低风险数量")
    unhandled_count: int = Field(0, description="未处理风险数量")
    confirmed_count: int = Field(0, description="已确认违规数量")
    false_alarm_count: int = Field(0, description="误报数量")
    reviewed_count: int = Field(0, description="已复核无问题数量")
    risks: List[RiskFragment] = Field(default_factory=list, description="风险片段列表")


class RiskConclusionUpdateRequest(BaseModel):
    conclusion: RiskConclusion = Field(..., description="处理结论")
    reviewer: Optional[str] = Field(None, description="处理人")
    review_note: Optional[str] = Field(None, description="处理备注")

    class Config:
        json_schema_extra = {
            "example": {
                "conclusion": "confirmed_violation",
                "reviewer": "COMPLIANCE_007",
                "review_note": "坐席确有引导加微信行为，已约谈并记录扣分。",
            }
        }


class CallbackRecord(BaseModel):
    id: str = Field(..., description="回调记录ID")
    task_id: str = Field(..., description="任务ID")
    callback_url: str = Field(..., description="回调地址")
    status: CallbackStatus = Field(..., description="回调状态")
    attempt: int = Field(1, description="第几次尝试")
    http_status_code: Optional[int] = Field(None, description="HTTP 响应状态码")
    response_body: Optional[str] = Field(None, description="响应内容（截断前200字符）")
    error_message: Optional[str] = Field(None, description="错误信息")
    sent_at: Optional[datetime] = Field(None, description="发起时间")
    completed_at: Optional[datetime] = Field(None, description="完成时间")
    duration_ms: Optional[int] = Field(None, description="耗时（毫秒）")
    payload_summary: Dict[str, Any] = Field(default_factory=dict, description="推送内容摘要")

    class Config:
        json_schema_extra = {
            "example": {
                "id": "CB_20240115_143045_XYZ789",
                "task_id": "TASK_20240115_143022_ABC123",
                "callback_url": "https://crm.example.com/api/callbacks/compliance",
                "status": "success",
                "attempt": 1,
                "http_status_code": 200,
                "response_body": "{\"success\":true,\"received_at\":\"...\"}",
                "error_message": None,
                "sent_at": "2024-01-15T14:30:45.123000",
                "completed_at": "2024-01-15T14:30:45.556000",
                "duration_ms": 433,
                "payload_summary": {
                    "task_id": "TASK_20240115_143022_ABC123",
                    "status": "completed",
                    "agent_id": "AGENT_8823",
                    "has_risk": True,
                    "total_risks": 2,
                    "high_risk_count": 1,
                },
            }
        }


class CallbackListResponse(BaseModel):
    task_id: str = Field(..., description="任务ID")
    total: int = Field(0, description="回调总次数")
    success_count: int = Field(0, description="成功次数")
    failed_count: int = Field(0, description="失败次数")
    items: List[CallbackRecord] = Field(default_factory=list, description="回调记录列表（按时间倒序）")


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
    unhandled_risk_count: int = Field(0, description="未处理风险数量")
    has_callback: bool = Field(False, description="是否配置回调")
    last_callback_status: Optional[CallbackStatus] = Field(None, description="最近一次回调状态")
    error_message: Optional[str] = Field(None, description="错误信息")


class TaskListResponse(BaseModel):
    total: int = Field(0, description="总任务数")
    page: int = Field(1, description="当前页")
    page_size: int = Field(20, description="每页数量")
    items: List[TaskSummary] = Field(default_factory=list, description="任务列表")
