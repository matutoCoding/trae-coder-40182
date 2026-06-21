import logging
from fastapi import FastAPI, HTTPException
from fastapi.responses import JSONResponse

from .config import settings
from .schemas import (
    RecordingSubmitRequest,
    RecordingSubmitResponse,
    TranscriptResult,
    RiskAnalysisResponse,
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
        "提供三个核心接口：\n"
        "1. **POST /api/v1/tasks** - 提交录音转写任务\n"
        "2. **GET /api/v1/tasks/{task_id}** - 查询转写状态及获取对话文本\n"
        "3. **GET /api/v1/tasks/{task_id}/risks** - 获取风险检测结果\n"
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
        "- `customer_id`（可选）: 客户编号\n\n"
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
    "/api/v1/tasks/{task_id}",
    response_model=TranscriptResult,
    tags=["Tasks"],
    summary="查询转写状态与对话文本",
    description=(
        "合规岗后台按任务号查询转写进度和结果。\n\n"
        "**状态流转**：`pending` → `transcribing` → `analyzing` → `completed`\n\n"
        "当状态为 `completed` 时，`segments` 字段包含带时间戳的完整对话文本。"
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
    tags=["Tasks"],
    summary="获取风险检测结果",
    description=(
        "获取通话中的合规风险片段列表。\n\n"
        "每个风险片段包含：\n"
        "- `original_text`: 原句内容\n"
        "- `speaker`: 说话人（agent/customer）\n"
        "- `start_time` / `end_time`: 起止时间（秒）\n"
        "- `risk_category`: 风险类别\n"
        "- `risk_level`: 建议风险等级（low/medium/high/critical）\n"
        "- `suggestion`: 合规处理建议\n\n"
        "业务系统可将高风险通话自动推入人工复核队列。"
    ),
    responses={
        404: {"description": "任务不存在"},
    },
)
async def get_task_risks(task_id: str):
    processor = get_task_processor()
    result = processor.get_task_risks(task_id)
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
