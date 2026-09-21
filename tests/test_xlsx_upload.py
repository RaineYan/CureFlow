"""End-to-end contracts for safe XLSX ingestion in the desktop API."""
from __future__ import annotations

import base64
import csv
import json
from datetime import date
from io import BytesIO, StringIO
from pathlib import Path
from unittest.mock import patch
from zipfile import ZIP_DEFLATED, ZipFile

import pytest
from openpyxl import Workbook

from cureflow_app.server import (
    ApiInputError,
    MAX_UPLOAD_BYTES,
    inspect_tabular_input,
    make_handler,
    xlsx_base64_to_csv,
    xlsx_base64_to_tables,
)


def _workbook_base64(rows: list[list[object]], *, title: str = "经营数据") -> str:
    workbook = Workbook()
    sheet = workbook.active
    sheet.title = title
    for row in rows:
        sheet.append(row)
    stream = BytesIO()
    workbook.save(stream)
    workbook.close()
    return base64.b64encode(stream.getvalue()).decode("ascii")


def _sample_rows() -> list[list[object]]:
    source = Path(__file__).resolve().parents[1] / "data" / "sample_company.csv"
    return list(csv.reader(StringIO(source.read_text(encoding="utf-8"))))


@pytest.fixture
def handler_request(tmp_path: Path):
    handler_type = make_handler(tmp_path)

    def invoke(payload: object):
        body = json.dumps(payload).encode("utf-8")
        handler = object.__new__(handler_type)
        handler.path = "/api/analyze"
        handler.headers = {"Content-Length": str(len(body))}
        handler.rfile = BytesIO(body)
        handler.wfile = BytesIO()
        response: dict[str, object] = {"status": None, "headers": {}}
        handler.send_response = lambda status: response.update(status=status)
        handler.send_header = lambda key, value: response["headers"].__setitem__(key, value)
        handler.end_headers = lambda: None
        handler.do_POST()
        response["json"] = json.loads(handler.wfile.getvalue().decode("utf-8"))
        return response

    return invoke


def test_xlsx_upload_runs_existing_workflow(handler_request) -> None:
    response = handler_request(
        {"filename": "经营数据.xlsx", "xlsxBase64": _workbook_base64(_sample_rows())}
    )
    assert response["status"] == 200
    assert len(response["json"]["metrics"]) == 6
    assert {
        key: response["json"]["input"][key]
        for key in ("format", "filename", "sheet")
    } == {
        "format": "xlsx",
        "filename": "经营数据.xlsx",
        "sheet": "经营数据",
    }
    assert response["json"]["input"]["analysisMode"] == "business"
    assert response["json"]["input"]["rowCount"] == len(_sample_rows()) - 1
    assert response["json"]["input"]["columnCount"] == len(_sample_rows()[0])
    assert len(response["json"]["input"]["inputFingerprint"]) == 64


def test_xlsx_converter_preserves_unicode_dates_and_zero() -> None:
    encoded = _workbook_base64(
        [["名称", "日期", "值"], ["华东", date(2026, 9, 16), 0]]
    )
    csv_text, sheet = xlsx_base64_to_csv(encoded, "input.xlsx")
    assert sheet == "经营数据"
    assert "华东,2026-09-16T00:00:00,0" in csv_text


def test_xlsx_uses_first_visible_non_empty_sheet() -> None:
    workbook = Workbook()
    first = workbook.active
    first.title = "隐藏页"
    first.sheet_state = "hidden"
    first.append(["ignored"])
    workbook.create_sheet("空白页")
    selected = workbook.create_sheet("数据页")
    selected.append(["year", "revenue"])
    selected.append([2025, 10])
    stream = BytesIO()
    workbook.save(stream)
    workbook.close()
    encoded = base64.b64encode(stream.getvalue()).decode("ascii")
    csv_text, sheet = xlsx_base64_to_csv(encoded, "input.xlsx")
    assert sheet == "数据页"
    assert csv_text.startswith("year,revenue\n")


