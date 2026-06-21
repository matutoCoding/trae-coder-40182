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
    CallbackRetryRequest,
    SupervisorStatsResponse,
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
        "核心接口：\n"
        "1. **POST /api/v1/tasks** - 提交录音转写任务（支持回调URL、自定义文本）\n"
        "2. **GET /api/v1/tasks** - 任务列表（按坐席/状态/通话类型/时间范围过滤，返回失败分类）\n"
        "3. **GET /api/v1/tasks/{task_id}** - 查询转写状态（含失败类型/可读原因/是否建议重试）\n"
        "4. **GET /api/v1/tasks/{task_id}/risks** - 风险检测结果（每条风险独立 risk_id，按结论筛选）\n"
        "5. **POST /api/v1/tasks/{task_id}/risks/{risk_id}/conclusion** - 按 risk_id 更新风险处理结论\n"
        "6. **GET /api/v1/tasks/{task_id}/callbacks** - 查询回调历史（含真实 HTTP 状态码和失败原因）\n"
        "7. **POST /api/v1/tasks/{task_id}/callbacks/retry** - 手动重试回调\n"
        "8. **GET /api/v1/stats/supervisor** - 主管统计（按坐席/通话类型/日期范围汇总任务、失败、风险、确认违规）"
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
        "- `callback_url`（可选）: 任务完成/失败后接收状态推送的回调地址（真实 HTTP POST）\n\n"
        "任务会在后台异步执行转写和风险检测，完成后若配置了 callback_url 会主动推送。"
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
    summary="查询任务列表（含失败分类、可读原因、建议重试）",
    description=(
        "按坐席、状态、通话类型、提交时间范围查询任务列表，支持分页。\n\n"
        "每条任务摘要会带出：\n"
        "- `failure_type`: recording_unreachable/recording_corrupted/asr_service_error/analysis_error/unknown\n"
        "- `failure_reason`: 面向合规岗的可读失败原因（列表页直接可见）\n"
        "- `suggest_retry`: 是否建议重试\n"
        "- `confirmed_risk_count`: 已确认违规数量"
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
        "按任务号查询转写进度和结果。\n\n"
        "任务失败时除了 `error_message`（内部详细）外，额外返回：\n"
        "- `failure_type`: 失败分类枚举\n"
        "- `failure_reason`: 面向合规岗的可读失败原因\n"
        "- `suggest_retry`: 是否建议重试（地址/文件问题不建议重试，服务异常建议重试）"
    ),
    responses={404: {"description": "任务不存在"}},
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
    summary="获取风险检测结果（每条风险独立 risk_id）",
    description=(
        "获取通话中的合规风险片段列表。\n\n"
        "**每条风险都有独立 risk_id**，同一句话（同一 segment）如果同时触发多类风险（例如既加微信又保证收益），会返回多条风险片段并可以分别打处理结论。\n\n"
        "支持按 risk_level / risk_category / speaker / conclusion 多维筛选。"
    ),
    responses={404: {"description": "任务不存在"}},
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
    "/api/v1/tasks/{task_id}/risks/{risk_id}/conclusion",
    response_model=RiskAnalysisResponse,
    tags=["Risk & Compliance"],
    summary="按 risk_id 更新风险处理结论",
    description=(
        "合规岗对某条风险片段（通过独立 risk_id 定位）打处理结论，形成人工质检闭环。\n\n"
        "同一句话同时命中多类风险时，分别有不同的 risk_id，可分别打不同结论（如一个确认违规，另一个误报）。\n\n"
        "**处理结论可选值**：\n"
        "- `unhandled`: 未处理\n"
        "- `confirmed_violation`: 确认违规\n"
        "- `false_alarm`: 误报\n"
        "- `reviewed_no_issue`: 已复核无问题\n"
        "- `pending_review`: 待进一步复核"
    ),
    responses={
        404: {"description": "任务不存在，或 risk_id 不存在"},
        409: {"description": "任务尚未完成，无法处理"},
    },
)
async def update_risk_conclusion(
    task_id: str,
    risk_id: str,
    update: RiskConclusionUpdateRequest,
):
    processor = get_task_processor()
    ok, err, result = processor.update_risk_conclusion_by_risk_id(task_id, risk_id, update)
    if not ok and f"任务 {task_id} 不存在" in (err or ""):
        raise HTTPException(status_code=404, detail=err)
    if not ok and "未找到 risk_id" in (err or ""):
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
        "查询某个任务的所有回调推送历史，用于排查客服平台是否成功接收。\n\n"
        "每条回调记录包含：\n"
        "- `triggered_by`: auto（自动触发）/ manual（手动重试）\n"
        "- `http_status_code`: 对方返回的真实 HTTP 状态码（4xx/5xx/2xx 等）\n"
        "- `error_message`: 真实失败原因（超时、连接被拒绝、HTTP 500 等）\n"
        "- `response_body`: 对方响应体（截断前 500 字符）"
    ),
    responses={404: {"description": "任务不存在"}},
)
async def get_callback_history(task_id: str):
    processor = get_task_processor()
    result = processor.get_callback_history(task_id)
    if result is None:
        raise HTTPException(status_code=404, detail=f"任务 {task_id} 不存在")
    return result


