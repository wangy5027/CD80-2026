"""Read-only market preflight and frozen, reproducible diagnostic input snapshots.

The current CD80 delivery cannot pass the formal-report gate. No browser or
operator-provided boolean can turn that gate on; a future verified dataset
release needs an independently implemented server-side validation contract.
"""
from __future__ import annotations

from dataclasses import asdict
from datetime import date
from hashlib import sha256
import json
from pathlib import Path
import re
import sqlite3
import threading

from strategy_workshop.catalog import get_strategy
from strategy_workshop.models import BacktestConfig, Bar, CorporateAction, Instrument, validate_bars
from strategy_workshop.prepare_cd80 import parse_date, validate_actions, validate_factors, validate_quotes
from strategy_workshop.sqlite_source import load_snapshot
from ..workbench.errors import GatewayError


RULE_SOURCE = "https://www.sse.com.cn/lawandrules/sselawsrules2025/stocks/exchange/c/c_20260424_10816482.shtml"
COMMON_GAPS = [
    "尚未核验完整交易日历；不填补缺失行情日。",
    "缺少逐日涨跌停价格、停复牌状态及历史交易规则核验。",
    "公司行动覆盖完整性尚未独立核验，个人红利税按明确的模拟假设处理。",
]
CHECK_KEYS = ("unadjusted_execution_prices", "corporate_actions_complete", "market_rules_verified",
              "calendar_complete", "signal_adjustment_consistent")

# Explicit reviewed security identities. Never infer stock/ETF class or board
# from a numeric prefix. These older ordinary SSE A-share listings predate the
# STAR board; identity and listing dates can be checked at the linked exchange
# company profiles. Historical tradability still remains UNVERIFIED.
REVIEWED = {
    "510300.SH": {"name": "华泰柏瑞沪深300ETF", "asset_type": "etf", "lot_size": 100,
                  "quantity_step": 100, "tick_size": 0.001, "settlement_days": 1,
                  "reviewSource": "https://www.sse.com.cn/disclosure/fund/announcement/c/2013-03-27/510300_2012_n.pdf"},
    "000001.SZ": {"name": "平安银行", "asset_type": "stock", "lot_size": 100,
                  "quantity_step": 100, "tick_size": 0.01, "settlement_days": 1,
                  "reviewSource": "https://bank.pingan.com/about/investor_relations.shtml"},
}
for _code, _name in (("600036", "招商银行"), ("600519", "贵州茅台"),
                     ("601318", "中国平安"), ("601398", "工商银行")):
    REVIEWED[_code + ".SH"] = {"name": _name, "asset_type": "stock", "lot_size": 100,
                                "quantity_step": 100, "tick_size": 0.01, "settlement_days": 1,
                                "reviewSource": "https://www.sse.com.cn/assortment/stock/list/info/company/index.shtml?COMPANY_CODE=" + _code}


def _digest(value):
    return sha256(json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"),
                             allow_nan=False).encode()).hexdigest()


def _instrument(symbol, item):
    fields = {key: item[key] for key in ("name", "asset_type", "lot_size", "quantity_step", "tick_size", "settlement_days")}
    return Instrument(code=symbol, **fields)


def _three_years_before(day):
    try:
        return day.replace(year=day.year - 3)
    except ValueError:
        return day.replace(year=day.year - 3, day=28)


