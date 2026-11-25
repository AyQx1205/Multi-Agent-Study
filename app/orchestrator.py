from __future__ import annotations

import asyncio
import hashlib
import logging
import uuid
from dataclasses import dataclass
from datetime import datetime
from typing import Any, Callable

from app.agents import AssessmentAgent, CurriculumAgent,EngagementAgent,HintAgent,TutorAgent
from app.config import Settings, get_settings
from app.core.cache import BoundedCache, LockCache
from app.core.content import Attempt, Question
from app.core.errors import IdempotencyConflictError
from app.core.events import Event, EventType,EventBus
from app.core.grading import grade_answer, normalize_answer
from app.core.knowledge_graph import KnowledgeGraph
from app.core.learner_model import LearnerModel
from app.core.spaced_repetition import ReviewItem
from app.llm.client import LLMClient
from app.metrics import metrics
from app.store_base import Store

logger = logging.getLogger(__name__)


class QuestionNotFoundError(LookupError):
    """题目不存在；API 层转成 404。"""


@dataclass(frozen=True)
class SubmissionOutcome:
    """一次答题提交的完整结果。

    除了事件链和 attempt，还带出「这道题答过几次」「是否计入掌握度」
    「是否可以把答案告诉学生」——这三件事决定了教学反馈是否可信。
    """

    events: list[Event]
    attempt: Attempt
    replayed: bool
    attempts_for_question: int
    counts_toward_mastery: bool
    reveal_answer: bool
    # 揭示时才填；由事务里那份 Question 直接带出，避免提交后再查一次库。
    revealed_answer: str | None = None
    revealed_explanation: str = ""