@app.post(
    "/api/v1/tasks/{task_id}/callbacks/retry",
    response_model=CallbackListResponse,
    tags=["Callbacks"],
    summary="手动重试回调",
    description=(
        "手动触发该任务的回调推送（仅尝试 1 次），用于客服平台修复后手动补推。\n\n"
        "请求体可选传 `reviewer` 记录操作人。\n\n"
        "返回更新后的回调历史，可查看最新一次手动重试是否成功。"
    ),
    responses={
        404: {"description": "任务不存在"},
        400: {"description": "任务未配置回调地址，或任务尚未结束"},
    },
)
async def retry_callback(
    task_id: str,
    request: Optional[CallbackRetryRequest] = None,
):
    processor = get_task_processor()
    reviewer = request.reviewer if request else None
    ok, err, result = processor.retry_callback(task_id, reviewer=reviewer)
    if not ok and f"任务 {task_id} 不存在" in (err or ""):
        raise HTTPException(status_code=404, detail=err)
    if not ok:
        raise HTTPException(status_code=400, detail=err)
    return result


@app.get(
    "/api/v1/stats/supervisor",
    response_model=SupervisorStatsResponse,
    tags=["Statistics"],
    summary="主管统计：坐席维度日汇总",
    description=(
        "面向主管的每日收工统计，按坐席（或坐席+通话类型）+ 日期聚合。\n\n"
        "每个分组返回：\n"
        "- `total_tasks`: 任务总量\n"
        "- `failed_tasks` / `failed_rate`: 失败量与失败率\n"
        "- `tasks_with_risk` / `risk_rate`: 有风险任务量与风险率\n"
        "- `total_risks`: 风险片段总数\n"
        "- `confirmed_violations`: 已确认违规数量\n"
        "- `unhandled_risks`: 未处理风险数量\n\n"
        "顶部还有全量汇总（total_tasks/total_failed/total_risks/total_confirmed）。"
    ),
)
async def supervisor_stats(
    date_from: str = Query(..., description="统计起始日期 YYYY-MM-DD（含）"),
    date_to: str = Query(..., description="统计结束日期 YYYY-MM-DD（含）"),
    agent_id: Optional[str] = Query(None, description="坐席编号（可选，不填则全部坐席）"),
    call_type: Optional[CallType] = Query(None, description="通话类型（可选）"),
    group_by: str = Query("agent", description="聚合维度：agent（按坐席+日期）或 agent_call_type（按坐席+通话类型+日期）"),
):
    if group_by not in ("agent", "agent_call_type"):
        raise HTTPException(status_code=400, detail="group_by 仅支持 agent 或 agent_call_type")
    processor = get_task_processor()
    return processor.supervisor_stats(
        date_from=date_from,
        date_to=date_to,
        agent_id=agent_id,
        call_type=call_type,
        group_by=group_by,
    )


@app.exception_handler(Exception)
async def global_exception_handler(request, exc):
    logger.exception(f"Unhandled exception: {exc}")
    return JSONResponse(
        status_code=500,
        content={"detail": "服务器内部错误", "error": str(exc) if settings.debug else None},
    )
