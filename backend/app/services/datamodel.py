"""数据建模业务规则：全字段登记、维度引用解析与停用引用留痕。

登记按「模型编号」幂等 upsert；关联维度必须能按维度编号解析到维度管理里的
记录，只认编号不认名称，同名不同编号不会被合并。维度被停用后不改动模型数据，
读取时在引用上标注状态与错误原因。历史文本字段原样保留，无法转换的结构化
尝试直接拒绝，绝不覆盖原文。
"""
from __future__ import annotations

import copy
from typing import Any

from app.services.dimension import DimensionService
from app.services.field_rules import (
    FieldValidationError,
    accept_text_or_structured_list,
    as_text,
    check_protected,
    check_required,
    is_scalar,
    optional_text,
    type_name,
)
from app.store import store

MODULE = "datamodel"
REQUIRED_FIELDS = ["模型编号", "模型名称", "事实表名"]
OPTIONAL_TEXT_FIELDS = ["增量键", "刷新周期"]
LEGACY_OR_STRUCT_FIELDS = ["关联维度", "度量列定义", "分区字段"]
ALL_BUSINESS_FIELDS = frozenset(REQUIRED_FIELDS + OPTIONAL_TEXT_FIELDS + LEGACY_OR_STRUCT_FIELDS)
STATUS_ORDER = ["草稿", "待审核", "已生效", "已废弃"]
ACTION_RULES = {"提交审核": "待审核", "生效发布": "已生效", "废弃下线": "已废弃"}
NEGATIVE_ACTIONS = []

_DIM_REF_REQUIRED = ("维度编号",)
_DIM_REF_OPTIONAL = ("维度名称",)
_MEASURE_REQUIRED = ("列名", "聚合方式")
_MEASURE_OPTIONAL = ("数据类型", "备注")

dimension_service = DimensionService()


