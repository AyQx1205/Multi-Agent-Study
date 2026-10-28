from __future__ import annotations

import logging
from collections import deque

from pydantic import BaseModel, Field

logger = logging.getLogger(__name__)

class KnowledgeNode(BaseModel):
    id:str = Field(description="节点ID")
    name:str = Field(description="节点名称")
    subject:str = Field(default="math", description="节点所属科目")
    grade:int = Field(default=0, ge=0, le=9, description="年级；0 表示通用/样例")
    difficulty:float = Field(default=0.5, description="节点难度")
    prerequisites:list[str] = Field(default_factory=list, description="先修节点ID列表")
    tags:list[str] = Field(default_factory=list, description="节点标签列表")

class KnowledgeGraph:
    def __init__(self) -> None:
        self.nodes:dict[str,KnowledgeNode] = {}
        self._succ:dict[str,list[str]] = {}
        self._pred:dict[str,list[str]] = {}

    def add_node(self, node:KnowledgeNode) -> None:
        if node.id in self.nodes:
            return
        self.nodes[node.id] = node
        self._succ.setdefault(node.id,[])
        self._pred.setdefault(node.id,[])
        for pre in node.prerequisites:
            self._succ.setdefault(pre,[])
            self._pred.setdefault(node.id,[])
            if node.id not in self._succ[pre]:
                self._succ[pre].append(node.id)
            if pre not in self._pred[node.id]:
                self._pred[node.id].append(pre)

    def get_prerequisites(self, node_id:str) -> list[str]:
        return self._pred.get(node_id,[])

    def get_successors(self, node_id:str) -> list[str]:
        return self._succ.get(node_id,[])

    def get_all_prerequisites(self, node_id:str) -> set[str]:
        visited: set[str] = set()
        queue = deque(self.get_prerequisites(node_id))
        while queue:
            current = queue.popleft()
            if current not in visited:
                visited.add(current)
                queue.extend(self.get_prerequisites(current))
        return visited

    def topological_sort(self) -> list[str]:
        in_degree = {nid: 0 for nid in self.nodes}
        for nid in self.nodes:
            for successor in self._succ.get(nid,[]):
                if successor in in_degree:
                    in_degree[successor] += 1

        queue = deque([nid for nid, degree in in_degree.items() if degree == 0])
        order: list[str] = []
        while queue:
            nid = queue.popleft()
            order.append(nid)
            for successor in self._succ.get(nid,[]):
                in_degree[successor] -= 1
                if in_degree[successor] == 0:
                    queue.append(successor)

        if len(order) != len(self.nodes):
            logger.warning("knowledge graph has a cycle: %d/%d nodes sorted", len(order), len(self.nodes))
        return order

    def get_ready_nodes(self, mastered_ids: set[str]) -> list[str]:
        ready = [
            nid
            for nid,node in self.nodes.items()
            if nid not in mastered_ids and set(node.prerequisites).issubset(mastered_ids)
        ]
        return sorted(ready,key=lambda nid: (self.nodes[nid].difficulty,nid))

    def get_learning_path(self, target_id:str, mastered_ids: set[str]) -> list[str]:
        if target_id not in self.nodes:
            return []
        needed = self.get_all_prerequisites(target_id) - mastered_ids
        if target_id not in mastered_ids:
            needed.add(target_id)
        return [nid for nid in self.topological_sort() if nid in needed]

def build_sample_math_graph() -> KnowledgeGraph:

    graph = KnowledgeGraph()
    rows = [
        ("arithmetic", "四则运算", 0.10, [], ["基础"]),
        ("fractions", "分数运算", 0.20, ["arithmetic"], ["基础"]),
        ("negative_numbers", "负数", 0.15, ["arithmetic"], ["基础"]),
        ("algebraic_expr", "代数式", 0.30, ["arithmetic", "negative_numbers"], ["代数"]),
        ("linear_eq_1", "一元一次方程", 0.35, ["algebraic_expr"], ["方程"]),
        ("linear_eq_2", "二元一次方程组", 0.45, ["linear_eq_1"], ["方程"]),
        ("factoring", "因式分解", 0.40, ["algebraic_expr"], ["代数"]),
        ("quadratic_eq", "一元二次方程", 0.55, ["factoring", "linear_eq_1"], ["方程"]),
        ("quadratic_func", "二次函数", 0.60, ["quadratic_eq"], ["函数"]),
        ("inequality", "不等式", 0.40, ["linear_eq_1"], ["不等式"]),
        ("coordinate", "平面直角坐标系", 0.30, ["negative_numbers"], ["几何"]),
        ("linear_func", "一次函数", 0.45, ["linear_eq_1", "coordinate"], ["函数"]),
        ("pythagorean", "勾股定理", 0.35, ["arithmetic"], ["几何"]),
        ("similar_triangle", "相似三角形", 0.50, ["pythagorean", "fractions"], ["几何"]),
        ("trig_basic", "三角函数基础", 0.55, ["pythagorean", "fractions"], ["三角"]),
        ("probability", "概率初步", 0.40, ["fractions"], ["统计"]),
        ("statistics", "数据统计", 0.35, ["arithmetic", "fractions"], ["统计"]),
        ("sequence", "数列", 0.50, ["algebraic_expr"], ["代数"]),
        ("sets", "集合", 0.25, ["arithmetic"], ["基础"]),
        ("logic", "简易逻辑", 0.30, ["sets"], ["基础"]),
    ]
    for nid,name,difficulty,prerequisites,tags in rows:
        graph.add_node(
            KnowledgeNode(id=nid,name=name,difficulty=difficulty,prerequisites=prerequisites,tags=tags)
        )
    logger.info("sample math graph built with %d nodes", len(graph.nodes))
    return graph