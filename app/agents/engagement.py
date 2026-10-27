from __future__ import annotations

import asyncio
import logging
from datetime import datetime
from enum import Enum
from typing import Any, Callable

from app.agents.base import BaseAgent
from app.core.events import Event, EventType

logger = logging.getLogger(__name__)


class LearningState(str, Enum):
    FOCUSED = "focused"
    STRUGGLING = "struggling"
    FRUSTRATED = "frustrated"
    BORED = "bored"
    FATIGUED = "fatigued"
    IDLE = "idle"


class LearnerEngagement:
    """单个学习者的行为特征窗口。"""

    def __init__(self, learner_id: str, clock: Callable[[], datetime] | None = None) -> None:
        self.learner_id = learner_id
        self._clock = clock or datetime.now
        self.state = LearningState.FOCUSED
        self.recent_results: list[bool] = []
        self.recent_response_times: list[float] = []
        self.session_start = self._clock()
        self.last_activity = self._clock()
        self.consecutive_errors = 0
        self.consecutive_correct = 0
        self.total_interactions = 0
        self.encouragement_count = 0

    @property
    def session_duration_minutes(self) -> float:
        return (self._clock() - self.session_start).total_seconds() / 60

    @property
    def idle_seconds(self) -> float:
        return (self._clock() - self.last_activity).total_seconds()

    @property
    def recent_accuracy(self) -> float:
        window = self.recent_results[-10:]
        return sum(window) / len(window) if window else 0.5

    @property
    def avg_response_time(self) -> float:
        window = self.recent_response_times[-10:]
        return sum(window) / len(window) if window else 0.0


