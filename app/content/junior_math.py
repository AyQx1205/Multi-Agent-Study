"""初中数学内容入口：知识图谱 + 题库。"""

from __future__ import annotations

from app.content.junior_questions import GENERATORS
from app.content.specs import NODES, NodeSpec
from app.core.content import Question
from app.core.knowledge_graph import KnowledgeGraph, KnowledgeNode


def build_junior_math_graph() -> KnowledgeGraph:
    graph = KnowledgeGraph()
    for spec in NODES:
        graph.add_node(
            KnowledgeNode(
                id=spec.id,
                name=spec.name,
                subject="math",
                grade=spec.grade,
                difficulty=spec.difficulty,
                prerequisites=list(spec.prerequisites),
                tags=list(spec.tags),
            )
        )
    return graph


def build_junior_math_questions() -> list[Question]:
    questions: list[Question] = []
    for spec in NODES:
        generator = GENERATORS.get(spec.kind)
        if generator is None:
            raise ValueError(f"missing question generator for {spec.id} ({spec.kind})")
        generated = generator(spec)
        if len(generated) != 3:
            raise ValueError(f"{spec.id} generated {len(generated)} questions, expected 3")
        questions.extend(generated)
    return questions


def content_summary() -> dict[str, int]:
    return {
        "grades": len({spec.grade for spec in NODES}),
        "knowledge_nodes": len(NODES),
        "questions": len(NODES) * 3,
    }


def node_by_id(node_id: str) -> NodeSpec | None:
    return next((spec for spec in NODES if spec.id == node_id), None)