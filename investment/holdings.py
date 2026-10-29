"""Explicit, preview-first imports of user supplied brokerage position exports.

Only the normalized fields below are retained. Account numbers, user names and
other export columns are deliberately discarded. This module never authenticates
to a broker, reads existing P1 data, or sends data over the network.
"""
from __future__ import annotations

import csv
import hashlib
import io
import json
import os
from pathlib import Path, PurePosixPath
import re
import stat
import uuid
from datetime import date, datetime, timedelta, timezone
from decimal import Decimal, InvalidOperation
import xml.etree.ElementTree as ET
import zipfile


SHANGHAI = timezone(timedelta(hours=8))
MAX_INPUT = 10 * 1024 * 1024
MAX_ROWS = 5000
MAX_COLUMNS = 100
MAX_XML = 2 * 1024 * 1024
MAX_UNPACKED = 20 * 1024 * 1024
NS = {"m": "http://schemas.openxmlformats.org/spreadsheetml/2006/main"}
REL_NS = "http://schemas.openxmlformats.org/package/2006/relationships"
DOC_REL = "http://schemas.openxmlformats.org/officeDocument/2006/relationships"
ALIASES = {
    "availableQuantity": {"可卖数量", "可用股份", "可用数量", "可卖股份", "availablequantity"},
    "symbol": {"证券代码", "股票代码", "代码", "symbol", "ticker"},
    "name": {"证券名称", "股票名称", "名称", "name"},
    "quantity": {"股票余额", "持仓数量", "证券数量", "持有数量", "股份余额", "数量", "quantity"},
    "costPrice": {"成本价", "持仓成本价", "参考成本价", "摊薄成本价", "costprice"},
    "reportedMarketValue": {"市值", "证券市值", "股票市值", "参考市值", "marketvalue", "reportedmarketvalue"},
    "reportedPnl": {"盈亏", "浮动盈亏", "持仓盈亏", "参考盈亏", "pnl", "reportedpnl"},
    "availableCash": {"可用资金", "可用金额", "可用现金", "availablecash"},
}
UNKNOWN = {"", "--", "—", "-", "未知", "不详", "n/a", "na", "null", "none"}
TOTAL_HEADERS = {"总资产", "账户总额", "证券账户总额", "资产总值", "证券资产"}


class HoldingsError(ValueError):
    def __init__(self, code: str, message: str):
        super().__init__(message)
        self.code = code


def _fail(code: str, message: str):
    raise HoldingsError(code, message)


def _canonical(value) -> bytes:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"), allow_nan=False).encode("utf-8")


def _now() -> datetime:
    return datetime.now(SHANGHAI)


def _safe_path(path: str | Path, *, file: bool = False, missing: bool = False) -> Path:
    p = Path(os.path.abspath(os.fspath(path)))
    if str(p).startswith("\\\\"):
        _fail("UNSAFE_PATH", "请使用本机普通文件目录，不能使用网络共享或链接。")
    for part in reversed((p, *p.parents)):
        try:
            s = part.lstat()
        except FileNotFoundError:
            if missing:
                continue
            _fail("FILE_NOT_FOUND", "所选文件或目录不存在。")
        if stat.S_ISLNK(s.st_mode) or getattr(s, "st_file_attributes", 0) & 0x400:
            _fail("UNSAFE_PATH", "路径含符号链接或目录联接，已拒绝访问。")
    if file and (not p.is_file() or p.stat().st_size > MAX_INPUT):
        _fail("INPUT_SIZE", "请选择不超过 10 MiB 的普通 CSV 或 XLSX 文件。")
    return p


