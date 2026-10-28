"""初中数学种子题库。

生成题使用确定性模板，保证每个知识点都有 3 道可判分的题；
几何/统计等需要图示或表述的节点使用人工整理的题。
"""

from __future__ import annotations

from collections.abc import Callable

from app.content.specs import NodeSpec
from app.core.content import Question, QuestionType


def _q(
    spec: NodeSpec,
    index: int,
    stem: str,
    answer: str,
    explanation: str,
    *,
    qtype: QuestionType = QuestionType.FILL,
    options: list[str] | None = None,
    accepted: list[str] | None = None,
    difficulty: int | None = None,
) -> Question:
    return Question(
        id=f"q-{spec.id}-{index}",
        knowledge_id=spec.id,
        grade=spec.grade,
        type=qtype,
        stem=stem,
        options=options or [],
        answer=str(answer),
        accepted=accepted or [],
        explanation=explanation,
        difficulty=difficulty or min(3, 1 + index),
        source="seed-v1",
    )


def _choice(
    spec: NodeSpec,
    index: int,
    stem: str,
    correct: str,
    wrong: list[str],
    explanation: str,
    difficulty: int | None = None,
) -> Question:
    options = [correct, *wrong]
    shift = (index - 1) % len(options)
    options = options[shift:] + options[:shift]
    letter = "ABCD"[options.index(correct)]
    return _q(
        spec,
        index,
        stem,
        letter,
        explanation,
        qtype=QuestionType.CHOICE,
        options=options,
        difficulty=difficulty,
    )


def _fill(
    spec: NodeSpec,
    index: int,
    stem: str,
    answer: str,
    explanation: str,
    accepted: list[str] | None = None,
) -> Question:
    return _q(spec, index, stem, answer, explanation, accepted=accepted)


# ── 生成器 ────────────────────────────────────────────────────────────────

def _rational(spec: NodeSpec) -> list[Question]:
    return [
        _choice(spec, 1, "下列各数中，是负有理数的是（ ）", "-3", ["0", "1/3", "2.5"], "负有理数是小于 0 的有理数。"),
        _fill(spec, 2, "|-7| = ______", "7", "绝对值表示数轴上到原点的距离。"),
        _fill(spec, 3, "-2 的相反数是 ______", "2", "a 的相反数是 -a。"),
    ]


def _number_line(spec: NodeSpec) -> list[Question]:
    return [
        _fill(spec, 1, "数轴上表示 -3 的点到原点的距离是 ______", "3", "距离等于该数的绝对值。"),
        _fill(spec, 2, "数轴上 -2 和 3 两点之间的距离是 ______", "5", "距离 = |3 - (-2)| = 5。"),
        _fill(spec, 3, "与 -4 关于原点对称的点表示的数是 ______", "4", "关于原点对称的两个数互为相反数。"),
    ]


def _abs(spec: NodeSpec) -> list[Question]:
    return [
        _fill(spec, 1, "|-5| = ______", "5", "负数的绝对值是它的相反数。"),
        _fill(spec, 2, "|3 - 7| = ______", "4", "先算 3-7=-4，再取绝对值。"),
        _choice(spec, 3, "若 |x| = 3，则 x 的值是（ ）", "3 或 -3", ["3", "-3", "0"], "绝对值为 3 的数有两个：3 和 -3。"),
    ]


def _rational_add_sub(spec: NodeSpec) -> list[Question]:
    return [
        _fill(spec, 1, "(-7) + 3 = ______", "-4", "异号相加，取绝对值较大的符号，再用大绝对值减小绝对值。"),
        _fill(spec, 2, "5 - (-8) = ______", "13", "减去一个负数等于加上它的相反数。"),
        _fill(spec, 3, "-1/2 + 1/3 = ______", "-1/6", "通分：-3/6 + 2/6 = -1/6。", ["-0.1666666667"]),
    ]


def _rational_mul_div(spec: NodeSpec) -> list[Question]:
    return [
        _fill(spec, 1, "(-4) × 6 = ______", "-24", "异号相乘得负。"),
        _fill(spec, 2, "(-3/4) ÷ (1/2) = ______", "-3/2", "除以 1/2 等于乘 2。", ["-1.5"]),
        _choice(spec, 3, "下列计算正确的是（ ）", "(-2) × (-3) = 6", ["(-2) × 3 = 6", "(-2) ÷ (-3) = -2/3", "0 × (-5) = -5"], "同号得正，0 乘任何数得 0。"),
    ]


def _power(spec: NodeSpec) -> list[Question]:
    return [
        _fill(spec, 1, "(-2)^3 = ______", "-8", "负数的奇次幂为负。"),
        _fill(spec, 2, "-3^2 = ______", "-9", "先算 3²=9，再取相反数。"),
        _fill(spec, 3, "(2/3)^2 = ______", "4/9", "分子分母分别平方。"),
    ]


