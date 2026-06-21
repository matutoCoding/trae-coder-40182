import asyncio
import logging
from datetime import datetime
from typing import Optional

from .schemas import (
    TaskStatus,
    RecordingSubmitRequest,
    TranscriptResult,
    RiskAnalysisResponse,
    TaskSummary,
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
    ) -> Optional[RiskAnalysisResponse]:
        task = self.storage.get_task(task_id)
        if not task:
            return None

        risks = list(task.risks)
        if risk_level:
            risks = [r for r in risks if r.risk_level == risk_level]
        if risk_category:
            risks = [r for r in risks if r.risk_category == risk_category]
        if speaker:
            risks = [r for r in risks if r.speaker == speaker]

        high = sum(1 for r in risks if r.risk_level in (RiskLevel.HIGH, RiskLevel.CRITICAL))
        medium = sum(1 for r in risks if r.risk_level == RiskLevel.MEDIUM)
        low = sum(1 for r in risks if r.risk_level == RiskLevel.LOW)

        return RiskAnalysisResponse(
            task_id=task.task_id,
            status=task.status,
            agent_id=task.agent_id,
            total_risks=len(risks),
            high_risk_count=high,
            medium_risk_count=medium,
            low_risk_count=low,
            risks=risks,
        )

    def list_tasks(
        self,
        agent_id: Optional[str] = None,
        status: Optional[TaskStatus] = None,
        page: int = 1,
        page_size: int = 20,
    ) -> TaskListResponse:
        all_tasks = self.storage.list_tasks(agent_id=agent_id, status=status)
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
