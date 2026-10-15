"""Regression inputs and source titles from the eleven exported Explore reports.

These are the actual saved user/confirmed questions in the 2026-10-13 handoff.
The regression deliberately does not contact a model or an external source.
"""

import unittest

from tests import test_explore_research_runtime as runtime_fixtures
from backend.app.modules.explore.research_task import (
    build_research_task, keywords, qualified_source, relevance, search_queries,
    search_result_acceptable, search_result_score,
)
from backend.app.modules.workbench.tool_registry import ToolResult, ToolSpec


OLD_RUNS = {
    "R01": ("event", "华为尊界v800刹车", None),
    "R02": ("relation", "该事件涉及哪些主体及其相互关系（华为、制造方、车主、监管机构、媒体）？", None),
    "R03": ("event", "小米汽车自燃事件", None),
    "R04": ("event", "华为尊界v800刹车",
            "请梳理尊界V800制动踏板支架断裂事件自2026年10月8日懂车帝发布测试视频以来的完整时间线，涵盖：懂车帝的测试结果与对'暴力测试''4589N'争议的说明、尊界汽车官方回应（10月8日情况说明及后续检测情况说明）、江淮汽车及服务热线说法、门店说法（如可获得）、相关上市公司股价影响、以及官方提出的免费升级与设计复核补强等后续安排。"),
    "R05": ("event", "研究华为尊界v800",
            "分析尊界V800刹车踏板支架断裂事件中华为与江淮的责任划分，以及懂车帝媒体测试的资质与公信力争议"),
    "R06": ("relation", "华为官方是否已就尊界V800踏板支架断裂事件表态？其在鸿蒙智行/尊界合作中的合同责任边界如何约定？",
            "华为官方是否已就尊界V800踏板支架断裂事件表态？其在鸿蒙智行/尊界合作中的合同责任边界如何约定？"),
    "R07": ("history", "鸿蒙智行模式下（问界、智界、享界、尊界）历次质量争议中华为与车企的责任承担与公开表态方式有无先例？",
            "待验证判断：尊界刹车断了,被骂的是华为,跌停的是江淮?——鸿蒙智行合作模式下的安全责任之困是否会产生后续影响？请用真实历史材料对照历史相似案例。"),
    "R08": ("event", "研究最近的华为",
            "研究华为2007年至2025年累计超1.8万亿元研发投入的主要投向（芯片、操作系统、通信等）及产出效果"),
    "R09": ("history", "华为历年（2007—2025）年报中披露的逐年研发费用、研发费用率及资本化情况是怎样的？",
            "待验证判断：余承东:从2007年到2025年底 华为研发累计投入超1.8万亿是否会产生后续影响？请用真实历史材料对照历史相似案例。"),
    "R10": ("event", "https://www.nature.com/articles/s41562-022-01320-y#data-availability 研究一下这个文章",
            "https://www.nature.com/articles/s41562-022-01320-y#data-availability 研究一下这个文章"),
    "R11": ("event", "该 Nature 文章（编号 s41562-022-01320-y）的确切标题、作者与发表期刊是什么？其数据可用性声明具体内容如何？",
            "该 Nature 文章（编号 s41562-022-01320-y）的确切标题、作者与发表期刊是什么？其数据可用性声明具体内容如何？"),
}


def old_run(label):
    module, original, resolved = OLD_RUNS[label]
    run = {"module": module, "question": original, "resolved_question": resolved,
           "time_policy": {"as_of": "2026-10-13T16:00:00+08:00"}, "context_snapshot": []}
    if label == "R02":
        run["context_snapshot"] = [{"question": "华为尊界v800刹车"}]
    return run


