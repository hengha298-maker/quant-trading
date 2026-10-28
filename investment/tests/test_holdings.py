"""Fictional fixtures only. These tests never read actual wealth data."""
import copy
import hashlib
import io
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch
from datetime import datetime, timedelta
from xml.sax.saxutils import escape
import zipfile

from investment import holdings as h


HEADER = "证券代码,证券名称,持仓数量,成本价,市值,盈亏,可用资金\n"
FAKE = HEADER + "600001.SH,虚构证券甲,200,12.3456,2500.00,30.88,1000.01\n515001.SH,虚构基金乙,105,未知,未知,--,1000.01\n"


def xlsx(rows, *, formula=False, external=False, extra_sheet=False, hidden=False):
    def sheet(data):
        chunks = []
        for n, row in enumerate(data, 1):
            cells = []
            for i, text in enumerate(row):
                col = chr(65 + i)
                extra = "<f>1+1</f>" if formula and n == 2 and i == 2 else ""
                cells.append(f'<c r="{col}{n}" t="inlineStr">{extra}<is><t>{escape(str(text))}</t></is></c>')
            chunks.append(f'<row r="{n}">' + "".join(cells) + "</row>")
        return '<worksheet xmlns="http://schemas.openxmlformats.org/spreadsheetml/2006/main"><sheetData>' + "".join(chunks) + '</sheetData></worksheet>'
    out = io.BytesIO()
    with zipfile.ZipFile(out, "w", zipfile.ZIP_DEFLATED) as z:
        z.writestr('[Content_Types].xml', '<Types xmlns="http://schemas.openxmlformats.org/package/2006/content-types"/>')
        s2 = '<sheet name="Other" sheetId="2" r:id="rId2"/>' if extra_sheet else ''
        state = ' state="hidden"' if hidden else ''
        z.writestr('xl/workbook.xml', '<workbook xmlns="http://schemas.openxmlformats.org/spreadsheetml/2006/main" xmlns:r="http://schemas.openxmlformats.org/officeDocument/2006/relationships"><sheets><sheet name="Export" sheetId="1" r:id="rId1"' + state + '/>' + s2 + '</sheets></workbook>')
        rel2 = '<Relationship Id="rId2" Target="worksheets/sheet2.xml"/>' if extra_sheet else ''
        ext = '<Relationship Id="external" Target="https://example.invalid/" TargetMode="External"/>' if external else ''
        z.writestr('xl/_rels/workbook.xml.rels', '<Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships"><Relationship Id="rId1" Target="worksheets/sheet1.xml"/>' + rel2 + ext + '</Relationships>')
        z.writestr('xl/worksheets/sheet1.xml', sheet(rows))
        if extra_sheet:
            z.writestr('xl/worksheets/sheet2.xml', sheet(rows))
    return out.getvalue()


