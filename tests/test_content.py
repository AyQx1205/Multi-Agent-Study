"""初中数学内容：图谱、题库与服务端判分。"""

from __future__ import annotations

from collections import Counter

from app.content import (
    build_junior_math_graph,
    build_junior_math_questions,
    content_summary,
)
from app.core.content import QuestionType
from app.core.grading import grade_answer


def test_content_summary_and_node_count() -> None:
    summary = content_summary()
    assert summary == {"grades": 3, "knowledge_nodes": 60, "questions": 180}


def test_graph_is_a_dag_with_valid_prerequisites() -> None:
    graph = build_junior_math_graph()
    assert len(graph.nodes) == 60
    assert len(graph.topological_sort()) == 60

    for node in graph.nodes.values():
        assert 7 <= node.grade <= 9
        for prerequisite in node.prerequisites:
            assert prerequisite in graph.nodes, f"{node.id} -> unknown {prerequisite}"
        assert node.id not in graph.get_all_prerequisites(node.id)


def test_every_node_has_three_questions() -> None:
    graph = build_junior_math_graph()
    questions = build_junior_math_questions()
    counts = Counter(question.knowledge_id for question in questions)
    assert set(counts) == set(graph.nodes)
    assert set(counts.values()) == {3}
    assert len({question.id for question in questions}) == len(questions)


def test_choice_questions_have_valid_options() -> None:
    for question in build_junior_math_questions():
        if question.type is not QuestionType.CHOICE:
            continue
        assert question.answer in "ABCD"
        assert len(question.options) == 4
        assert len(set(question.options)) == 4
        index = "ABCD".index(question.answer)
        assert question.options[index]


def test_every_question_grades_its_own_answer() -> None:
    for question in build_junior_math_questions():
        assert grade_answer(question, question.answer), question.id
        assert not grade_answer(question, "__definitely_wrong__"), question.id


def test_numeric_equivalent_answers() -> None:
    questions = {q.id: q for q in build_junior_math_questions()}
    fraction = questions["q-rational_add_sub-3"]
    assert fraction.answer == "-1/6"
    assert grade_answer(fraction, "-2/12")
    assert grade_answer(fraction, "-0.1666666667")

    decimal = questions["q-fraction_expr-3"]
    assert decimal.answer == "1/2"
    assert grade_answer(decimal, "0.5")
    assert grade_answer(decimal, "2/4")


def test_equation_answer_accepts_variable_prefix() -> None:
    questions = {q.id: q for q in build_junior_math_questions()}
    equation = questions["q-linear_eq-1"]
    assert grade_answer(equation, "4")
    assert grade_answer(equation, "x=4")


def test_choice_accepts_letter_or_text() -> None:
    questions = {q.id: q for q in build_junior_math_questions()}
    question = questions["q-rational_numbers-1"]
    assert grade_answer(question, question.answer)
    correct_text = question.options["ABCD".index(question.answer)]
    assert grade_answer(question, correct_text)
    wrong_letter = "D" if question.answer != "D" else "A"
    assert not grade_answer(question, wrong_letter)


def test_curated_geometry_questions_are_present() -> None:
    questions = {q.id: q for q in build_junior_math_questions()}
    assert "q-triangle-1" in questions
    assert "q-circle_angle-1" in questions
    assert questions["q-triangle-1"].answer


def test_unknown_generator_is_not_silently_used() -> None:
    # 这个测试保护“新增节点必须补题”的约束：内容入口会抛错而不是返回空题。
    from app.content import junior_math

    original = junior_math.GENERATORS.pop("rational")
    try:
        try:
            junior_math.build_junior_math_questions()
        except ValueError as exc:
            assert "missing question generator" in str(exc)
        else:  # pragma: no cover
            raise AssertionError("missing generator should fail fast")
    finally:
        junior_math.GENERATORS["rational"] = original