def validate_as_of(value: str) -> tuple[str, str]:
    if not isinstance(value, str) or not value.strip():
        _fail("AS_OF_REQUIRED", "必须手动填写该券商持仓导出的日期或时间，不能默认今天。")
    value = value.strip()
    try:
        if re.fullmatch(r"\d{4}-\d{2}-\d{2}", value):
            d = date.fromisoformat(value)
            if d > _now().date():
                _fail("FUTURE_AS_OF", "持仓日期不能晚于北京时间今天。")
            return d.isoformat(), "date"
        if not re.fullmatch(r"\d{4}-\d{2}-\d{2}T\d{2}:\d{2}(?::\d{2}(?:\.\d{1,6})?)?(?:Z|[+-]\d{2}:\d{2})?", value):
            raise ValueError()
        moment = datetime.fromisoformat(value.replace("Z", "+00:00"))
        moment = moment.replace(tzinfo=SHANGHAI) if moment.tzinfo is None else moment.astimezone(SHANGHAI)
        if moment > _now() + timedelta(minutes=1):
            _fail("FUTURE_AS_OF", "持仓时间不能晚于当前北京时间。")
        return moment.isoformat(), "datetime"
    except ValueError as exc:
        if isinstance(exc, HoldingsError):
            raise
        _fail("AS_OF_INVALID", "持仓日期格式应为 YYYY-MM-DD，或明确的 ISO 日期时间。")


def _decimal(value: str, field: str, row: int, *, nonnegative: bool = True) -> str | None:
    value = str(value).strip()
    if value.lower() in UNKNOWN:
        return None
    value = re.sub(r"^(?:CNY|RMB|人民币|[¥￥])\s*", "", value, flags=re.I)
    value = re.sub(r"\s*元$", "", value).strip()
    if "," in value and not re.fullmatch(r"[+-]?\d{1,3}(?:,\d{3})+(?:\.\d{1,8})?", value):
        _fail("INVALID_AMOUNT", f"第 {row} 行的{field}不是有效金额。")
    value = value.replace(",", "")
    if not re.fullmatch(r"[+-]?\d+(?:\.\d{1,8})?", value):
        _fail("INVALID_AMOUNT", f"第 {row} 行的{field}不是有效金额（不接受公式、百分比或科学计数）。")
    try:
        result = Decimal(value)
    except InvalidOperation:
        _fail("INVALID_AMOUNT", f"第 {row} 行的{field}无法解析。")
    if abs(result) > Decimal("1000000000000000") or (nonnegative and result < 0):
        _fail("INVALID_AMOUNT", f"第 {row} 行的{field}超出允许范围。")
    return format(result.normalize(), "f") if result else "0"


def _quantity(value: str, row: int) -> int:
    result = _decimal(value, "持仓数量", row)
    if result is None:
        _fail("UNKNOWN_QUANTITY", f"第 {row} 行持仓数量未知，请核对后再导入。")
    d = Decimal(result)
    if d != d.to_integral_value() or d > 10**12:
        _fail("INVALID_QUANTITY", f"第 {row} 行持仓数量必须是非负整数。")
    return int(d)


def _symbol(value: str, row: int) -> str:
    value = str(value).strip().upper().removeprefix("'")
    # XLSX numeric cells can contain a trailing .0, but never infer an exchange
    # from a name or accept a conflicting explicit exchange suffix.
    if re.fullmatch(r"\d{1,6}\.0+", value):
        value = value.split(".")[0]
    match = re.fullmatch(r"(?:(SH|SZ))?(\d{1,6})(?:\.(SH|SZ))?", value)
    if not match:
        _fail("INVALID_SYMBOL", f"第 {row} 行证券代码格式不受支持，仅接受沪深股票及 ETF。")
    prefix, digits, suffix = match.groups()
    digits = digits.zfill(6)
    inferred = "SH" if digits[0] in "569" else "SZ" if digits[0] in "0123" else None
    explicit = prefix or suffix
    if (prefix and suffix and prefix != suffix) or inferred is None or (explicit and inferred != explicit):
        _fail("SYMBOL_EXCHANGE", f"第 {row} 行证券代码与市场不一致，请人工核对。")
    return f"{digits}.{explicit or inferred}"


