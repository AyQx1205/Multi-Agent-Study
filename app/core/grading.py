"""服务端判分。

原则：能确定性判分的题（选择、数值、简单表达式）不交给 LLM。
"""

from __future__ import annotations

import math
import re
from fractions import Fraction

from app.core.content import Question, QuestionType

_SPACE_RE = re.compile(r"\s+")
_VAR_PREFIX_RE = re.compile(r"^[a-zA-Z]=")


def _clean(text: str) -> str:
    value = str(text).strip().lower()
    value = _SPACE_RE.sub("", value)
    value = (
        value.replace("＝", "=")
        .replace("，", ",")
        .replace("。", "")
        .replace("°", "")
        .replace("％", "%")
    )
    return value


def normalize_answer(text: str) -> str:
    """判分用的归一化形式，同时作为幂等指纹的一部分。

    指纹和判分必须用同一套归一化，否则「x = 4」和「x=4」判分相同、
    指纹却不同，客户端重试会拿到 409 而不是幂等重放。
    """
    return _clean(text)


def _as_number(text: str) -> float | None:
    """把 3/4、0.75、75%、x=2 转成数值；失败返回 None。"""
    value = _clean(text).replace(",", "")
    value = _VAR_PREFIX_RE.sub("", value)
    percent = value.endswith("%")
    if percent:
        value = value[:-1]
    try:
        number = float(Fraction(value))
    except (ValueError, ZeroDivisionError):
        try:
            number = float(value)
        except ValueError:
            return None
    if percent:
        number /= 100
    return number


def _number_equal(left: str, right: str) -> bool:
    a = _as_number(left)
    b = _as_number(right)
    if a is None or b is None:
        return False
    return math.isclose(a, b, rel_tol=1e-9, abs_tol=1e-9)


def _choice_letter(text: str, options: list[str]) -> str | None:
    value = text.strip().upper()
    if len(value) == 1 and "A" <= value <= "Z":
        return value
    target = _clean(text)
    for index, option in enumerate(options):
        if _clean(option) == target:
            return "ABCDEFGHIJKLMNOPQRSTUVWXYZ"[index]
    return None


def grade_answer(question: Question, submitted: str) -> bool:
    """返回学生答案是否正确；空答案恒错。"""
    if submitted is None or not str(submitted).strip():
        return False

    if question.type is QuestionType.CHOICE:
        letter = _choice_letter(str(submitted), question.options)
        return letter is not None and letter == question.answer.strip().upper()

    candidates = [question.answer, *question.accepted]
    submitted_clean = _clean(submitted)
    for candidate in candidates:
        if _clean(candidate) == submitted_clean:
            return True
        if _number_equal(submitted, candidate):
            return True
    return False