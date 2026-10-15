"""Market-gate, snapshot and bounded matching tests using temporary data only."""
from __future__ import annotations

from dataclasses import asdict
from datetime import date, timedelta
import json
from pathlib import Path
import sqlite3
from tempfile import TemporaryDirectory
import threading
import time
from types import SimpleNamespace
import unittest
from unittest.mock import patch

from backend.app.modules.strategy_workshop.catalog_content import public_catalog
from backend.app.modules.strategy_workshop.market import MarketData, _three_years_before
from backend.app.modules.strategy_workshop.matching import MatchingService
from backend.app.modules.workbench.errors import GatewayError


def days(count):
    return [(date(2024, 1, 1) + timedelta(days=i)).isoformat() for i in range(count)]


def normalized(folder, count=320):
    database = folder / "normalized.sqlite3"
    connection = sqlite3.connect(database)
    for table, symbol in (("etf_quotes", "510300.SH"), ("stock_quotes", "000001.SZ")):
        connection.execute(f'CREATE TABLE {table}(symbol,date,open,high,low,close,volume,adjustment_factor)')
        connection.executemany(f'INSERT INTO {table} VALUES(?,?,?,?,?,?,?,?)',
                               [(symbol, day, 10, 11, 9, 10 + (i % 2) * .1, 10000, 1) for i, day in enumerate(days(count))])
        mapping = {"table": table, "date_format": "%Y-%m-%d",
                   "columns": {key: key for key in ("symbol", "date", "open", "high", "low", "close", "volume", "adjustment_factor")},
                   "instrument": {"name": symbol, "asset_type": "stock" if table == "stock_quotes" else "etf",
                                  "lot_size": 100, "quantity_step": 100, "tick_size": .01 if table == "stock_quotes" else .001,
                                  "settlement_days": 1},
                   # A hostile mapping boolean must not release formal reports.
                   "verified_capabilities": {key: True for key in ("unadjusted_execution_prices", "corporate_actions_complete", "market_rules_verified", "calendar_complete", "signal_adjustment_consistent")}}
        (folder / (symbol + ".mapping.json")).write_text(json.dumps(mapping), encoding="utf-8")
    connection.commit()
    connection.close()
    return {"STRATEGY_WORKSHOP_MARKET_DB": str(database), "STRATEGY_WORKSHOP_MAPPING_DIR": str(folder),
            "STRATEGY_WORKSHOP_SOURCE_DB": str(folder / "absent.sqlite3")}


def source(folder, count=100, missing_factor=90, conflict=None):
    database = folder / "source.sqlite3"
    connection = sqlite3.connect(database)
    for table in ("cd80_daily", "cd80_etf_fund_daily"):
        connection.execute(f'CREATE TABLE {table}(_row_id INTEGER,ts_code TEXT,trade_date TEXT,open,high,low,close,vol)')
    connection.execute('CREATE TABLE cd80_stock_market_pr_factor(_row_id,ts_code,trade_date,adj_factor)')
    connection.execute('CREATE TABLE cd80_stock_fin_dividend(_row_id,ts_code,div_proc,ex_date,pay_date,cash_div_tax,stk_div,stk_bo_rate,stk_co_rate)')
    connection.execute('CREATE TABLE cd80_stock_basic(ts_code,name)')
    connection.execute('CREATE TABLE cd80_etf_basic(ts_code,csname)')
    connection.executemany('INSERT INTO cd80_stock_basic VALUES(?,?)', [("000001.SZ", "平安银行"), ("688001.SH", "未审核证券")])
    connection.execute('INSERT INTO cd80_etf_basic VALUES(?,?)', ("510300.SH", "沪深300ETF"))
    for table, symbol in (("cd80_daily", "000001.SZ"), ("cd80_etf_fund_daily", "510300.SH")):
        connection.executemany(f'INSERT INTO {table} VALUES(?,?,?,?,?,?,?,?)',
                               [(i + 1, symbol, day.replace("-", ""), 10, 11, 9, 10 + (i % 2) * .1, 100) for i, day in enumerate(days(count))])
    connection.executemany('INSERT INTO cd80_stock_market_pr_factor VALUES(?,?,?,?)',
                           [(i + 1, "000001.SZ", day.replace("-", ""), 2) for i, day in enumerate(days(count)) if i != missing_factor])
    if conflict is not None:
        for i in range(2):
            connection.execute('INSERT INTO cd80_stock_fin_dividend VALUES(?,?,?,?,?,?,?,?,?)',
                               (i + 1, "000001.SZ", "Implemented", days(count)[conflict], days(count)[conflict], .2 + i * .1, 0, 0, 0))
    connection.commit()
    connection.close()
    return {"STRATEGY_WORKSHOP_SOURCE_DB": str(database), "STRATEGY_WORKSHOP_MARKET_DB": str(folder / "missing.sqlite3"),
            "STRATEGY_WORKSHOP_MAPPING_DIR": str(folder)}