def _csv_rows(raw: bytes) -> list[list[str]]:
    text = None
    for encoding in ("utf-8-sig", "gb18030"):
        try:
            text = raw.decode(encoding)
            break
        except UnicodeDecodeError:
            pass
    if text is None or "\x00" in text:
        _fail("CSV_ENCODING", "CSV 编码不受支持，请使用 UTF-8 或 GB18030 导出。")
    sample = text[:65536]
    try:
        dialect = csv.Sniffer().sniff(sample, delimiters=",\t;")
    except csv.Error:
        dialect = csv.excel_tab if "\t" in sample else csv.excel
    try:
        rows = []
        for row in csv.reader(io.StringIO(text, newline=""), dialect, strict=True):
            if len(row) > MAX_COLUMNS or len(rows) >= MAX_ROWS + 30:
                _fail("TABLE_SIZE", "导出文件超过 5000 条记录或 100 列，请缩小导出范围。")
            rows.append(row)
        return rows
    except csv.Error:
        _fail("CSV_INVALID", "CSV 表格结构无法读取。")


def _xml(raw: bytes):
    if len(raw) > MAX_XML:
        _fail("XLSX_UNSAFE", "XLSX XML 结构不符合安全限制。")
    try:
        text = raw.decode("utf-8-sig")
    except UnicodeDecodeError:
        _fail("XLSX_UNSAFE", "仅接受标准 UTF-8 XLSX XML。")
    if "\x00" in text or "<!DOCTYPE" in text.upper() or "<!ENTITY" in text.upper():
        _fail("XLSX_UNSAFE", "XLSX XML 结构不符合安全限制。")
    try:
        return ET.fromstring(text)
    except ET.ParseError:
        _fail("XLSX_INVALID", "XLSX XML 结构损坏。")


