"""数据建模 / 维度管理登记与读取的验收测试。

覆盖：
1. 合法结构化登记：全字段保存后列表、详情、导出读到同一份完整数据；
2. 未知维度引用：整条拒绝、不发生部分写入，旧记录保持完好；
3. 历史文本：层级关系/度量列/日期样例的文本原文保留，不被结构化转换覆盖；
4. 同名不同编号维度：引用只认编号，名称对不上直接拒绝；
5. 受保护字段注入：id/status/pending/abnormal 与未定义字段一律拦截；
6. 非法类型：布尔、对象混入等整条拒绝；
7. 维度停用：模型引用与错误原因保留，不静默删除模型字段；
8. 同一编号重复提交：幂等更新，不产生矛盾副本；
9. 模型页与维度页对同一引用关系的展示一致（正向 + 反向解析）。
"""
from __future__ import annotations

import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from fastapi.testclient import TestClient  # noqa: E402

from app.main import app  # noqa: E402
from app.store import store  # noqa: E402


class RegistrationFlowTest(unittest.TestCase):
    def setUp(self) -> None:
        store.reset()
        self.client = TestClient(app)

    # ------------------------------------------------------------------ #
    def test_dimension_full_structured_registration_round_trip(self) -> None:
        payload = {
            "维度编号": "DIME-1001",
            "维度名称": "地区维度",
            "数据来源表": "dim_region",
            "维度键列": "region_id",
            "层级关系": [
                {"层级名称": "大区", "上级层级": "", "层级取值列": "region_l1"},
                {"层级名称": "省份", "上级层级": "大区", "层级取值列": "region_l2"},
            ],
            "历史拉链策略": "拉链表",
            "刷新周期": "每日",
            "默认取值": "未知",
            "日期样例": [
                {"字段名": "biz_date", "样例值": "2026-11-12", "日期格式": "yyyy-MM-dd"},
            ],
        }
        response = self.client.post("/api/dimension", json={"values": payload})
        self.assertEqual(response.status_code, 200)
        body = response.json()
        self.assertTrue(body["ok"], body)
        entry_id = body["entry"]["id"]

        # 详情、列表、导出三个出口读到的字段必须完全一致。
        detail = self.client.get(f"/api/dimension/{entry_id}").json()
        listing = self.client.get("/api/dimension", params={"keyword": "DIME-1001"}).json()
        exported = self.client.get("/api/dimension/export").json()
        export_row = next(item for item in exported["items"] if item["维度编号"] == "DIME-1001")
        list_row = next(item for item in listing["items"] if item["id"] == entry_id)
        for source in (detail, list_row, export_row):
            self.assertEqual(source["维度键列"], "region_id")
            self.assertEqual(source["历史拉链策略"], "拉链表")
            self.assertEqual(source["刷新周期"], "每日")
            self.assertEqual(source["默认取值"], "未知")
            self.assertEqual(source["层级关系"], payload["层级关系"])
            self.assertEqual(source["日期样例"], payload["日期样例"])
        # 新维度没有被任何模型引用，反向引用为空数组而不是缺字段。
        self.assertEqual(detail["关联模型"], [])

    def test_datamodel_full_structured_registration_round_trip(self) -> None:
        self._register_dimension("DIME-2001", "渠道维度")
        payload = {
            "模型编号": "MDL-2001",
            "模型名称": "交易事实模型",
            "事实表名": "fact_trade",
            "关联维度": [{"维度编号": "DIME-2001", "维度名称": "渠道维度"}],
            "度量列定义": [
                {"列名": "gmv", "聚合方式": "sum", "数据类型": "decimal(18,2)"},
                {"列名": "order_cnt", "聚合方式": "count"},
            ],
            "分区字段": ["dt", "hour"],
            "增量键": "updated_at",
            "刷新周期": "每小时",
        }
        body = self.client.post("/api/datamodel", json={"values": payload}).json()
        self.assertTrue(body["ok"], body)
        entry_id = body["entry"]["id"]

        detail = self.client.get(f"/api/datamodel/{entry_id}").json()
        listing = self.client.get("/api/datamodel", params={"keyword": "MDL-2001"}).json()
        exported = self.client.get("/api/datamodel/export").json()
        export_row = next(item for item in exported["items"] if item["模型编号"] == "MDL-2001")
        list_row = next(item for item in listing["items"] if item["id"] == entry_id)
        for source in (detail, list_row, export_row):
            self.assertEqual(source["事实表名"], "fact_trade")
            self.assertEqual(source["关联维度"], payload["关联维度"])
            self.assertEqual(source["度量列定义"], payload["度量列定义"])
            self.assertEqual(source["分区字段"], ["dt", "hour"])
            self.assertEqual(source["增量键"], "updated_at")
            self.assertEqual(source["刷新周期"], "每小时")
        # 读取侧的引用解析：有效引用，无错误原因。
        ref = detail["关联维度引用"][0]
        self.assertEqual(ref["维度编号"], "DIME-2001")
        self.assertEqual(ref["引用状态"], "有效")
        self.assertIsNone(ref["错误原因"])

        # 维度页的反向引用与模型页的正向引用指向同一条关系。
        dim_detail = self.client.get(
            "/api/dimension", params={"keyword": "DIME-2001"}
        ).json()["items"][0]
        self.assertEqual(len(dim_detail["关联模型"]), 1)
        self.assertEqual(dim_detail["关联模型"][0]["模型编号"], "MDL-2001")
        self.assertEqual(dim_detail["关联模型"][0]["模型id"], entry_id)

    def test_unknown_dimension_reference_rejects_whole_submission(self) -> None:
        before = self.client.get("/api/datamodel/export").json()
        payload = {
            "模型编号": "MDL-4040",
            "模型名称": "坏引用模型",
            "事实表名": "fact_bad",
            "关联维度": [{"维度编号": "DIME-NOT-EXIST"}],
            "增量键": "id",
        }
        body = self.client.post("/api/datamodel", json={"values": payload}).json()
        self.assertFalse(body["ok"])
        self.assertIn("DIME-NOT-EXIST", body["message"])
        # 失败后再次查询：记录没有部分写入，旧数据完好。
        after = self.client.get("/api/datamodel/export").json()
        self.assertEqual(after, before)
        self.assertFalse(any(item["模型编号"] == "MDL-4040" for item in after["items"]))

    def test_legacy_text_fields_are_preserved_verbatim(self) -> None:
        # 维度侧的历史文本：层级关系、日期样例。
        dim_payload = {
            "维度编号": "DIME-3001",
            "维度名称": "历史维度",
            "数据来源表": "dim_legacy",
            "层级关系": "大区 > 省份 > 城市（历史文本）",
            "日期样例": "2026/01/01（历史文本）",
        }
        dim_body = self.client.post("/api/dimension", json={"values": dim_payload}).json()
        self.assertTrue(dim_body["ok"], dim_body)
        dim_id = dim_body["entry"]["id"]
        dim_detail = self.client.get(f"/api/dimension/{dim_id}").json()
        self.assertEqual(dim_detail["层级关系"], "大区 > 省份 > 城市（历史文本）")
        self.assertEqual(dim_detail["日期样例"], "2026/01/01（历史文本）")

        # 模型侧的历史文本：关联维度、度量列定义、分区字段。
        model_payload = {
            "模型编号": "MDL-3001",
            "模型名称": "历史模型",
            "事实表名": "fact_legacy",
            "关联维度": "DIME-3001, DIME-3002（历史文本）",
            "度量列定义": "gmv=sum(amount)（历史文本）",
            "分区字段": "dt（历史文本）",
        }
        model_body = self.client.post("/api/datamodel", json={"values": model_payload}).json()
        self.assertTrue(model_body["ok"], model_body)
        model_id = model_body["entry"]["id"]
        model_detail = self.client.get(f"/api/datamodel/{model_id}").json()
        self.assertEqual(model_detail["关联维度"], model_payload["关联维度"])
        self.assertEqual(model_detail["度量列定义"], model_payload["度量列定义"])
        self.assertEqual(model_detail["分区字段"], model_payload["分区字段"])
        # 文本引用不强行解析，但要给出非破坏性提示，原文不动。
        hint = model_detail["关联维度引用"][0]
        self.assertEqual(hint["引用状态"], "历史文本")
        self.assertEqual(hint["原始引用"], model_payload["关联维度"])

        # 再次提交同样的文本仍然是同一份数据（幂等、不覆盖）。
        again = self.client.post("/api/datamodel", json={"values": model_payload}).json()
        self.assertTrue(again["ok"])
        self.assertEqual(again["entry"]["id"], model_id)
        reread = self.client.get(f"/api/datamodel/{model_id}").json()
        self.assertEqual(reread["关联维度"], model_payload["关联维度"])

    def test_same_name_dimensions_are_distinguished_by_code(self) -> None:
        self._register_dimension("DIME-5001", "同名维度")
        self._register_dimension("DIME-5002", "同名维度")
        # 编号与名称正确匹配：按编号解析，两个同名维度各自可被引用。
        for code in ("DIME-5001", "DIME-5002"):
            body = self.client.post("/api/datamodel", json={"values": {
                "模型编号": f"MDL-50{code[-2:]}",
                "模型名称": f"引用{code}",
                "事实表名": "fact_same_name",
                "关联维度": [{"维度编号": code, "维度名称": "同名维度"}],
            }}).json()
            self.assertTrue(body["ok"], body)
        # 张冠李戴：编号是 5001 却报 5002 的名称，必须拒绝，不能混成同一项。
        bad = self.client.post("/api/datamodel", json={"values": {
            "模型编号": "MDL-5099",
            "模型名称": "错配引用",
            "事实表名": "fact_mismatch",
            "关联维度": [{"维度编号": "DIME-5001", "维度名称": "完全不同的名字"}],
        }}).json()
        self.assertFalse(bad["ok"])
        self.assertIn("不一致", bad["message"])

    def test_protected_and_undefined_fields_are_rejected(self) -> None:
        for endpoint, payload in (
            ("/api/dimension", {
                "维度编号": "DIME-6001", "维度名称": "注入维度", "数据来源表": "t",
            }),
            ("/api/datamodel", {
                "模型编号": "MDL-6001", "模型名称": "注入模型", "事实表名": "t",
            }),
        ):
            before = self.client.get(f"{endpoint}/export").json()
            for injected in (
                {"id": 999},
                {"status": "已生效"},
                {"pending": False},
                {"abnormal": True},
                {"未定义字段": "x"},
            ):
                body = self.client.post(
                    endpoint, json={"values": {**payload, **injected}}
                ).json()
                self.assertFalse(body["ok"], (endpoint, injected))
            after = self.client.get(f"{endpoint}/export").json()
            self.assertEqual(after, before)

        # 业务编号也不能借登记改写已有记录的 id：重复编号走 upsert，id 保持不变。
        first = self.client.post("/api/dimension", json={"values": {
            "维度编号": "DIME-6002", "维度名称": "幂等维度", "数据来源表": "t",
        }}).json()
        second = self.client.post("/api/dimension", json={"values": {
            "维度编号": "DIME-6002", "维度名称": "幂等维度改名", "数据来源表": "t2",
        }}).json()
        self.assertTrue(second["ok"])
        self.assertEqual(second["entry"]["id"], first["entry"]["id"])
        self.assertEqual(second["entry"]["status"], first["entry"]["status"])
        self.assertEqual(second["entry"]["维度名称"], "幂等维度改名")
        self.assertEqual(
            len(self.client.get("/api/dimension", params={"keyword": "DIME-6002"}).json()["items"]),
            1,
        )

    def test_illegal_types_reject_entire_submission(self) -> None:
        self._register_dimension("DIME-7001", "类型维度")
        before = self.client.get("/api/datamodel/export").json()
        bad_payloads = [
            # 布尔值冒充文本
            {"模型编号": "MDL-7001", "模型名称": True, "事实表名": "t"},
            # 结构化关联维度不是数组
            {"模型编号": "MDL-7002", "模型名称": "n", "事实表名": "t",
             "关联维度": {"维度编号": "DIME-7001"}},
            # 数组里混入字符串
            {"模型编号": "MDL-7003", "模型名称": "n", "事实表名": "t",
             "关联维度": ["DIME-7001"]},
            # 度量列缺少必填子字段
            {"模型编号": "MDL-7004", "模型名称": "n", "事实表名": "t",
             "度量列定义": [{"列名": "gmv"}]},
            # 分区字段数组里混入对象
            {"模型编号": "MDL-7005", "模型名称": "n", "事实表名": "t",
             "分区字段": [{"字段名": "dt"}]},
            # 层级关系数组里混入布尔
            {"维度编号": "DIME-7002", "维度名称": "n", "数据来源表": "t",
             "层级关系": [True]},
        ]
        endpoints = ["/api/datamodel"] * 5 + ["/api/dimension"]
        for endpoint, payload in zip(endpoints, bad_payloads):
            body = self.client.post(endpoint, json={"values": payload}).json()
            self.assertFalse(body["ok"], (endpoint, payload))
        after = self.client.get("/api/datamodel/export").json()
        self.assertEqual(after, before)

    def test_disabled_dimension_keeps_model_reference_with_error_reason(self) -> None:
        dim_id = self._register_dimension("DIME-8001", "即将停用维度")
        model_body = self.client.post("/api/datamodel", json={"values": {
            "模型编号": "MDL-8001",
            "模型名称": "引用模型",
            "事实表名": "fact_ref",
            "关联维度": [{"维度编号": "DIME-8001", "维度名称": "即将停用维度"}],
        }}).json()
        self.assertTrue(model_body["ok"], model_body)
        model_id = model_body["entry"]["id"]

        # 停用维度：动作接口维护状态，模型侧不被静默改动。
        action = self.client.post(
            f"/api/dimension/{dim_id}/actions", json={"values": {"action": "废弃下线"}}
        ).json()
        self.assertTrue(action["ok"])
        self.assertEqual(action["entry"]["status"], "已废弃")

        model_detail = self.client.get(f"/api/datamodel/{model_id}").json()
        self.assertEqual(
            model_detail["关联维度"],
            [{"维度编号": "DIME-8001", "维度名称": "即将停用维度"}],
        )
        ref = model_detail["关联维度引用"][0]
        self.assertEqual(ref["引用状态"], "已停用")
        self.assertIn("已停用", ref["错误原因"])
        self.assertEqual(ref["维度编号"], "DIME-8001")

        # 维度页仍能看到这条反向引用。
        dim_detail = self.client.get(f"/api/dimension/{dim_id}").json()
        self.assertEqual(dim_detail["关联模型"][0]["模型编号"], "MDL-8001")

        # 停用后新登记的模型仍可引用该编号（历史约束不删数据），读取时标注已停用。
        legacy_ref = self.client.post("/api/datamodel", json={"values": {
            "模型编号": "MDL-8002",
            "模型名称": "停用后仍引用",
            "事实表名": "fact_ref2",
            "关联维度": [{"维度编号": "DIME-8001", "维度名称": "即将停用维度"}],
        }}).json()
        self.assertTrue(legacy_ref["ok"], legacy_ref)
        self.assertEqual(
            self.client.get(
                f"/api/datamodel/{legacy_ref['entry']['id']}"
            ).json()["关联维度引用"][0]["引用状态"],
            "已停用",
        )

    def test_repeated_submission_same_code_stays_consistent(self) -> None:
        payload = {
            "模型编号": "MDL-9001",
            "模型名称": "首版名称",
            "事实表名": "fact_v1",
            "增量键": "id",
            "刷新周期": "每日",
        }
        first = self.client.post("/api/datamodel", json={"values": payload}).json()
        second_payload = {**payload, "模型名称": "改版名称", "刷新周期": "每小时"}
        second = self.client.post("/api/datamodel", json={"values": second_payload}).json()
        self.assertTrue(second["ok"])
        self.assertEqual(second["entry"]["id"], first["entry"]["id"])
        rows = self.client.get(
            "/api/datamodel", params={"keyword": "MDL-9001"}
        ).json()["items"]
        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0]["模型名称"], "改版名称")
        self.assertEqual(rows[0]["刷新周期"], "每小时")
        self.assertEqual(rows[0]["增量键"], "id")  # 未提交的合并字段保留

    def test_seed_legacy_rows_remain_readable_with_all_fields(self) -> None:
        # 既有示例数据是纯文本，读取时必须原样可见，且新字段键齐全。
        rows = self.client.get("/api/datamodel/export").json()["items"]
        seed = next(item for item in rows if item["模型编号"] == "DATA-0001")
        self.assertEqual(seed["关联维度"], "数据建模样例1")
        self.assertEqual(seed["度量列定义"], "数据建模样例1")
        self.assertEqual(seed["分区字段"], "数据建模样例1")
        self.assertEqual(seed["增量键"], "数据建模样例1")
        self.assertEqual(seed["刷新周期"], "数据建模样例1")
        self.assertEqual(seed["关联维度引用"][0]["引用状态"], "历史文本")

        dims = self.client.get("/api/dimension/export").json()["items"]
        dim_seed = next(item for item in dims if item["维度编号"] == "DIME-0001")
        self.assertEqual(dim_seed["层级关系"], "维度管理样例1")
        self.assertIsNone(dim_seed["日期样例"])
        self.assertEqual(dim_seed["关联模型"], [])

    # ------------------------------------------------------------------ #
    def _register_dimension(self, code: str, name: str) -> int:
        body = self.client.post("/api/dimension", json={"values": {
            "维度编号": code,
            "维度名称": name,
            "数据来源表": f"tbl_{code.lower()}",
        }}).json()
        self.assertTrue(body["ok"], body)
        return int(body["entry"]["id"])


if __name__ == "__main__":
    unittest.main(verbosity=2)