class MarketTests(unittest.TestCase):
    def test_leap_day_three_natural_years(self):
        self.assertEqual(_three_years_before(date(2024, 2, 29)), date(2021, 2, 28))

    def test_capabilities_are_lazy_and_validate_schema(self):
        with TemporaryDirectory() as name:
            folder = Path(name)
            config = normalized(folder)
            market = MarketData(config)
            self.assertEqual(market._cache, {})
            caps = market.capabilities()
            self.assertTrue(caps["available"])
            self.assertEqual(caps["defaults"]["endDate"], days(320)[-1])
            self.assertFalse(caps["formalEligible"])
            sqlite3.connect(folder / "bad.sqlite3").close()
            config["STRATEGY_WORKSHOP_SOURCE_DB"] = str(folder / "bad.sqlite3")
            self.assertFalse(MarketData(config).capabilities()["available"])

    def test_coverage_freezes_inputs_and_rejects_operator_gate_bypass(self):
        with TemporaryDirectory() as name:
            market = MarketData(normalized(Path(name)))
            result = market.prepare(cash=12000.25)
            self.assertTrue(result["coverage"]["required"])
            self.assertEqual(result["actual"]["warmupBars"], 250)
            self.assertEqual(result["actual"]["startDate"], days(320)[250])
            self.assertEqual(result["snapshot"]["config"]["initial_cash"], 12000.25)
            self.assertFalse(result["gate"]["eligible"])
            self.assertFalse(result["snapshot"]["provenance"]["corporate_actions_complete"])
            self.assertTrue(any("2026-06-23" in item for item in result["gate"]["reasons"]))
            json.dumps(result, allow_nan=False)

    def test_explicit_start_can_use_under_250_valid_preheat(self):
        with TemporaryDirectory() as name:
            market = MarketData(normalized(Path(name), 100))
            result = market.prepare(start=days(100)[70], end=days(100)[90])
            self.assertEqual(result["actual"]["startDate"], days(100)[70])
            self.assertEqual(result["actual"]["warmupBars"], 70)
            self.assertEqual(result["snapshot"]["bars"][-1]["date"], days(100)[90])

    def test_source_factor_gap_is_not_filled_or_skipped(self):
        with TemporaryDirectory() as name:
            folder = Path(name)
            market = MarketData(source(folder, 100, 90))
            result = market.prepare("000001.SZ", days(100)[65], None)
            self.assertEqual(result["asof"], days(100)[99])
            self.assertEqual(result["actual"]["endDate"], days(100)[89])
            self.assertTrue(result["coverage"]["required"])
            self.assertEqual(result["snapshot"]["bars"][-1]["adjustment_factor"], 2)
            self.assertEqual(result["snapshot"]["bars"][-1]["volume"], 10000)
            self.assertTrue(any("复权因子" in value for value in result["coverage"]["reasons"]))

    def test_conflicting_action_crops_before_event(self):
        with TemporaryDirectory() as name:
            market = MarketData(source(Path(name), 100, None, 80))
            result = market.prepare("000001.SZ", days(100)[65], None)
            self.assertEqual(result["actual"]["endDate"], days(100)[79])
            self.assertEqual(result["snapshot"]["actions"], [])
            self.assertTrue(any("公司行动" in value for value in result["coverage"]["reasons"]))

    def test_source_search_discloses_unsupported_metadata(self):
        with TemporaryDirectory() as name:
            market = MarketData(source(Path(name)))
            values = market.symbols("未审核")
            self.assertEqual(values[0]["symbol"], "688001.SH")
            self.assertFalse(values[0]["supported"])
            self.assertEqual(market.symbols("' OR 1=1 --"), [])
            with self.assertRaises(GatewayError):
                market.prepare("688001.SH")

    def test_cache_reloads_after_database_changes(self):
        with TemporaryDirectory() as name:
            folder = Path(name)
            config = normalized(folder, 100)
            market = MarketData(config)
            before = market.prepare()["asof"]
            connection = sqlite3.connect(folder / "normalized.sqlite3")
            connection.execute('INSERT INTO etf_quotes VALUES(?,?,?,?,?,?,?,?)', ("510300.SH", days(101)[-1], 10, 11, 9, 10, 10000, 1))
            connection.commit()
            connection.close()
            after = market.prepare()["asof"]
            self.assertNotEqual(before, after)

    def test_pending_wal_refused(self):
        with TemporaryDirectory() as name:
            folder = Path(name)
            config = source(folder)
            Path(config["STRATEGY_WORKSHOP_SOURCE_DB"] + "-wal").write_bytes(b"pending")
            market = MarketData(config)
            self.assertFalse(market.capabilities()["available"])
            with self.assertRaises(GatewayError):
                market.prepare()

    def test_frozen_provenance_has_only_selected_lineage_and_is_independent(self):
        with TemporaryDirectory() as name:
            market = MarketData(source(Path(name), 100, None))
            first = market.prepare("000001.SZ", days(100)[65], days(100)[80])
            self.assertEqual(len(first["snapshot"]["provenance"]["source_quote_row_ids"]), 81)
            first["snapshot"]["provenance"]["source_quote_row_ids"].append("mutated")
            second = market.prepare("000001.SZ", days(100)[65], days(100)[80])
            self.assertNotIn("mutated", second["snapshot"]["provenance"]["source_quote_row_ids"])

    def test_input_dates_cash_and_unknown_strategy_fail_safely(self):
        with TemporaryDirectory() as name:
            market = MarketData(normalized(Path(name)))
            for kwargs in ({"cash": True}, {"cash": 12.001}, {"cash": float("nan")}, {"start": "2026-99-99"}, {"strategy_id": "import os"}):
                with self.assertRaises(GatewayError):
                    market.prepare(**kwargs)