def _xlsx_rows(raw: bytes) -> list[list[str]]:
    try:
        archive = zipfile.ZipFile(io.BytesIO(raw))
        infos = archive.infolist()
        if len(infos) > 1000 or sum(x.file_size for x in infos) > MAX_UNPACKED:
            _fail("XLSX_SIZE", "XLSX 解压大小超过安全限制。")
        names = set()
        for item in infos:
            p = PurePosixPath(item.filename)
            mode = (item.external_attr >> 16) & 0xFFFF
            if (item.filename in names or p.is_absolute() or ".." in p.parts or "\\" in item.filename
                    or ":" in item.filename or stat.S_ISLNK(mode) or item.flag_bits & 1
                    or item.file_size > MAX_XML
                    or item.file_size > max(item.compress_size, 1) * 200):
                _fail("XLSX_UNSAFE", "XLSX 含不安全的压缩项、加密内容或超限数据。")
            names.add(item.filename)
            lowered = item.filename.lower()
            if any(s in lowered for s in ("vbaproject", "externallinks/", "embeddings/", "connections.xml", "querytables/")):
                _fail("XLSX_UNSAFE", "XLSX 含宏、外部数据或嵌入对象，请导出纯数据表。")
            if lowered.endswith(".rels"):
                rel = _xml(archive.read(item))
                if any(x.attrib.get("TargetMode", "").lower() == "external" for x in rel):
                    _fail("XLSX_EXTERNAL", "XLSX 含外部链接，请另存为无外部链接的纯数据表。")
        required = {"[Content_Types].xml", "xl/workbook.xml", "xl/_rels/workbook.xml.rels"}
        if not required <= names:
            _fail("XLSX_INVALID", "所选文件不是标准 XLSX 工作簿。")
        content = archive.read("[Content_Types].xml")
        if b"macroenabled" in content.lower():
            _fail("XLSX_UNSAFE", "不接受带宏的工作簿。")
        _xml(content)
        shared = []
        if "xl/sharedStrings.xml" in names:
            tree = _xml(archive.read("xl/sharedStrings.xml"))
            shared = ["".join(t.text or "" for t in si.findall(".//m:t", NS)) for si in tree.findall("m:si", NS)]
        workbook = _xml(archive.read("xl/workbook.xml"))
        relations = _xml(archive.read("xl/_rels/workbook.xml.rels"))
        by_id = {r.attrib.get("Id"): r.attrib.get("Target", "") for r in relations}
        tables = []
        for sheet in workbook.findall("m:sheets/m:sheet", NS):
            target = by_id.get(sheet.attrib.get(f"{{{DOC_REL}}}id"), "")
            if target.startswith("/"):
                target = target.lstrip("/")
            else:
                target = "xl/" + target
            if ".." in PurePosixPath(target).parts or target not in names or not target.startswith("xl/worksheets/"):
                _fail("XLSX_INVALID", "XLSX 工作表引用不受支持。")
            tree = _xml(archive.read(target))
            if tree.find(".//m:f", NS) is not None:
                _fail("XLSX_FORMULA", "持仓文件含公式，请从券商导出纯数值，或先粘贴为值再导入。")
            rows = []
            for row in tree.findall("m:sheetData/m:row", NS):
                if len(rows) >= MAX_ROWS + 30:
                    _fail("TABLE_SIZE", "XLSX 记录数超过安全限制。")
                values = {}
                for cell in row.findall("m:c", NS):
                    match = re.fullmatch(r"([A-Z]{1,3})([1-9]\d*)", cell.attrib.get("r", ""))
                    if not match or int(match.group(2)) > MAX_ROWS + 30:
                        _fail("TABLE_SIZE", "XLSX 单元格范围超过安全限制。")
                    col = 0
                    for c in match.group(1):
                        col = col * 26 + ord(c) - 64
                    if col > MAX_COLUMNS or col in values:
                        _fail("TABLE_SIZE", "XLSX 列范围或重复单元格不受支持。")
                    kind = cell.attrib.get("t", "n")
                    v = cell.find("m:v", NS)
                    value = v.text or "" if v is not None else ""
                    if kind == "s":
                        if not value.isdigit() or int(value) >= len(shared):
                            _fail("XLSX_INVALID", "XLSX 共享文本引用损坏。")
                        value = shared[int(value)]
                    elif kind == "inlineStr":
                        value = "".join(x.text or "" for x in cell.findall("m:is//m:t", NS))
                    elif kind not in {"n", "str"}:
                        _fail("XLSX_CELL", "XLSX 含错误、日期类型或不受支持的单元格，请导出纯数据。")
                    values[col] = value
                rows.append([values.get(i, "") for i in range(1, max(values, default=0) + 1)])
            if any(any(str(x).strip() for x in row) for row in rows):
                if sheet.attrib.get("state", "visible") != "visible":
                    _fail("XLSX_HIDDEN", "工作簿含非空隐藏工作表，请导出单张持仓数据表。")
                tables.append(rows)
        if len(tables) != 1:
            _fail("XLSX_SHEETS", "请提供只有一张非空工作表的持仓导出，避免遗漏或合并错误。")
        return tables[0]
    except HoldingsError:
        raise
    except (zipfile.BadZipFile, KeyError, OSError, RuntimeError, ValueError):
        _fail("XLSX_INVALID", "XLSX 压缩包损坏或结构不受支持。")


def _header(value):
    return re.sub(r"[\s（()）\[\]【】]", "", str(value)).lower().replace("元", "").replace("人民币", "")