def _scientific(spec: NodeSpec) -> list[Question]:
    return [
        _fill(spec, 1, "3.2 × 10^5 写成普通数是 ______", "320000", "小数点向右移动 5 位。"),
        _choice(spec, 2, "0.00045 用科学记数法表示为（ ）", "4.5 × 10^-4", ["4.5 × 10^-3", "45 × 10^-5", "4.5 × 10^4"], "科学记数法写成 a×10^n，其中 1≤|a|<10。"),
        _fill(spec, 3, "6 × 10^-3 = ______", "0.006", "小数点向左移动 3 位。"),
    ]


def _algebraic(spec: NodeSpec) -> list[Question]:
    return [
        _fill(spec, 1, "当 x = 4 时，2x + 3 = ______", "11", "代入计算：2×4+3=11。"),
        _fill(spec, 2, "单项式 3x²y 的系数是 ______", "3", "单项式中的数字因数是系数。"),
        _fill(spec, 3, "2a + 3a = ______", "5a", "同类项合并，系数相加。"),
    ]


def _algebraic_add_sub(spec: NodeSpec) -> list[Question]:
    return [
        _choice(spec, 1, "(3x + 2) + (x - 5) 化简结果是（ ）", "4x - 3", ["4x + 7", "3x - 3", "4x - 7"], "合并同类项：3x+x=4x，2-5=-3。"),
        _choice(spec, 2, "(2a - 3b) - (a - 2b) 化简结果是（ ）", "a - b", ["a - 5b", "3a - b", "a + b"], "去括号后合并：2a-a=a，-3b+2b=-b。"),
        _fill(spec, 3, "当 a = 2 时，3a - 1 = ______", "5", "代入：3×2-1=5。"),
    ]


def _linear_eq(spec: NodeSpec) -> list[Question]:
    return [
        _fill(spec, 1, "解方程：2x + 3 = 11，x = ______", "4", "移项得 2x=8，所以 x=4。", ["x=4"]),
        _fill(spec, 2, "解方程：3(x - 2) = 12，x = ______", "6", "两边除以 3 得 x-2=4，所以 x=6。", ["x=6"]),
        _fill(spec, 3, "解方程：x/2 - 1 = 3，x = ______", "8", "移项得 x/2=4，所以 x=8。", ["x=8"]),
    ]


def _word_problem(spec: NodeSpec) -> list[Question]:
    if spec.id == "linear_eq_app":
        return [
            _fill(spec, 1, "某数的 3 倍加 2 等于 17，这个数是 ______", "5", "设这个数为 x，3x+2=17，解得 x=5。"),
            _fill(spec, 2, "长方形周长 20 厘米，长 6 厘米，宽是 ______ 厘米", "4", "2(长+宽)=20，所以宽=4。"),
            _fill(spec, 3, "买 3 支笔和一本 5 元的本子共花 17 元，每支笔 ______ 元", "4", "设每支笔 x 元，3x+5=17，x=4。"),
        ]
    if spec.id == "linear_function_app":
        return [
            _fill(spec, 1, "一次函数 y=3x+2，当 x=4 时 y = ______", "14", "代入：3×4+2=14。"),
            _fill(spec, 2, "套餐月租 20 元，每 GB 流量 3 元，用 5 GB 总费用是 ______ 元", "35", "20+3×5=35。"),
            _fill(spec, 3, "水池有 10 吨水，每小时放 2 吨，x 小时后剩 y=10-2x 吨；x=3 时 y = ______", "4", "10-2×3=4。"),
        ]
    if spec.id == "quadratic_eq_app":
        return [
            _fill(spec, 1, "长方形长比宽多 3 米，面积 10 平方米，宽是 ______ 米", "2", "设宽 x，x(x+3)=10，x=2。"),
            _fill(spec, 2, "直角三角形两直角边相差 1，斜边 5，较短直角边是 ______", "3", "3²+4²=5²。"),
            _fill(spec, 3, "某数的平方等于这个数的 2 倍，非零解是 ______", "2", "x²=2x，x=0 或 2，非零解为 2。"),
        ]
    if spec.id == "quadratic_function_app":
        return [
            _fill(spec, 1, "抛物线 h=-5t²+20t，t=2 时高度 h = ______", "20", "代入：-5×4+40=20。"),
            _fill(spec, 2, "周长 20 的矩形，面积最大时长是 ______", "5", "长+宽=10，长=宽=5 时面积最大。"),
            _fill(spec, 3, "二次函数 y=-x²+6x 的最大值是 ______", "9", "配方得 y=-(x-3)²+9，最大值 9。"),
        ]
    if spec.id == "similar_app":
        return [
            _fill(spec, 1, "身高 1.6 米的人影长 2 米，同一时刻树影长 6 米，树高 ______ 米", "4.8", "1.6/2 = h/6，h=4.8。"),
            _fill(spec, 2, "相似三角形对应边比为 2:3，小三角形面积 4，大三角形面积是 ______", "9", "面积比等于边长比的平方，4:S=4:9。"),
            _fill(spec, 3, "比例尺 1:10000，图上 3 厘米表示实际 ______ 米", "300", "3×10000=30000 厘米=300 米。"),
        ]
    if spec.id == "inverse_app":
        return [
            _fill(spec, 1, "y 与 x 成反比例，x=2 时 y=6，x=3 时 y = ______", "4", "k=12，y=12/3=4。"),
            _fill(spec, 2, "修路队每天修 v 米，完成时间 t 天；v=20 时 t=6，v=30 时 t = ______", "4", "工程总量 120，120/30=4。"),
            _fill(spec, 3, "矩形面积 12，长 x 宽 y，x=4 时 y = ______", "3", "xy=12，y=3。"),
        ]
    return _generic(spec)


