"""数据建模 / 维度管理共用的字段规则。

两个模块的登记请求都通过 ``EntryPayload.values`` 传入，这里集中处理三件事：

1. 受保护字段（id、status、pending、abnormal）不允许由业务登记接口改写；
2. 各字段按其声明的类型做严格校验，布尔值冒充字符串/数字会被识别为非法；
3. 历史数据里用纯文本表示的层级关系、度量列等字段原样保留，
   新登记的结构化值走独立的规范化路径，无法转换时不覆盖原文。
"""
from __future__ import annotations

from typing import Any

# 服务端内部标记：只能由状态流转动作（actions 接口）维护，登记接口一律拒绝。
PROTECTED_KEYS = frozenset({"id", "status", "pending", "abnormal"})

# 允许出现的简单标量（字符串、整数、浮点），bool 是 int 的子类，需要单独排除。
_SCALAR_TYPES = (str, int, float)


class FieldValidationError(ValueError):
    """登记内容非法；携带面向调用方的中文原因，且保证写入尚未发生。"""


def is_scalar(value: Any) -> bool:
    return isinstance(value, _SCALAR_TYPES) and not isinstance(value, bool)


def as_text(value: Any, field: str) -> str:
    """把标量字段归一为文本；布尔、列表、字典一律视为非法类型。"""
    if not is_scalar(value):
        raise FieldValidationError(
            f"字段「{field}」只接受文本或数字，收到的是 {type_name(value)}"
        )
    text = str(value).strip()
    if not text:
        raise FieldValidationError(f"字段「{field}」不能为空字符串")
    return text


def optional_text(value: Any, field: str) -> str:
    """可选标量字段：空值归一成空串（调用方决定是否落库），非空必须是标量。"""
    if value is None or (isinstance(value, str) and not value.strip()):
        return ""
    return as_text(value, field)


def type_name(value: Any) -> str:
    if isinstance(value, bool):
        return "布尔值"
    if isinstance(value, list):
        return "数组"
    if isinstance(value, dict):
        return "对象"
    if value is None:
        return "空值"
    if isinstance(value, str):
        return "文本"
    return type(value).__name__


def check_protected(values: dict[str, Any], allowed: frozenset[str]) -> list[str]:
    """挑出受保护字段与未在模块定义里声明的字段；有任何一个都拒绝整条登记。"""
    problems: list[str] = []
    for key in values:
        if key in PROTECTED_KEYS:
            problems.append(f"字段「{key}」是服务端内部标记，不允许通过登记接口写入")
        elif key not in allowed:
            problems.append(f"字段「{key}」不属于该模块定义的业务字段")
    return problems


def check_required(candidate: dict[str, Any], required: list[str]) -> list[str]:
    problems: list[str] = []
    for field in required:
        value = candidate.get(field)
        if value is None or (isinstance(value, str) and not value.strip()):
            problems.append(f"缺少必填字段：{field}")
    return problems


def normalize_structured_list(
    value: Any,
    field: str,
    *,
    required_item_keys: tuple[str, ...],
    optional_item_keys: tuple[str, ...] = (),
) -> list[dict[str, str]]:
    """校验并规范化结构化数组：每个元素必须是对象，必填子字段缺失即拒绝。"""
    if not isinstance(value, list) or isinstance(value, bool):
        raise FieldValidationError(
            f"字段「{field}」的结构化值必须是数组，收到的是 {type_name(value)}"
        )
    normalized: list[dict[str, str]] = []
    seen_required: set[str] = set()
    for index, item in enumerate(value):
        if not isinstance(item, dict) or isinstance(item, bool):
            raise FieldValidationError(
                f"字段「{field}」第 {index + 1} 项必须是对象，收到的是 {type_name(item)}"
            )
        row: dict[str, str] = {}
        for key in required_item_keys:
            if key not in item:
                raise FieldValidationError(
                    f"字段「{field}」第 {index + 1} 项缺少必填子字段「{key}」"
                )
            row[key] = as_text(item[key], f"{field}[{index + 1}].{key}")
        for key in optional_item_keys:
            if key in item and item[key] is not None:
                # 提交了就按文本规范化并保留键（含空串），保证保存与读取完全一致；
                # 未提交的可选键不落库。
                row[key] = optional_text(item[key], f"{field}[{index + 1}].{key}")
        unknown = set(item) - set(required_item_keys) - set(optional_item_keys)
        if unknown:
            raise FieldValidationError(
                f"字段「{field}」第 {index + 1} 项存在未定义子字段：{'、'.join(sorted(unknown))}"
            )
        identity = "|".join(row[key] for key in required_item_keys)
        if identity in seen_required:
            raise FieldValidationError(
                f"字段「{field}」第 {index + 1} 项与前面的记录重复：{identity}"
            )
        seen_required.add(identity)
        normalized.append(row)
    return normalized


def accept_text_or_structured_list(
    value: Any,
    field: str,
    *,
    required_item_keys: tuple[str, ...],
    optional_item_keys: tuple[str, ...] = (),
) -> str | list[dict[str, str]]:
    """兼容历史文本与新结构化值：

    - 历史数据/旧客户端提交的纯文本：原样保留，绝不尝试自动改写；
    - 数组：按结构化规则逐条校验，任一项非法则抛错（整条登记作废）；
    - 其它类型：拒绝，不做隐式转换。
    """
    if isinstance(value, str):
        text = value.strip()
        if not text:
            raise FieldValidationError(f"字段「{field}」不能为空字符串")
        return text
    if isinstance(value, list):
        return normalize_structured_list(
            value,
            field,
            required_item_keys=required_item_keys,
            optional_item_keys=optional_item_keys,
        )
    raise FieldValidationError(
        f"字段「{field}」只接受文本（历史格式）或结构化数组，收到的是 {type_name(value)}"
    )