def test_xlsx_selects_largest_credible_header_table_and_quarantines_headerless_auxiliary() -> None:
    workbook = Workbook()
    main = workbook.active
    main.title = "0"
    main.append(["学号", "姓名", "综合成绩", "专业排名", "专业总人数"])
    for index in range(1, 105):
        main.append([f"92513083{index:04d}", f"学生{index}", 90-index/10, index, 104])
    auxiliary = workbook.create_sheet("Sheet3")
    for index in range(1, 36):
        auxiliary.append([f"92513083{index:04d}", f"学生{index}", 80-index/10, index, 104])
    stream = BytesIO()
    workbook.save(stream)
    workbook.close()
    encoded = base64.b64encode(stream.getvalue()).decode("ascii")

    tables = xlsx_base64_to_tables(encoded, "scores.xlsx")
    main_table = next(table for table in tables if table["sheet"] == "0")
    auxiliary_table = next(table for table in tables if table["sheet"] == "Sheet3")
    assert main_table["headerDetected"] is True
    assert main_table["headerRow"] == 1
    assert main_table["dataRowCount"] == 104
    assert auxiliary_table["headerDetected"] is False
    assert auxiliary_table["dataRowCount"] == 35
    csv_text, selected_sheet = xlsx_base64_to_csv(encoded, "scores.xlsx")
    assert selected_sheet == "0"
    assert csv_text.startswith("学号,姓名,综合成绩,专业排名,专业总人数\n")


def test_api_profiles_primary_sheet_when_other_tabs_have_no_explicit_relationship(handler_request) -> None:
    workbook = Workbook()
    main = workbook.active
    main.title = "总表"
    main.append(["学号", "姓名", "综合成绩", "专业排名", "专业总人数"])
    for index in range(1, 41):
        main.append([f"2025{index:04d}", f"学生{index}", 60+index/2, index, 40])
    auxiliary = workbook.create_sheet("临时筛选")
    for index in range(1, 8):
        auxiliary.append([f"2025{index:04d}", f"学生{index}", 60+index/2])
    stream = BytesIO()
    workbook.save(stream)
    workbook.close()

    response = handler_request({
        "filename": "综合测评.xlsx",
        "xlsxBase64": base64.b64encode(stream.getvalue()).decode("ascii"),
    })
    assert response["status"] == 200
    payload = response["json"]
    assert payload["input"]["analysisMode"] == "generic_profile"
    assert payload["workbookInventory"]["selectedSheet"] == "总表"
    assert next(metric for metric in payload["metrics"] if metric["key"] == "row_count")["value"] == 40
    assert "辅助表“临时筛选”" in payload["report"]
    assert "未混入主表统计" in payload["report"]


def test_inspection_exposes_selected_sheet_columns_and_preview_without_provider() -> None:
    workbook = Workbook()
    main = workbook.active
    main.title = "主表"
    main.append(["学号", "姓名", "综合成绩", "专业排名", "专业总人数"])
    main.append(["20250001", "甲", 88.5, 1, 2])
    main.append(["20250002", "乙", 76.0, 2, 2])
    auxiliary = workbook.create_sheet("辅助")
    auxiliary.append(["无表头值", 1])
    stream = BytesIO()
    workbook.save(stream)
    workbook.close()

    payload = inspect_tabular_input({
        "filename": "测评.xlsx",
        "xlsxBase64": base64.b64encode(stream.getvalue()).decode("ascii"),
    })
    assert payload["providerUsed"] is False
    assert payload["purpose"] == "input_inspection_only"
    assert payload["selectedSheet"] == "主表"
    assert payload["rowCount"] == 2
    assert payload["columnCount"] == 5
    assert len(payload["inputFingerprint"]) == 64
    assert payload["columns"][0]["kind"] == "标识符"
    assert next(column for column in payload["columns"] if column["name"] == "综合成绩")["kind"] == "成绩数值"
    assert payload["previewRows"][0]["姓名"] == "甲"
    assert payload["requiresUserConfirmation"] is True


def test_inspection_fingerprint_is_stable_and_changes_with_selected_sheet() -> None:
    workbook = Workbook()
    first = workbook.active
    first.title = "总表"
    first.append(["编号", "成绩"])
    first.append(["A01", 90])
    second = workbook.create_sheet("复核表")
    second.append(["编号", "备注"])
    second.append(["A01", "待核对"])
    stream = BytesIO()
    workbook.save(stream)
    workbook.close()
    encoded = base64.b64encode(stream.getvalue()).decode("ascii")

    first_check = inspect_tabular_input({
        "filename": "测评.xlsx", "xlsxBase64": encoded, "selectedSheet": "总表",
    })
    repeated_check = inspect_tabular_input({
        "filename": "测评.xlsx", "xlsxBase64": encoded, "selectedSheet": "总表",
    })
    second_check = inspect_tabular_input({
        "filename": "测评.xlsx", "xlsxBase64": encoded, "selectedSheet": "复核表",
    })
    assert first_check["inputFingerprint"] == repeated_check["inputFingerprint"]
    assert first_check["inputFingerprint"] != second_check["inputFingerprint"]


