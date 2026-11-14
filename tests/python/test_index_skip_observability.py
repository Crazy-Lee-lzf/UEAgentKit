from __future__ import annotations

import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
SRC_ROOT = ROOT / "src"
if str(SRC_ROOT) not in sys.path:
    sys.path.insert(0, str(SRC_ROOT))

from ue_agent_kit.indexer import _CanonicalPathResolver  # noqa: E402


class CanonicalFallbackSkipTests(unittest.TestCase):
    def test_invalid_canonical_json_is_counted_not_silently_skipped(self) -> None:
        with tempfile.TemporaryDirectory(prefix="ueak_index_skip_") as temporary:
            root = Path(temporary)
            canonical = root / "canonical"
            canonical.mkdir(parents=True)
            (canonical / "a_bad.json").write_text("{not-json", encoding="utf-8")
            (canonical / "z_good.json").write_text(
                '{"assetPath": "/Game/Folder/DA_Good.DA_Good"}',
                encoding="utf-8",
            )

            resolver = _CanonicalPathResolver(root)
            resolved = resolver.resolve(
                {"assetPath": "/Game/Folder/DA_Good.DA_Good"}
            )
            self.assertEqual(resolved.name, "z_good.json")
            self.assertEqual(len(resolver.skipped_canonical_files), 1)
            self.assertIn("a_bad.json", resolver.skipped_canonical_files[0])
            self.assertIn(
                "unreadable-or-invalid-json",
                resolver.skipped_canonical_files[0],
            )


if __name__ == "__main__":
    unittest.main()