def _table(rows: list[list[str]]) -> tuple[list[dict], str | None, list[str]]:
    mapping = None
    start = None
    warnings = []
    for index, row in enumerate(rows[:20]):
        result = {}
        for column, text in enumerate(row):
            h = _header(text)
            for key, aliases in ALIASES.items():
                if h in aliases:
                    if key in result:
                        _fail("DUPLICATE_COLUMN", "导出表含多个同义金额或持仓列，请保留一列后再导入。")
                    result[key] = column
        if "symbol" in result and "quantity" in result:
            mapping, start = result, index + 1
            if any(_header(x) in TOTAL_HEADERS for x in row):
                warnings.append("账户总资产列仅作来源信息，未与证券市值或现金重复相加。")
            break
    if mapping is None:
        _fail("MISSING_COLUMNS", "未找到证券代码及持仓数量列。请使用导出模板，或核对券商表头。")
    if start > 1:
        warnings.append("已跳过持仓表头前的说明；账户身份信息未保留，请核对预览数量及资金。")
    positions, seen, cash_values = [], set(), set()
    for number, row in enumerate(rows[start:], start + 1):
        if not any(str(v).strip() for v in row):
            continue
        if len(row) > MAX_COLUMNS:
            _fail("TABLE_SIZE", "表格列数超过安全限制。")
        get = lambda key: str(row[mapping[key]]).strip() if key in mapping and mapping[key] < len(row) else ""
        cash = _decimal(get("availableCash"), "可用资金", number)
        if cash is not None:
            cash_values.add(cash)
        raw_symbol, name = get("symbol"), get("name")
        if raw_symbol in {"合计", "总计", "汇总"} or (not raw_symbol and name in {"合计", "总计", "汇总"}):
            warnings.append("导出中的合计行未计为证券持仓，避免重复计算。")
            continue
        if not raw_symbol:
            if any(get(x) for x in ("quantity", "costPrice", "reportedMarketValue", "reportedPnl")):
                _fail("MISSING_SYMBOL", f"第 {number} 行含持仓金额但缺证券代码。")
            if name and cash is None:
                _fail("UNKNOWN_ROW", f"第 {number} 行无法归类为持仓或现金，请核对导出文件。")
            continue
        symbol = _symbol(raw_symbol, number)
        if symbol in seen:
            _fail("DUPLICATE_POSITION", f"第 {number} 行存在重复证券代码；不自动相加，请先核对账户及重复行。")
        seen.add(symbol)
        if len(name) > 120 or any(ord(c) < 32 for c in name):
            _fail("INVALID_NAME", f"第 {number} 行证券名称异常。")
        positions.append({
            "symbol": symbol, "name": name or symbol, "quantity": _quantity(get("quantity"), number),
            "costPrice": _decimal(get("costPrice"), "成本价", number),
            "reportedMarketValue": _decimal(get("reportedMarketValue"), "市值", number),
            "reportedPnl": _decimal(get("reportedPnl"), "盈亏", number, nonnegative=False),
        })
        if get('availableQuantity'):
            available=_quantity(get('availableQuantity'),number)
            if available>positions[-1]['quantity']:_fail('AVAILABLE_QUANTITY_INVALID','可卖数量不能超过持有数量，请核对字段含义。')
            positions[-1]['availableQuantity']=available
    if len(positions) > MAX_ROWS or not positions:
        _fail("EMPTY_POSITIONS", "没有可导入的沪深证券持仓；不会用空数据覆盖现有持仓。")
    if len(cash_values) > 1:
        _fail("CASH_CONFLICT", "可用资金列包含不同金额，可能混有多个账户；请分账户导出，不自动累加。")
    if not cash_values:
        warnings.append("可用资金未知；不会默认零，也不能计算完整证券账户仓位。")
    if any(p["costPrice"] is None for p in positions):
        warnings.append("部分持仓成本未知，相关浮动盈亏无法由成本重算。")
    if any(p["quantity"] % 100 for p in positions):
        warnings.append("持仓包含非整百数量，按导出原值保留；持仓不等于可卖数量。")
    warnings.append("券商导出市值及盈亏仅对应导出时点，不代表当前行情；账户总额不会与持仓重复相加。")
    return positions, next(iter(cash_values), None), list(dict.fromkeys(warnings))