class ResearchTaskTests(unittest.TestCase):
    def test_all_eleven_exported_questions_keep_original_and_resolved_goal(self):
        for label in OLD_RUNS:
            with self.subTest(label=label):
                run = old_run(label)
                task = build_research_task(run)
                self.assertEqual(task["original_input"], run["question"])
                self.assertEqual(task["resolved_goal"], run["resolved_question"] or run["question"])
                self.assertEqual(task["as_of"], run["time_policy"]["as_of"])
                self.assertTrue(task["subquestions"])
                self.assertEqual([q["id"] for q in task["subquestions"]],
                                 [f"Q{i + 1}" for i in range(len(task["subquestions"]))])
                self.assertTrue(all(q["status"] == "unanswered" and q["gap"] for q in task["subquestions"]))
                self.assertTrue(search_queries(run))
                self.assertTrue(all(1 <= len(query) <= 200 for query in search_queries(run)))

    def test_old_multi_part_requests_keep_all_targets_in_task_and_queries(self):
        for label, targets in {
            "R04": ("时间线", "江淮汽车", "股价影响", "免费升级"),
            "R05": ("责任划分", "公信力争议"),
            "R06": ("表态", "合同责任边界"),
            "R11": ("标题", "作者", "发表期刊", "数据可用性声明"),
        }.items():
            with self.subTest(label=label):
                task = build_research_task(old_run(label))
                questions = "\n".join(q["question"] for q in task["subquestions"])
                queries = "\n".join(search_queries(old_run(label)))
                for target in targets:
                    self.assertIn(target, questions)
                    self.assertIn(target, queries)
        self.assertGreaterEqual(len(build_research_task(old_run("R06"))["subquestions"]), 2)
        self.assertGreaterEqual(len(build_research_task(old_run("R11"))["subquestions"]), 2)
        self.assertTrue(any("v800" in q.casefold() and "合同责任" in q
                            for q in search_queries(old_run("R06"))))

    def test_old_explicit_article_url_and_fragment_survive(self):
        run = old_run("R10")
        expected = "https://www.nature.com/articles/s41562-022-01320-y#data-availability"
        task = build_research_task(run)
        self.assertEqual(task["input_type"], "public_url")
        self.assertEqual(task["urls"], [expected])
        self.assertEqual(search_queries(run)[0], expected)

    def test_url_query_mark_and_new_multiple_questions_are_distinct(self):
        url = "https://example.org/report?id=42&lang=zh#methods"
        run = {"module": "event", "question": f"请读{url}。作者是谁？数据方法是什么？"}
        task = build_research_task(run)
        self.assertIn(url, task["urls"])
        self.assertEqual(len(task["subquestions"]), 2)
        self.assertIn(url, task["subquestions"][0]["question"])
        self.assertTrue(any("数据方法" in query for query in search_queries(run)))
        self.assertEqual(search_queries(run)[0], url)

    def test_doi_and_original_url_are_kept_when_confirmed_goal_changes(self):
        original = "先看 https://example.org/a#discussion 再核查 DOI:10.1038/s41562-022-01320-y"
        run = {"module": "event", "question": original,
               "resolved_question": "核查 https://example.org/b#data-availability 的数据声明？"}
        task = build_research_task(run)
        self.assertEqual(task["original_input"], original)
        self.assertEqual(task["urls"], ["https://example.org/b#data-availability", "https://example.org/a#discussion"])
        self.assertIn("10.1038/s41562-022-01320-y", task["dois"])

    def test_old_irrelevant_news_is_rejected_and_event_sources_remain(self):
        question = old_run("R05")["resolved_question"]
        old_titles = {
            "S1": "尊界V800刹车板断裂,能否让行业冷静一下?",
            "S4": "上海都市旅游“十五五”规划发布：2030年旅游收入力争突破9000亿元",
            "S5": "华为：逻辑折叠技术不是简单把几颗芯片叠在一起 而是最初就以三维视角进行系统设计",
            "S6": "华为徐松余：下一代麒麟2027芯片已经就绪",
            "S7": "懂车帝实测三台全新尊界V800踩断踏板,车企只提升级不召回,这反常事件背后需要一个真相?",
        }
        self.assertNotIn("力争", keywords(question))
        for label in ("S4", "S5", "S6"):
            with self.subTest(label=label):
                item = {"title": old_titles[label], "summary": ""}
                self.assertEqual(relevance(item, keywords(question)), 0)
                self.assertFalse(qualified_source(question, item))
        for label in ("S1", "S7"):
            self.assertTrue(qualified_source(question, {"title": old_titles[label], "summary": ""}))

    def test_broad_brand_news_can_match_but_document_id_must_match(self):
        chip = {"title": "华为下一代麒麟芯片已经就绪", "summary": ""}
        self.assertTrue(qualified_source("研究最近的华为新闻", chip))
        self.assertFalse(qualified_source(old_run("R05")["resolved_question"], chip))
        article = old_run("R11")["resolved_question"]
        self.assertFalse(qualified_source(article, {"title": "Nature 本周论文导读", "summary": ""}))
        self.assertTrue(qualified_source(article, {"title": "Nature article s41562-022-01320-y",
                                                   "summary": "", "url": "https://nature.com/a"}))

    def test_direct_requested_url_wins_even_when_title_is_metadata_only(self):
        question = old_run("R10")["question"]
        item = {"title": "Article", "summary": "", "url": "https://www.nature.com/articles/s41562-022-01320-y"}
        self.assertTrue(qualified_source(question, item))

    def test_meta_instructions_stay_in_original_but_do_not_become_research_targets(self):
        question = "伊朗发生了什么？我还未确定具体事件，请先给我材料。"
        task = build_research_task({"question": question})
        self.assertEqual(task["original_input"], question)
        self.assertEqual([x["question"] for x in task["subquestions"]], ["伊朗发生了什么"])
        self.assertEqual(search_queries({"question": question}), ["伊朗发生了什么"])
        self.assertEqual(keywords(question), {"伊朗"})
        self.assertTrue(qualified_source(question, {"title": "伊朗发生新外交事件", "summary": ""}))
        self.assertTrue(qualified_source("最近有什么新闻？", {"title": "伊朗发生新外交事件"}))
        self.assertFalse(qualified_source("研究一下", {"title": "伊朗发生新外交事件"}))
        policy = "半导体产业是否会受到出口政策影响？不用最新新闻冒充历史证据。"
        self.assertEqual(search_queries({"question": policy}), ["半导体产业是否会受到出口政策影响"])

    def test_selected_branch_title_disambiguates_pronoun_and_continue(self):
        for question in ("这项政策可能通过哪些环节影响A股产业链？", "继续分析它的影响"):
            with self.subTest(question=question):
                queries = search_queries({"question": question,
                    "context_snapshot": [{"title": "新能源政策研究报告"}]})
                self.assertTrue(queries[0].startswith("新能源政策研究报告 "))

    def test_web_search_leads_do_not_require_literal_subject_and_event_overlap(self):
        question = "历史上资金抱团防御题材之后市场怎么走？"
        related = {"title": "银行、电力等红利板块走强后的A股表现",
                   "snippet": "复盘防御方向的资金流向与随后指数变化"}
        self.assertFalse(qualified_source(question, related))
        self.assertTrue(search_result_acceptable(question, related))
        self.assertGreaterEqual(search_result_score(question, related), 0)
        self.assertTrue(search_result_acceptable(
            "尊界V800制动支架事件", {"title": "车企回应零部件争议", "snippet": "正文待读取"}))
        self.assertTrue(search_result_acceptable(
            "A股这周行情怎么样", {"title": "A股主要指数本周震荡", "snippet": "沪深市场分化"}))

    def test_web_search_explicit_model_and_index_identity_conflicts(self):
        for question, title in (
            ("尊界V800制动支架事件", "尊界V8000制动支架测试"),
            ("尊界V800制动支架事件", "尊界V700制动支架测试"),
            ("问界M7事故", "问界M70事故"),
            ("沪深300这两天走势", "沪深3000策略报告"),
            ("沪深300这两天走势", "上证50这两天走势"),
        ):
            with self.subTest(question=question, title=title):
                self.assertFalse(search_result_acceptable(question, {"title": title, "snippet": ""}))
        self.assertTrue(search_result_acceptable(
            "尊界V800制动支架事件", {"title": "尊界V800 Pro测试", "snippet": ""}))
        self.assertTrue(search_result_acceptable(
            "沪深300这两天走势", {"title": "沪深300走势", "snippet": ""}))
        self.assertTrue(search_result_acceptable(
            "沪深300这两天走势", {"title": "市场走势观察", "snippet": "未注明指数"}))

    def test_web_search_precise_document_identity_remains_a_hard_boundary(self):
        article = "请核查论文s41562-022-01320-y的数据声明"
        self.assertFalse(search_result_acceptable(article, {"title": "Nature另一论文", "snippet": ""}))
        self.assertTrue(search_result_acceptable(article, {
            "title": "Nature论文", "url": "https://www.nature.com/articles/s41562-022-01320-y"}))
        doi = "核查DOI:10.1038/s41562-022-01320-y的数据声明"
        self.assertFalse(search_result_acceptable(doi, {"title": "Nature另一论文", "snippet": ""}))
        self.assertTrue(search_result_acceptable(doi, {
            "title": "Nature论文", "url": "https://doi.org/10.1038/s41562-022-01320-y"}))

    def test_web_search_score_is_soft_and_repeated_calls_are_stable(self):
        question = "尊界V800制动支架事件"
        exact = {"title": "尊界V800制动支架事件回应", "snippet": "车企公开表态"}
        related = {"title": "尊界汽车回应零部件问题", "snippet": "制动支架仍待核对"}
        unknown = {"title": "行业观察", "snippet": ""}
        self.assertTrue(all(search_result_acceptable(question, item)
                            for item in (exact, related, unknown)))
        self.assertGreater(search_result_score(question, exact), search_result_score(question, related))
        self.assertGreater(search_result_score(question, related), search_result_score(question, unknown))
        self.assertEqual(search_result_score(question, unknown), 0)


