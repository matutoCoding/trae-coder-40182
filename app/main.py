import logging
from datetime import datetime
from typing import Optional
from fastapi import FastAPI, HTTPException, Query
from fastapi.responses import JSONResponse

from .config import settings
from .schemas import (
    TaskStatus,
    CallType,
    RiskConclusion,
    RiskLevel,
    RiskCategory,
    Speaker,
    RecordingSubmitRequest,
    RecordingSubmitResponse,
    TranscriptResult,
    RiskAnalysisResponse,
    RiskConclusionUpdateRequest,
    CallbackListResponse,
    TaskListResponse,
)
from .task_processor import get_task_processor

logging.basicConfig(
    level=logging.INFO if not settings.debug else logging.DEBUG,
    format="%(asctime)s - %(name)s - %(levelname)s - %(message)s",
)
logger = logging.getLogger(__name__)

app = FastAPI(
    title=settings.app_name,
    version=settings.app_version,
    description=(
        "面向呼叫中心合规岗的后端转写与预警服务。\n\n"
        "提供以下核心接口：\n"
        "1. **POST /api/v1/tasks** - 提交录音转写任务（支持回调URL、自定义文本）\n"
        "2. **GET /api/v1/tasks** - 任务列表（按坐席/状态/通话类型/时间范围过滤）\n"
        "3. **GET /api/v1/tasks/{task_id}** - 查询转写状态及获取对话文本\n"
        "4. **GET /api/v1/tasks/{task_id}/risks** - 获取风险检测结果（按结论/等级/类别筛选）\n"
        "5. **POST /api/v1/tasks/{task_id}/risks/{segment_index}/conclusion** - 更新风险处理结论\n"
        "6. **GET /api/v1/tasks/{task_id}/callbacks** - 查询回调历史"
    ),
)


@app.get("/", tags=["System"])
async def root():
    return {
        "service": settings.app_name,
        "version": settings.app_version,
        "status": "running",
        "docs": "/docs",
    }


@app.get("/health", tags=["System"])
async def health_check():
    return {"status": "healthy"}


@app.post(
    "/api/v1/tasks",
    response_model=RecordingSubmitResponse,
    tags=["Tasks"],
    summary="提交录音转写任务",
    description=(
        "业务系统调用此接口提交通话录音。\n\n"
        "**请求参数**：\n"
        "- `recording_url`: 录音文件的 HTTP/HTTPS 访问地址\n"
        "- `agent_id`: 坐席编号\n"
        "- `call_type`: 通话类型（outbound/inbound/callback）\n"
        "- `call_id`（可选）: 业务系统通话ID\n"
        "- `customer_id`（可选）: 客户编号\n"
        "- `mock_text`（可选）: 联调用自定义转写文本（按句号切句，坐席/客户轮流）\n"
        "- `callback_url`（可选）: 任务完成/失败后接收状态推送的回调地址\n\n"
        "**返回**：任务ID和当前状态，任务会在后台异步执行转写和风险检测。"
    ),
)
async def submit_recording(request: RecordingSubmitRequest):
    processor = get_task_processor()
    task = await processor.submit_task(request)
    return RecordingSubmitResponse(
        task_id=task.task_id,
        status=task.status,
        submitted_at=task.submitted_at,
        agent_id=task.agent_id,
    )


@app.get(
    "/api/v1/tasks",
    response_model=TaskListResponse,
    tags=["Tasks"],
    summary="查询任务列表",
    description=(
        "按坐席、状态、通话类型、提交时间范围查询任务列表，支持分页。\n\n"
        "**筛选参数**：\n"
        "- `agent_id`（可选）: 按坐席编号筛选\n"
        "- `status`（可选）: 按任务状态筛选（pending/transcribing/analyzing/completed/failed）\n"
        "- `call_type`（可选）: 按通话类型筛选（outbound/inbound/callback）\n"
        "- `submitted_after`（可选）: 提交时间 >= 该值（ISO 8601，如 2024-01-01T00:00:00）\n"
        "- `submitted_before`（可选）: 提交时间 <= 该值（ISO 8601）\n"
        "- `page`: 页码，默认 1\n"
        "- `page_size`: 每页数量，默认 20"
    ),
)
async def list_tasks(
    agent_id: Optional[str] = Query(None, description="坐席编号"),
    status: Optional[TaskStatus] = Query(None, description="任务状态"),
    call_type: Optional[CallType] = Query(None, description="通话类型"),
    submitted_after: Optional[datetime] = Query(None, description="提交时间 >= (ISO 8601)"),
    submitted_before: Optional[datetime] = Query(None, description="提交时间 <= (ISO 8601)"),
    page: int = Query(1, ge=1, description="页码"),
    page_size: int = Query(20, ge=1, le=200, description="每页数量"),
):
    processor = get_task_processor()
    return processor.list_tasks(
        agent_id=agent_id,
        status=status,
        call_type=call_type,
        submitted_after=submitted_after,
        submitted_before=submitted_before,
        page=page,
        page_size=page_size,
    )