def _broker(broker):
    if not isinstance(broker, str) or not 1 <= len(broker.strip()) <= 80 or any(ord(c) < 32 for c in broker):
        _fail("BROKER_INVALID", "来源名称需为 1—80 个可见字符，请勿填写账户号码或凭证。")
    return broker.strip()


def _make_preview(positions, cash, warnings, as_of, precision, broker, kind, source_sha):
    if as_of[:10] != _now().date().isoformat():
        warnings.insert(0, "该持仓日期不是今天；仅作历史快照，不能视为今天的真实持仓。")
    elif precision == "date":
        warnings.insert(0, "仅确认了持仓日期，未取得券商来源时间；生成报告前仍需核对是否为最新持仓。")
    preview = {
        "format": "goose-holdings-preview", "version": 1,
        "previewId": uuid.uuid4().hex, "generatedAt": _now().isoformat(),
        "asOf": as_of, "asOfPrecision": precision, "sourceSha256": source_sha,
        "source": {"kind": kind, "broker": broker,
                   "fileName": "手工录入.json" if kind == "manual" else "持仓导出." + kind},
        "positions": positions, "positionsCount": len(positions), "availableCash": cash,
        "warnings": warnings, "requiresConfirmation": True,
    }
    preview["previewSha256"] = hashlib.sha256(_canonical(preview)).hexdigest()
    return preview


def parse_file(path: str | Path, as_of: str, broker: str = "用户导出") -> dict:
    """Return a preview; never write holdings. ``as_of`` is user asserted."""
    as_of, precision = validate_as_of(as_of)
    broker = _broker(broker)
    p = _safe_path(path, file=True)
    extension = p.suffix.lower()
    if extension not in {".csv", ".xlsx"}:
        _fail("FORMAT_UNSUPPORTED", "仅支持 CSV、XLSX。截图须人工核对后填入模板，不会自动写入持仓。")
    before = p.stat()
    with p.open("rb") as stream:
        raw = stream.read(MAX_INPUT + 1)
    after = p.stat()
    if len(raw) > MAX_INPUT or before.st_size != after.st_size or before.st_mtime_ns != after.st_mtime_ns:
        _fail("SOURCE_CHANGED", "导出文件在读取期间变化或超限，请关闭编辑程序后重试。")
    rows = _csv_rows(raw) if extension == ".csv" else _xlsx_rows(raw)
    positions, cash, warnings = _table(rows)
    return _make_preview(positions, cash, warnings, as_of, precision, broker,
                         extension[1:], hashlib.sha256(raw).hexdigest())


def parse_manual(rows: list[dict], as_of: str, broker: str, available_cash=None) -> dict:
    """Preview explicitly entered holdings; no broker/API verification is implied.

    Monetary values must be decimal strings or integers, never binary floats.
    The source hash covers the explicit date as part of this new user assertion.
    """
    as_of, precision = validate_as_of(as_of)
    broker = _broker(broker)
    if not isinstance(rows, list) or not 1 <= len(rows) <= MAX_ROWS:
        _fail("MANUAL_ROWS_INVALID", "请手动填写 1—5000 行真实持仓，不会预填或推断历史数量。")
    def cell(value):
        if value is None:
            return ""
        if isinstance(value, bool) or not isinstance(value, (str, int, Decimal)):
            _fail("MANUAL_VALUE_INVALID", "手工输入金额请使用十进制文本，数量请使用整数；不接受浮点或复杂对象。")
        return str(value).strip()
    cash_input = cell(available_cash)
    table = [["证券代码", "证券名称", "持仓数量", "成本价", "市值", "盈亏", "可用资金"]]
    for row in rows:
        if not isinstance(row, dict):
            _fail("MANUAL_ROWS_INVALID", "每条手工持仓必须包含证券代码及明确数量。")
        table.append([cell(row.get(key)) for key in ("symbol", "name", "quantity", "costPrice", "reportedMarketValue", "reportedPnl")] + [cash_input])
    positions, cash, warnings = _table(table)
    warnings.insert(0, "来源为用户手工录入并核对，不是券商 API 数据；请对照券商 App 核实数量、成本、资金和日期。")
    payload = {"kind": "manual", "asOf": as_of, "broker": broker,
               "positions": positions, "availableCash": cash}
    return _make_preview(positions, cash, warnings, as_of, precision, broker, "manual",
                         hashlib.sha256(_canonical(payload)).hexdigest())