class FakeProvider:
    key = "test-key-do-not-send"
    model = "GLM-test"
    connect_timeout = 5
    read_timeout = 120
    request_options = {}

    def __init__(self, value=None, hang=False):
        self.messages = []
        self.options = []
        self.timeouts = []
        self.value = value
        self.hang = hang

    def json_tokens(self, messages, model, stopped, on_response):
        self.messages.append(messages)
        self.options.append(dict(self.request_options))
        self.timeouts.append((self.connect_timeout, self.read_timeout))
        if self.hang:
            stopped.wait(1)
            raise RuntimeError("stopped")
        yield self.value if isinstance(self.value, str) else json.dumps(self.value, ensure_ascii=False)

    tokens = json_tokens


def valid_match():
    return {"strategyId": "donchian_breakout", "quality": "aligned", "intentValidity": "meaningful",
            "explanation": {"understoodIdea": "原来你是想做突破跟随策略，在价格创出前期新高时跟随趋势。",
                            "implementationApproach": "可以用目标股票的每日价格构建此前一段时间的高点，突破时买入；还需要说明何时退出。",
                            "whyThisStrategy": "此前二十日高点构成入场门槛。",
                            "coveredParts": ["突破入场。"], "uncoveredParts": ["模板不包含加仓。"]}}


