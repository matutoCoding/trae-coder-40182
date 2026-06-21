from abc import ABC, abstractmethod
from typing import Dict, List, Optional
from datetime import datetime
import uuid

from .schemas import (
    TaskStatus,
    CallType,
    TranscriptSegment,
    RiskFragment,
    RecordingSubmitRequest,
    TranscriptResult,
    RiskAnalysisResponse,
    TaskSummary,
    RiskLevel,
)


class TranscriptionTask:
    def __init__(self, request: RecordingSubmitRequest):
        self.task_id: str = f"TASK_{datetime.now().strftime('%Y%m%d_%H%M%S')}_{uuid.uuid4().hex[:6].upper()}"
        self.status: TaskStatus = TaskStatus.PENDING
        self.recording_url: str = request.recording_url
        self.agent_id: str = request.agent_id
        self.call_type: CallType = request.call_type
        self.call_id: Optional[str] = request.call_id
        self.customer_id: Optional[str] = request.customer_id
        self.call_start_time: Optional[datetime] = request.call_start_time
        self.mock_text: Optional[str] = request.mock_text
        self.submitted_at: datetime = datetime.now()
        self.started_at: Optional[datetime] = None
        self.completed_at: Optional[datetime] = None
        self.duration_seconds: Optional[float] = None
        self.segments: List[TranscriptSegment] = []
        self.risks: List[RiskFragment] = []
        self.has_risk: bool = False
        self.error_message: Optional[str] = None

    def to_transcript_result(self) -> TranscriptResult:
        return TranscriptResult(
            task_id=self.task_id,
            status=self.status,
            agent_id=self.agent_id,
            call_type=self.call_type,
            call_id=self.call_id,
            submitted_at=self.submitted_at,
            started_at=self.started_at,
            completed_at=self.completed_at,
            duration_seconds=self.duration_seconds,
            segments=self.segments,
            has_risk=self.has_risk,
            error_message=self.error_message,
        )

    def to_risk_analysis_response(self) -> RiskAnalysisResponse:
        high = sum(1 for r in self.risks if r.risk_level in (RiskLevel.HIGH, RiskLevel.CRITICAL))
        medium = sum(1 for r in self.risks if r.risk_level == RiskLevel.MEDIUM)
        low = sum(1 for r in self.risks if r.risk_level == RiskLevel.LOW)
        return RiskAnalysisResponse(
            task_id=self.task_id,
            status=self.status,
            agent_id=self.agent_id,
            total_risks=len(self.risks),
            high_risk_count=high,
            medium_risk_count=medium,
            low_risk_count=low,
            risks=self.risks,
        )

    def to_task_summary(self) -> TaskSummary:
        high_risk = sum(1 for r in self.risks if r.risk_level in (RiskLevel.HIGH, RiskLevel.CRITICAL))
        return TaskSummary(
            task_id=self.task_id,
            status=self.status,
            agent_id=self.agent_id,
            call_type=self.call_type,
            call_id=self.call_id,
            submitted_at=self.submitted_at,
            completed_at=self.completed_at,
            duration_seconds=self.duration_seconds,
            has_risk=self.has_risk,
            risk_count=len(self.risks),
            high_risk_count=high_risk,
            error_message=self.error_message,
        )


class TaskStorage(ABC):
    @abstractmethod
    def create_task(self, request: RecordingSubmitRequest) -> TranscriptionTask:
        ...

    @abstractmethod
    def get_task(self, task_id: str) -> Optional[TranscriptionTask]:
        ...

    @abstractmethod
    def update_task(self, task: TranscriptionTask) -> None:
        ...

    @abstractmethod
    def list_tasks(self, agent_id: Optional[str] = None, status: Optional[TaskStatus] = None) -> List[TranscriptionTask]:
        ...


class InMemoryTaskStorage(TaskStorage):
    def __init__(self):
        self._tasks: Dict[str, TranscriptionTask] = {}

    def create_task(self, request: RecordingSubmitRequest) -> TranscriptionTask:
        task = TranscriptionTask(request)
        self._tasks[task.task_id] = task
        return task

    def get_task(self, task_id: str) -> Optional[TranscriptionTask]:
        return self._tasks.get(task_id)

    def update_task(self, task: TranscriptionTask) -> None:
        if task.task_id in self._tasks:
            self._tasks[task.task_id] = task

    def list_tasks(self, agent_id: Optional[str] = None, status: Optional[TaskStatus] = None) -> List[TranscriptionTask]:
        tasks = list(self._tasks.values())
        if agent_id:
            tasks = [t for t in tasks if t.agent_id == agent_id]
        if status:
            tasks = [t for t in tasks if t.status == status]
        return sorted(tasks, key=lambda t: t.submitted_at, reverse=True)


_storage_instance: Optional[TaskStorage] = None


def get_storage() -> TaskStorage:
    global _storage_instance
    if _storage_instance is None:
        _storage_instance = InMemoryTaskStorage()
    return _storage_instance