def test_inspection_allows_credible_sheet_override_and_rejects_headerless_sheet() -> None:
    workbook = Workbook()
    first = workbook.active
    first.title = "总表"
    first.append(["编号", "分数"])
    first.append(["A", 90])
    second = workbook.create_sheet("复核表")
    second.append(["编号", "备注"])
    second.append(["A", "待复核"])
    raw = BytesIO()
    workbook.save(raw)
    workbook.close()
    encoded = base64.b64encode(raw.getvalue()).decode("ascii")

    selected = inspect_tabular_input({
        "filename": "两表.xlsx", "xlsxBase64": encoded, "selectedSheet": "复核表",
    })
    assert selected["selectedSheet"] == "复核表"
    assert selected["columns"][1]["name"] == "备注"
    with pytest.raises(ApiInputError, match="不存在"):
        inspect_tabular_input({
            "filename": "两表.xlsx", "xlsxBase64": encoded, "selectedSheet": "不存在",
        })


@pytest.mark.parametrize("encoded", ["", "not-base64!", base64.b64encode(b"PKbad").decode("ascii")])
def test_xlsx_rejects_empty_or_malformed_content(encoded: str) -> None:
    with pytest.raises(ApiInputError):
        xlsx_base64_to_csv(encoded, "input.xlsx")


def test_xlsx_rejects_legacy_xls_extension() -> None:
    with pytest.raises(ApiInputError, match=".xlsx"):
        xlsx_base64_to_csv(_workbook_base64([["a"], [1]]), "input.xls")


def test_xlsx_rejects_decoded_file_over_five_megabytes() -> None:
    encoded = base64.b64encode(b"PK" + b"x" * MAX_UPLOAD_BYTES).decode("ascii")
    with pytest.raises(ApiInputError, match="5 MB"):
        xlsx_base64_to_csv(encoded, "input.xlsx")


def test_xlsx_rejects_archive_expansion_over_safety_limit() -> None:
    stream = BytesIO()
    with ZipFile(stream, "w", compression=ZIP_DEFLATED) as archive:
        archive.writestr("xl/worksheets/sheet1.xml", b"x" * 100)
    encoded = base64.b64encode(stream.getvalue()).decode("ascii")
    with patch("cureflow_app.server.MAX_XLSX_UNCOMPRESSED_BYTES", 32):
        with pytest.raises(ApiInputError, match="解压后"):
            xlsx_base64_to_csv(encoded, "input.xlsx")


def test_xlsx_rejects_more_than_ten_thousand_data_rows() -> None:
    workbook = Workbook()
    sheet = workbook.active
    sheet.cell(row=10_002, column=1, value="too many")
    stream = BytesIO()
    workbook.save(stream)
    workbook.close()
    encoded = base64.b64encode(stream.getvalue()).decode("ascii")
    with pytest.raises(ApiInputError, match="10000"):
        xlsx_base64_to_csv(encoded, "input.xlsx")


def test_xlsx_rejects_more_than_one_hundred_columns() -> None:
    workbook = Workbook()
    sheet = workbook.active
    sheet.cell(row=1, column=101, value="too wide")
    stream = BytesIO()
    workbook.save(stream)
    workbook.close()
    encoded = base64.b64encode(stream.getvalue()).decode("ascii")
    with pytest.raises(ApiInputError, match="100 列"):
        xlsx_base64_to_csv(encoded, "input.xlsx")


def test_api_rejects_csv_and_xlsx_in_same_request(handler_request) -> None:
    response = handler_request(
        {
            "filename": "input.xlsx",
            "csvText": "year,revenue\n2025,1",
            "xlsxBase64": _workbook_base64([["year"], [2025]]),
        }
    )
    assert response["status"] == 400
    assert "不能同时提交" in response["json"]["error"]


def test_api_rejects_xlsx_filename_sent_as_text(handler_request) -> None:
    response = handler_request({"filename": "input.xlsx", "csvText": "not binary"})
    assert response["status"] == 400
    assert "二进制上传通道" in response["json"]["error"]