def _angle(spec: NodeSpec) -> list[Question]:
    return [
        _fill(spec, 1, "30° 角的余角是 ______ 度", "60", "两角互余，和为 90°。"),
        _fill(spec, 2, "30° 角的补角是 ______ 度", "150", "两角互补，和为 180°。"),
        _fill(spec, 3, "钟表 3 点整时，时针与分针的夹角是 ______ 度", "90", "3 点整时分针指向 12，时针指向 3，相差 3 大格，每格 30°。"),
    ]


def _parallel(spec: NodeSpec) -> list[Question]:
    return [
        _fill(spec, 1, "两直线平行，同位角相等；若一个同位角是 65°，另一个是 ______ 度", "65", "平行线同位角相等。"),
        _fill(spec, 2, "两直线平行，同旁内角互补；若一个角是 65°，另一个是 ______ 度", "115", "180-65=115。"),
        _choice(spec, 3, "下列条件能判定两直线平行的是（ ）", "同位角相等", ["对顶角相等", "邻补角互补", "同旁内角相等"], "同位角相等、内错角相等、同旁内角互补都可判定平行。"),
    ]


def _radical(spec: NodeSpec) -> list[Question]:
    if spec.id == "cube_root":
        return [
            _fill(spec, 1, "∛(-8) = ______", "-2", "(-2)³=-8。"),
            _fill(spec, 2, "∛27 = ______", "3", "3³=27。"),
            _fill(spec, 3, "0 的立方根是 ______", "0", "0³=0。"),
        ]
    return [
        _fill(spec, 1, "√16 = ______", "4", "4²=16，算术平方根取非负值。"),
        _fill(spec, 2, "√(9/25) = ______", "3/5", "分子分母分别开平方。", ["0.6"]),
        _fill(spec, 3, "(-3)² 的算术平方根是 ______", "3", "(-3)²=9，√9=3。"),
    ]


def _coordinate(spec: NodeSpec) -> list[Question]:
    return [
        _choice(spec, 1, "点 (3, -2) 在（ ）", "第四象限", ["第一象限", "第二象限", "第三象限"], "横坐标正、纵坐标负，在第四象限。"),
        _fill(spec, 2, "点 (3, 4) 到原点的距离是 ______", "5", "√(3²+4²)=5。"),
        _fill(spec, 3, "点 (2, 3) 向右平移 3 个单位后是 ______", "(5,3)", "横坐标加 3，纵坐标不变。", ["5,3"]),
    ]


def _linear_system(spec: NodeSpec) -> list[Question]:
    return [
        _fill(spec, 1, "解方程组 x+y=5，x-y=1，得 x=______，y=______", "x=3,y=2", "两式相加得 2x=6，x=3；再得 y=2。", ["3,2", "x=3;y=2"]),
        _fill(spec, 2, "解方程组 2x+y=7，x-y=2，得 x=______，y=______", "x=3,y=1", "两式相加得 3x=9，x=3；y=1。", ["3,1", "x=3;y=1"]),
        _choice(spec, 3, "二元一次方程组的解是（ ）", "使两个方程都成立的一组未知数的值", ["使第一个方程成立的值", "任意一组值", "使两个方程都不成立的值"], "解必须同时满足两个方程。"),
    ]


def _inequality(spec: NodeSpec) -> list[Question]:
    return [
        _fill(spec, 1, "解不等式 2x - 3 < 5，得 x ______ 4", "<", "2x<8，所以 x<4。", ["小于"]),
        _fill(spec, 2, "解不等式 -2x ≥ 6，得 x ______ -3", "≤", "两边除以 -2，不等号方向改变。", ["小于等于"]),
        _choice(spec, 3, "不等式 x+1>3 的解集是（ ）", "x>2", ["x<2", "x>4", "x<4"], "x>3-1=2。"),
    ]