class MarketData:
    def __init__(self, config):
        self.database = Path(config.get("STRATEGY_WORKSHOP_MARKET_DB", "data/strategy-workshop-market/derived/normalized.sqlite3")).absolute()
        self.source = Path(config.get("STRATEGY_WORKSHOP_SOURCE_DB", "data/strategy-workshop-market/cd80_source_snapshot.sqlite3")).absolute()
        self.mapping_dir = Path(config.get("STRATEGY_WORKSHOP_MAPPING_DIR", self.database.parent)).absolute()
        self.reviewed = {key: dict(value) for key, value in REVIEWED.items()}
        extra = config.get("STRATEGY_WORKSHOP_INSTRUMENTS") or {}
        if isinstance(extra, str):
            extra = json.loads(extra)
        if not isinstance(extra, dict):
            raise ValueError("STRATEGY_WORKSHOP_INSTRUMENTS must be an operator-reviewed mapping")
        for symbol, value in extra.items():
            if not re.fullmatch(r"\d{6}\.(?:SH|SZ)", symbol) or not isinstance(value, dict) or not value.get("reviewSource"):
                raise ValueError("Explicit reviewed instrument identity and reference required")
            _instrument(symbol, value)
            # The reference engine currently models these ordinary units only.
            if value["lot_size"] != 100 or value["quantity_step"] != 100 or value["settlement_days"] != 1:
                raise ValueError("This release supports reviewed ordinary A shares and domestic equity ETFs only")
            self.reviewed[symbol] = dict(value)
        self._lock = threading.RLock()
        self._cache = {}

    @staticmethod
    def _stat(path):
        try:
            stat = path.stat()
            return stat.st_size, stat.st_mtime_ns
        except FileNotFoundError:
            return None

    def _fingerprint(self, symbol):
        return tuple(self._stat(path) for path in (
            self.source, Path(str(self.source) + "-wal"), Path(str(self.source) + "-journal"),
            self.database, self.mapping_dir / (symbol + ".mapping.json")))

    def _availability(self):
        """Small schema checks only; no large scan and no reads at app creation."""
        checks = {"databaseFile": self.database.is_file(), "sourceFile": self.source.is_file(),
                  "sampleMappings": False, "sampleTables": False, "sourceSchema": False,
                  "metadataSearchSchema": False}
        try:
            checks["sampleMappings"] = all(
                json.loads((self.mapping_dir / (symbol + ".mapping.json")).read_text(encoding="utf-8")).get("table") == table
                for symbol, table in (("510300.SH", "etf_quotes"), ("000001.SZ", "stock_quotes")))
            if checks["databaseFile"]:
                connection = sqlite3.connect(self.database.as_uri() + "?mode=ro", uri=True)
                try:
                    checks["sampleTables"] = all(connection.execute(
                        f'SELECT 1 FROM "{table}" WHERE symbol=? LIMIT 1', (symbol,)).fetchone()
                        for symbol, table in (("510300.SH", "etf_quotes"), ("000001.SZ", "stock_quotes")))
                finally:
                    connection.close()
        except (ValueError, TypeError, AttributeError, OSError, KeyError, sqlite3.Error):
            pass
        if checks["sourceFile"]:
            try:
                connection = self._source_connection()
                try:
                    tables = {row[0] for row in connection.execute("SELECT name FROM sqlite_master WHERE type='table'")}
                    needed = {
                        "cd80_daily": {"_row_id", "ts_code", "trade_date", "open", "high", "low", "close", "vol"},
                        "cd80_etf_fund_daily": {"_row_id", "ts_code", "trade_date", "open", "high", "low", "close", "vol"},
                        "cd80_stock_market_pr_factor": {"_row_id", "ts_code", "trade_date", "adj_factor"},
                        "cd80_stock_fin_dividend": {"_row_id", "ts_code", "div_proc", "ex_date", "pay_date", "cash_div_tax", "stk_div", "stk_bo_rate", "stk_co_rate"},
                    }
                    checks["sourceSchema"] = all(table in tables and fields.issubset(
                        {row[1] for row in connection.execute(f'PRAGMA table_info("{table}")')}) for table, fields in needed.items())
                    checks["metadataSearchSchema"] = all(table in tables and fields.issubset(
                        {row[1] for row in connection.execute(f'PRAGMA table_info("{table}")')})
                        for table, fields in (("cd80_stock_basic", {"ts_code", "name"}), ("cd80_etf_basic", {"ts_code", "csname"})))
                finally:
                    connection.close()
            except (ValueError, OSError, sqlite3.Error):
                pass
        available = bool(checks["sourceSchema"] if checks["sourceFile"] else checks["sampleMappings"] and checks["sampleTables"])
        return available, checks

    def _source_connection(self):
        if not self.source.is_file():
            raise ValueError("Source snapshot unavailable")
        # immutable is safe only for the operator-owned stable delivery. Refuse
        # a nonempty WAL/journal instead of silently ignoring newer records.
        for suffix in ("-wal", "-journal"):
            sidecar = Path(str(self.source) + suffix)
            if sidecar.exists() and sidecar.stat().st_size:
                raise ValueError("Stable source snapshot has pending journal records")
        connection = sqlite3.connect(self.source.as_uri() + "?mode=ro&immutable=1", uri=True)
        connection.row_factory = sqlite3.Row
        connection.execute("PRAGMA query_only=ON")
        connection.execute("BEGIN")
        return connection

    def capabilities(self):
        ready, data_checks = self._availability()
        defaults = {"symbol": "510300.SH", "startDate": "", "endDate": "", "initialCash": 100000}
        asof = None
        if ready:
            try:
                _bars, _actions, asof, _provenance, _reasons, _instrument_value = self._load("510300.SH")
                defaults.update({"endDate": asof, "startDate": _three_years_before(date.fromisoformat(asof)).isoformat()})
            except (ValueError, TypeError, OSError, KeyError, sqlite3.Error):
                ready = False
        return {"available": ready, "defaults": defaults, "asof": asof,
                "defaultSymbol": "510300.SH", "defaultCash": 100000,
                "defaultYears": 3, "defaultEnd": "selected_symbol_latest_complete_ohlcv",
                "frequency": "daily", "longOnly": True, "leverage": False,
                "mode": "diagnostic", "formalEligible": False,
                "formalGateReason": "当前数据交付未通过公司行动、交易日历与历史可成交状态核验。",
                "gaps": list(COMMON_GAPS), "reviewedSymbols": len(self.reviewed),
                "searchIncludesUnsupported": data_checks["metadataSearchSchema"], "dataChecks": data_checks,
                "availabilityReason": "已找到当前诊断数据与映射。" if ready else "诊断数据文件、必要表或映射尚未就绪。"}

    def symbols(self, query=""):
        if not isinstance(query, str) or len(query) > 80:
            raise GatewayError("标的搜索内容过长。", 400, "workshop_symbol_invalid")
        query = query.strip()
        found = {symbol: {"symbol": symbol, "name": item["name"], "assetType": item["asset_type"],
                          "supported": self.source.is_file() or (self.mapping_dir / (symbol + ".mapping.json")).is_file(),
                          "reason": "已核验普通交易单位；仅开放诊断回测。" if self.source.is_file() or (self.mapping_dir / (symbol + ".mapping.json")).is_file() else "交易单位已审核，但当前未找到该标的行情映射。"}
                 for symbol, item in self.reviewed.items()
                 if not query or query.lower() in (symbol + " " + item["name"]).lower()}
        if query and self.source.is_file():
            try:
                connection = self._source_connection()
                try:
                    # A literal LIKE pattern, bound as a value. Search only small
                    # security metadata tables, never arbitrary business tables.
                    needle = "%" + query.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_") + "%"
                    for table, name, kind in (("cd80_stock_basic", "name", "stock"), ("cd80_etf_basic", "csname", "etf")):
                        for row in connection.execute(f'SELECT DISTINCT ts_code, "{name}" AS name FROM "{table}" WHERE ts_code LIKE ? ESCAPE \'\\\' OR "{name}" LIKE ? ESCAPE \'\\\' LIMIT 30', (needle, needle)):
                            symbol = row["ts_code"]
                            if not isinstance(symbol, str) or not re.fullmatch(r"\d{6}\.(?:SH|SZ)", symbol) or symbol in found:
                                continue
                            found[symbol] = {"symbol": symbol, "name": row["name"] or symbol,
                                             "assetType": kind, "supported": symbol in self.reviewed,
                                             "reason": "已核验普通交易单位；仅开放诊断回测。" if symbol in self.reviewed else "库中存在该证券；尚未完成资产类别与交易单位审核，当前不能运行。"}
                finally:
                    connection.close()
            except (ValueError, sqlite3.Error, OSError):
                # Existing reviewed symbols remain usable if optional source
                # metadata search is temporarily unavailable.
                pass
        return list(found.values())[:30]

    def _load_source(self, symbol, instrument):
        connection = self._source_connection()
        before = (self.source.stat().st_size, self.source.stat().st_mtime_ns)
        try:
            table = "cd80_daily" if instrument.asset_type == "stock" else "cd80_etf_fund_daily"
            rows = [dict(row) for row in connection.execute(f'SELECT * FROM "{table}" WHERE ts_code=?', (symbol,))]
            quotes = validate_quotes(rows, table)
            if not quotes:
                raise ValueError("No complete quotes for reviewed symbol")
            asof = quotes[-1]["date"]
            reasons = []
            factors = {}
            actions = []
            action_lineage = []
            signal_adjusted = False
            if instrument.asset_type == "stock":
                factor_rows = [dict(row) for row in connection.execute('SELECT * FROM cd80_stock_market_pr_factor WHERE ts_code=?', (symbol,))]
                factors = validate_factors(factor_rows)
                first_missing = next((row["date"] for row in quotes if not factors.get(row["date"], {}).get("factor")), None)
                if first_missing:
                    quotes = [row for row in quotes if row["date"] < first_missing]
                    reasons.append("复权因子从 " + first_missing + " 起存在缺口，实际区间截在此前，不填值、不跳过内部缺口。")
                action_rows = [dict(row) for row in connection.execute('SELECT * FROM cd80_stock_fin_dividend WHERE ts_code=? AND div_proc=?', (symbol, "Implemented"))]
                grouped = {}
                for row in action_rows:
                    day = parse_date(row["ex_date"])
                    if quotes and quotes[0]["date"] <= day <= quotes[-1]["date"]:
                        grouped.setdefault(day, []).append(row)
                for day, group in sorted(grouped.items()):
                    try:
                        # Repeated or conflicting records are not arbitrarily
                        # deduplicated. Unsupported share events also stop here.
                        parsed = validate_actions(group)
                        actions.extend(CorporateAction(item["date"], item["cash_per_share"], item["share_ratio"], item["pay_date"]) for item in parsed)
                        action_lineage.extend({"date": item["date"], "rowId": item["source"]["_row_id"]} for item in parsed)
                    except (ValueError, TypeError, KeyError):
                        quotes = [row for row in quotes if row["date"] < day]
                        actions = [action for action in actions if action.date < day]
                        action_lineage = [item for item in action_lineage if item["date"] < day]
                        reasons.append("公司行动 " + day + " 存在冲突或不支持的股本变化，实际区间截在此前，未猜测处理。")
                        break
                signal_adjusted = True
            else:
                reasons.extend(["ETF 缺完整分红和复权因子，本次只检验未经复权的价格规则。"])
                if symbol == "510300.SH":
                    reasons.append("510300 原始行情缺 2026-06-23、2026-07-15 两个对照交易日；不补值，日线窗口按现有记录计算。")
            bars = [Bar(row["date"], row["open"], row["high"], row["low"], row["close"], row["volume"],
                        factors[row["date"]]["factor"] if signal_adjusted else 1.0) for row in quotes]
            validate_bars(bars)
            provenance = {"dataset_kind": "real_sqlite", "read_only": True,
                          "unadjusted_execution_prices": True, "signal_adjustment_consistent": signal_adjusted,
                          "corporate_actions_complete": False, "market_rules_verified": False, "calendar_complete": False,
                          "source_file": str(self.source), "table": table,
                          "source_quote_row_ids": [row["source"]["_row_id"] for row in quotes],
                          "source_corporate_action_rows": action_lineage,
                          "source_volume_unit": "100-share/unit lots", "volume_multiplier": 100,
                          "instrument_review_source": self.reviewed[symbol]["reviewSource"],
                          "trading_unit_rule_source": RULE_SOURCE,
                          "formal_report_allowed": False, "known_gaps": list(COMMON_GAPS) + reasons}
            after = (self.source.stat().st_size, self.source.stat().st_mtime_ns)
            if after != before:
                raise ValueError("Source snapshot changed while reading")
            return bars, actions, asof, provenance, reasons
        finally:
            connection.close()

    def _load(self, symbol):
        fingerprint = self._fingerprint(symbol)
        with self._lock:
            cached = self._cache.get(symbol)
            if cached and cached[0] == fingerprint:
                return cached[1]
        instrument = _instrument(symbol, self.reviewed[symbol])
        if self.source.is_file():
            value = self._load_source(symbol, instrument)
        else:
            mapping_path = self.mapping_dir / (symbol + ".mapping.json")
            mapping = json.loads(mapping_path.read_text(encoding="utf-8"))
            bars, actions, _mapped_instrument, provenance = load_snapshot(self.database, mapping, symbol, "1900-01-01", "9999-12-31", 0)
            reasons = []
            if instrument.asset_type == "etf":
                reasons.append("ETF 缺完整分红和复权因子，本次只检验未经复权的价格规则。")
                if symbol == "510300.SH":
                    reasons.append("510300 原始行情缺 2026-06-23、2026-07-15 两个对照交易日；不补值，日线窗口按现有记录计算。")
            # Never accept the mapping's operator boolean as formal proof.
            provenance.update({"corporate_actions_complete": False, "market_rules_verified": False,
                               "calendar_complete": False, "formal_report_allowed": False})
            if instrument.asset_type == "etf":
                provenance["signal_adjustment_consistent"] = False
            value = bars, actions, bars[-1].date, provenance, reasons
        result = (*value, instrument)
        with self._lock:
            self._cache[symbol] = (fingerprint, result)
        return result

    def prepare(self, symbol=None, start=None, end=None, strategy_id="sma_trend", cash=100000, custom_plan=None):
        symbol = symbol or "510300.SH"
        if not isinstance(symbol, str):
            raise GatewayError("请输入股票或 ETF 代码。", 400, "workshop_symbol_invalid")
        symbol = symbol.strip().upper()
        if re.fullmatch(r"\d{6}", symbol):
            matches = [key for key in self.reviewed if key[:6] == symbol]
            symbol = matches[0] if len(matches) == 1 else symbol
        if symbol not in self.reviewed:
            raise GatewayError("该标的尚未完成资产类别及交易单位审核，请从当前支持的标的中选择。", 422, "workshop_symbol_unsupported")
        try:
            if custom_plan is None:
                strategy = get_strategy(strategy_id)
            else:
                from types import SimpleNamespace
                from .custom_rules import normalize_plan, plan_warmup, reference_targets
                custom_plan = normalize_plan(custom_plan)
                strategy = SimpleNamespace(SPEC=SimpleNamespace(warmup_bars=plan_warmup(custom_plan)),
                                           build_targets=lambda values: reference_targets(custom_plan, values))
            bars, actions, asof, provenance, source_reasons, instrument = self._load(symbol)
            requested_end = date.fromisoformat(end) if end else date.fromisoformat(asof)
            requested_start = date.fromisoformat(start) if start else _three_years_before(requested_end)
            if isinstance(cash, bool) or not isinstance(cash, (int, float)):
                raise ValueError("Initial cash must be a number")
            config = BacktestConfig(requested_start.isoformat(), requested_end.isoformat(), float(cash))
            if config.initial_cash > 1000000000:
                raise ValueError("Initial cash too large")
            relevant = [bar for bar in bars if bar.date <= config.end_date]
            if len(relevant) <= strategy.SPEC.warmup_bars:
                raise ValueError("Not enough complete bars for this strategy")
            targets = strategy.build_targets(relevant)
            ready_index = next(i for i in range(1, len(relevant)) if targets[i - 1] is not None)
            candidate_index = next((i for i, bar in enumerate(relevant) if bar.date >= config.start_date), len(relevant))
            if candidate_index >= len(relevant):
                raise ValueError("Requested interval has no runnable quotes")
            # Prefer the documented 250-bar preheat when the request begins
            # before coverage; never require 250 for a valid explicit interval.
            index = max(candidate_index, ready_index)
            if candidate_index < ready_index and len(relevant) > 250:
                index = max(index, 250)
            actual_start, actual_end = relevant[index].date, relevant[-1].date
            reasons = list(source_reasons)
            if actual_start != config.start_date:
                reasons.append("请求开始日 " + config.start_date + " 调整为 " + actual_start + "（行情覆盖、预热或交易日对齐）。")
            if actual_end != config.end_date:
                reasons.append("请求结束日 " + config.end_date + " 调整为 " + actual_end + "（行情覆盖或交易日对齐）。")
            # Custom nested/recursive indicators retain the full causal prefix;
            # a 250-bar truncation can alter an EMA or a composite indicator.
            prefix_start = max(0, index - 250) if custom_plan is None else 0
            frozen_bars = relevant[prefix_start:]
            frozen_actions = [action for action in actions if frozen_bars[0].date <= action.date <= actual_end]
            actual_config = BacktestConfig(actual_start, actual_end, config.initial_cash)
            frozen_provenance = json.loads(json.dumps(provenance, ensure_ascii=False, allow_nan=False))
            if "source_quote_row_ids" in frozen_provenance:
                frozen_provenance["source_quote_row_ids"] = frozen_provenance["source_quote_row_ids"][prefix_start:len(relevant)]
            if "source_corporate_action_rows" in frozen_provenance:
                frozen_provenance["source_corporate_action_rows"] = [item for item in frozen_provenance["source_corporate_action_rows"]
                                                                    if frozen_bars[0].date <= item["date"] <= actual_end]
            snapshots = {"bars": [asdict(bar) for bar in frozen_bars], "actions": [asdict(action) for action in frozen_actions],
                         "instrument": asdict(instrument), "config": asdict(actual_config), "provenance": frozen_provenance}
            checks = {key: provenance.get(key) is True for key in CHECK_KEYS}
            checks.update({"dataset_release_certified": False})
            gate = {"eligible": False, "formalEligible": False, "mode": "diagnostic", "checks": checks,
                    "reasons": list(COMMON_GAPS) + source_reasons,
                    "message": "本次仅为真实行情诊断回测，不能作为完整金融收益报告。"}
            requested = {"symbol": symbol, "startDate": config.start_date, "endDate": config.end_date,
                         "initialCash": config.initial_cash}
            actual = {"startDate": actual_start, "endDate": actual_end, "bars": len(relevant) - index,
                      "warmupBars": index - prefix_start}
            coverage_required = actual_start != config.start_date or actual_end != config.end_date or bool(source_reasons)
            return {"requested": requested, "actual": actual, "instrument": asdict(instrument), "asof": asof,
                    "coverage": {"required": coverage_required, "reasons": reasons,
                                 "digest": _digest({"requested": requested, "actual": actual, "reasons": reasons,
                                                     "snapshot": snapshots})},
                    "gate": gate, "defaults": asdict(actual_config), "snapshot": snapshots}
        except GatewayError:
            raise
        except (ValueError, TypeError, KeyError, OSError, sqlite3.Error, StopIteration):
            raise GatewayError("该标的或请求区间缺少可运行的完整行情及预热数据，请调整标的或日期后重试。", 422, "workshop_market_unavailable") from None
