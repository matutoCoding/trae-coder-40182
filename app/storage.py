from abc import ABC, abstractmethod
from typing import Dict, List, Optional, Tuple
from datetime import datetime
import uuid

from .schemas import (
    TaskStatus,
    CallType,
    FailureType,
    RiskConclusion,
    CallbackStatus,
    CallbackRecord,
    TranscriptSegment,
    RiskFragment,
    RecordingSubmitRequest,
    TranscriptResult,
    RiskAnalysisResponse,
    TaskSummary,
    AgentDailyStats,
    SupervisorStatsResponse,
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
        self.callback_url: Optional[str] = request.callback_url
        self.submitted_at: datetime = datetime.now()
        self.started_at: Optional[datetime] = None
        self.completed_at: Optional[datetime] = None
        self.duration_seconds: Optional[float] = None
        self.segments: List[TranscriptSegment] = []
        self.risks: List[RiskFragment] = []
        self.has_risk: bool = False
        self.callbacks: List[CallbackRecord] = []
        self.error_message: Optional[str] = None
        self.failure_type: Optional[FailureType] = None
        self.failure_reason: Optional[str] = None
        self.suggest_retry: Optional[bool] = None

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
            callback_url=self.callback_url,
            error_message=self.error_message,
            failure_type=self.failure_type,
            failure_reason=self.failure_reason,
            suggest_retry=self.suggest_retry,
        )

    def to_risk_analysis_response(self) -> RiskAnalysisResponse:
        high = sum(1 for r in self.risks if r.risk_level in (RiskLevel.HIGH, RiskLevel.CRITICAL))
        medium = sum(1 for r in self.risks if r.risk_level == RiskLevel.MEDIUM)
        low = sum(1 for r in self.risks if r.risk_level == RiskLevel.LOW)
        unhandled = sum(1 for r in self.risks if r.conclusion == RiskConclusion.UNHANDLED)
        confirmed = sum(1 for r in self.risks if r.conclusion == RiskConclusion.CONFIRMED_VIOLATION)
        false_alarm = sum(1 for r in self.risks if r.conclusion == RiskConclusion.FALSE_ALARM)
        reviewed = sum(1 for r in self.risks if r.conclusion in (RiskConclusion.REVIEWED_NO_ISSUE, RiskConclusion.PENDING_REVIEW))
        return RiskAnalysisResponse(
            task_id=self.task_id,
            status=self.status,
            agent_id=self.agent_id,
            total_risks=len(self.risks),
            high_risk_count=high,
            medium_risk_count=medium,
            low_risk_count=low,
            unhandled_count=unhandled,
            confirmed_count=confirmed,
            false_alarm_count=false_alarm,
            reviewed_count=reviewed,
            risks=self.risks,
        )

    def to_task_summary(self) -> TaskSummary:
        high_risk = sum(1 for r in self.risks if r.risk_level in (RiskLevel.HIGH, RiskLevel.CRITICAL))
        unhandled = sum(1 for r in self.risks if r.conclusion == RiskConclusion.UNHANDLED)
        confirmed = sum(1 for r in self.risks if r.conclusion == RiskConclusion.CONFIRMED_VIOLATION)
        last_cb = self.callbacks[-1] if self.callbacks else None
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
            unhandled_risk_count=unhandled,
            confirmed_risk_count=confirmed,
            has_callback=bool(self.callback_url),
            last_callback_status=last_cb.status if last_cb else None,
            error_message=self.error_message,
            failure_type=self.failure_type,
            failure_reason=self.failure_reason,
            suggest_retry=self.suggest_retry,
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
    def list_tasks(
        self,
        agent_id: Optional[str] = None,
        status: Optional[TaskStatus] = None,
        call_type: Optional[CallType] = None,
        submitted_after: Optional[datetime] = None,
        submitted_before: Optional[datetime] = None,
    ) -> List[TranscriptionTask]:
        ...

    @abstractmethod
    def add_callback_record(self, task_id: str, record: CallbackRecord) -> None:
        ...

    @abstractmethod
    def get_callback_records(self, task_id: str) -> List[CallbackRecord]:
        ...

    @abstractmethod
    def aggregate_supervisor_stats(
        self,
        date_from: str,
        date_to: str,
        agent_id: Optional[str] = None,
        call_type: Optional[CallType] = None,
        group_by: str = "agent",
    ) -> SupervisorStatsResponse:
        ...


def _task_date_key(t: TranscriptionTask) -> str:
    return t.submitted_at.strftime("%Y-%m-%d")


