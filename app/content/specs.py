"""初中数学知识点规格。

一个年级一个学科先做扎实：这里覆盖七、八、九年级的核心知识点。
后续扩教材版本时，只需要增删这个列表并补题。
"""

from __future__ import annotations

from dataclasses import dataclass, field


@dataclass(frozen=True)
class NodeSpec:
    id: str
    name: str
    grade: int
    difficulty: float
    prerequisites: tuple[str, ...] = ()
    tags: tuple[str, ...] = field(default_factory=tuple)
    kind: str = "generic"


# 60 个节点：七年级 23 个、八年级 21 个、九年级 16 个。
NODES: tuple[NodeSpec, ...] = (
    # ── 七年级 ──
    NodeSpec("rational_numbers", "有理数", 7, 0.10, (), ("数与式",), "rational"),
    NodeSpec("number_line", "数轴", 7, 0.12, ("rational_numbers",), ("数与式",), "number_line"),
    NodeSpec("opposite_absolute", "相反数与绝对值", 7, 0.15, ("number_line",), ("数与式",), "abs"),
    NodeSpec("rational_add_sub", "有理数加减法", 7, 0.20, ("opposite_absolute",), ("数与式",), "rational_add_sub"),
    NodeSpec("rational_mul_div", "有理数乘除法", 7, 0.22, ("rational_add_sub",), ("数与式",), "rational_mul_div"),
    NodeSpec("power", "有理数乘方", 7, 0.25, ("rational_mul_div",), ("数与式",), "power"),
    NodeSpec("scientific_notation", "科学记数法", 7, 0.28, ("power",), ("数与式",), "scientific"),
    NodeSpec("algebraic_expr", "整式", 7, 0.25, ("rational_mul_div",), ("代数式",), "algebraic"),
    NodeSpec("algebraic_add_sub", "整式加减", 7, 0.28, ("algebraic_expr",), ("代数式",), "algebraic_add_sub"),
    NodeSpec("linear_eq", "一元一次方程", 7, 0.30, ("algebraic_add_sub",), ("方程",), "linear_eq"),
    NodeSpec("linear_eq_app", "一元一次方程应用", 7, 0.35, ("linear_eq",), ("方程",), "word_problem"),
    NodeSpec("geometry_intro", "几何图形初步", 7, 0.15, (), ("几何",), "curated"),
    NodeSpec("lines_rays_segments", "直线、射线、线段", 7, 0.18, ("geometry_intro",), ("几何",), "curated"),
    NodeSpec("angle", "角", 7, 0.20, ("lines_rays_segments",), ("几何",), "angle"),
    NodeSpec("intersecting_lines", "相交线", 7, 0.25, ("angle",), ("几何",), "curated"),
    NodeSpec("parallel_lines", "平行线", 7, 0.30, ("intersecting_lines",), ("几何",), "parallel"),
    NodeSpec("square_root", "平方根", 7, 0.30, ("power",), ("实数",), "radical"),
    NodeSpec("cube_root", "立方根", 7, 0.32, ("square_root",), ("实数",), "radical"),
    NodeSpec("coordinate_system", "平面直角坐标系", 7, 0.30, ("number_line",), ("函数",), "coordinate"),
    NodeSpec("linear_system", "二元一次方程组", 7, 0.40, ("linear_eq", "coordinate_system"), ("方程",), "linear_system"),
    NodeSpec("inequality", "不等式与不等式组", 7, 0.38, ("linear_eq",), ("不等式",), "inequality"),
    NodeSpec("data_collect", "数据的收集与整理", 7, 0.25, ("rational_numbers",), ("统计",), "curated"),
    NodeSpec("histogram", "直方图", 7, 0.32, ("data_collect",), ("统计",), "curated"),
    # ── 八年级 ──
    NodeSpec("triangle", "三角形", 8, 0.30, ("geometry_intro",), ("几何",), "curated"),
    NodeSpec("congruent_triangle", "全等三角形", 8, 0.38, ("triangle",), ("几何",), "curated"),
    NodeSpec("isosceles_triangle", "等腰三角形", 8, 0.40, ("congruent_triangle",), ("几何",), "curated"),
    NodeSpec("axial_symmetry", "轴对称", 8, 0.35, ("triangle",), ("几何",), "curated"),
    NodeSpec("pythagorean", "勾股定理", 8, 0.42, ("triangle", "square_root"), ("几何",), "pythagorean"),
    NodeSpec("parallelogram", "平行四边形", 8, 0.45, ("congruent_triangle", "parallel_lines"), ("几何",), "curated"),
    NodeSpec("special_parallelogram", "特殊平行四边形", 8, 0.48, ("parallelogram",), ("几何",), "curated"),
    NodeSpec("function_basic", "函数", 8, 0.35, ("coordinate_system",), ("函数",), "function"),
    NodeSpec("linear_function", "一次函数", 8, 0.40, ("function_basic", "linear_eq"), ("函数",), "linear_function"),
    NodeSpec("proportional_function", "正比例函数", 8, 0.38, ("linear_function",), ("函数",), "proportional"),
    NodeSpec("linear_function_app", "一次函数应用", 8, 0.45, ("linear_function",), ("函数",), "word_problem"),
    NodeSpec("polynomial_mul", "整式乘法", 8, 0.32, ("algebraic_add_sub", "power"), ("代数式",), "polynomial_mul"),
    NodeSpec("multiplication_formula", "乘法公式", 8, 0.35, ("polynomial_mul",), ("代数式",), "formula"),
    NodeSpec("factorization", "因式分解", 8, 0.40, ("multiplication_formula",), ("代数式",), "factor"),
    NodeSpec("fraction_expr", "分式", 8, 0.42, ("factorization",), ("分式",), "fraction_expr"),
    NodeSpec("fraction_equation", "分式方程", 8, 0.45, ("fraction_expr", "linear_eq"), ("分式",), "fraction_equation"),
    NodeSpec("quadratic_radical", "二次根式", 8, 0.40, ("square_root",), ("实数",), "radical_quadratic"),
    NodeSpec("data_analysis", "数据的分析", 8, 0.35, ("data_collect",), ("统计",), "statistics"),
    NodeSpec("probability_basic", "概率初步", 8, 0.38, ("data_collect",), ("统计",), "probability"),
    NodeSpec("translation_rotation", "平移与旋转", 8, 0.35, ("coordinate_system", "triangle"), ("几何",), "translation"),
    NodeSpec("data_line", "折线统计图", 8, 0.30, ("data_analysis",), ("统计",), "curated"),
    # ── 九年级 ──
    NodeSpec("quadratic_eq", "一元二次方程", 9, 0.50, ("factorization", "linear_eq"), ("方程",), "quadratic_eq"),
    NodeSpec("quadratic_eq_app", "一元二次方程应用", 9, 0.55, ("quadratic_eq",), ("方程",), "word_problem"),
    NodeSpec("quadratic_function", "二次函数", 9, 0.55, ("linear_function", "quadratic_eq"), ("函数",), "quadratic_function"),
    NodeSpec("quadratic_function_props", "二次函数图象与性质", 9, 0.58, ("quadratic_function",), ("函数",), "quadratic_props"),
    NodeSpec("quadratic_function_app", "二次函数应用", 9, 0.62, ("quadratic_function_props",), ("函数",), "word_problem"),
    NodeSpec("circle", "圆", 9, 0.50, ("pythagorean", "geometry_intro"), ("几何",), "circle"),
    NodeSpec("circle_angle", "圆周角", 9, 0.55, ("circle",), ("几何",), "circle_angle"),
    NodeSpec("line_circle", "直线与圆", 9, 0.58, ("circle_angle",), ("几何",), "line_circle"),
    NodeSpec("regular_polygon", "正多边形与圆", 9, 0.55, ("circle",), ("几何",), "regular_polygon"),
    NodeSpec("similar_triangle", "相似三角形", 9, 0.55, ("congruent_triangle", "fraction_expr"), ("几何",), "similar"),
    NodeSpec("similar_app", "相似三角形应用", 9, 0.58, ("similar_triangle",), ("几何",), "word_problem"),
    NodeSpec("trig_basic", "锐角三角函数", 9, 0.60, ("similar_triangle", "square_root"), ("三角",), "trig"),
    NodeSpec("solve_triangle", "解直角三角形", 9, 0.62, ("trig_basic",), ("三角",), "solve_triangle"),
    NodeSpec("projection_view", "投影与视图", 9, 0.45, ("geometry_intro",), ("几何",), "curated"),
    NodeSpec("inverse_function", "反比例函数", 9, 0.55, ("function_basic", "fraction_expr"), ("函数",), "inverse_function"),
    NodeSpec("inverse_app", "反比例函数应用", 9, 0.60, ("inverse_function",), ("函数",), "word_problem"),
)