class HoldingsImportTests(unittest.TestCase):
    def setUp(self):
        test_root = Path(__file__).resolve().parents[1] / "work" / "test-holdings"
        test_root.mkdir(parents=True, exist_ok=True)
        self.temp = tempfile.TemporaryDirectory(prefix="fictional-", dir=test_root)
        self.root = Path(self.temp.name)

    def tearDown(self):
        self.temp.cleanup()

    def parse(self, content=FAKE, ext="csv", as_of="2020-01-02"):
        p = self.root / ("fictional." + ext)
        p.write_bytes(content if isinstance(content, bytes) else content.encode("utf-8-sig"))
        return h.parse_file(p, as_of, "虚构测试券商")

    def assert_code(self, code, callable_, *args, **kwargs):
        with self.assertRaises(h.HoldingsError) as got:
            callable_(*args, **kwargs)
        self.assertEqual(code, got.exception.code)

    def test_decimal_unknown_and_cash_once(self):
        result = self.parse()
        self.assertEqual("12.3456", result["positions"][0]["costPrice"])
        self.assertEqual("1000.01", result["availableCash"])
        self.assertEqual(105, result["positions"][1]["quantity"])
        self.assertIsNone(result["positions"][1]["costPrice"])
        self.assertIsNone(result["positions"][1]["reportedMarketValue"])
        self.assertTrue(any("历史快照" in w for w in result["warnings"]))
        self.assertTrue(any("非整百" in w for w in result["warnings"]))
        self.assertFalse((self.root / "store").exists())

    def test_blank_template_does_not_replace_holdings(self):
        self.assert_code("EMPTY_POSITIONS", self.parse, HEADER)

    def test_explicit_date_required(self):
        self.assert_code("AS_OF_REQUIRED", self.parse, as_of="")
        self.assert_code("AS_OF_INVALID", self.parse, as_of="2020/01/02")
        self.assert_code("FUTURE_AS_OF", self.parse, as_of="2099-01-01")

    def test_datetime_timezone_and_date_precision(self):
        result = self.parse(as_of="2020-01-02T07:30:00Z")
        self.assertEqual("2020-01-02T15:30:00+08:00", result["asOf"])
        self.assertEqual("datetime", result["asOfPrecision"])
        today = self.parse(as_of=h._now().date().isoformat())
        self.assertTrue(any("未取得券商来源时间" in x for x in today["warnings"]))

    def test_csv_gb18030_and_tab(self):
        result = self.parse(FAKE.replace(",", "\t").encode("gb18030"))
        self.assertEqual(2, result["positionsCount"])

    def test_numeric_excel_symbols_keep_leading_zero(self):
        result = self.parse("股票代码,名称,数量\n1.0,虚构,200\n")
        self.assertEqual("000001.SZ", result["positions"][0]["symbol"])

    def test_conflicting_market_rejected(self):
        self.assert_code("SYMBOL_EXCHANGE", self.parse, FAKE.replace("600001.SH", "600001.SZ"))

    def test_symbol_formula_not_evaluated(self):
        self.assert_code("INVALID_SYMBOL", self.parse, FAKE.replace("600001.SH", "=600001"))

    def test_duplicate_symbol_never_summed(self):
        self.assert_code("DUPLICATE_POSITION", self.parse, FAKE + "600001,重复,200,12,2500,0,1000.01\n")

    def test_unknown_quantity_never_zero(self):
        self.assert_code("UNKNOWN_QUANTITY", self.parse, FAKE.replace(",200,", ",--,"))

    def test_negative_and_fractional_quantities_rejected(self):
        self.assert_code("INVALID_AMOUNT", self.parse, FAKE.replace(",200,", ",-200,"))
        self.assert_code("INVALID_QUANTITY", self.parse, FAKE.replace(",200,", ",1.5,"))

    def test_money_scientific_and_invalid_grouping_rejected(self):
        self.assert_code("INVALID_AMOUNT", self.parse, FAKE.replace("12.3456", "1e2"))
        self.assert_code("INVALID_AMOUNT", self.parse, FAKE.replace("12.3456", '"1,23"'))

    def test_currency_grouping_supported(self):
        result = self.parse(FAKE.replace("12.3456", '"￥1,234.5600元"'))
        self.assertEqual("1234.56", result["positions"][0]["costPrice"])

    def test_cash_conflict_not_aggregated(self):
        self.assert_code("CASH_CONFLICT", self.parse, FAKE.replace("1000.01\n515", "2000.01\n515"))

    def test_cash_unknown_is_null(self):
        result = self.parse(FAKE.replace("1000.01", "未知"))
        self.assertIsNone(result["availableCash"])

    def test_identity_fields_and_filename_not_retained(self):
        result = self.parse(FAKE.replace("证券代码", "资金账号,证券代码").replace("600001.SH", "SECRET-ACCOUNT,600001.SH").replace("515001.SH", "SECRET-ACCOUNT,515001.SH"))
        self.assertNotIn("SECRET-ACCOUNT", json.dumps(result))
        self.assertEqual("持仓导出.csv", result["source"]["fileName"])

    def test_totals_are_not_positions(self):
        result = self.parse(FAKE + "合计,,305,,0,0,1000.01\n")
        self.assertEqual(2, len(result["positions"]))

    def test_header_collision_rejected(self):
        self.assert_code("DUPLICATE_COLUMN", self.parse, FAKE.replace("成本价", "成本价,持仓成本价"))

    def test_xlsx_actual_parse(self):
        rows = [row.split(",") for row in FAKE.splitlines()]
        result = self.parse(xlsx(rows), "xlsx")
        self.assertEqual("600001.SH", result["positions"][0]["symbol"])
        self.assertEqual("1000.01", result["availableCash"])

    def test_xlsx_formula_rejected(self):
        self.assert_code("XLSX_FORMULA", self.parse, xlsx([r.split(",") for r in FAKE.splitlines()], formula=True), "xlsx")

    def test_xlsx_external_link_rejected(self):
        self.assert_code("XLSX_EXTERNAL", self.parse, xlsx([r.split(",") for r in FAKE.splitlines()], external=True), "xlsx")

    def test_xlsx_multiple_nonempty_sheets_rejected(self):
        self.assert_code("XLSX_SHEETS", self.parse, xlsx([r.split(",") for r in FAKE.splitlines()], extra_sheet=True), "xlsx")

    def test_xlsx_hidden_data_rejected(self):
        self.assert_code("XLSX_HIDDEN", self.parse, xlsx([r.split(",") for r in FAKE.splitlines()], hidden=True), "xlsx")

    def test_xlsx_traversal_rejected(self):
        raw = io.BytesIO()
        with zipfile.ZipFile(raw, "w") as z:
            z.writestr("../outside.xml", "bad")
        self.assert_code("XLSX_UNSAFE", self.parse, raw.getvalue(), "xlsx")

    def test_xlsx_compression_bomb_rejected(self):
        raw = io.BytesIO()
        with zipfile.ZipFile(raw, "w", zipfile.ZIP_DEFLATED) as z:
            z.writestr("huge.xml", b"x" * 100000)
        self.assert_code("XLSX_UNSAFE", self.parse, raw.getvalue(), "xlsx")

    def test_xml_entity_rejected(self):
        self.assert_code("XLSX_UNSAFE", h._xml, b'<!DOCTYPE z [<!ENTITY x "bad">]><z/>')

    def test_xml_utf16_entity_cannot_bypass_detection(self):
        self.assert_code("XLSX_UNSAFE", h._xml, '<!DOCTYPE z [<!ENTITY x "bad">]><z/>'.encode("utf-16"))

    def test_malformed_csv_quoting_rejected(self):
        self.assert_code("CSV_INVALID", self.parse, HEADER + '"600001.SH,unclosed')

    def test_unsupported_screenshot_never_commits(self):
        self.assert_code("FORMAT_UNSUPPORTED", self.parse, "fictional-image", "png")

    def test_preview_confirmation_required_before_mkdir(self):
        preview = self.parse()
        store = self.root / "store"
        self.assert_code("CONFIRM_REQUIRED", h.commit_preview, preview, store, False)
        self.assertFalse(store.exists())

    def test_snapshot_immutable_deduplicated_and_latest(self):
        preview = self.parse()
        store = self.root / "store"
        result = h.commit_preview(preview, store, True)
        path = Path(result["path"])
        before = path.read_bytes()
        self.assertEqual(hashlib.sha256(before).hexdigest(), result["sha256"])
        self.assertEqual("user-reviewed-preview", h.load_latest(store)["confirmation"])
        self.assert_code("DUPLICATE_IMPORT", h.commit_preview, self.parse(), store, True)
        self.assertEqual(before, path.read_bytes())
        self.assertEqual(1, len(list(store.glob("*.json"))))
        self.assertFalse((store / ".import.lock").exists())

    def test_altered_preview_rejected(self):
        preview = self.parse()
        preview["positions"][0]["quantity"] = 999
        self.assert_code("PREVIEW_CHANGED", h.commit_preview, preview, self.root / "store", True)

    def test_expired_preview_rejected(self):
        preview = self.parse()
        with patch.object(h, "_now", return_value=h._now() + timedelta(hours=2)):
            self.assert_code("PREVIEW_EXPIRED", h.commit_preview, preview, self.root / "store", True)

    def test_existing_broken_store_blocks_write(self):
        store = self.root / "store"
        store.mkdir()
        (store / "existing.json").write_text("broken", encoding="utf-8")
        self.assert_code("STORE_INVALID", h.commit_preview, self.parse(), store, True)
        self.assertEqual(1, len(list(store.glob("*.json"))))

    def test_concurrent_lock_never_deleted(self):
        store = self.root / "store"
        store.mkdir()
        lock = store / ".import.lock"
        lock.write_text("owned by other import")
        self.assert_code("IMPORT_BUSY", h.commit_preview, self.parse(), store, True)
        self.assertEqual("owned by other import", lock.read_text())

    def test_existing_snapshot_edit_detected(self):
        store = self.root / "store"
        result = h.commit_preview(self.parse(), store, True)
        path = Path(result["path"])
        data = json.loads(path.read_text(encoding="utf-8"))
        data["availableCash"] = "1000000"
        path.write_text(json.dumps(data), encoding="utf-8")
        self.assert_code("PREVIEW_CHANGED", h.load_latest, store)

    def test_no_latest_does_not_create_directory(self):
        store = self.root / "missing"
        self.assertIsNone(h.load_latest(store))
        self.assertFalse(store.exists())

    def test_manual_preview_preserves_unknowns_and_is_not_broker_api(self):
        result = h.parse_manual([{"symbol": "600001.SH", "name": "虚构证券甲", "quantity": "123",
                                  "costPrice": "", "reportedMarketValue": None}], "2020-01-02", "虚构券商甲")
        self.assertEqual("manual", result["source"]["kind"])
        self.assertEqual(123, result["positions"][0]["quantity"])
        self.assertIsNone(result["positions"][0]["costPrice"])
        self.assertIsNone(result["availableCash"])
        self.assertTrue(any("不是券商 API" in w for w in result["warnings"]))
        self.assertTrue(result["requiresConfirmation"])

    def test_manual_requires_explicit_quantity_and_decimal_money(self):
        rows = [{"symbol": "600001.SH", "name": "虚构证券", "quantity": None}]
        self.assert_code("UNKNOWN_QUANTITY", h.parse_manual, rows, "2020-01-02", "虚构券商")
        rows[0]["quantity"] = 100
        rows[0]["costPrice"] = 12.34
        self.assert_code("MANUAL_VALUE_INVALID", h.parse_manual, rows, "2020-01-02", "虚构券商")
        rows[0]["costPrice"] = "12.34"
        rows[0]["quantity"] = True
        self.assert_code("MANUAL_VALUE_INVALID", h.parse_manual, rows, "2020-01-02", "虚构券商")

    def test_manual_cash_and_snapshot_confirm_flow(self):
        rows = [{"symbol": "600001.SH", "name": "虚构证券", "quantity": "100", "costPrice": "12.34"}]
        preview = h.parse_manual(rows, "2020-01-02", "虚构券商", available_cash="1234.56")
        store = self.root / "manual-store"
        self.assert_code("CONFIRM_REQUIRED", h.commit_preview, preview, store, False)
        h.commit_preview(preview, store, True)
        saved = h.load_latest(store)
        self.assertEqual("manual", saved["source"]["kind"])
        self.assertEqual("1234.56", saved["availableCash"])
        self.assertEqual("12.34", saved["positions"][0]["costPrice"])
        self.assert_code("DUPLICATE_IMPORT", h.commit_preview, h.parse_manual(rows, "2020-01-02", "虚构券商", "1234.56"), store, True)

    def test_manual_new_date_is_new_explicit_user_assertion(self):
        rows = [{"symbol": "600001.SH", "name": "虚构证券", "quantity": "100"}]
        a = h.parse_manual(rows, "2020-01-02", "虚构券商")
        b = h.parse_manual(rows, "2020-01-03", "虚构券商")
        self.assertNotEqual(a["sourceSha256"], b["sourceSha256"])
        store = self.root / "manual-store"
        h.commit_preview(a, store, True)
        h.commit_preview(b, store, True)
        self.assertEqual(2, len(list(store.glob("*.json"))))

    def test_two_brokers_keep_separate_snapshots_and_scope_file_dedup(self):
        p = self.root / "fictional.csv"
        p.write_text(FAKE, encoding="utf-8")
        a = h.parse_file(p, "2020-01-02", "虚构券商甲")
        b = h.parse_file(p, "2020-01-02", "虚构券商乙")
        self.assertEqual(a["sourceSha256"], b["sourceSha256"])
        store = self.root / "two-brokers"
        h.commit_preview(a, store, True)
        h.commit_preview(b, store, True)
        accounts = h.load_latest_by_broker(store)
        self.assertEqual(2, len(accounts))
        self.assertEqual({"虚构券商甲", "虚构券商乙"}, {x["source"]["broker"] for x in accounts})
        self.assertEqual([2, 2], [len(x["positions"]) for x in accounts])
        self.assert_code("DUPLICATE_IMPORT", h.commit_preview, h.parse_file(p, "2020-01-02", "虚构券商甲"), store, True)
        self.assertEqual(2, len(list(store.glob("*.json"))))

    def test_broker_latest_selection_never_drops_other_broker(self):
        store = self.root / "broker-updates"
        a = [{"symbol": "600001.SH", "name": "虚构证券甲", "quantity": "100"}]
        b = [{"symbol": "515001.SH", "name": "虚构基金乙", "quantity": "200"}]
        h.commit_preview(h.parse_manual(a, "2020-01-02", "虚构券商甲"), store, True)
        h.commit_preview(h.parse_manual(b, "2020-01-02", "虚构券商乙"), store, True)
        a[0]["quantity"] = "300"
        h.commit_preview(h.parse_manual(a, "2020-01-03", "虚构券商甲"), store, True)
        accounts = {x["source"]["broker"]: x for x in h.load_latest_by_broker(store)}
        self.assertEqual(300, accounts["虚构券商甲"]["positions"][0]["quantity"])
        self.assertEqual(200, accounts["虚构券商乙"]["positions"][0]["quantity"])
        self.assertEqual(3, len(list(store.glob("*.json"))))

    def test_by_broker_empty_store_does_not_create_files(self):
        store = self.root / "missing-accounts"
        self.assertEqual([], h.load_latest_by_broker(store))
        self.assertFalse(store.exists())


if __name__ == "__main__":
    unittest.main()
