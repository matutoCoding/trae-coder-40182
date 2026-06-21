import asyncio
import json
import logging
import time
import uuid
from datetime import datetime
from typing import Optional, Tuple, List
from urllib.parse import urlparse

from .schemas import (
    TaskStatus,
    CallType,
    RiskConclusion,
    CallbackStatus,
    CallbackRecord,
    CallbackListResponse,
    RecordingSubmitRequest,
    TranscriptResult,
    RiskAnalysisResponse,
    RiskFragment,
    RiskConclusionUpdateRequest,
    TaskListResponse,
    RiskLevel,
    RiskCategory,
    Speaker,
)
from .storage import get_storage, TranscriptionTask
from .asr_service import get_asr_service, ASRError, RecordingURLError, TranscriptionFailedError
from .compliance_engine import get_compliance_engine

logger = logging.getLogger(__name__)


class TaskProcessor:
    def __init__(self):
        self.storage = get_storage()
        self.asr = get_asr_service()
        self.engine = get_compliance_engine()

    async def submit_task(self, request: RecordingSubmitRequest) -> TranscriptionTask:
        task = self.storage.create_task(request)
        asyncio.create_task(self._process_task(task.task_id))
        logger.info(f"Task {task.task_id} submitted for agent {task.agent_id}")
        return task

    def _build_callback_payload(self, task: TranscriptionTask) -> dict:
        high = sum(1 for r in task.risks if r.risk_level in (RiskLevel.HIGH, RiskLevel.CRITICAL))
        medium = sum(1 for r in task.risks if r.risk_level == RiskLevel.MEDIUM)
        low = sum(1 for r in task.risks if r.risk_level == RiskLevel.LOW)
        return {
            "event": "task_finished",
            "task_id": task.task_id,
            "status": task.status.value,
            "agent_id": task.agent_id,
            "call_type": task.call_type.value,
            "call_id": task.call_id,
            "customer_id": task.customer_id,
            "recording_url": task.recording_url,
            "submitted_at": task.submitted_at.isoformat(),
            "started_at": task.started_at.isoformat() if task.started_at else None,
            "completed_at": task.completed_at.isoformat() if task.completed_at else None,
            "duration_seconds": task.duration_seconds,
            "segment_count": len(task.segments),
            "has_risk": task.has_risk,
            "total_risks": len(task.risks),
            "high_risk_count": high,
            "medium_risk_count": medium,
            "low_risk_count": low,
            "error_message": task.error_message,
            "top_risks": [
                {
                    "segment_index": r.segment_index,
                    "risk_category": r.risk_category.value,
                    "risk_level": r.risk_level.value,
                    "speaker": r.speaker.value,
                    "original_text": r.original_text[:80],
                }
                for r in task.risks[:5]
            ],
        }

    async def _send_callback_once(
        self,
        callback_url: str,
        payload: dict,
        task_id: str,
        attempt: int,
    ) -> Tuple[CallbackStatus, Optional[int], Optional[str], Optional[str], int]:
        start_ts = time.time()
        http_status: Optional[int] = None
        resp_body: Optional[str] = None
        err_msg: Optional[str] = None
        final_status = CallbackStatus.FAILED

        parsed = urlparse(callback_url)
        host_lower = (parsed.hostname or "").lower()

        if any(kw in host_lower for kw in ("timeout", "timedout", "time-out")):
            await asyncio.sleep(0.5)
            err_msg = "回调连接超时 (1000ms timeout exceeded)"
        elif any(kw in host_lower for kw in ("fail", "error", "broken", "500", "502", "503", "504")):
            await asyncio.sleep(0.05)
            http_status = 500
            resp_body = '{"status":"error","message":"internal server error"}'
            err_msg = "回调服务器返回 HTTP 500"
        elif any(kw in host_lower for kw in ("success", "ok", "200", "mock-callback")):
            await asyncio.sleep(0.05)
            http_status = 200
            resp_body = '{"success":true,"received_at":"' + datetime.now().isoformat() + '"}'
            final_status = CallbackStatus.SUCCESS
        else:
            await asyncio.sleep(0.1)
            http_status = 200
            resp_body = (
                '{"success":true,"message":"mock received","task_id":"'
                + task_id
                + '","attempt":'
                + str(attempt)
                + "}"
            )
            final_status = CallbackStatus.SUCCESS

        duration_ms = int((time.time() - start_ts) * 1000)
        return final_status, http_status, resp_body, err_msg, duration_ms

    async def _dispatch_callback(self, task: TranscriptionTask) -> None:
        if not task.callback_url:
            return

        payload = self._build_callback_payload(task)
        max_attempts = 3
        for attempt in range(1, max_attempts + 1):
            record_id = f"CB_{datetime.now().strftime('%Y%m%d_%H%M%S')}_{uuid.uuid4().hex[:6].upper()}"
            sent_at = datetime.now()

            status, http_code, resp_body, err_msg, duration = await self._send_callback_once(
                task.callback_url, payload, task.task_id, attempt
            )
            completed_at = datetime.now()

            record = CallbackRecord(
                id=record_id,
                task_id=task.task_id,
                callback_url=task.callback_url,
                status=status,
                attempt=attempt,
                http_status_code=http_code,
                response_body=resp_body[:200] if resp_body else None,
                error_message=err_msg,
                sent_at=sent_at,
                completed_at=completed_at,
                duration_ms=duration,
                payload_summary={
                    "status": payload["status"],
                    "has_risk": payload["has_risk"],
                    "total_risks": payload["total_risks"],
                    "high_risk_count": payload["high_risk_count"],
                },
            )
            self.storage.add_callback_record(task.task_id, record)
            self.storage.update_task(task)

            if status == CallbackStatus.SUCCESS:
                logger.info(
                    f"Callback for task {task.task_id} succeeded on attempt {attempt} ({duration}ms)"
                )
                return

            logger.warning(
                f"Callback for task {task.task_id} attempt {attempt} failed: {err_msg}"
            )
            if attempt < max_attempts:
                await asyncio.sleep(1 * attempt)

        logger.error(f"Callback for task {task.task_id} failed after {max_attempts} attempts")

    async def _process_task(self, task_id: str):
        task = self.storage.get_task(task_id)
        if not task:
            logger.error(f"Task {task_id} not found")
            return

        try:
            task.status = TaskStatus.TRANSCRIBING
            task.started_at = datetime.now()
            self.storage.update_task(task)

            segments = await self.asr.transcribe(task.recording_url, mock_text=task.mock_text)
            task.segments = segments
            if segments:
                task.duration_seconds = round(segments[-1].end_time, 2)

            task.status = TaskStatus.ANALYZING
            self.storage.update_task(task)

            risks = self.engine.analyze_segments(segments)
            task.risks = risks
            task.has_risk = len(risks) > 0

            task.status = TaskStatus.COMPLETED
            task.completed_at = datetime.now()
            self.storage.update_task(task)

            logger.info(
                f"Task {task_id} completed. "
                f"Segments: {len(segments)}, Risks: {len(risks)}"
            )

        except RecordingURLError as e:
            logger.error(f"Task {task_id} recording URL error: {e}")
            task.status = TaskStatus.FAILED
            task.error_message = f"录音地址无效: {e}"
            task.completed_at = datetime.now()
            self.storage.update_task(task)

        except TranscriptionFailedError as e:
            logger.error(f"Task {task_id} transcription failed: {e}")
            task.status = TaskStatus.FAILED
            task.error_message = f"转写失败: {e}"
            task.completed_at = datetime.now()
            self.storage.update_task(task)

        except ASRError as e:
            logger.error(f"Task {task_id} ASR error: {e}")
            task.status = TaskStatus.FAILED
            task.error_message = f"语音识别服务异常: {e}"
            task.completed_at = datetime.now()
            self.storage.update_task(task)

        except Exception as e:
            logger.exception(f"Task {task_id} unexpected error")
            task.status = TaskStatus.FAILED
            task.error_message = f"处理异常: {e}"
            task.completed_at = datetime.now()
            self.storage.update_task(task)

        finally:
            asyncio.create_task(self._dispatch_callback(task))

    def get_task_status(self, task_id: str) -> Optional[TranscriptResult]:
        task = self.storage.get_task(task_id)
        if not task:
            return None
        return task.to_transcript_result()

    def get_task_risks(self, task_id: str) -> Optional[RiskAnalysisResponse]:
        task = self.storage.get_task(task_id)
        if not task:
            return None
        return task.to_risk_analysis_response()

    def get_filtered_risks(
        self,
        task_id: str,
        risk_level: Optional[RiskLevel] = None,
        risk_category: Optional[RiskCategory] = None,
        speaker: Optional[Speaker] = None,
        conclusion: Optional[RiskConclusion] = None,
    ) -> Optional[RiskAnalysisResponse]:
        task = self.storage.get_task(task_id)
        if not task:
            return None

        risks: List[RiskFragment] = list(task.risks)
        if risk_level:
            risks = [r for r in risks if r.risk_level == risk_level]
        if risk_category:
            risks = [r for r in risks if r.risk_category == risk_category]
        if speaker:
            risks = [r for r in risks if r.speaker == speaker]
        if conclusion:
            risks = [r for r in risks if r.conclusion == conclusion]

        high = sum(1 for r in risks if r.risk_level in (RiskLevel.HIGH, RiskLevel.CRITICAL))
        medium = sum(1 for r in risks if r.risk_level == RiskLevel.MEDIUM)
        low = sum(1 for r in risks if r.risk_level == RiskLevel.LOW)
        unhandled = sum(1 for r in risks if r.conclusion == RiskConclusion.UNHANDLED)
        confirmed = sum(1 for r in risks if r.conclusion == RiskConclusion.CONFIRMED_VIOLATION)
        false_alarm = sum(1 for r in risks if r.conclusion == RiskConclusion.FALSE_ALARM)
        reviewed = sum(
            1 for r in risks if r.conclusion in (RiskConclusion.REVIEWED_NO_ISSUE, RiskConclusion.PENDING_REVIEW)
        )

        return RiskAnalysisResponse(
            task_id=task.task_id,
            status=task.status,
            agent_id=task.agent_id,
            total_risks=len(risks),
            high_risk_count=high,
            medium_risk_count=medium,
            low_risk_count=low,
            unhandled_count=unhandled,
            confirmed_count=confirmed,
            false_alarm_count=false_alarm,
            reviewed_count=reviewed,
            risks=risks,
        )

    def update_risk_conclusion(
        self,
        task_id: str,
        segment_index: int,
        update: RiskConclusionUpdateRequest,
    ) -> Tuple[bool, Optional[str], Optional[RiskAnalysisResponse]]:
        task = self.storage.get_task(task_id)
        if not task:
            return False, f"任务 {task_id} 不存在", None

        if task.status not in (TaskStatus.COMPLETED, TaskStatus.FAILED):
            return False, f"任务尚未完成（当前状态: {task.status.value}），暂无法处理风险", None

        target: Optional[RiskFragment] = None
        for r in task.risks:
            if r.segment_index == segment_index:
                target = r
                break

        if target is None:
            return False, f"未找到 segment_index={segment_index} 的风险片段", None

        target.conclusion = update.conclusion
        target.reviewer = update.reviewer
        target.reviewed_at = datetime.now()
        target.review_note = update.review_note

        self.storage.update_task(task)
        return True, None, task.to_risk_analysis_response()

    def get_callback_history(self, task_id: str) -> Optional[CallbackListResponse]:
        task = self.storage.get_task(task_id)
        if not task:
            return None
        records = self.storage.get_callback_records(task_id)
        success = sum(1 for r in records if r.status == CallbackStatus.SUCCESS)
        failed = sum(1 for r in records if r.status == CallbackStatus.FAILED)
        return CallbackListResponse(
            task_id=task_id,
            total=len(records),
            success_count=success,
            failed_count=failed,
            items=records,
        )

    def list_tasks(
        self,
        agent_id: Optional[str] = None,
        status: Optional[TaskStatus] = None,
        call_type: Optional[CallType] = None,
        submitted_after: Optional[datetime] = None,
        submitted_before: Optional[datetime] = None,
        page: int = 1,
        page_size: int = 20,
    ) -> TaskListResponse:
        all_tasks = self.storage.list_tasks(
            agent_id=agent_id,
            status=status,
            call_type=call_type,
            submitted_after=submitted_after,
            submitted_before=submitted_before,
        )
        total = len(all_tasks)

        start = (page - 1) * page_size
        end = start + page_size
        paged = all_tasks[start:end]

        items = [t.to_task_summary() for t in paged]
        return TaskListResponse(
            total=total,
            page=page,
            page_size=page_size,
            items=items,
        )


_processor_instance: Optional[TaskProcessor] = None


def get_task_processor() -> TaskProcessor:
    global _processor_instance
    if _processor_instance is None:
        _processor_instance = TaskProcessor()
    return _processor_instance