def _statistics(spec: NodeSpec) -> list[Question]:
    return [
        _fill(spec, 1, "数据 2, 4, 6, 8 的平均数是 ______", "5", "(2+4+6+8)÷4=5。"),
        _fill(spec, 2, "数据 1, 3, 5, 7, 9 的中位数是 ______", "5", "排序后正中间的数是 5。"),
        _fill(spec, 3, "数据 3, 3, 4, 5, 3 的众数是 ______", "3", "出现次数最多的数是 3。"),
    ]


def _pythagorean(spec: NodeSpec) -> list[Question]:
    return [
        _fill(spec, 1, "直角三角形两直角边为 3 和 4，斜边是 ______", "5", "3²+4²=5²。"),
        _fill(spec, 2, "直角三角形两直角边为 6 和 8，斜边是 ______", "10", "6²+8²=10²。"),
        _fill(spec, 3, "直角三角形斜边 13，一条直角边 5，另一条是 ______", "12", "13²-5²=12²。"),
    ]


def _function(spec: NodeSpec) -> list[Question]:
    return [
        _fill(spec, 1, "已知 f(x)=2x+1，则 f(3) = ______", "7", "2×3+1=7。"),
        _fill(spec, 2, "已知 f(x)=2x+1，则 f(-2) = ______", "-3", "2×(-2)+1=-3。"),
        _fill(spec, 3, "函数 y=x² 中，当 x=3 时 y = ______", "9", "3²=9。"),
    ]


def _linear_function(spec: NodeSpec) -> list[Question]:
    return [
        _fill(spec, 1, "一次函数 y=2x-3 的斜率是 ______", "2", "y=kx+b 中 k=2。"),
        _fill(spec, 2, "一次函数 y=2x-3 的图象与 y 轴交点是 ______", "(0,-3)", "令 x=0，得 y=-3。", ["0,-3"]),
        _fill(spec, 3, "一次函数 y=2x-3，当 x=4 时 y = ______", "5", "2×4-3=5。"),
    ]


def _proportional(spec: NodeSpec) -> list[Question]:
    return [
        _fill(spec, 1, "正比例函数 y=3x 中，比例系数是 ______", "3", "y=kx 中 k=3。"),
        _fill(spec, 2, "正比例函数 y=3x，当 x=4 时 y = ______", "12", "3×4=12。"),
        _fill(spec, 3, "正比例函数 y=3x，当 y=12 时 x = ______", "4", "12÷3=4。"),
    ]


def _polynomial_mul(spec: NodeSpec) -> list[Question]:
    return [
        _choice(spec, 1, "2x(3x+1) 展开结果是（ ）", "6x²+2x", ["6x²+1", "5x²+2x", "6x+2x"], "单项式乘多项式，逐项相乘。"),
        _choice(spec, 2, "(x+2)(x+3) 展开结果是（ ）", "x²+5x+6", ["x²+6x+5", "x²+5x+5", "x²+6"], "用多项式乘法展开。"),
        _choice(spec, 3, "(a-2)(a+2) 展开结果是（ ）", "a²-4", ["a²+4", "a²-4a-4", "a²-2"], "平方差公式。"),
    ]


def _formula(spec: NodeSpec) -> list[Question]:
    return [
        _choice(spec, 1, "(x+3)² 展开结果是（ ）", "x²+6x+9", ["x²+9", "x²+3x+9", "x²+6x+6"], "完全平方公式。"),
        _choice(spec, 2, "(x-2)(x+2) 的结果是（ ）", "x²-4", ["x²+4", "x²-4x+4", "x²-2"], "平方差公式。"),
        _choice(spec, 3, "(2a+1)² 展开结果是（ ）", "4a²+4a+1", ["4a²+1", "2a²+4a+1", "4a²+2a+1"], "完全平方公式。"),
    ]


def _factor(spec: NodeSpec) -> list[Question]:
    return [
        _choice(spec, 1, "x²+5x+6 因式分解结果是（ ）", "(x+2)(x+3)", ["(x+1)(x+6)", "(x-2)(x-3)", "(x+5)(x+1)"], "找两个数积为 6、和为 5，即 2 和 3。"),
        _choice(spec, 2, "x²-9 因式分解结果是（ ）", "(x-3)(x+3)", ["(x-9)(x+1)", "(x-3)²", "(x+9)(x-1)"], "平方差公式。"),
        _choice(spec, 3, "2x²+4x 因式分解结果是（ ）", "2x(x+2)", ["2(x²+2)", "x(2x+4x)", "2x(x+4)"], "先提公因式 2x。"),
    ]