class SoftSearchRuntimeTests(unittest.TestCase):
    owner = runtime_fixtures.ResearchRuntimeTests.owner
    setUp = runtime_fixtures.ResearchRuntimeTests.setUp
    tearDown = runtime_fixtures.ResearchRuntimeTests.tearDown
    make_runtime = runtime_fixtures.ResearchRuntimeTests.make_runtime

    def test_history_runtime_keeps_a_semantic_search_lead_without_literal_overlap(self):
        self.search.title = "银行电力等红利板块走强后的A股表现"
        self.search.snippet = "复盘防御方向资金流向与随后指数变化"
        runtime, _service, _saved, _control = self.make_runtime(
            runtime_fixtures.ScriptProvider([]), "历史上资金抱团防御题材之后市场怎么走？",
            module="history")
        result = runtime.execute("web_search", {"query": "资金抱团防御题材"}, "soft-history")
        self.assertEqual(len(result["evidence"]), 1)
        self.assertEqual(result["rejected_irrelevant"], 0)
        self.assertEqual(result["evidence"][0]["title"], self.search.title)

    def test_runtime_stably_ranks_first_eight_and_keeps_homepage_guard(self):
        rows = [
            {"title": "行业观察", "snippet": "暂无详情", "url": "https://other.example.test/a"},
            {"title": "尊界汽车制动支架观察", "snippet": "需核对车型", "url": "https://public.example.test/b"},
            {"title": "尊界V800制动支架事件回应", "snippet": "车企已说明", "url": "https://public.example.test/c"},
            {"title": "尊界V8000制动支架事件", "snippet": "其他车型", "url": "https://public.example.test/d"},
        ]

        def tool():
            return ToolSpec(name="web_search", description="本地排序测试",
                parameters={"type": "object", "properties": {"query": {"type": "string"}},
                            "required": ["query"], "additionalProperties": False},
                handler=lambda _args, _context: ToolResult(content={"searchedAt": "2026-10-10"},
                                                          sources=rows),
                capabilities=frozenset({"search"}), max_calls=3)

        self.search.tool = tool
        runtime, _service, _saved, _control = self.make_runtime(
            runtime_fixtures.ScriptProvider([]), "尊界V800制动支架事件")
        result = runtime.execute("web_search", {"query": "尊界V800制动支架事件"}, "soft-ranked")
        self.assertEqual([item["title"] for item in result["evidence"]],
                         [rows[2]["title"], rows[1]["title"], rows[0]["title"]])
        self.assertEqual(result["rejected_irrelevant"], 1)
        self.assertIn(rows[0]["url"], runtime.allowed_urls)
        self.assertNotIn("https://other.example.test/", runtime.allowed_urls)
        self.assertIn("https://public.example.test/", runtime.allowed_urls)


if __name__ == "__main__":
    unittest.main()
