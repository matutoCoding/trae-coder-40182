import asyncio
import json
import logging
import socket
import time
import uuid
from datetime import datetime
from typing import Optional, Tuple, List
from urllib.parse import urlparse
import urllib.request
import urllib.error

from .schemas import (
    TaskStatus,
    CallType,
    FailureType,
    RiskConclusion,
    CallbackStatus,
    CallbackRecord,
    CallbackListResponse,
    RecordingSubmitRequest,
    TranscriptResult,
    RiskAnalysisResponse,
    RiskFragment,
    RiskConclusionUpdateRequest,
    SupervisorStatsResponse,
    TaskListResponse,
    RiskLevel,
    RiskCategory,
    Speaker,
)
from .storage import get_storage, TranscriptionTask
from .asr_service import get_asr_service, ASRError, RecordingURLError, TranscriptionFailedError
from .compliance_engine import get_compliance_engine

logger = logging.getLogger(__name__)

_CALLBACK_TIMEOUT_SEC = 5


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
            "failure_type": task.failure_type.value if task.failure_type else None,
            "failure_reason": task.failure_reason,
            "suggest_retry": task.suggest_retry,
            "error_message": task.error_message,
            "top_risks": [
                {
                    "risk_id": r.risk_id,
                    "segment_index": r.segment_index,
                    "risk_category": r.risk_category.value,
                    "risk_level": r.risk_level.value,
                    "speaker": r.speaker.value,
                    "original_text": r.original_text[:80],
                }
                for r in task.risks[:5]
            ],
        }

    def _send_http_callback_sync(
        self,
        callback_url: str,
        payload: dict,
    ) -> Tuple[CallbackStatus, Optional[int], Optional[str], Optional[str], int]:
        start_ts = time.time()
        http_status: Optional[int] = None
        resp_body: Optional[str] = None
        err_msg: Optional[str] = None
        final_status = CallbackStatus.FAILED

        try:
            data = json.dumps(payload, ensure_ascii=False).encode("utf-8")
            req = urllib.request.Request(
                callback_url,
                data=data,
                headers={
                    "Content-Type": "application/json; charset=utf-8",
                    "User-Agent": "ComplianceASR-Callback/1.0",
                    "X-Task-Id": payload.get("task_id", ""),
                    "X-Event": payload.get("event", "task_finished"),
                },
                method="POST",
            )
            with urllib.request.urlopen(req, timeout=_CALLBACK_TIMEOUT_SEC) as resp:
                http_status = resp.status
                raw = resp.read(512).decode("utf-8", errors="replace")
                resp_body = raw[:500] if raw else None
                if 200 <= resp.status < 300:
                    final_status = CallbackStatus.SUCCESS
                else:
                    final_status = CallbackStatus.FAILED
                    err_msg = f"回调服务器返回非 2xx 状态码 HTTP {resp.status}"
        except urllib.error.HTTPError as e:
            http_status = e.code
            try:
                raw = e.read(512).decode("utf-8", errors="replace")
                resp_body = raw[:500] if raw else None
            except Exception:
                resp_body = None
            err_msg = f"回调服务器返回 HTTP {e.code} {e.reason}"
            final_status = CallbackStatus.FAILED
        except urllib.error.URLError as e:
            reason = e.reason
            if isinstance(reason, socket.timeout):
                err_msg = f"回调连接超时（>{_CALLBACK_TIMEOUT_SEC}s）"
            elif isinstance(reason, ConnectionRefusedError):
                err_msg = "回调地址连接被拒绝（Connection refused）"
            elif isinstance(reason, OSError):
                err_msg = f"回调网络错误: {reason}"
            else:
                err_msg = f"回调 URL 错误: {reason}"
            final_status = CallbackStatus.FAILED
        except socket.timeout:
            err_msg = f"回调连接超时（>{_CALLBACK_TIMEOUT_SEC}s）"
            final_status = CallbackStatus.FAILED
        except Exception as e:
            err_msg = f"回调发送异常: {type(e).__name__}: {e}"
            final_status = CallbackStatus.FAILED

        duration_ms = int((time.time() - start_ts) * 1000)
        return final_status, http_status, resp_body, err_msg, duration_ms

    async def _send_callback_once(
        self,
        callback_url: str,
        payload: dict,
        task_id: str,
        attempt: int,
    ) -> Tuple[CallbackStatus, Optional[int], Optional[str], Optional[str], int]:
        parsed = urlparse(callback_url)
        host_lower = (parsed.hostname or "").lower()

        if any(kw in host_lower for kw in ("mock-callback", "mock-cb", "200.ok", "success.callback")):
            start_ts = time.time()
            await asyncio.sleep(0.05)
            resp_body = '{"success":true,"received_at":"' + datetime.now().isoformat() + '","mode":"mock"}'
            duration_ms = int((time.time() - start_ts) * 1000)
            return CallbackStatus.SUCCESS, 200, resp_body, None, duration_ms

        loop = asyncio.get_running_loop()
        return await loop.run_in_executor(
            None,
            lambda: self._send_http_callback_sync(callback_url, payload),
        )

    async def _dispatch_callback(
        self,
        task: TranscriptionTask,
        triggered_by: str = "auto",
        max_attempts: int = 3,
    ) -> None:
        if not task.callback_url:
            return

        payload = self._build_callback_payload(task)
        existing = len(task.callbacks)

        for attempt_offset in range(max_attempts):
            attempt = existing + attempt_offset + 1
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
                triggered_by=triggered_by,
                http_status_code=http_code,
                response_body=resp_body[:500] if resp_body else None,
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
            if attempt_offset + 1 < max_attempts:
                await asyncio.sleep(1 * (attempt_offset + 1))

        logger.error(f"Callback for task {task.task_id} failed after {max_attempts} attempts")

    def _set_task_failure(
        self,
        task: TranscriptionTask,
        exc: Exception,
        default_type: FailureType = FailureType.UNKNOWN,
        default_reason: Optional[str] = None,
        default_suggest_retry: bool = True,
    ) -> None:
        task.status = TaskStatus.FAILED
        task.error_message = default_reason or str(exc)
        task.completed_at = datetime.now()

        if isinstance(exc, ASRError):
            task.failure_type = exc.failure_type
            task.failure_reason = exc.failure_reason
            task.suggest_retry = exc.suggest_retry
        elif isinstance(exc, RecordingURLError):
            task.failure_type = FailureType.RECORDING_UNREACHABLE
            task.failure_reason = default_reason or "录音地址无法访问"
            task.suggest_retry = False
        elif isinstance(exc, TranscriptionFailedError):
            task.failure_type = FailureType.RECORDING_CORRUPTED
            task.failure_reason = default_reason or "录音文件转写失败（文件可能已损坏或格式不支持）"
            task.suggest_retry = False
        else:
            task.failure_type = default_type
            task.failure_reason = default_reason or f"处理异常：{type(exc).__name__}"
            task.suggest_retry = default_suggest_retry

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
            self._set_task_failure(
                task, e,
                default_type=FailureType.RECORDING_UNREACHABLE,
                default_reason=f"录音地址无效: {e}",
                default_suggest_retry=False,
            )
            self.storage.update_task(task)

        except TranscriptionFailedError as e:
            logger.error(f"Task {task_id} transcription failed: {e}")
            self._set_task_failure(
                task, e,
                default_type=FailureType.RECORDING_CORRUPTED,
                default_reason=f"转写失败: {e}",
                default_suggest_retry=False,
            )
            self.storage.update_task(task)

        except ASRError as e:
            logger.error(f"Task {task_id} ASR error: {e}")
            self._set_task_failure(
                task, e,
                default_type=FailureType.ASR_SERVICE_ERROR,
                default_reason=f"语音识别服务异常: {e}",
                default_suggest_retry=True,
            )
            self.storage.update_task(task)

        except Exception as e:
            logger.exception(f"Task {task_id} unexpected error")
            self._set_task_failure(
                task, e,
                default_type=FailureType.ANALYSIS_ERROR,
                default_reason=f"处理异常: {type(e).__name__}: {e}",
                default_suggest_retry=True,
            )
            self.storage.update_task(task)

        finally:
            asyncio.create_task(self._dispatch_callback(task, triggered_by="auto"))

    def retry_callback(
        self,
        task_id: str,
        callback_record_id: Optional[str] = None,
        reviewer: Optional[str] = None,
    ) -> Tuple[bool, Optional[str], Optional[CallbackListResponse]]:
        task = self.storage.get_task(task_id)
        if not task:
            return False, f"任务 {task_id} 不存在", None
        if not task.callback_url:
            return False, f"任务 {task_id} 未配置回调地址", None
        if task.status not in (TaskStatus.COMPLETED, TaskStatus.FAILED):
            return False, f"任务尚未结束（当前状态: {task.status.value}），暂无法回调", None

        asyncio.create_task(
            self._dispatch_callback(task, triggered_by="manual" if reviewer else "manual", max_attempts=1)
        )
        logger.info(f"Manual callback retry triggered for task {task_id} by {reviewer or 'unknown'}")
        history = self.get_callback_history(task_id)
        return True, None, history

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

    def update_risk_conclusion_by_risk_id(
        self,
        task_id: str,
        risk_id: str,
        update: RiskConclusionUpdateRequest,
    ) -> Tuple[bool, Optional[str], Optional[RiskAnalysisResponse]]:
        task = self.storage.get_task(task_id)
        if not task:
            return False, f"任务 {task_id} 不存在", None

        if task.status not in (TaskStatus.COMPLETED, TaskStatus.FAILED):
            return False, f"任务尚未完成（当前状态: {task.status.value}），暂无法处理风险", None

        target: Optional[RiskFragment] = None
        for r in task.risks:
            if r.risk_id == risk_id:
                target = r
                break

        if target is None:
            return False, f"未找到 risk_id={risk_id} 的风险片段", None

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

    def supervisor_stats(
        self,
        date_from: str,
        date_to: str,
        agent_id: Optional[str] = None,
        call_type: Optional[CallType] = None,
        group_by: str = "agent",
    ) -> SupervisorStatsResponse:
        return self.storage.aggregate_supervisor_stats(
            date_from=date_from,
            date_to=date_to,
            agent_id=agent_id,
            call_type=call_type,
            group_by=group_by,
        )


_processor_instance: Optional[TaskProcessor] = None


def get_task_processor() -> TaskProcessor:
    global _processor_instance
    if _processor_instance is None:
        _processor_instance = TaskProcessor()
    return _processor_instance