def _validate_preview(preview: dict, *, check_expiry: bool = True):
    if not isinstance(preview, dict) or preview.get("format") != "goose-holdings-preview" or preview.get("version") != 1:
        _fail("PREVIEW_INVALID", "持仓预览结构不受支持，请重新上传导出文件。")
    clean = {k: v for k, v in preview.items() if k != "previewSha256"}
    if preview.get("previewSha256") != hashlib.sha256(_canonical(clean)).hexdigest():
        _fail("PREVIEW_CHANGED", "预览内容已改变，请重新导入并人工核对。")
    try:
        created = datetime.fromisoformat(preview["generatedAt"])
        if created.tzinfo is None or (check_expiry and abs((_now() - created).total_seconds()) > 3600):
            _fail("PREVIEW_EXPIRED", "预览已超过 1 小时，请重新导入并核对。")
    except (KeyError, TypeError, ValueError):
        _fail("PREVIEW_EXPIRED", "预览时间无效或已过期。")
    validate_as_of(preview.get("asOf"))
    if not re.fullmatch(r"[0-9a-f]{64}", preview.get("sourceSha256", "")):
        _fail("PREVIEW_INVALID", "导出来源校验不完整。")
    if not isinstance(preview.get("source"), dict):
        _fail("PREVIEW_INVALID", "持仓来源信息不完整。")
    _broker(preview["source"].get("broker"))
    if preview["source"].get("kind") not in {"manual", "csv", "xlsx"}:
        _fail("PREVIEW_INVALID", "持仓来源类型不受支持。")
    positions = preview.get("positions")
    if not isinstance(positions, list) or not 1 <= len(positions) <= MAX_ROWS or len(positions) != preview.get("positionsCount"):
        _fail("PREVIEW_INVALID", "持仓行数异常。")
    symbols = set()
    for i, p in enumerate(positions, 1):
        if not isinstance(p, dict) or _symbol(p.get("symbol", ""), i) != p.get("symbol") or p["symbol"] in symbols:
            _fail("PREVIEW_INVALID", "持仓证券代码异常或重复。")
        symbols.add(p["symbol"])
        if isinstance(p.get("quantity"), bool) or not isinstance(p.get("quantity"), int) or not 0 <= p["quantity"] <= 10**12:
            _fail("PREVIEW_INVALID", "持仓数量异常。")
        if 'availableQuantity' in p and (type(p['availableQuantity']) is not int or not 0<=p['availableQuantity']<=p['quantity']):
            _fail('PREVIEW_INVALID','可卖数量异常。')
        for key in ("costPrice", "reportedMarketValue", "reportedPnl"):
            value = p.get(key)
            if value is not None and (not isinstance(value, str) or _decimal(value, key, i, nonnegative=key != "reportedPnl") != value):
                _fail("PREVIEW_INVALID", "持仓金额异常。")
    cash = preview.get("availableCash")
    if cash is not None and (not isinstance(cash, str) or _decimal(cash, "可用资金", 1) != cash):
        _fail("PREVIEW_INVALID", "可用资金异常。")


def commit_preview(preview: dict, root: str | Path, confirmed: bool) -> dict:
    from .private_store import store_lock
    with store_lock(Path(root).parent):
        return _commit_preview(preview, root, confirmed)


