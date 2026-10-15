"""Preparation guards using tiny generated fixtures, never the 3 GB market DB."""
from __future__ import annotations

from contextlib import closing
from datetime import date, timedelta
import json
from pathlib import Path
import sqlite3
from tempfile import TemporaryDirectory
import unittest

from strategy_workshop.prepare_cd80 import (
    hash_file, parse_date, prepare, validate_actions, validate_factors, validate_quotes,
)
from strategy_workshop.sqlite_source import load_snapshot


def quote(day="20240701", symbol="000001.SZ", row_id=1):
    return {"_row_id": row_id, "ts_code": symbol, "trade_date": day,
            "open": 10.0, "high": 11.0, "low": 9.0, "close": 10.5, "vol": 2.5}


def action(day="20240701", **changes):
    row = {"_row_id": 1, "ts_code": "000001.SZ", "div_proc": "Implemented",
           "ex_date": day, "pay_date": "20240703.0", "cash_div_tax": 0.25,
           "stk_div": 0.0, "stk_bo_rate": None, "stk_co_rate": None}
    row.update(changes)
    return row


class NormalizationGuardTests(unittest.TestCase):
    def test_mixed_date_representations_normalize_explicitly(self):
        for raw in ("20240701", "20240701.0", "2024-07-01", 20240701):
            with self.subTest(raw=raw):
                self.assertEqual(parse_date(raw), "2024-07-01")
        for raw in (None, True, "20240701.5", "2024/07/01", "20240230", "2.0240701e7"):
            with self.subTest(raw=raw), self.assertRaises(ValueError):
                parse_date(raw)

    def test_quote_units_leading_zero_code_and_raw_row_are_preserved(self):
        raw = quote()
        result = validate_quotes([raw], "cd80_daily")[0]
        self.assertEqual(result["symbol"], "000001.SZ")
        self.assertEqual(result["volume"], 250.0)
        self.assertEqual(result["date"], "2024-07-01")
        self.assertEqual(result["source"], raw)
        self.assertEqual(result["source_table"], "cd80_daily")

    def test_duplicate_quote_dates_rejected_after_normalization(self):
        with self.assertRaisesRegex(ValueError, "Duplicate quote date"):
            validate_quotes([quote(), quote("2024-07-01", row_id=2)], "cd80_daily")

    def test_incomplete_or_invalid_ohlcv_is_rejected(self):
        for changes in ({"open": None}, {"high": 10}, {"low": 11}, {"vol": -1}, {"close": float("nan")}):
            with self.subTest(changes=changes), self.assertRaises(ValueError):
                validate_quotes([{**quote(), **changes}], "cd80_daily")

    def test_duplicate_factor_dates_rejected_after_normalization(self):
        rows = [{"_row_id": 1, "trade_date": "20240701", "adj_factor": 1.2},
                {"_row_id": 2, "trade_date": "2024-07-01", "adj_factor": 1.3}]
        with self.assertRaisesRegex(ValueError, "Duplicate adjustment-factor date"):
            validate_factors(rows)

    def test_null_factor_stays_missing_and_invalid_factor_is_rejected(self):
        result = validate_factors([{"_row_id": 1, "trade_date": "20240701", "adj_factor": None}])
        self.assertIsNone(result["2024-07-01"]["factor"])
        for value in (0, -1, float("inf")):
            with self.subTest(value=value), self.assertRaises(ValueError):
                validate_factors([{"_row_id": 1, "trade_date": "20240701", "adj_factor": value}])

    def test_duplicate_corporate_action_ex_dates_are_rejected(self):
        with self.assertRaisesRegex(ValueError, "Conflicting/repeated"):
            validate_actions([action(), action("2024-07-01", _row_id=2)])

    def test_share_distribution_requires_a_listing_date_model(self):
        for field in ("stk_div", "stk_bo_rate", "stk_co_rate"):
            with self.subTest(field=field), self.assertRaisesRegex(ValueError, "listing-date model"):
                validate_actions([action(**{field: 0.1})])

    def test_cash_dividend_requires_valid_explicit_payment_date(self):
        for changes in ({"pay_date": None}, {"pay_date": "20240630"}, {"cash_div_tax": -0.1}):
            with self.subTest(changes=changes), self.assertRaises(ValueError):
                validate_actions([action(**changes)])
        normalized = validate_actions([action()])[0]
        self.assertEqual(normalized["cash_per_share"], 0.25)
        self.assertEqual(normalized["pay_date"], "2024-07-03")
        self.assertEqual(normalized["share_ratio"], 1.0)