def _fraction_expr(spec: NodeSpec) -> list[Question]:
    return [
        _choice(spec, 1, "化简 (x²-1)/(x-1)（x≠1），结果是（ ）", "x+1", ["x-1", "x²+1", "1/(x+1)"], "分子因式分解为 (x-1)(x+1)。"),
        _choice(spec, 2, "化简 (x²+2x)/x（x≠0），结果是（ ）", "x+2", ["x", "x+2x", "2"], "分子提公因式 x。"),
        _fill(spec, 3, "当 x=3 时，1/(x-1) = ______", "1/2", "1/(3-1)=1/2。", ["0.5"]),
    ]


def _fraction_equation(spec: NodeSpec) -> list[Question]:
    return [
        _fill(spec, 1, "解分式方程 1/x = 2，x = ______", "1/2", "2x=1，x=1/2，需检验 x≠0。", ["0.5", "x=0.5"]),
        _fill(spec, 2, "解分式方程 2/(x-1) = 1，x = ______", "3", "x-1=2，x=3，需检验 x≠1。", ["x=3"]),
        _choice(spec, 3, "解分式方程后必须做的一步是（ ）", "检验分母是否为零", ["直接写答案", "两边平方", "去分子"], "分式方程可能产生增根。"),
    ]


def _radical_quadratic(spec: NodeSpec) -> list[Question]:
    return [
        _fill(spec, 1, "√12 = ______", "2√3", "12=4×3，√12=2√3。", ["2sqrt3"]),
        _fill(spec, 2, "√8 × √2 = ______", "4", "√8×√2=√16=4。"),
        _fill(spec, 3, "√(1/2) = ______", "√2/2", "分母有理化。", ["sqrt2/2"]),
    ]


def _quadratic_eq(spec: NodeSpec) -> list[Question]:
    return [
        _choice(spec, 1, "方程 x²-5x+6=0 的解是（ ）", "x=2 或 x=3", ["x=1 或 x=6", "x=-2 或 x=-3", "x=2 或 x=-3"], "因式分解为 (x-2)(x-3)=0。"),
        _choice(spec, 2, "方程 x²=9 的解是（ ）", "x=±3", ["x=3", "x=-3", "x=9"], "开平方得 x=±3。"),
        _choice(spec, 3, "方程 (x-1)²=4 的解是（ ）", "x=3 或 x=-1", ["x=3", "x=-1", "x=1 或 x=3"], "x-1=±2。"),
    ]


def _quadratic_function(spec: NodeSpec) -> list[Question]:
    return [
        _choice(spec, 1, "抛物线 y=x²-4x+3 的顶点是（ ）", "(2,-1)", ["(-2,-1)", "(2,1)", "(4,3)"], "配方得 y=(x-2)²-1。"),
        _fill(spec, 2, "抛物线 y=x²-4x+3 的对称轴是 x = ______", "2", "顶点横坐标为 2。"),
        _fill(spec, 3, "抛物线 y=x²-4x+3 的最小值是 ______", "-1", "顶点纵坐标为 -1，开口向上。"),
    ]


def _quadratic_props(spec: NodeSpec) -> list[Question]:
    return [
        _choice(spec, 1, "抛物线 y=x²-4x+3 与 x 轴的交点是（ ）", "(1,0) 和 (3,0)", ["(0,0) 和 (3,0)", "(1,0) 和 (-3,0)", "(2,0)"], "令 y=0，x²-4x+3=0，解得 1 和 3。"),
        _fill(spec, 2, "抛物线 y=x²-4x+3 与 y 轴交点的纵坐标是 ______", "3", "令 x=0，y=3。"),
        _fill(spec, 3, "抛物线 y=x²-4x+3 的开口方向是 ______", "向上", "二次项系数 1>0。"),
    ]


def _circle(spec: NodeSpec) -> list[Question]:
    return [
        _fill(spec, 1, "半径为 3 的圆，直径是 ______", "6", "直径=2r。"),
        _fill(spec, 2, "半径为 3 的圆，周长是 ______", "6π", "周长=2πr=6π。", ["6pi"]),
        _fill(spec, 3, "半径为 3 的圆，面积是 ______", "9π", "面积=πr²=9π。", ["9pi"]),
    ]


def _circle_angle(spec: NodeSpec) -> list[Question]:
    return [
        _fill(spec, 1, "同弧所对的圆心角是 80°，圆周角是 ______ 度", "40", "圆周角等于同弧圆心角的一半。"),
        _fill(spec, 2, "同弧所对的圆周角是 35°，圆心角是 ______ 度", "70", "圆心角是圆周角的 2 倍。"),
        _choice(spec, 3, "直径所对的圆周角是（ ）", "90°", ["60°", "120°", "180°"], "半圆所对的圆周角是直角。"),
    ]