def _commit_preview(preview: dict, root: str | Path, confirmed: bool) -> dict:
    """After explicit human confirmation create one new immutable local snapshot.

    ``root`` is the exact private snapshot directory, not a repository root.
    Existing files are never replaced, and identical source files are rejected.
    """
    if confirmed is not True:
        _fail("CONFIRM_REQUIRED", "需人工逐项核对预览并明确确认，才能保存正式持仓快照。")
    _validate_preview(preview)
    directory = _safe_path(root, missing=True)
    directory.mkdir(parents=True, exist_ok=True)
    _safe_path(directory)
    if not directory.is_dir():
        _fail("UNSAFE_PATH", "快照目录不是普通目录。")
    # Exclusive lock protects duplicate detection across simultaneous local tabs.
    lock = directory / ".import.lock"
    try:
        fd = os.open(lock, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    except FileExistsError:
        _fail("IMPORT_BUSY", "另一持仓导入正在保存；如上次异常退出，请先人工检查锁文件。")
    try:
        os.close(fd)
        from .private_store import record_paths
        for existing in record_paths(directory):
            if existing.suffix != ".json":
                continue
            _safe_path(existing, file=True)
            try:
                old = json.loads(existing.read_text(encoding="utf-8"))
            except (OSError, ValueError):
                _fail("STORE_INVALID", "现有快照无法验证，已停止保存；请检查私有目录。")
            if not isinstance(old, dict):
                _fail("STORE_INVALID", "现有快照无法验证，已停止保存；请检查私有目录。")
            if old.get("sourceSha256") == preview["sourceSha256"] and old.get("source", {}).get("broker") == preview["source"]["broker"]:
                _fail("DUPLICATE_IMPORT", "同一导出文件已经保存，未重复导入或覆盖。")
        snapshot_id = _now().strftime("%Y%m%dT%H%M%S%f") + "-" + uuid.uuid4().hex
        snapshot = dict(preview)
        snapshot.update({"format": "goose-holdings-snapshot", "snapshotId": snapshot_id,
                         "confirmedAt": _now().isoformat(), "confirmation": "user-reviewed-preview"})
        content = _canonical(snapshot) + b"\n"
        target = directory / (snapshot_id + ".json")
        with target.open("xb") as stream:
            stream.write(content)
            stream.flush()
            os.fsync(stream.fileno())
        return {"status": "saved", "path": str(target), "sha256": hashlib.sha256(content).hexdigest(), "snapshotId": snapshot_id}
    finally:
        # Only our own just-created lock is removed; never an existing snapshot.
        lock.unlink()


def _read_snapshot(path):
    path = _safe_path(path, file=True)
    try:
        result = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        _fail("STORE_INVALID", "最新持仓快照无法读取，已停止分析。")
    if not isinstance(result, dict) or result.get("format") != "goose-holdings-snapshot" or result.get("confirmation") != "user-reviewed-preview":
        _fail("STORE_INVALID", "最新持仓没有可验证的人工确认记录。")
    preview = {k: v for k, v in result.items() if k not in {"snapshotId", "confirmedAt", "confirmation"}}
    preview["format"] = "goose-holdings-preview"
    _validate_preview(preview, check_expiry=False)
    return result


def load_latest(root: str | Path) -> dict | None:
    """Compatibility: read the latest globally confirmed snapshot."""
    directory = _safe_path(root, missing=True)
    from .private_store import record_paths
    files = record_paths(directory)
    return _read_snapshot(files[-1]) if files else None


def load_latest_by_broker(root: str | Path) -> list[dict]:
    """Read each broker's most recently confirmed immutable snapshot separately.

    All saved snapshots are verified before selecting. Files are never merged;
    old dates remain old dates and quantities are never silently aggregated.
    """
    directory = _safe_path(root, missing=True)
    from .private_store import record_paths
    latest = {}
    files = record_paths(directory)
    for path in files:
        snapshot = _read_snapshot(path)
        broker = snapshot["source"]["broker"]
        latest[broker] = snapshot
    return [latest[broker] for broker in sorted(latest)]
