import pytest

from contractguard.data_access.reader import SourceFileError, read_csv_file


def write(tmp_path, text, name="orders.csv"):
    path = tmp_path / name
    path.write_bytes(text.encode("utf-8") if isinstance(text, str) else text)
    return path


def test_happy_path(tmp_path):
    result = read_csv_file(write(tmp_path, "order_id,status\nORD-1,placed\nORD-2,shipped\n"))
    assert result.header == ["order_id", "status"]
    assert result.rows == [{"order_id": "ORD-1", "status": "placed"},
                           {"order_id": "ORD-2", "status": "shipped"}]
    assert result.row_numbers == [2, 3]
    assert result.malformed == [] and result.total_records == 2


def test_utf8_bom_is_removed_from_header(tmp_path):
    result = read_csv_file(write(tmp_path, b"\xef\xbb\xbforder_id,status\r\nORD-1,placed\r\n"))
    assert result.header == ["order_id", "status"]
    assert result.rows[0]["order_id"] == "ORD-1"


def test_empty_file(tmp_path):
    result = read_csv_file(write(tmp_path, b""))
    assert result.header == [] and result.rows == [] and result.total_records == 0


def test_whitespace_only_file_counts_as_empty(tmp_path):
    result = read_csv_file(write(tmp_path, "  \n\n  \n"))
    assert result.header == [] and result.total_records == 0


def test_header_only_file_has_no_rows(tmp_path):
    result = read_csv_file(write(tmp_path, "order_id,status\n"))
    assert result.header == ["order_id", "status"] and result.total_records == 0


def test_row_with_too_few_fields_is_malformed(tmp_path):
    result = read_csv_file(write(tmp_path, "order_id,status\nORD-1,placed\nORD-2\n"))
    assert len(result.rows) == 1 and len(result.malformed) == 1
    bad = result.malformed[0]
    assert bad.line_number == 3 and bad.raw == ["ORD-2"]
    assert "expected 2 fields, got 1" in bad.reason
    assert result.total_records == 2


def test_row_with_too_many_fields_is_malformed(tmp_path):
    result = read_csv_file(write(tmp_path, "order_id,status\nORD-1,placed,extra\n"))
    assert result.rows == [] and "got 3" in result.malformed[0].reason


def test_blank_lines_are_skipped_and_counted_with_true_line_numbers(tmp_path):
    result = read_csv_file(write(tmp_path, "order_id,status\nORD-1,placed\n\nORD-2,shipped\n"))
    assert result.row_numbers == [2, 4]
    assert result.blank_lines_skipped == 1


def test_quoted_comma_stays_in_one_field(tmp_path):
    result = read_csv_file(write(tmp_path, 'order_id,coupon_code\nORD-1,"SAVE,10"\n'))
    assert result.rows[0]["coupon_code"] == "SAVE,10"


def test_bad_quoting_is_malformed_and_reading_continues(tmp_path):
    result = read_csv_file(write(tmp_path, 'order_id,status\nORD-1,"abc"def\nORD-2,placed\n'))
    assert len(result.malformed) == 1 and result.malformed[0].line_number == 2
    assert result.rows[0]["order_id"] == "ORD-2" and result.row_numbers == [3]


def test_missing_file_raises(tmp_path):
    with pytest.raises(SourceFileError, match="not found"):
        read_csv_file(tmp_path / "nope.csv")


def test_non_utf8_file_raises(tmp_path):
    with pytest.raises(SourceFileError, match="UTF-8"):
        read_csv_file(write(tmp_path, b"order_id\n\xff\xfe\n"))


def test_duplicate_header_names_raise(tmp_path):
    with pytest.raises(SourceFileError, match="duplicate column"):
        read_csv_file(write(tmp_path, "order_id,order_id\nORD-1,ORD-1\n"))


def test_empty_header_name_raises(tmp_path):
    with pytest.raises(SourceFileError, match="empty column name"):
        read_csv_file(write(tmp_path, "order_id,\nORD-1,x\n"))


def test_sha256_is_stable_and_changes_with_content(tmp_path):
    first = read_csv_file(write(tmp_path, "a,b\n1,2\n", "one.csv"))
    same = read_csv_file(write(tmp_path, "a,b\n1,2\n", "two.csv"))
    other = read_csv_file(write(tmp_path, "a,b\n1,3\n", "three.csv"))
    assert first.sha256 == same.sha256 != other.sha256
