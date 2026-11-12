"""维度管理业务规则：全字段登记、结构化校验与跨模块引用解析。

登记接口按「维度编号」做幂等 upsert：同一编号重复提交得到的是同一条记录，
不会产生互相矛盾的副本。读取时额外解析被哪些数据模型引用（反向引用），
维度停用不会改动模型侧数据，引用状态由模型读取侧实时计算。
"""
from __future__ import annotations

import copy
from typing import Any

from app.services.field_rules import (
    PROTECTED_KEYS,
    FieldValidationError,
    accept_text_or_structured_list,
    as_text,
    check_protected,
    check_required,
    optional_text,
)
from app.store import store

MODULE = "dimension"
REQUIRED_FIELDS = ["维度编号", "维度名称", "数据来源表"]
OPTIONAL_TEXT_FIELDS = ["维度键列", "历史拉链策略", "刷新周期", "默认取值"]
# 同时兼容历史文本与结构化数组的字段。
LEGACY_OR_STRUCT_FIELDS = ["层级关系", "日期样例"]
ALL_BUSINESS_FIELDS = frozenset(REQUIRED_FIELDS + OPTIONAL_TEXT_FIELDS + LEGACY_OR_STRUCT_FIELDS)
STATUS_ORDER = ["草稿", "待审核", "已生效", "已废弃"]
ACTION_RULES = {"提交审核": "待审核", "生效启用": "已生效", "废弃下线": "已废弃"}
NEGATIVE_ACTIONS = []

_HIERARCHY_REQUIRED = ("层级名称",)
_HIERARCHY_OPTIONAL = ("上级层级", "层级取值列", "备注")
_DATE_SAMPLE_REQUIRED = ("字段名", "样例值")
_DATE_SAMPLE_OPTIONAL = ("日期格式",)


class DimensionService:
    def list_entries(
        self,
        *,
        keyword: str | None = None,
        status: str | None = None,
        page: int = 1,
        size: int = 20,
    ) -> tuple[list[dict[str, Any]], int]:
        rows = [self._serialize(row) for row in store.rows(MODULE)]
        if keyword:
            rows = [row for row in rows if keyword in str(row.get("维度编号", ""))]
        if status:
            rows = [row for row in rows if row.get("status") == status]
        total = len(rows)
        start = max(page - 1, 0) * size
        return rows[start:start + size], total

    def get_entry(self, entry_id: int) -> dict[str, Any] | None:
        row = store.find(MODULE, entry_id)
        return self._serialize(row) if row is not None else None

    def find_by_code(self, dimension_code: str) -> dict[str, Any] | None:
        """按业务编号（而非名称）精确查找维度；同名不同编号不会被混淆。"""
        code = str(dimension_code).strip()
        for row in store.rows(MODULE):
            if str(row.get("维度编号", "")).strip() == code:
                return row
        return None

    def create_entry(
        self, values: dict[str, Any]
    ) -> tuple[dict[str, Any] | None, list[str]]:
        """登记或更新维度。任何一步校验失败都直接返回，不会发生部分写入。"""
        problems = check_protected(values, ALL_BUSINESS_FIELDS)
        if problems:
            return None, problems

        existing = self.find_by_code(str(values.get("维度编号", "")).strip()) if values.get("维度编号") else None
        candidate = self._build_candidate(values, existing)
        problems = check_required(candidate, REQUIRED_FIELDS)
        if problems:
            return None, problems
        try:
            self._validate(candidate)
        except FieldValidationError as exc:
            return None, [str(exc)]

        # 校验全部通过后才落库；更新时保留 id、状态与内部标记，只替换业务字段。
        if existing is None:
            rows = store.rows(MODULE)
            entry: dict[str, Any] = {
                "id": max((int(row.get("id", 0)) for row in rows), default=0) + 1,
                "status": STATUS_ORDER[0],
                "pending": True,
                "abnormal": False,
            }
            rows.append(entry)
        else:
            entry = existing
        for field in ALL_BUSINESS_FIELDS:
            if field in candidate:
                entry[field] = candidate[field]
        return self._serialize(entry), []

    def run_action(self, entry_id: int, action: str) -> tuple[dict[str, Any] | None, str]:
        entry = store.find(MODULE, entry_id)
        if entry is None:
            return None, f"分析维度 {entry_id} 不存在或已归档"
        if action not in ACTION_RULES:
            return None, f"动作「{action}」不属于维度管理可执行范围"
        target = ACTION_RULES[action]
        if target not in STATUS_ORDER:
            return None, f"目标状态「{target}」不在允许的状态序列里"
        entry["status"] = target
        entry["pending"] = target != STATUS_ORDER[-1]
        entry["abnormal"] = action in NEGATIVE_ACTIONS
        return self._serialize(entry), f"分析维度已{action}"

    # ------------------------------------------------------------------ #
    # 内部辅助
    # ------------------------------------------------------------------ #
    def _build_candidate(
        self, values: dict[str, Any], existing: dict[str, Any] | None
    ) -> dict[str, Any]:
        """合并已有记录与本次提交：未提交的字段沿用旧值，避免部分更新误清空。"""
        candidate: dict[str, Any] = {}
        if existing is not None:
            for field in ALL_BUSINESS_FIELDS:
                if field in existing:
                    candidate[field] = existing[field]
        candidate.update(values)
        return candidate

    def _validate(self, candidate: dict[str, Any]) -> None:
        for field in REQUIRED_FIELDS:
            candidate[field] = as_text(candidate.get(field), field)
        for field in OPTIONAL_TEXT_FIELDS:
            if field in candidate and candidate[field] is not None:
                text = optional_text(candidate[field], field)
                if text:
                    candidate[field] = text
                else:
                    candidate.pop(field, None)
        if "层级关系" in candidate and candidate["层级关系"] is not None:
            candidate["层级关系"] = accept_text_or_structured_list(
                candidate["层级关系"],
                "层级关系",
                required_item_keys=_HIERARCHY_REQUIRED,
                optional_item_keys=_HIERARCHY_OPTIONAL,
            )
        if "日期样例" in candidate and candidate["日期样例"] is not None:
            candidate["日期样例"] = accept_text_or_structured_list(
                candidate["日期样例"],
                "日期样例",
                required_item_keys=_DATE_SAMPLE_REQUIRED,
                optional_item_keys=_DATE_SAMPLE_OPTIONAL,
            )

    def _referenced_by_models(self, dimension_code: str) -> list[dict[str, Any]]:
        """反向解析：找出所有结构化引用该维度编号的模型（文本引用无法可靠解析）。"""
        references: list[dict[str, Any]] = []
        for model in store.rows("datamodel"):
            relation = model.get("关联维度")
            if not isinstance(relation, list):
                continue
            for item in relation:
                if isinstance(item, dict) and str(item.get("维度编号", "")).strip() == dimension_code:
                    references.append({
                        "模型id": model.get("id"),
                        "模型编号": model.get("模型编号"),
                        "模型名称": model.get("模型名称"),
                        "关联名称": item.get("维度名称", ""),
                    })
        return references

    def _serialize(self, row: dict[str, Any]) -> dict[str, Any]:
        """读取出口：返回拷贝并补齐全部业务字段，列表/详情/导出保持同一形状。"""
        result = copy.deepcopy(row)
        for field in ALL_BUSINESS_FIELDS:
            result.setdefault(field, None)
        result["关联模型"] = self._referenced_by_models(str(row.get("维度编号", "")).strip())
        return result


service = DimensionService()