def _line_circle(spec: NodeSpec) -> list[Question]:
    return [
        _fill(spec, 1, "圆的半径为 3，圆心到直线距离为 3，直线与圆的位置关系是 ______", "相切", "d=r 时相切。"),
        _fill(spec, 2, "圆的半径为 3，圆心到直线距离为 5，直线与圆 ______", "相离", "d>r 时相离。"),
        _fill(spec, 3, "圆的半径 3，圆心到切线距离为 5，切线长为 ______", "4", "切线长=√(5²-3²)=4。"),
    ]


def _regular_polygon(spec: NodeSpec) -> list[Question]:
    return [
        _fill(spec, 1, "正六边形的每个内角是 ______ 度", "120", "(6-2)×180÷6=120。"),
        _fill(spec, 2, "正六边形的每个外角是 ______ 度", "60", "360÷6=60。"),
        _fill(spec, 3, "正六边形的内角和是 ______ 度", "720", "(6-2)×180=720。"),
    ]


def _similar(spec: NodeSpec) -> list[Question]:
    return [
        _fill(spec, 1, "相似三角形对应边比为 2:3，面积比是 ______", "4:9", "面积比等于相似比的平方。"),
        _fill(spec, 2, "两个相似三角形相似比为 1:4，周长比是 ______", "1:4", "周长比等于相似比。"),
        _choice(spec, 3, "判定两个三角形相似，常用的是（ ）", "两角对应相等", ["两边对应相等", "三边对应相等", "一角对应相等"], "两角对应相等即可判定相似。"),
    ]


def _trig(spec: NodeSpec) -> list[Question]:
    return [
        _fill(spec, 1, "sin 30° = ______", "1/2", "特殊角三角函数值。", ["0.5"]),
        _fill(spec, 2, "cos 60° = ______", "1/2", "cos60°=1/2。", ["0.5"]),
        _fill(spec, 3, "tan 45° = ______", "1", "tan45°=1。"),
    ]


def _solve_triangle(spec: NodeSpec) -> list[Question]:
    return [
        _fill(spec, 1, "含 30° 角的直角三角形，斜边 10，30° 角所对边是 ______", "5", "30° 角所对边等于斜边的一半。"),
        _fill(spec, 2, "等腰直角三角形直角边为 3，斜边是 ______", "3√2", "斜边=3√2。", ["3sqrt2"]),
        _fill(spec, 3, "直角三角形中，sin A=3/5，斜边 10，A 的对边是 ______", "6", "对边=10×3/5=6。"),
    ]


def _inverse_function(spec: NodeSpec) -> list[Question]:
    return [
        _fill(spec, 1, "反比例函数 y=k/x 经过点 (2,3)，k = ______", "6", "k=xy=6。"),
        _fill(spec, 2, "反比例函数 y=6/x，当 x=3 时 y = ______", "2", "6÷3=2。"),
        _fill(spec, 3, "反比例函数 y=6/x，当 y=2 时 x = ______", "3", "6÷2=3。"),
    ]


def _probability(spec: NodeSpec) -> list[Question]:
    return [
        _fill(spec, 1, "掷一枚均匀硬币，正面朝上的概率是 ______", "1/2", "两种等可能结果。", ["0.5"]),
        _fill(spec, 2, "掷一枚均匀骰子，点数为偶数的概率是 ______", "1/2", "2、4、6 三种结果。", ["0.5"]),
        _fill(spec, 3, "袋中有 3 个红球和 2 个白球，随机摸一个红球的概率是 ______", "3/5", "红球数÷总数。", ["0.6"]),
    ]


def _translation(spec: NodeSpec) -> list[Question]:
    return [
        _fill(spec, 1, "点 (2,3) 向右平移 3 个单位，再向下平移 1 个单位，得到 ______", "(5,2)", "横坐标加 3，纵坐标减 1。", ["5,2"]),
        _fill(spec, 2, "点 (2,3) 关于 x 轴对称的点是 ______", "(2,-3)", "关于 x 轴对称，纵坐标变相反数。", ["2,-3"]),
        _choice(spec, 3, "平移不改变图形的（ ）", "形状和大小", ["位置", "坐标", "方向"], "平移只改变位置。"),
    ]


def _curated(spec: NodeSpec) -> list[Question]:
    items = CURATED.get(spec.id)
    if not items:
        return _generic(spec)
    result: list[Question] = []
    for index, item in enumerate(items, 1):
        if "correct" in item:
            result.append(
                _choice(
                    spec,
                    index,
                    item["stem"],
                    item["correct"],
                    item["wrong"],
                    item["explanation"],
                )
            )
        else:
            result.append(
                _fill(
                    spec,
                    index,
                    item["stem"],
                    item["answer"],
                    item["explanation"],
                    item.get("accepted"),
                )
            )
    return result