@app.get(
    "/api/v1/tasks/{task_id}",
    response_model=TranscriptResult,
    tags=["Tasks"],
    summary="查询转写状态与对话文本",
    description=(
        "合规岗后台按任务号查询转写进度和结果。\n\n"
        "**状态流转**：`pending` → `transcribing` → `analyzing` → `completed`\n\n"
        "当状态为 `completed` 时，`segments` 字段包含带时间戳的完整对话文本。\n"
        "若任务配了 `callback_url`，可通过 `/callbacks` 接口查看回调历史。"
    ),
    responses={
        404: {"description": "任务不存在"},
    },
)
async def get_task_status(task_id: str):
    processor = get_task_processor()
    result = processor.get_task_status(task_id)
    if result is None:
        raise HTTPException(status_code=404, detail=f"任务 {task_id} 不存在")
    return result


@app.get(
    "/api/v1/tasks/{task_id}/risks",
    response_model=RiskAnalysisResponse,
    tags=["Risk & Compliance"],
    summary="获取风险检测结果",
    description=(
        "获取通话中的合规风险片段列表，支持按风险等级、类别、说话人、处理结论筛选。\n\n"
        "每个风险片段包含：\n"
        "- `original_text`: 原句内容\n"
        "- `speaker`: 说话人（agent/customer）\n"
        "- `start_time` / `end_time`: 起止时间（秒）\n"
        "- `risk_category`: 风险类别\n"
        "- `risk_level`: 建议风险等级（low/medium/high/critical）\n"
        "- `suggestion`: 合规处理建议\n"
        "- `conclusion`: 处理结论（unhandled/confirmed_violation/false_alarm/reviewed_no_issue/pending_review）\n"
        "- `reviewer` / `reviewed_at` / `review_note`: 复核信息\n\n"
        "业务系统可将高风险通话自动推入人工复核队列。"
    ),
    responses={
        404: {"description": "任务不存在"},
    },
)
async def get_task_risks(
    task_id: str,
    risk_level: Optional[RiskLevel] = Query(None, description="按风险等级筛选"),
    risk_category: Optional[RiskCategory] = Query(None, description="按风险类别筛选"),
    speaker: Optional[Speaker] = Query(None, description="按说话人筛选"),
    conclusion: Optional[RiskConclusion] = Query(None, description="按处理结论筛选"),
):
    processor = get_task_processor()
    result = processor.get_filtered_risks(
        task_id=task_id,
        risk_level=risk_level,
        risk_category=risk_category,
        speaker=speaker,
        conclusion=conclusion,
    )
    if result is None:
        raise HTTPException(status_code=404, detail=f"任务 {task_id} 不存在")
    return result


@app.post(
    "/api/v1/tasks/{task_id}/risks/{segment_index}/conclusion",
    response_model=RiskAnalysisResponse,
    tags=["Risk & Compliance"],
    summary="更新风险处理结论",
    description=(
        "合规岗对某条风险片段打处理结论，形成人工质检闭环。\n\n"
        "**处理结论（conclusion）可选值**：\n"
        "- `unhandled`: 未处理（初始状态）\n"
        "- `confirmed_violation`: 确认违规\n"
        "- `false_alarm`: 误报\n"
        "- `reviewed_no_issue`: 已复核无问题\n"
        "- `pending_review`: 待进一步复核\n\n"
        "**路径参数**：\n"
        "- `task_id`: 任务ID\n"
        "- `segment_index`: 风险片段的 segment_index（与风险列表返回值一致）\n\n"
        "成功返回更新后的完整风险分析结果。"
    ),
    responses={
        404: {"description": "任务不存在，或风险片段不存在"},
        409: {"description": "任务尚未完成，无法处理"},
    },
)
async def update_risk_conclusion(
    task_id: str,
    segment_index: int,
    update: RiskConclusionUpdateRequest,
):
    processor = get_task_processor()
    ok, err, result = processor.update_risk_conclusion(task_id, segment_index, update)
    if not ok and f"任务 {task_id} 不存在" in (err or ""):
        raise HTTPException(status_code=404, detail=err)
    if not ok and "未找到 segment_index" in (err or ""):
        raise HTTPException(status_code=404, detail=err)
    if not ok:
        raise HTTPException(status_code=409, detail=err)
    return result


@app.get(
    "/api/v1/tasks/{task_id}/callbacks",
    response_model=CallbackListResponse,
    tags=["Callbacks"],
    summary="查询回调历史",
    description=(
        "查询某个任务的回调推送历史，用于排查客服平台是否成功接收了任务状态。\n\n"
        "**返回内容**：\n"
        "- `total`: 总回调次数\n"
        "- `success_count`: 成功次数\n"
        "- `failed_count`: 失败次数\n"
        "- `items`: 每次回调的详细记录（时间、耗时、HTTP状态码、错误原因、响应片段）"
    ),
    responses={
        404: {"description": "任务不存在"},
    },
)
async def get_callback_history(task_id: str):
    processor = get_task_processor()
    result = processor.get_callback_history(task_id)
    if result is None:
        raise HTTPException(status_code=404, detail=f"任务 {task_id} 不存在")
    return result


@app.exception_handler(Exception)
async def global_exception_handler(request, exc):
    logger.exception(f"Unhandled exception: {exc}")
    return JSONResponse(
        status_code=500,
        content={"detail": "服务器内部错误", "error": str(exc) if settings.debug else None},
    )
