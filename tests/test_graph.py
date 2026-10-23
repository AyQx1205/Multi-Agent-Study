"""知识图谱：拓扑序、可学节点、补救路径、环。"""

from __future__ import annotations

from app.core.knowledge_graph import KnowledgeGraph, KnowledgeNode, build_sample_math_graph


def test_topological_order_respects_edges() -> None:
    graph = build_sample_math_graph()
    order = graph.topological_sort()
    position = {nid: i for i, nid in enumerate(order)}
    for node_id in graph.nodes:
        for successor in graph.get_successors(node_id):
            assert position[node_id] < position[successor]


def test_ready_nodes_from_empty() -> None:
    graph = build_sample_math_graph()
    assert graph.get_ready_nodes(set()) == ["arithmetic"]


def test_ready_nodes_after_mastery() -> None:
    graph = build_sample_math_graph()
    ready = graph.get_ready_nodes({"arithmetic"})
    assert ready[0] == "negative_numbers"
    assert {"fractions", "sets", "pythagorean"} <= set(ready)


def test_learning_path() -> None:
    graph = build_sample_math_graph()
    path = graph.get_learning_path("quadratic_eq", set())
    assert path[0] == "arithmetic"
    assert path[-1] == "quadratic_eq"
    assert "factoring" in path


def test_learning_path_skips_mastered() -> None:
    graph = build_sample_math_graph()
    path = graph.get_learning_path("quadratic_eq", {"factoring"})
    assert "factoring" not in path


def test_cycle_returns_partial() -> None:
    graph = KnowledgeGraph()
    graph.add_node(KnowledgeNode(id="a", name="A"))
    graph.add_node(KnowledgeNode(id="b", name="B", prerequisites=["c"]))
    graph.add_node(KnowledgeNode(id="c", name="C", prerequisites=["b"]))
    assert len(graph.topological_sort()) == 1


def test_sample_graph_shape() -> None:
    graph = build_sample_math_graph()
    assert len(graph.nodes) == 20
    assert graph.topological_sort()[0] == "arithmetic"