def _generic(spec: NodeSpec) -> list[Question]:
    return [
        _fill(spec, 1, f"请写出「{spec.name}」中你掌握的一条核心结论。", "略", "开放题，仅作内容占位。"),
        _fill(spec, 2, f"「{spec.name}」的英文关键词是什么？", "略", "开放题，仅作内容占位。"),
        _fill(spec, 3, f"关于「{spec.name}」，你最想问老师的问题是什么？", "略", "开放题，仅作内容占位。"),
    ]


GENERATORS: dict[str, Callable[[NodeSpec], list[Question]]] = {
    "rational": _rational,
    "number_line": _number_line,
    "abs": _abs,
    "rational_add_sub": _rational_add_sub,
    "rational_mul_div": _rational_mul_div,
    "power": _power,
    "scientific": _scientific,
    "algebraic": _algebraic,
    "algebraic_add_sub": _algebraic_add_sub,
    "linear_eq": _linear_eq,
    "word_problem": _word_problem,
    "angle": _angle,
    "parallel": _parallel,
    "radical": _radical,
    "coordinate": _coordinate,
    "linear_system": _linear_system,
    "inequality": _inequality,
    "statistics": _statistics,
    "pythagorean": _pythagorean,
    "function": _function,
    "linear_function": _linear_function,
    "proportional": _proportional,
    "polynomial_mul": _polynomial_mul,
    "formula": _formula,
    "factor": _factor,
    "fraction_expr": _fraction_expr,
    "fraction_equation": _fraction_equation,
    "radical_quadratic": _radical_quadratic,
    "quadratic_eq": _quadratic_eq,
    "quadratic_function": _quadratic_function,
    "quadratic_props": _quadratic_props,
    "circle": _circle,
    "circle_angle": _circle_angle,
    "line_circle": _line_circle,
    "regular_polygon": _regular_polygon,
    "similar": _similar,
    "trig": _trig,
    "solve_triangle": _solve_triangle,
    "inverse_function": _inverse_function,
    "probability": _probability,
    "translation": _translation,
    "curated": _curated,
}


