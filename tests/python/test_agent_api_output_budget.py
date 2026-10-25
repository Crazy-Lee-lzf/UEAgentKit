"""Regression: ``outputBudget.truncated`` must mean "content was withheld".

Pre-fix behaviour: ``outputBudget.truncated`` was
``hasMore or estimatedTokens > maxTokens``, so the *final* page of a paged read
reported ``truncated: true`` with ``truncationReason:
"single-result-exceeds-token-budget"`` even though ``hasMore`` was false and
every requested item had been returned.  ``truncationReason`` also accumulated
reasons from earlier pages, so a complete page carried a stale reason.

Post-fix contract:
  * ``truncated`` / ``truncationReason`` describe withheld content only;
  * ``budgetExceeded`` (new, additive) reports that the serialized response is
    larger than the requested ``max_output_tokens``;
  * ``budgetExceededReason`` (new, additive) keeps the historic
    ``single-result-exceeds-token-budget`` diagnostic when nothing was withheld;
  * existing fields (``maxTokens``, ``estimatedTokens``, ``hasMore``,
    ``mayHaveMore``, ``continuationToken``, per-section ``truncationReason``)
    keep their meaning.
"""

from __future__ import annotations

import sys
import tempfile
import unittest
from pathlib import Path

TOOL_ROOT = Path(__file__).resolve().parents[2]
SRC_ROOT = TOOL_ROOT / "src"
TEST_ROOT = TOOL_ROOT / "tests" / "python"
for entry in (str(SRC_ROOT), str(TEST_ROOT)):
    if entry not in sys.path:
        sys.path.insert(0, entry)

from test_indexer_queries import (  # noqa: E402
    ASSET_A,
    ASSET_B,
    REVISION_A,
    make_asset,
    write_export,
)
from ue_agent_kit.agent_api import IndexQueryService  # noqa: E402
from ue_agent_kit.database import open_database  # noqa: E402
from ue_agent_kit.indexer import build_index  # noqa: E402

TINY_BUDGET = 256  # == MIN_OUTPUT_TOKEN_BUDGET


class OutputBudgetSemanticsTests(unittest.TestCase):
    def setUp(self) -> None:
        self._temp = tempfile.TemporaryDirectory()
        root = Path(self._temp.name)
        export = root / "export"
        write_export(
            export,
            [
                make_asset(ASSET_A, profile="asset-index", revision=REVISION_A, rich=True),
                make_asset(ASSET_B, profile="asset-index", revision=REVISION_A, rich=True),
            ],
        )
        database_path = root / "index.sqlite"
        with open_database(database_path) as connection:
            build_index(connection, export, database_path)
        self.service = IndexQueryService(database_path)

    def tearDown(self) -> None:
        self._temp.cleanup()

    def _all_symbols(self) -> int:
        payload = self.service.get_asset(ASSET_A, sections=["symbols"], symbol_limit=500)
        return len(payload["asset"]["symbols"])

    def _page_symbols(self, *, max_pages: int = 16):
        pages = []
        page = self.service.get_asset(
            ASSET_A,
            sections=["identity", "symbols"],
            symbol_limit=500,
            max_output_tokens=TINY_BUDGET,
        )
        pages.append(page)
        while True:
            section = page["sectionPagination"]["symbols"]
            if not section["hasMore"]:
                return pages
            self.assertTrue(section["continuationToken"], "hasMore without a continuation token")
            if len(pages) > max_pages:
                self.fail("paging did not terminate")
            page = self.service.get_asset(continuation_token=section["continuationToken"])
            pages.append(page)

    def test_intermediate_pages_report_real_withheld_content(self) -> None:
        pages = self._page_symbols()
        self.assertGreater(len(pages), 1, "fixture must need more than one page at this budget")
        first = pages[0]
        budget = first["outputBudget"]
        self.assertTrue(first["sectionPagination"]["symbols"]["hasMore"])
        self.assertTrue(budget["truncated"])
        self.assertEqual(budget["truncationReason"], "token-budget")
        # A 256-token budget cannot hold identity + one symbol + pagination, so the
        # response legitimately overflows the budget while also withholding content.
        self.assertTrue(budget["budgetExceeded"])
        self.assertEqual(budget["budgetExceededReason"], "single-result-exceeds-token-budget")
        self.assertEqual(set(budget), {
            "maxTokens",
            "estimatedTokens",
            "truncated",
            "truncationReason",
            "budgetExceeded",
            "budgetExceededReason",
        })

    def test_final_page_is_not_reported_truncated(self) -> None:
        total_symbols = self._all_symbols()
        pages = self._page_symbols()
        collected = [
            symbol["stable_id"]
            for page in pages
            for symbol in (page.get("asset") or {}).get("symbols") or []
        ]
        self.assertEqual(len(collected), total_symbols, "paging lost or duplicated symbols")
        self.assertEqual(len(set(collected)), total_symbols)

        final = pages[-1]
        section = final["sectionPagination"]["symbols"]
        budget = final["outputBudget"]
        self.assertFalse(section["hasMore"])
        self.assertEqual(section["continuationToken"], "")
        self.assertEqual(section["truncationReason"], "")
        # The response may still exceed the caller's budget; that is not withheld content.
        self.assertTrue(budget["budgetExceeded"])
        self.assertEqual(budget["budgetExceededReason"], "single-result-exceeds-token-budget")
        self.assertFalse(budget["truncated"])
        self.assertEqual(budget["truncationReason"], "")

    def test_search_page_limit_is_reported_without_budget_overflow(self) -> None:
        page = self.service.search(
            "",
            scope="assets",
            limit=1,
            max_output_tokens=32768,
        )
        budget = page["outputBudget"]
        self.assertTrue(page["pagination"]["hasMore"])
        self.assertTrue(budget["truncated"])
        self.assertEqual(budget["truncationReason"], "page-limit")
        self.assertFalse(budget["budgetExceeded"])
        self.assertEqual(budget["budgetExceededReason"], "")

    def test_search_with_everything_returned_is_not_truncated(self) -> None:
        page = self.service.search(
            "",
            scope="assets",
            limit=100,
            max_output_tokens=32768,
        )
        budget = page["outputBudget"]
        self.assertFalse(page["pagination"]["hasMore"])
        self.assertFalse(budget["truncated"])
        self.assertEqual(budget["truncationReason"], "")
        self.assertFalse(budget["budgetExceeded"])

    def test_missing_asset_budget_block_keeps_the_new_fields(self) -> None:
        payload = self.service.get_asset("/Game/Missing/BP_None.BP_None")
        self.assertFalse(payload["found"])
        budget = payload["outputBudget"]
        self.assertFalse(budget["truncated"])
        self.assertEqual(budget["truncationReason"], "")
        self.assertFalse(budget["budgetExceeded"])


if __name__ == "__main__":
    unittest.main()