class DatamodelService:
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
            rows = [row for row in rows if keyword in str(row.get("模型编号", ""))]
        if status:
            rows = [row for row in rows if row.get("status") == status]
        total = len(rows)
        start = max(page - 1, 0) * size
        return rows[start:start + size], total

    def get_entry(self, entry_id: int) -> dict[str, Any] | None:
        row = store.find(MODULE, entry_id)
        return self._serialize(row) if row is not None else None

    def find_by_code(self, model_code: str) -> dict[str, Any] | None:
        code = str(model_code).strip()
        for row in store.rows(MODULE):
            if str(row.get("模型编号", "")).strip() == code:
                return row
        return None

    def create_entry(
        self, values: dict[str, Any]
    ) -> tuple[dict[str, Any] | None, list[str]]:
        """登记或更新模型定义；任何校验失败都在落库前返回，保证不发生部分写入。"""
        problems = check_protected(values, ALL_BUSINESS_FIELDS)
        if problems:
            return None, problems

        existing = self.find_by_code(str(values.get("模型编号", "")).strip()) if values.get("模型编号") else None
        candidate = self._build_candidate(values, existing)
        problems = check_required(candidate, REQUIRED_FIELDS)
        if problems:
            return None, problems
        try:
            self._validate(candidate)
        except FieldValidationError as exc:
            return None, [str(exc)]

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
            return None, f"模型定义 {entry_id} 不存在或已归档"
        if action not in ACTION_RULES:
            return None, f"动作「{action}」不属于数据建模可执行范围"
        target = ACTION_RULES[action]
        if target not in STATUS_ORDER:
            return None, f"目标状态「{target}」不在允许的状态序列里"
        entry["status"] = target
        entry["pending"] = target != STATUS_ORDER[-1]
        entry["abnormal"] = action in NEGATIVE_ACTIONS
        return self._serialize(entry), f"模型定义已{action}"

    # ------------------------------------------------------------------ #
    # 内部辅助
    # ------------------------------------------------------------------ #
    def _build_candidate(
        self, values: dict[str, Any], existing: dict[str, Any] | None
    ) -> dict[str, Any]:
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
        if "关联维度" in candidate and candidate["关联维度"] is not None:
            candidate["关联维度"] = self._normalize_dimension_refs(candidate["关联维度"])
        if "度量列定义" in candidate and candidate["度量列定义"] is not None:
            candidate["度量列定义"] = accept_text_or_structured_list(
                candidate["度量列定义"],
                "度量列定义",
                required_item_keys=_MEASURE_REQUIRED,
                optional_item_keys=_MEASURE_OPTIONAL,
            )
        if "分区字段" in candidate and candidate["分区字段"] is not None:
            candidate["分区字段"] = self._normalize_partition_fields(candidate["分区字段"])

    def _normalize_dimension_refs(
        self, value: Any
    ) -> str | list[dict[str, str]]:
        normalized = accept_text_or_structured_list(
            value,
            "关联维度",
            required_item_keys=_DIM_REF_REQUIRED,
            optional_item_keys=_DIM_REF_OPTIONAL,
        )
        # 历史文本：不解析、不改写，原样落库，读取侧只做非破坏性提示。
        if isinstance(normalized, str):
            return normalized
        for item in normalized:
            code = item["维度编号"]
            dimension = dimension_service.find_by_code(code)
            if dimension is None:
                raise FieldValidationError(
                    f"关联维度引用了不存在的维度编号「{code}」，请先在维度管理登记该维度"
                )
            stored_name = str(dimension.get("维度名称", "")).strip()
            submitted_name = item.get("维度名称", "").strip()
            # 编号是唯一锚点：名称与编号对不上时直接拒绝，防止同名不同编号被混成一项。
            if submitted_name and stored_name and submitted_name != stored_name:
                raise FieldValidationError(
                    f"关联维度「{code}」提交的名称「{submitted_name}」"
                    f"与维度登记名称「{stored_name}」不一致"
                )
        return normalized

    def _normalize_partition_fields(self, value: Any) -> str | list[str]:
        """分区字段：历史文本原样保留；结构化值必须是标量字符串数组。"""
        if isinstance(value, str):
            text = value.strip()
            if not text:
                raise FieldValidationError("字段「分区字段」不能为空字符串")
            return text
        if not isinstance(value, list) or isinstance(value, bool):
            raise FieldValidationError(
                f"字段「分区字段」的结构化值必须是数组，收到的是 {type_name(value)}"
            )
        fields: list[str] = []
        for index, item in enumerate(value):
            if not is_scalar(item):
                raise FieldValidationError(
                    f"字段「分区字段」第 {index + 1} 项必须是字段名文本，"
                    f"收到的是 {type_name(item)}"
                )
            text = str(item).strip()
            if not text:
                raise FieldValidationError(f"字段「分区字段」第 {index + 1} 项不能为空")
            if text in fields:
                raise FieldValidationError(f"字段「分区字段」存在重复字段名「{text}」")
            fields.append(text)
        return fields

    def _enrich_dimension_refs(self, stored: Any) -> list[dict[str, Any]]:
        """读取侧解析：不改动存储，只在返回副本上补充解析状态与错误原因。"""
        if isinstance(stored, str):
            return [{
                "原始引用": stored,
                "引用状态": "历史文本",
                "错误原因": None,
                "说明": "文本格式无法可靠解析到维度编号，原文保留未改动，建议重新登记为结构化引用",
            }]
        if not isinstance(stored, list):
            return []
        enriched: list[dict[str, Any]] = []
        for item in stored:
            if not isinstance(item, dict):
                continue
            code = str(item.get("维度编号", "")).strip()
            dimension = dimension_service.find_by_code(code)
            if dimension is None:
                enriched.append({
                    "维度编号": code,
                    "维度名称": item.get("维度名称"),
                    "解析名称": None,
                    "引用状态": "无效",
                    "错误原因": f"维度编号「{code}」不存在，引用已保留但无法解析",
                })
                continue
            stored_name = str(dimension.get("维度名称", "")).strip()
            dim_status = str(dimension.get("status", "")).strip()
            if dim_status == "已废弃":
                enriched.append({
                    "维度编号": code,
                    "维度名称": item.get("维度名称") or stored_name,
                    "解析名称": stored_name,
                    "引用状态": "已停用",
                    "错误原因": f"维度「{stored_name}」（{code}）已停用，模型原有引用保留",
                })
            else:
                enriched.append({
                    "维度编号": code,
                    "维度名称": item.get("维度名称") or stored_name,
                    "解析名称": stored_name,
                    "引用状态": "有效",
                    "错误原因": None,
                })
        return enriched

    def _serialize(self, row: dict[str, Any]) -> dict[str, Any]:
        """统一读取出口：列表、详情、重新进入页面、导出拿到的字段形状完全一致。"""
        result = copy.deepcopy(row)
        for field in ALL_BUSINESS_FIELDS:
            result.setdefault(field, None)
        result["关联维度引用"] = self._enrich_dimension_refs(row.get("关联维度"))
        return result


service = DatamodelService()