CURATED: dict[str, list[dict]] = {
    "geometry_intro": [
        {
            "stem": "下列几何体中，有 6 个面的是（ ）",
            "correct": "长方体",
            "wrong": ["三棱柱", "四棱锥", "圆柱"],
            "explanation": "长方体有 6 个面；三棱柱 5 个面，四棱锥 5 个面，圆柱 3 个面。",
        },
        {"stem": "长方体有 ______ 个顶点。", "answer": "8", "explanation": "长方体有 8 个顶点。"},
        {
            "stem": "点动成线，线动成面，面动成（ ）",
            "correct": "体",
            "wrong": ["点", "线", "角"],
            "explanation": "点、线、面、体的基本关系。",
        },
    ],
    "lines_rays_segments": [
        {"stem": "两点之间，______ 最短。", "answer": "线段", "explanation": "两点之间线段最短。"},
        {
            "stem": "射线有（ ）个端点。",
            "correct": "1 个",
            "wrong": ["0 个", "2 个", "无数个"],
            "explanation": "射线有一个端点，向一方无限延伸。",
        },
        {"stem": "经过两点可以画 ______ 条直线。", "answer": "1", "explanation": "两点确定一条直线。"},
    ],
    "triangle": [
        {"stem": "三角形内角和是 ______ 度。", "answer": "180", "explanation": "三角形内角和定理。"},
        {
            "stem": "三角形两边长分别为 3 和 5，第三边可能是（ ）",
            "correct": "4",
            "wrong": ["2", "8", "9"],
            "explanation": "第三边应大于 2 且小于 8，4 符合。",
        },
        {"stem": "直角三角形一个锐角为 30°，另一个锐角是 ______ 度。", "answer": "60", "explanation": "两锐角互余。"},
    ],
    "congruent_triangle": [
        {
            "stem": "判定两个三角形全等，不能使用的是（ ）",
            "correct": "SSA",
            "wrong": ["SSS", "SAS", "ASA"],
            "explanation": "SSA 不能保证唯一确定三角形。",
        },
        {"stem": "全等三角形的对应边 ______。", "answer": "相等", "explanation": "全等三角形对应边、对应角都相等。"},
        {
            "stem": "下列条件能判定两个三角形全等的是（ ）",
            "correct": "两边及其夹角对应相等",
            "wrong": ["两边及其中一边的对角对应相等", "三个角对应相等", "一边及一个角对应相等"],
            "explanation": "SAS 是判定定理。",
        },
    ],
    "isosceles_triangle": [
        {"stem": "等腰三角形顶角为 80°，底角是 ______ 度。", "answer": "50", "explanation": "(180-80)÷2=50。"},
        {
            "stem": "等腰三角形两边长为 3 和 7，周长是（ ）",
            "correct": "17",
            "wrong": ["13", "10", "21"],
            "explanation": "腰只能是 7，3+3<7 不能构成三角形，周长 7+7+3=17。",
        },
        {"stem": "等边三角形每个内角是 ______ 度。", "answer": "60", "explanation": "等边三角形三内角相等。"},
    ],
    "axial_symmetry": [
        {
            "stem": "轴对称图形沿对称轴折叠后，两部分（ ）",
            "correct": "完全重合",
            "wrong": ["面积相等但不重合", "形状不同", "一定全等但位置不变"],
            "explanation": "轴对称的定义。",
        },
        {"stem": "点 (2,3) 关于 x 轴对称的点是 ______。", "answer": "(2,-3)", "accepted": ["2,-3"], "explanation": "纵坐标变相反数。"},
        {
            "stem": "等腰三角形的对称轴是（ ）",
            "correct": "顶角平分线所在的直线",
            "wrong": ["底边", "任意一条高", "一条腰"],
            "explanation": "等腰三角形三线合一。",
        },
    ],
    "parallelogram": [
        {"stem": "平行四边形的对角 ______。", "answer": "相等", "explanation": "平行四边形对角相等。"},
        {
            "stem": "平行四边形的对角线（ ）",
            "correct": "互相平分",
            "wrong": ["相等", "互相垂直", "平分一组对角"],
            "explanation": "平行四边形对角线互相平分。",
        },
        {"stem": "平行四边形一个内角为 70°，它的邻角是 ______ 度。", "answer": "110", "explanation": "邻角互补。"},
    ],
    "special_parallelogram": [
        {
            "stem": "矩形具有而一般平行四边形不一定具有的性质是（ ）",
            "correct": "对角线相等",
            "wrong": ["对边平行", "对角相等", "对角线互相平分"],
            "explanation": "矩形对角线相等。",
        },
        {"stem": "菱形的对角线互相 ______。", "answer": "垂直平分", "explanation": "菱形对角线互相垂直平分。"},
        {
            "stem": "正方形既是矩形，又是（ ）",
            "correct": "菱形",
            "wrong": ["梯形", "平行四边形但不是菱形", "一般四边形"],
            "explanation": "正方形是特殊的矩形和菱形。",
        },
    ],
    "projection_view": [
        {
            "stem": "主视图是从物体的（ ）方向看到的图形。",
            "correct": "正面",
            "wrong": ["上面", "左面", "右面"],
            "explanation": "主视图、俯视图、左视图分别从正面、上面、左面看。",
        },
        {"stem": "正方体的主视图是 ______。", "answer": "正方形", "explanation": "正方体从正面看是正方形。"},
        {
            "stem": "球的三视图都是（ ）",
            "correct": "圆",
            "wrong": ["正方形", "三角形", "椭圆"],
            "explanation": "球从任何方向看都是圆。",
        },
    ],
    "data_collect": [
        {
            "stem": "调查全班同学的身高，适合采用（ ）",
            "correct": "全面调查",
            "wrong": ["抽样调查", "重点调查", "典型调查"],
            "explanation": "班级人数少，且需要完整数据，适合全面调查。",
        },
        {"stem": "一组数据中出现次数最多的数叫做 ______。", "answer": "众数", "explanation": "众数的定义。"},
        {
            "stem": "抽样调查时，样本应当具有（ ）",
            "correct": "代表性",
            "wrong": ["特殊性", "随意性", "越大越好但不看结构"],
            "explanation": "样本要能代表总体。",
        },
    ],
    "histogram": [
        {"stem": "频数分布直方图中，小长方形的高表示 ______。", "answer": "频数", "explanation": "等距分组时，高表示频数。"},
        {
            "stem": "组距是指（ ）",
            "correct": "每组两个端点之间的距离",
            "wrong": ["每组的频数", "组数", "数据的总个数"],
            "explanation": "组距=组上限-组下限。",
        },
        {"stem": "某组频数为 8，数据总数为 40，该组频率是 ______。", "answer": "0.2", "explanation": "频率=频数÷总数=8/40=0.2。"},
    ],
    "data_line": [
        {
            "stem": "折线统计图最适合表示（ ）",
            "correct": "数量的增减变化",
            "wrong": ["各部分所占比例", "数的绝对值", "几何图形"],
            "explanation": "折线图强调变化趋势。",
        },
        {"stem": "折线统计图的横轴通常表示 ______。", "answer": "时间", "explanation": "折线图常用于随时间变化的数据。"},
        {
            "stem": "要比较两组数据的变化趋势，适合用（ ）",
            "correct": "复式折线统计图",
            "wrong": ["条形统计图", "扇形统计图", "直方图"],
            "explanation": "复式折线图可同时展示两组变化。",
        },
    ],
}