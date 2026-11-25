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


def test_every_curated_node_has_curated_content() -> None:
    """声明 kind="curated" 的节点必须真的在 CURATED 里有题。

    之前 `intersecting_lines` 漏了，`_curated` 静默回退到占位题，
    每个节点的题数还是 3，所以只数数量的测试完全发现不了。
    """
    from app.content.junior_questions import CURATED
    from app.content.specs import NODES

    curated_specs = {spec.id for spec in NODES if spec.kind == "curated"}
    assert curated_specs <= set(CURATED), f"缺少 CURATED 条目: {curated_specs - set(CURATED)}"


def test_no_placeholder_questions() -> None:
    """题库里不能出现「答案：略」这类占位题。"""
    for question in build_junior_math_questions():
        assert question.answer not in {"略", "", "TODO"}, question.id
        assert "英文关键词" not in question.stem, question.id
        assert "你最想问老师" not in question.stem, question.id


def test_intersecting_lines_is_answerable() -> None:
    """被漏掉的那个知识点：现在应该是真实题目且有正确答案。"""
    questions = {q.id: q for q in build_junior_math_questions()}
    first = questions["q-intersecting_lines-1"]
    assert first.answer in "ABCD"
    correct_text = first.options["ABCD".index(first.answer)]
    assert grade_answer(first, correct_text)
    assert not grade_answer(first, "____")


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