class EngagementAgent(BaseAgent):
    def __init__(self, *args: Any, clock: Callable[[], datetime] | None = None, **kwargs: Any) -> None:
        self._clock = clock or datetime.now
        self._engagements: dict[str, LearnerEngagement] = {}
        super().__init__(*args, **kwargs)

    @property
    def subscribed_events(self) -> list[EventType]:
        return [
            EventType.STUDENT_SUBMISSION,
            EventType.ASSESSMENT_COMPLETE,
            EventType.STUDENT_MESSAGE,
        ]

    async def handle_event(self, event: Event) -> None:
        if event.type is EventType.STUDENT_SUBMISSION:
            await self._track_submission(event)
        elif event.type is EventType.ASSESSMENT_COMPLETE:
            await self._analyze(event)
        elif event.type is EventType.STUDENT_MESSAGE:
            self._get(event.learner_id).last_activity = self._clock()

    def _get(self, learner_id: str) -> LearnerEngagement:
        if learner_id not in self._engagements:
            self._engagements[learner_id] = LearnerEngagement(learner_id, self._clock)
        return self._engagements[learner_id]

    async def _track_submission(self, event: Event) -> None:
        eng = self._get(event.learner_id)
        is_correct = bool(event.data.get("is_correct", False))
        time_spent = float(event.data.get("time_spent_seconds", 0) or 0)

        eng.last_activity = self._clock()
        eng.total_interactions += 1
        eng.recent_results.append(is_correct)
        if time_spent > 0:
            eng.recent_response_times.append(time_spent)

        if is_correct:
            eng.consecutive_correct += 1
            eng.consecutive_errors = 0
        else:
            eng.consecutive_errors += 1
            eng.consecutive_correct = 0

        eng.recent_results = eng.recent_results[-20:]
        eng.recent_response_times = eng.recent_response_times[-20:]

    async def start_idle_watcher(self, interval: float = 30.0) -> None:
        """后台定时器：解决「IDLE 只在答题时才判定」的死代码问题。"""
        while True:
            await asyncio.sleep(interval)
            await self.check_idle()

    async def check_idle(self, idle_threshold_seconds: float = 300) -> None:
        for eng in list(self._engagements.values()):
            if eng.idle_seconds > idle_threshold_seconds and eng.state is not LearningState.IDLE:
                eng.state = LearningState.IDLE
                logger.info("[Engagement] %s -> idle", eng.learner_id)
                await self.emit(
                    EventType.ENCOURAGEMENT,
                    eng.learner_id,
                    {
                        "message": "还在吗？需要帮忙随时说。",
                        "type": "idle_check",
                        "idle_seconds": round(eng.idle_seconds, 1),
                    },
                )

    async def _analyze(self, event: Event) -> None:
        # 提问也会触发 complete，但不是答题，跳过状态判定
        if event.data.get("context") == "student_question":
            self._get(event.learner_id).last_activity = self._clock()
            return

        eng = self._get(event.learner_id)
        previous = eng.state
        state = self._detect_state(eng)
        eng.state = state
        if state is not previous:
            logger.info("[Engagement] %s: %s -> %s", event.learner_id, previous.value, state.value)

        if state is LearningState.FRUSTRATED:
            await self._intervene_frustration(event.learner_id, eng, event.correlation_id)
        elif state is LearningState.BORED:
            await self._intervene_boredom(event.learner_id, eng, event.correlation_id)
        elif state is LearningState.FATIGUED:
            await self._intervene_fatigue(event.learner_id, eng, event.correlation_id)
        elif state is LearningState.FOCUSED and eng.consecutive_correct >= 3:
            await self._encourage(event.learner_id, eng, event.correlation_id)

    def _detect_state(self, eng: LearnerEngagement) -> LearningState:
        """判定顺序 = 优先级，先判更紧急的。"""
        if eng.idle_seconds > 300:
            return LearningState.IDLE
        if eng.consecutive_errors >= 3:
            return LearningState.FRUSTRATED
        if eng.session_duration_minutes > 45 and eng.recent_accuracy < 0.5:
            return LearningState.FATIGUED
        if eng.recent_accuracy > 0.9 and eng.consecutive_correct >= 5:
            return LearningState.BORED
        if eng.consecutive_errors >= 1:
            return LearningState.STRUGGLING
        return LearningState.FOCUSED

    async def _intervene_frustration(
        self, learner_id: str, eng: LearnerEngagement, correlation_id: str | None
    ) -> None:
        await self.emit(
            EventType.ENGAGEMENT_ALERT,
            learner_id,
            {
                "alert_type": "frustration",
                "consecutive_errors": eng.consecutive_errors,
                "recent_accuracy": round(eng.recent_accuracy, 4),
                "message": "别灰心！犯错是学习的一部分，我们换一个方式来学习。",
            },
            correlation_id=correlation_id,
        )
        await self.emit(
            EventType.PACE_ADJUSTMENT,
            learner_id,
            {"action": "slow_down", "reason": "frustration_detected"},
            correlation_id=correlation_id,
        )

    async def _intervene_boredom(
        self, learner_id: str, eng: LearnerEngagement, correlation_id: str | None
    ) -> None:
        await self.emit(
            EventType.ENGAGEMENT_ALERT,
            learner_id,
            {
                "alert_type": "boredom",
                "consecutive_correct": eng.consecutive_correct,
                "recent_accuracy": round(eng.recent_accuracy, 4),
                "message": "你表现得非常棒！这些题目对你来说简单了，挑战更难的内容吧！",
            },
            correlation_id=correlation_id,
        )
        await self.emit(
            EventType.PACE_ADJUSTMENT,
            learner_id,
            {"action": "speed_up", "reason": "boredom_detected"},
            correlation_id=correlation_id,
        )

    async def _intervene_fatigue(
        self, learner_id: str, eng: LearnerEngagement, correlation_id: str | None
    ) -> None:
        await self.emit(
            EventType.ENCOURAGEMENT,
            learner_id,
            {
                "message": f"你已经学习了 {eng.session_duration_minutes:.0f} 分钟，建议休息 10 分钟再继续。",
                "type": "fatigue_break",
                "session_minutes": round(eng.session_duration_minutes, 1),
            },
            correlation_id=correlation_id,
        )

    async def _encourage(
        self, learner_id: str, eng: LearnerEngagement, correlation_id: str | None
    ) -> None:
        if eng.encouragement_count % 3 == 0:
            await self.emit(
                EventType.ENCOURAGEMENT,
                learner_id,
                {
                    "message": f"连续 {eng.consecutive_correct} 题全对！保持这个状态。",
                    "type": "positive_streak",
                    "streak": eng.consecutive_correct,
                },
                correlation_id=correlation_id,
            )
        eng.encouragement_count += 1