class PreparationIntegrationTests(unittest.TestCase):
    def setUp(self):
        self.temp = TemporaryDirectory(prefix="cd80_preparation_test_", dir=Path(__file__).parent)
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name).absolute()
        self.source = self.root / "generated_source.sqlite3"
        self.output = self.root / "derived"
        self.days = [(date(2024, 1, 1) + timedelta(days=i)).isoformat() for i in range(260)]

        def encoded(i, offset=0):
            iso = self.days[i]
            compact = iso.replace("-", "")
            return (compact, compact + ".0", iso)[(i + offset) % 3]

        with closing(sqlite3.connect(self.source)) as connection, connection:
            for table in ("cd80_etf_fund_daily", "cd80_daily"):
                connection.execute(f"CREATE TABLE {table} (_row_id INTEGER PRIMARY KEY, ts_code TEXT, trade_date TEXT, open REAL, high REAL, low REAL, close REAL, vol REAL)")
            connection.execute("CREATE TABLE cd80_stock_market_pr_factor (_row_id INTEGER PRIMARY KEY, ts_code TEXT, trade_date TEXT, adj_factor REAL)")
            connection.execute("CREATE TABLE cd80_stock_fin_dividend (_row_id INTEGER PRIMARY KEY, ts_code TEXT, div_proc TEXT, ex_date TEXT, pay_date TEXT, cash_div_tax REAL, stk_div REAL, stk_bo_rate REAL, stk_co_rate REAL)")
            # Reverse insertion and mixed formats ensure matching/sorting is by
            # normalized dates, rather than lexical source strings or row order.
            for i in reversed(range(260)):
                for table, symbol, volume in (("cd80_etf_fund_daily", "510300.SH", 3.2), ("cd80_daily", "000001.SZ", 2.5)):
                    connection.execute(f"INSERT INTO {table} VALUES (?,?,?,?,?,?,?,?)", (i + 1, symbol, encoded(i), 10, 11, 9, 10.5, volume))
                # Factors resume after the gap. The preparer must truncate the
                # entire tail, rather than skip this one date or fill it forward.
                if i != 256:
                    connection.execute("INSERT INTO cd80_stock_market_pr_factor VALUES (?,?,?,?)", (i + 1, "000001.SZ", encoded(i, 1), 1.5))
            for row_id, i in ((1, 20), (2, 254)):
                connection.execute("INSERT INTO cd80_stock_fin_dividend VALUES (?,?,?,?,?,?,?,?,?)", (row_id, "000001.SZ", "Implemented", encoded(i), encoded(i + 1), 0.1, 0, None, None))
            # A proposal is not an executed corporate action and may have no ex date.
            connection.execute("INSERT INTO cd80_stock_fin_dividend VALUES (3, '000001.SZ', 'Proposal', NULL, NULL, 0.2, 0, NULL, NULL)")
        self.source_manifest = Path(str(self.source) + ".manifest.json")
        self.source_manifest.write_text(json.dumps({
            "source": str(self.root / "original_generated_fixture.sqlite3"),
            "snapshot": str(self.source), "bytes": self.source.stat().st_size,
            "sha256": hash_file(self.source),
        }), encoding="utf-8")

    def test_normalization_truncation_units_lineage_and_adapter_contract(self):
        source_hash = hash_file(self.source)
        result = prepare(self.source, self.output)
        self.assertEqual(hash_file(self.source), source_hash)
        self.assertFalse(result["source_modified"])
        self.assertFalse(result["formal_report_allowed"])
        etf = result["samples"]["510300.SH"]
        stock = result["samples"]["000001.SZ"]
        self.assertEqual(etf["rows"], 260)
        self.assertEqual(etf["suggested_run_start"], self.days[250])
        self.assertEqual(stock["rows"], 256)
        self.assertEqual(stock["first_missing_factor_date"], self.days[256])
        self.assertEqual(stock["available_end"], self.days[255])
        self.assertEqual(stock["excluded_quote_rows"], 4)
        self.assertEqual(stock["retained_action_rows"], 2)
        database = Path(result["normalized_database"])
        self.assertEqual(hash_file(database), result["normalized_database_sha256"])
        for symbol, item in result["samples"].items():
            mapping = json.loads(Path(item["mapping_path"]).read_text(encoding="utf-8"))
            bars, actions, instrument, provenance = load_snapshot(database, mapping, symbol, item["suggested_run_start"], item["suggested_run_end"], warmup_bars=250)
            self.assertEqual(len(bars), item["rows"])
            self.assertEqual(bars[250].date, self.days[250])
            self.assertEqual(instrument.code, symbol)
            self.assertFalse(provenance["calendar_complete"])
            self.assertFalse(provenance["market_rules_verified"])
            self.assertFalse(provenance["corporate_actions_complete"])
            if symbol == "510300.SH":
                self.assertEqual(bars[0].volume, 320)
                self.assertEqual(actions, [])
                self.assertFalse(provenance["signal_adjustment_consistent"])
                self.assertNotIn("adjustment_factor", mapping["columns"])
                self.assertEqual(mapping["sample_kind"], "unadjusted_price_diagnostic")
            else:
                self.assertEqual(bars[0].volume, 250)
                self.assertEqual(bars[0].signal_close, 15.75)
                self.assertEqual(len(actions), 2)
        with closing(sqlite3.connect(database.as_uri() + "?mode=ro", uri=True)) as connection:
            self.assertEqual(connection.execute("SELECT count(*) FROM lineage").fetchone()[0], 260 + 2 * 256 + 2)
            raw_id, raw_json, factor_id = connection.execute("SELECT source_row_id, source_values_json, factor_source_row_id FROM stock_quotes WHERE date=?", (self.days[0],)).fetchone()
            raw = json.loads(raw_json)
            self.assertEqual(raw_id, raw["_row_id"])
            self.assertEqual(raw_id, 1)
            self.assertEqual(factor_id, 1)
            self.assertEqual(raw["ts_code"], "000001.SZ")
            self.assertEqual(raw["vol"], 2.5)
            self.assertEqual(connection.execute("SELECT max(date) FROM stock_quotes").fetchone()[0], self.days[255])

    def test_existing_output_is_refused_without_modifying_artifacts(self):
        prepare(self.source, self.output)
        before = {path.name: hash_file(path) for path in self.output.iterdir() if path.is_file()}
        with self.assertRaises(FileExistsError):
            prepare(self.source, self.output)
        after = {path.name: hash_file(path) for path in self.output.iterdir() if path.is_file()}
        self.assertEqual(after, before)

    def test_source_manifest_hash_mismatch_rejects_before_output_creation(self):
        origin = json.loads(self.source_manifest.read_text(encoding="utf-8"))
        origin["sha256"] = "0" * 64
        self.source_manifest.write_text(json.dumps(origin), encoding="utf-8")
        with self.assertRaisesRegex(ValueError, "SHA-256"):
            prepare(self.source, self.output)
        self.assertFalse(self.output.exists())

    def test_pending_wal_is_refused_for_immutable_snapshot_reader(self):
        Path(str(self.source) + "-wal").write_bytes(b"Generated pending WAL marker")
        with self.assertRaisesRegex(ValueError, "pending WAL"):
            prepare(self.source, self.output)
        self.assertFalse(self.output.exists())


if __name__ == "__main__":
    unittest.main()