class MatchingTests(unittest.TestCase):
    def service(self, provider, timeout=.5):
        return MatchingService(SimpleNamespace(runtime=SimpleNamespace(provider=provider)), {"STRATEGY_WORKSHOP_MATCH_TIMEOUT": timeout})

    def test_catalog_ten_have_exact_rules_params_and_provenance(self):
        catalog = public_catalog()
        self.assertEqual(len(catalog), 10)
        self.assertEqual(len({item["strategyId"] for item in catalog}), 10)
        self.assertTrue(all(item["rules"] and item["params"] and item["sources"] and item["limitations"] for item in catalog))

    def test_glm_json_allowlist_without_secrets_or_quote_rankings(self):
        provider = FakeProvider(valid_match())
        result = self.service(provider).match("价格突破过去二十天的最高价时买入")
        self.assertEqual(result["strategyId"], "donchian_breakout")
        self.assertEqual(result["explanation"], valid_match()["explanation"])
        payload = json.loads(provider.messages[0][1]["content"])
        self.assertEqual(set(payload), {"idea", "catalog"})
        self.assertNotIn(provider.key, json.dumps(provider.messages))
        self.assertNotIn("annual_return", json.dumps(payload))

    def test_matching_uses_low_reasoning_without_changing_shared_gateway(self):
        provider = FakeProvider(valid_match())
        provider.request_options = {"max_tokens": 8192, "reasoning_effort": "max"}
        self.service(provider).match("我想用短期均线高于长期均线判断持有")
        self.assertEqual(provider.options[-1], {"max_tokens": 2048, "reasoning_effort": "low"})
        self.assertEqual(provider.request_options, {"max_tokens": 8192, "reasoning_effort": "max"})

    def test_read_idle_follows_total_deadline_without_five_second_cap(self):
        provider = FakeProvider(valid_match())
        self.assertEqual(self.service(provider, 999).match("我想用均线判断交易")["strategyId"],
                         "donchian_breakout")
        self.assertEqual(provider.timeouts[-1], (5, 30.0))
        self.assertEqual(provider.read_timeout, 120)
        self.assertEqual(self.service(provider, .2).match("我想用均线判断交易")["strategyId"],
                         "donchian_breakout")
        self.assertEqual(provider.timeouts[-1], (.2, .2))

    def test_fallback_diagnostics_distinguish_request_json_schema_and_deadline(self):
        secret = "private-upstream-response-and-key-must-not-be-logged"
        class RequestFailure(FakeProvider):
            def json_tokens(self, messages, model, stopped, on_response):
                raise GatewayError(secret, 504, "agent_model_deadline")
                yield ""  # Keep the fixture as a token generator.

        cases = ((RequestFailure(), "model_request", "status=504", "code=agent_model_deadline"),
                 (FakeProvider("{invalid json"), "json_invalid", "status=-", "code=-"),
                 (FakeProvider({**valid_match(), "quality": "not-allowed"}),
                  "schema_invalid", "status=-", "code=-"),
                 (FakeProvider(hang=True), "overall_deadline", "status=-", "code=-"))
        for provider, category, status, code in cases:
            with self.subTest(category=category), self.assertLogs(
                    "backend.app.modules.strategy_workshop.matching", level="WARNING") as captured:
                result = self.service(provider, .05).match("用户私密投资想法：希望趋势上涨时买入")
            self.assertEqual(result["fallbackReason"], "model_unavailable")
            logged = "\n".join(captured.output)
            self.assertIn("category=" + category, logged)
            self.assertIn(status, logged)
            self.assertIn(code, logged)
            self.assertIn("elapsed_ms=", logged)
            self.assertNotIn(secret, logged)
            self.assertNotIn("用户私密投资想法", logged)

    def test_nonsense_fixed_example_never_calls_provider(self):
        provider = FakeProvider(valid_match())
        result = self.service(provider).match("asdf")
        self.assertEqual(result["strategyId"], "sma_trend")
        self.assertEqual(result["fallbackReason"], "no_usable_rule")
        self.assertEqual(result["intentValidity"], "no_usable_rule")
        self.assertEqual(provider.messages, [])

    def test_external_signals_keep_model_selection_and_disclose_uncovered_data(self):
        # The model chooses the approximation; local keywords must not turn an
        # external signal into either a fixed SMA or a fabricated LLM success.
        cases = (
            ("我看到微博上在讨论股票我就知道股票要跌了，当微博上股票话题不火热的时候我就觉得是个好的买入时机", "bollinger_reversion"),
            ("我想在公司利润改善、市场价格还没反应的时候买入", "rsi_reversion"),
            ("新闻变多并且情绪乐观时买入，情绪转差后退出", "roc_momentum"),
        )
        for idea, strategy_id in cases:
            with self.subTest(strategy_id=strategy_id):
                value = {
                    "strategyId": strategy_id, "quality": "low", "intentValidity": "meaningful",
                    "explanation": {
                        "understoodIdea": "原来你是想用价格以外的信号判断买卖时机，而不是只看股价本身。",
                        "implementationApproach": "要实现原始想法，需要对应的外部信号及其历史日期，并明确什么变化触发买入或退出；当前没有这些信号数据，不能直接验证原假设。",
                        "whyThisStrategy": "仅提供价格规则上的近似，不能直接实现输入中的外部信号。",
                        "coveredParts": ["提供可回测的价格买卖规则。"],
                        "uncoveredParts": ["模板没有所需的舆情、新闻或财务信号数据，不能验证原假设。"],
                    },
                }
                provider = FakeProvider(value)
                result = self.service(provider).match(idea)
                self.assertEqual(result, value)
                self.assertNotIn("fallbackReason", result)
                self.assertEqual(len(provider.messages), 1)
                self.assertEqual(json.loads(provider.messages[0][1]["content"])["idea"], idea)

    def test_prompt_contract_does_not_equate_missing_data_with_no_intent(self):
        provider = FakeProvider(valid_match())
        self.service(provider).match("微博热度下降时买入股票")
        system = provider.messages[0][0]["content"]
        for contract in ("不判断现有数据或策略库是否足以实现它", "就必须使用 meaningful",
                         "不能因为缺少舆情、新闻、财务等外部数据", "uncoveredParts",
                         "先比较交易方向和逻辑结构", "再比较信号数据与条件",
                         "没有微博热度数据", "示例不指定 strategyId", "implementationApproach",
                         "先理解和分析用户原始想法", "原来你是想", "需要哪些数据或信号",
                         "如何形成买入/卖出或持有条件", "无论 quality 高低", "不能直接报策略名称",
                         "不要把原始想法偷换成已有模板的规则"):
            self.assertIn(contract, system)
        self.assertNotIn("无意义或完全无法量化的输入", system)

    def test_external_signal_service_failure_is_honest_unavailable_fallback(self):
        provider = FakeProvider(valid_match())
        provider.key = ""
        result = self.service(provider).match("微博热度下降时买入股票")
        self.assertEqual(result["strategyId"], "sma_trend")
        self.assertEqual(result["fallbackReason"], "model_unavailable")
        self.assertEqual(result["intentValidity"], "meaningful")
        self.assertIn("尚未完成", result["explanation"]["understoodIdea"])
        self.assertIn("暂时无法可靠拆解", result["explanation"]["implementationApproach"])

    def test_missing_credentials_returns_explicit_model_unavailable(self):
        provider = FakeProvider(valid_match())
        provider.key = ""
        result = self.service(provider).match("我希望买入上涨趋势中的股票")
        self.assertEqual(result["fallbackReason"], "model_unavailable")
        self.assertIn("尚未完成", result["explanation"]["understoodIdea"])

    def test_model_rejects_extra_unknown_duplicate_or_code_fields(self):
        invalid = [dict(valid_match(), strategyId="user-generated"), dict(valid_match(), code="print(1)"),
                   dict(valid_match(), quality="high"), '{"strategyId":"sma_trend","strategyId":"ema_trend"}']
        for value in invalid:
            result = self.service(FakeProvider(value)).match("希望上涨趋势延续时买入")
            self.assertEqual(result["fallbackReason"], "model_unavailable")

    def test_implementation_approach_is_required_text_with_finite_bounds(self):
        for approach in (None, "", " \t\n", [], {}, 123, "数" * 1801):
            with self.subTest(approach_type=type(approach).__name__, length=len(approach) if isinstance(approach, str) else None):
                value = valid_match()
                value["explanation"]["implementationApproach"] = approach
                result = self.service(FakeProvider(value)).match("微博热度下降时考虑买入股票")
                self.assertEqual(result["fallbackReason"], "model_unavailable")
        value = valid_match()
        del value["explanation"]["implementationApproach"]
        self.assertEqual(self.service(FakeProvider(value)).match("突破历史高点时买入")["fallbackReason"], "model_unavailable")
        for approach in ("数", "数" * 1800):
            value = valid_match()
            value["explanation"]["implementationApproach"] = approach
            self.assertEqual(self.service(FakeProvider(value)).match("突破历史高点时买入"), value)

    def test_all_fallback_paths_include_honest_original_analysis_and_approach(self):
        broken = valid_match()
        broken["explanation"].pop("implementationApproach")
        cases = (("asdf", FakeProvider(valid_match()), "no_usable_rule"),
                 ("微博话题冷时买入股票", FakeProvider(broken), "model_unavailable"),
                 ("想买价格上涨的股票", FakeProvider(hang=True), "model_unavailable"))
        fields = {"understoodIdea", "implementationApproach", "whyThisStrategy", "coveredParts", "uncoveredParts"}
        for idea, provider, reason in cases:
            with self.subTest(reason=reason, idea=idea):
                result = self.service(provider, .05).match(idea)
                self.assertEqual(result["fallbackReason"], reason)
                self.assertEqual(set(result["explanation"]), fields)
                self.assertTrue(result["explanation"]["implementationApproach"].strip())
                self.assertIn("不能确认", result["explanation"]["coveredParts"][0])
                self.assertNotIn("已实现你的策略", result["explanation"]["implementationApproach"])

    def test_model_nonsense_cannot_choose_different_strategy(self):
        value = dict(valid_match(), intentValidity="no_usable_rule")
        result = self.service(FakeProvider(value)).match("一段较长但与投资没什么关联的内容")
        self.assertEqual(result["strategyId"], "sma_trend")
        self.assertEqual(result["quality"], "low")

    def test_entire_matching_deadline_is_bounded(self):
        provider = FakeProvider(hang=True)
        started = time.monotonic()
        result = self.service(provider, .05).match("我希望价格上涨时跟随趋势")
        self.assertLess(time.monotonic() - started, .4)
        self.assertEqual(result["fallbackReason"], "model_unavailable")

    def test_broken_catalog_does_not_fallback_to_unhealthy_code(self):
        with patch("backend.app.modules.strategy_workshop.matching.public_catalog", side_effect=ValueError("missing")):
            with self.assertRaises(GatewayError):
                self.service(FakeProvider(valid_match())).match("我希望上涨时买入")

    def test_idea_size_and_type_bounds(self):
        for value in ("", " " * 20, "股" * 4001, {}, None):
            with self.assertRaises(GatewayError):
                self.service(FakeProvider(valid_match())).match(value)

    def test_close_stops_active_matching_and_refuses_new_work(self):
        provider = FakeProvider(hang=True)
        service = self.service(provider, .5)
        results = []
        thread = threading.Thread(target=lambda: results.append(service.match("希望价格上涨时持有")))
        thread.start()
        for _ in range(100):
            if service._active:
                break
            time.sleep(.001)
        service.close()
        thread.join(.4)
        self.assertFalse(thread.is_alive())
        self.assertEqual(results[0]["fallbackReason"], "model_unavailable")
        with self.assertRaises(GatewayError):
            service.match("希望价格上涨时持有")


if __name__ == "__main__":
    unittest.main()