def _build_agent_stat(
    agent_id: str,
    date_str: str,
    tasks: List[TranscriptionTask],
    call_type: Optional[CallType] = None,
) -> AgentDailyStats:
    total = len(tasks)
    failed = sum(1 for t in tasks if t.status == TaskStatus.FAILED)
    with_risk = sum(1 for t in tasks if t.has_risk)
    total_risks = sum(len(t.risks) for t in tasks)
    confirmed = sum(
        1 for t in tasks for r in t.risks if r.conclusion == RiskConclusion.CONFIRMED_VIOLATION
    )
    unhandled = sum(
        1 for t in tasks for r in t.risks if r.conclusion == RiskConclusion.UNHANDLED
    )
    return AgentDailyStats(
        agent_id=agent_id,
        call_type=call_type,
        date=date_str,
        total_tasks=total,
        failed_tasks=failed,
        failed_rate=round(failed / total, 4) if total else 0.0,
        tasks_with_risk=with_risk,
        risk_rate=round(with_risk / total, 4) if total else 0.0,
        total_risks=total_risks,
        confirmed_violations=confirmed,
        unhandled_risks=unhandled,
    )


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

    def list_tasks(
        self,
        agent_id: Optional[str] = None,
        status: Optional[TaskStatus] = None,
        call_type: Optional[CallType] = None,
        submitted_after: Optional[datetime] = None,
        submitted_before: Optional[datetime] = None,
    ) -> List[TranscriptionTask]:
        tasks = list(self._tasks.values())
        if agent_id:
            tasks = [t for t in tasks if t.agent_id == agent_id]
        if status:
            tasks = [t for t in tasks if t.status == status]
        if call_type:
            tasks = [t for t in tasks if t.call_type == call_type]
        if submitted_after:
            tasks = [t for t in tasks if t.submitted_at >= submitted_after]
        if submitted_before:
            tasks = [t for t in tasks if t.submitted_at <= submitted_before]
        return sorted(tasks, key=lambda t: t.submitted_at, reverse=True)

    def add_callback_record(self, task_id: str, record: CallbackRecord) -> None:
        task = self._tasks.get(task_id)
        if task:
            task.callbacks.append(record)

    def get_callback_records(self, task_id: str) -> List[CallbackRecord]:
        task = self._tasks.get(task_id)
        if not task:
            return []
        return sorted(task.callbacks, key=lambda r: r.sent_at or r.completed_at or datetime.min, reverse=True)

    def aggregate_supervisor_stats(
        self,
        date_from: str,
        date_to: str,
        agent_id: Optional[str] = None,
        call_type: Optional[CallType] = None,
        group_by: str = "agent",
    ) -> SupervisorStatsResponse:
        try:
            dt_from = datetime.strptime(date_from, "%Y-%m-%d")
        except ValueError:
            dt_from = datetime.min
        try:
            dt_to = datetime.strptime(date_to, "%Y-%m-%d").replace(hour=23, minute=59, second=59)
        except ValueError:
            dt_to = datetime.max

        all_tasks = list(self._tasks.values())
        filtered = [
            t for t in all_tasks
            if t.submitted_at >= dt_from and t.submitted_at <= dt_to
            and (agent_id is None or t.agent_id == agent_id)
            and (call_type is None or t.call_type == call_type)
        ]

        total_tasks = len(filtered)
        total_failed = sum(1 for t in filtered if t.status == TaskStatus.FAILED)
        total_risks = sum(len(t.risks) for t in filtered)
        total_confirmed = sum(
            1 for t in filtered for r in t.risks if r.conclusion == RiskConclusion.CONFIRMED_VIOLATION
        )

        groups: Dict[Tuple[str, str, Optional[CallType]], List[TranscriptionTask]] = {}
        for t in filtered:
            d = _task_date_key(t)
            if group_by == "agent_call_type":
                key = (t.agent_id, d, t.call_type)
            else:
                key = (t.agent_id, d, None)
            groups.setdefault(key, []).append(t)

        items: List[AgentDailyStats] = []
        for (aid, d, ct), ts in sorted(groups.items()):
            items.append(_build_agent_stat(aid, d, ts, ct))

        items.sort(key=lambda s: (-s.total_tasks, s.agent_id, s.date))

        return SupervisorStatsResponse(
            date_from=date_from,
            date_to=date_to,
            group_by=group_by,
            total_tasks=total_tasks,
            total_failed=total_failed,
            total_risks=total_risks,
            total_confirmed=total_confirmed,
            items=items,
        )


_storage_instance: Optional[TaskStorage] = None


def get_storage() -> TaskStorage:
    global _storage_instance
    if _storage_instance is None:
        _storage_instance = InMemoryTaskStorage()
    return _storage_instance