class AgentOrchestrator:
    def __init__(
        self,
        llm: LLMClient | None = None,
        clock: Callable[[],datetime] | None = None,
        settings: Settings | None = None,
        store: Store | None = None,
        graph: KnowledgeGraph | None = None,
    ) -> None:
        """graph 不传时使用 20 节点样例图（主要给单元测试）；
        正式应用在 lifespan 里传入 `build_junior_math_graph()`。"""
        self.settings = settings or get_settings()
        self.store = store
        # 有界：长期运行不会因为「每个学生一份模型 / 一把锁」而无限涨内存。
        # 冷数据被淘汰后从 store 重新加载即可（数据库才是真相源）。
        self._locks: dict[str, asyncio.Lock] = LockCache(
            self.settings.learner_cache_size, name="learner_locks"
        )
        self.event_bus = EventBus()
        self.learner_models: dict[str,LearnerModel] = BoundedCache(
            self.settings.learner_cache_size, name="learner_models"
        )
        state_size = self.settings.agent_state_cache_size
        # 提交事务期间，该学习者的事件先留在内存，由事务统一落库。
        self._submission_cid: dict[str, str] = {}
        # 提交期间产生的非本次事件（如 idle watcher），提交后补写。
        self._deferred_events: dict[str, list[Event]] = {}
        if store is not None:
            # 所有事件都落库；append_event 以 event.id 去重
            for event_type in EventType:
                self.event_bus.subscribe(event_type, self._persist_event)
        self.assessment = AssessmentAgent(
            "AssessmentAgent",
            self.event_bus,
            self.learner_models,
            weakness_threshold=self.settings.weakness_threshold,
        )
        self.tutor = TutorAgent(
            "TutorAgent",
            self.event_bus,
            self.learner_models,
            llm=llm,
            hint_after_attempts=self.settings.hint_after_attempts,
            llm_timeout=self.settings.llm_timeout_seconds,
            llm_for_answers=self.settings.tutor_llm_for_answers,
            cache_size=state_size,
        )
        self.curriculum = CurriculumAgent(
            "CurriculumAgent",
            self.event_bus,
            self.learner_models,
            clock=clock,
            graph=graph,
            mastery_threshold=self.settings.mastery_threshold,
            cache_size=state_size,
        )
        self.hint = HintAgent(
            "HintAgent", self.event_bus, self.learner_models, cache_size=state_size
        )
        self.engagement = EngagementAgent(
            "EngagementAgent",
            self.event_bus,
            self.learner_models,
            clock=clock,
            session_gap_seconds=self.settings.engagement_session_gap_minutes * 60,
            cache_size=state_size,
        )

    # ── 持久化：加载 / 保存 / 事件落库 ──
    def _lock(self, learner_id: str) -> asyncio.Lock:
        """单进程内串行化同一 learner；跨进程/多实例由存储层 learner 锁兜底。

        `LockCache` 只淘汰未被持有的锁：事件循环单线程，`_lock()` 返回后会立刻
        进入 `async with`，中间没有让出点，所以不会出现「同一学习者两把锁」。
        """
        return self._locks.setdefault(learner_id, asyncio.Lock())

    async def _publish_and_collect(
        self, event: Event, *, raise_errors: bool = False
    ) -> list[Event]:
        """发布事件并收集这条链路上的全部事件。

        不能用 `get_history(correlation_id=...)`：那是从全局环形缓冲里过滤，
        并发提交多时本次链路可能已经被挤出窗口，响应的 `events` 会缺事件。
        """
        collected: list[Event] = []

        async def collect(inner: Event) -> None:
            collected.append(inner)

        self.event_bus.subscribe_all(collect)
        try:
            await self.event_bus.publish(event, raise_errors=raise_errors)
        finally:
            self.event_bus.unsubscribe_all(collect)
        return collected

    async def _persist_event(self, event: Event) -> None:
        if self.store is None:
            return
        # submit_transaction 会把整条事件链和 attempt/mastery 一起提交，
        # 这里不能再逐条写库，否则会绕过事务边界。
        active_cid = self._submission_cid.get(event.learner_id)
        if active_cid is not None and event.correlation_id == active_cid:
            # 本次提交的事件会随 attempt/mastery 一起原子落库。
            return
        if self._submission_cid:
            # 只要还有提交事务未结束，其他学习者的事件也不能写 SQLite，
            # 否则会和事务内的 adapter 锁互相等待。
            self._deferred_events.setdefault(event.learner_id, []).append(event)
            return
        try:
            await self.store.append_event(event)
        except Exception:
            # 落库失败不能阻断教学流程；记录后继续
            logger.exception("persist event failed: %s", event.id)

    async def _ensure_loaded(self, learner_id: str) -> None:
        if self.store is None or learner_id in self.learner_models:
            return
        model = await self.store.load_model(learner_id)
        if model is not None:
            self.learner_models[learner_id] = model
            logger.info("loaded learner %s from %s", learner_id, self.store.name)
        reviews = await self.store.load_reviews(learner_id)
        self.curriculum.import_reviews(learner_id, reviews)

    async def ensure_learner(self, learner_id: str) -> None:
        """API 查询进度前从数据库刷新，避免多 worker 下读到旧缓存。"""
        if self.store is None:
            async with self._lock(learner_id):
                await self._ensure_loaded(learner_id)
            return
        async with self._lock(learner_id):
            await self._reload_learner(learner_id)

    def _invalidate_learner(self, learner_id: str) -> None:
        self.learner_models.pop(learner_id, None)
        self.curriculum.import_reviews(learner_id, {})

    async def _reload_learner(self, learner_id: str) -> None:
        if self.store is None:
            return
        model = await self.store.load_model(learner_id)
        if model is None:
            self.learner_models.pop(learner_id, None)
        else:
            self.learner_models[learner_id] = model
        reviews = await self.store.load_reviews(learner_id)
        self.curriculum.import_reviews(learner_id, reviews)

    async def _flush_deferred_events(self) -> None:
        if self.store is None or not self._deferred_events:
            return
        pending = self._deferred_events
        self._deferred_events = {}
        for learner_id, events in pending.items():
            if learner_id in self._submission_cid:
                # 该学习者仍有事务在跑，交给它自己的 finally 补写。
                self._deferred_events.setdefault(learner_id, []).extend(events)
                continue
            for event in events:
                try:
                    await self.store.append_event(event)
                except Exception:
                    logger.exception("persist deferred event failed: %s", event.id)

    async def submit_question(
        self,
        learner_id: str,
        question_id: str,
        answer: str,
        time_spent: float = 0.0,
        correlation_id: str | None = None,
        idempotency_key: str | None = None,
    ) -> SubmissionOutcome:
        """可信答题入口：从题库取题，服务端判分，再走事件链。

        整条链路（attempt + mastery + review + events）在存储层的一个事务里提交，
        并且用 learner 级锁串行化同一学生的并发提交。重复幂等键不会再次计算。

        这里是**唯一**的答题入口：客户端不能自己传 is_correct，也不能走一条
        不带判分/幂等/事务的旁路。
        """
        if self.store is None:
            raise RuntimeError("question submission requires a store")

        question = await self.store.get_question(question_id)
        if question is None:
            raise QuestionNotFoundError(question_id)

        # 事务内不能再调用存储层（SQLite 适配器锁不可重入），所以「这道题此前
        # 答过几次」在拿锁之后、进事务之前读，见下面的 `_lock` 分支。
        attempts_for_question = 1
        counts_toward_mastery = True
        prior_attempts = 0
        reveal_after = max(1, self.settings.answer_reveal_after_attempts)

        # 指纹必须用和判分同一套归一化：否则「x = 4」和「x=4」判分相同、
        # 指纹不同，客户端重试会拿到 409 而不是幂等重放。
        fingerprint = hashlib.sha256(
            f"{question.id}\0{normalize_answer(answer)}".encode("utf-8")
        ).hexdigest()
        cid = correlation_id or f"c-{uuid.uuid4().hex[:8]}"

        async def build(
            model: LearnerModel, reviews: dict[str, ReviewItem]
        ) -> tuple[Attempt, list[Event]]:
            # 模型和复习状态由存储事务加载；事件先缓冲，提交时统一写库。
            self.learner_models[learner_id] = model
            self.curriculum.import_reviews(learner_id, reviews)
            is_correct = grade_answer(question, answer)
            attempt = Attempt(
                learner_id=learner_id,
                question_id=question.id,
                knowledge_id=question.knowledge_id,
                answer=answer,
                is_correct=is_correct,
                time_spent_seconds=max(0.0, min(float(time_spent), 3600.0)),
                idempotency_key=idempotency_key,
                request_fingerprint=fingerprint,
            )
            self.event_bus.begin_strict(cid)
            try:
                # 用收集器而不是全局历史：全局环形缓冲会把本次链路挤出去。
                events = await self._publish_and_collect(
                    Event(
                        type=EventType.STUDENT_SUBMISSION,
                        source="api",
                        learner_id=learner_id,
                        data={
                            "knowledge_id": question.knowledge_id,
                            "is_correct": is_correct,
                            "time_spent_seconds": attempt.time_spent_seconds,
                            "question_id": question.id,
                            "idempotency_key": idempotency_key,
                            "attempts_for_question": attempts_for_question,
                            "counts_toward_mastery": counts_toward_mastery,
                            # 告诉 Tutor：这条链路在数据库事务里，禁止发起 LLM 调用。
                            "in_submission": True,
                        },
                        correlation_id=cid,
                    ),
                    raise_errors=True,
                )
            finally:
                self.event_bus.end_strict(cid)
            return attempt, events

        try:
            try:
                async with self._lock(learner_id):
                    # 在锁内统计：锁外读会让并发提交都认为「这是第一次作答」，
                    # 重复作答就会重复计入掌握度。
                    prior_attempts = await self.store.count_question_attempts(
                        learner_id, question.id
                    )
                    attempts_for_question = prior_attempts + 1
                    counts_toward_mastery = prior_attempts == 0
                    # 标记必须在锁内设置：放到锁外会被并发提交互相覆盖，
                    # 导致后者的事件绕过事务直接写库（SQLite 上会死锁）。
                    self._submission_cid[learner_id] = cid
                    try:
                        attempt, events, replayed = await self.store.submit_transaction(
                            learner_id,
                            idempotency_key,
                            fingerprint,
                            build,
                        )
                    finally:
                        self._submission_cid.pop(learner_id, None)
            except BaseException:
                # 事务已回滚；清掉可能被 Agent 改过的内存态，下次从库里重载。
                self._invalidate_learner(learner_id)
                raise
        finally:
            # 必须在 adapter 锁释放后再补写，避免其他学习者的事件死锁。
            await self._flush_deferred_events()

        if replayed:
            # 同一 key 可能由另一个 worker 写入；本进程缓存需要刷新。
            attempts_for_question = max(1, prior_attempts)
            counts_toward_mastery = False
            try:
                async with self._lock(learner_id):
                    await self._reload_learner(learner_id)
            except Exception:
                self._invalidate_learner(learner_id)

        if self.settings.tutor_llm_for_answers and not replayed:
            # LLM 讲解放到事务提交之后、锁之外：进事务会把数据库锁的持有时间
            # 从毫秒拉到秒级，直接把并发写能力打没。
            enriched = await self._enrich_teaching_response(
                learner_id, question.knowledge_id, cid
            )
            if enriched is not None:
                events = [*events, enriched]

        reveal_answer = attempt.is_correct or attempts_for_question >= reveal_after
        metrics.inc("submissions_total", result="replayed" if replayed else "issued")
        metrics.inc("submission_mastery_counted_total", counted=str(counts_toward_mastery))
        return SubmissionOutcome(
            events=events,
            attempt=attempt,
            replayed=replayed,
            attempts_for_question=attempts_for_question,
            counts_toward_mastery=counts_toward_mastery,
            # 答对就揭示；答错则要等尝试次数达到阈值，避免「一次乱答换答案」。
            reveal_answer=reveal_answer,
            revealed_answer=question.answer if reveal_answer else None,
            revealed_explanation=question.explanation if reveal_answer else "",
        )

    async def _enrich_teaching_response(
        self, learner_id: str, knowledge_id: str, correlation_id: str
    ) -> Event | None:
        """答题事务提交后，在锁外补一条 LLM 讲解（仅当 TUTOR_LLM_FOR_ANSWERS=true）。

        失败要吞掉：讲解是锦上添花，不能因为模型超时把已经成功的提交变成 500。
        """
        model = self.learner_models.get(learner_id)
        if model is None:
            return None
        state = model.peek_state(knowledge_id)
        mastery = state.mastery if state is not None else model.bkt.p_init
        level = state.level.value if state is not None else "not_started"
        try:
            return await self.tutor.enrich_answer(
                learner_id=learner_id,
                knowledge_id=knowledge_id,
                level=level,
                mastery=mastery,
                correlation_id=correlation_id,
            )
        except Exception:
            logger.exception("post-submit teaching enrichment failed")
            return None

    async def get_questions(
        self,
        knowledge_id: str | None = None,
        grade: int | None = None,
        limit: int = 100,
    ) -> list[Question]:
        if self.store is None:
            return []
        return await self.store.list_questions(knowledge_id, grade, limit)

    async def get_question(self, question_id: str) -> Question | None:
        if self.store is None:
            return None
        return await self.store.get_question(question_id)

    async def get_attempts(self, learner_id: str, limit: int = 50) -> list[Attempt]:
        if self.store is None:
            return []
        return await self.store.list_attempts(learner_id, limit)

    async def ask_question(self,learner_id: str,knowledge_id: str,question: str) -> list[Event]:
        # 只在加载模型时持锁。Tutor 处理提问会调 LLM（8~20 秒），
        # 持锁调用会把同一学生的 /submit 一起堵住。
        async with self._lock(learner_id):
            await self._ensure_loaded(learner_id)
        cid = f"c-{uuid.uuid4().hex[:8]}"
        return await self._publish_and_collect(
            Event(
                type=EventType.STUDENT_QUESTION,
                source="api",
                learner_id=learner_id,
                data={
                    "knowledge_id": knowledge_id,
                    "question": question
                },
                correlation_id=cid
            )
        )

    async def send_message(self,learner_id: str,message: str,knowledge_id: str = "general") -> list[Event]:
        async with self._lock(learner_id):
            await self._ensure_loaded(learner_id)
        cid = f"c-{uuid.uuid4().hex[:8]}"
        return await self._publish_and_collect(
            Event(
                type=EventType.STUDENT_MESSAGE,
                source="api",
                learner_id=learner_id,
                data={
                    "message": message,
                    "knowledge_id": knowledge_id
                },
                correlation_id=cid
            )
        )

    def get_learner_progress(self,learner_id: str) -> dict[str,Any]:
        model = self.learner_models.get(learner_id)
        if model is None:
            return {"learner_id": learner_id,"status": "no_data"}
        return {
            "learner_id": learner_id,
            "progress":model.get_overall_progress(),
            "weak_points":[
                {"id":s.knowledge_id, "mastery": round(s.mastery,4)} for s in model.get_weak_points()
            ],
            "strong_points":[
                {"id":s.knowledge_id, "mastery": round(s.mastery,4)} for s in model.get_strong_points()
            ],
        }

