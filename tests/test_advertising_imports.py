import io

from openpyxl import Workbook

from amazon_ops.advertising.imports import AdvertisingImportService, HEADERS, InMemoryImportStore, ReportType


def csv_for(report_type: ReportType, *, spend: str = "10") -> bytes:
    headers = HEADERS[report_type]
    values = {"Profile ID": "profile-1", "日期": "2026-08-01", "Campaign ID": "campaign-1", "Campaign 名称": "Test", "广告类型": "SP", "Ad Group ID": "group-1", "Ad Group 名称": "Group", "Keyword ID": "keyword-1", "关键词": "shoe", "Target ID": "target-1", "投放目标": "asin=ABC", "投放类型": "product", "Search Term": "running shoe", "匹配类型": "exact", "曝光": "100", "点击": "10", "花费": spend, "销售额": "40", "订单": "2", "广告销量": "2"}
    return (",".join(headers) + "\n" + ",".join(values.get(header, "") for header in headers) + "\n").encode()


def test_all_templates_are_imported_into_the_shared_data_set():
    store = InMemoryImportStore(); service = AdvertisingImportService(store)
    report_types = (ReportType.GENERIC, ReportType.CAMPAIGN, ReportType.AD_GROUP, ReportType.KEYWORD, ReportType.TARGETING, ReportType.SEARCH_TERM)
    for report_type in report_types:
        result = service.import_file(uploaded_by="owner-a", report_type=report_type, file_name=f"{report_type.value}.csv", content=csv_for(report_type))
        assert result.inserted_rows == 1
    summary = store.query_shared_summary()
    assert summary["row_count"] == len(report_types)
    assert summary["date_start"] == "2026-08-01"


def test_invalid_rows_block_import_and_duplicate_rows_are_overwritten_for_everyone():
    store = InMemoryImportStore(); service = AdvertisingImportService(store)
    first = service.import_file(uploaded_by="owner-a", report_type=ReportType.CAMPAIGN, file_name="a.csv", content=csv_for(ReportType.CAMPAIGN))
    assert first.inserted_rows == 1
    second = service.import_file(uploaded_by="owner-b", report_type=ReportType.CAMPAIGN, file_name="b.csv", content=csv_for(ReportType.CAMPAIGN, spend="20"))
    assert second.overwritten_rows == 1
    assert store.query_shared_summary()["row_count"] == 1
    try:
        service.import_file(uploaded_by="owner-a", report_type=ReportType.CAMPAIGN, file_name="bad.csv", content=csv_for(ReportType.CAMPAIGN, spend="-1"))
    except ValueError as exc:
        assert "第 2 行" in str(exc)
    else:
        raise AssertionError("invalid row must block the entire import")


def test_import_rejects_wrong_headers_and_templates_are_downloadable():
    service = AdvertisingImportService(InMemoryImportStore())
    try:
        service.import_file(uploaded_by="owner-a", report_type=ReportType.CAMPAIGN, file_name="bad.csv", content=b"wrong\nrow\n")
    except ValueError as exc:
        assert "表头" in str(exc)
    else:
        raise AssertionError("wrong header must be rejected")
    assert "Profile ID" in service.template(ReportType.SEARCH_TERM)


def test_xlsx_is_parsed_with_the_same_template_contract():
    workbook = Workbook(); sheet = workbook.active; headers = HEADERS[ReportType.CAMPAIGN]
    sheet.append(headers)
    sheet.append(["profile-1", "2026-08-01", "campaign-1", "Test", "SP", 100, 10, 10, 40, 2, 2])
    buffer = io.BytesIO(); workbook.save(buffer)
    result = AdvertisingImportService(InMemoryImportStore()).import_file(uploaded_by="owner-a", report_type=ReportType.CAMPAIGN, file_name="campaign.xlsx", content=buffer.getvalue())
    assert result.inserted_rows == 1


def test_generic_template_accepts_rows_without_optional_entity_dimensions():
    headers = HEADERS[ReportType.GENERIC]
    values = {"Profile ID": "profile-1", "日期": "2026-08-01", "Campaign ID": "campaign-1", "曝光": "100", "点击": "10", "花费": "10", "销售额": "40", "订单": "2", "广告销量": "2"}
    content = (",".join(headers) + "\n" + ",".join(values.get(header, "") for header in headers) + "\n").encode()
    result = AdvertisingImportService(InMemoryImportStore()).import_file(uploaded_by="owner-a", report_type=ReportType.GENERIC, file_name="generic.csv", content=content)
    assert result.inserted_rows == 1


def test_generic_template_accepts_reordered_common_english_headers():
    headers = ["Campaign ID", "Sales", "Date", "Orders", "Profile ID", "Impressions", "Spend", "Clicks", "Ad Units"]
    values = ["campaign-1", "40", "2026-08-01", "2", "profile-1", "100", "10", "10", "2"]
    result = AdvertisingImportService(InMemoryImportStore()).import_file(uploaded_by="owner-a", report_type=ReportType.GENERIC, file_name="report.csv", content=(",".join(headers) + "\n" + ",".join(values) + "\n").encode())
    assert result.inserted_rows == 1
