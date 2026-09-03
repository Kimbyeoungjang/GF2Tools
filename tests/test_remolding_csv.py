import csv
from pathlib import Path

from gfl2tool.models import Remolding
from gfl2tool.repository import Repository
from gfl2tool.services.remolding_csv import REMOLDING_CSV_FIELDS, export_remoldings_csv


def test_remolding_csv_matches_declared_interchange_stat_splitting(tmp_path):
    with Repository(tmp_path / "logger.db") as repo:
        repo.replace_remoldings([
            Remolding("1851660852722159616", 985401, "c7 92 43 d2 a6 86 01", []),
            Remolding("42", 985401, "aa bb cc 01 dd ee ff 11 22 33", []),
        ])
        out = tmp_path / "gfl2logger_remoldings_test.csv"
        assert export_remoldings_csv(repo, out) == 2

    raw = out.read_bytes()
    assert raw.startswith(b"\xef\xbb\xbf")  # utf-8-sig, same as logger
    with out.open("r", encoding="utf-8-sig", newline="") as handle:
        reader = csv.DictReader(handle)
        assert tuple(reader.fieldnames or ()) == REMOLDING_CSV_FIELDS
        rows = {row["uid"]: row for row in reader}
    assert rows["U1851660852722159616"] == {
        "uid": "U1851660852722159616",
        "stat1": "c7 92 43",
        "stat2": "d2 a6 86",
        "stat3": "",
    }
    assert rows["U42"] == {
        "uid": "U42",
        "stat1": "aa bb cc",
        "stat2": "dd ee ff",
        "stat3": "11 22 33",
    }
