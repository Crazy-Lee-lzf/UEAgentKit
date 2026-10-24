"""R1.4 T6 cursor-pagination contract tests (offline only).

The C++ implementation is validated against these vectors when the UE heavy
slot is available; this file proves the contract, bounds, fail-closed paths and
512-descriptor paging invariants with the reference codec.
"""

from __future__ import annotations

import json
import re
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
VECTORS = ROOT / "Plugin" / "UEAgentKit" / "Resources" / "NativeTools" / "Golden" / "r1-cursor-vectors.json"
NATIVE_SOURCE = ROOT / "Plugin" / "UEAgentKit" / "Source" / "UEAgentKitEditor" / "Private" / "Native"

from r1_cursor_reference import (  # noqa: E402
    CURSOR_TTL_SECONDS,
    CursorError,
    collect,
    decode,
    encode,
    paginate,
)


def load_vectors():
    with VECTORS.open("r", encoding="utf-8-sig") as handle:
        return json.load(handle)


def make_ids(count: int) -> list[str]:
    return [f"action:{index:032x}" for index in range(count)]


def query(**overrides):
    base = dict(
        kind="catalog",
        query_hash="sha256:" + "1" * 16,
        set_fingerprint="sha256:" + "2" * 16,
        epoch="sha256:" + "3" * 16,
    )
    base.update(overrides)
    return base


class CursorCodecTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.fixture = load_vectors()
        cls.key = bytes.fromhex(cls.fixture["keyHex"])
        cls.now = cls.fixture["nowUnix"]

    def test_golden_vectors_round_trip(self) -> None:
        self.assertEqual(self.fixture["schemaVersion"], "ueak.r1.cursor-vectors/1.0")
        self.assertEqual(self.fixture["payloadKeys"], ["v", "k", "q", "s", "e", "o", "n", "p", "iat", "exp"])
        for vector in self.fixture["vectors"]:
            with self.subTest(vector=vector["id"]):
                self.assertEqual(encode(vector["payload"], self.key), vector["token"])
                self.assertEqual(decode(vector["token"], self.key, self.now), vector["payload"])

    def test_tampered_token_fails_closed(self) -> None:
        token = self.fixture["vectors"][0]["token"]
        tampered = token[:-1] + ("A" if token[-1] != "A" else "B")
        with self.assertRaises(CursorError) as context:
            decode(tampered, self.key, self.now)
        self.assertEqual(context.exception.code, "native-schema-invalid-arguments")
        self.assertIn("authentication", context.exception.message)

    def test_malformed_tokens_fail_closed(self) -> None:
        for token in ("", "v2.a.b", "v1..b", "v1.a", "v1.a.b.c", "v1.***.b"):
            with self.subTest(token=token):
                with self.assertRaises(CursorError):
                    decode(token, self.key, self.now)

    def test_expired_token_fails_closed(self) -> None:
        token = self.fixture["vectors"][0]["token"]
        with self.assertRaises(CursorError) as context:
            decode(token, self.key, self.fixture["vectors"][0]["payload"]["exp"] + 1)
        self.assertIn("expired", context.exception.message)

    def test_wrong_key_fails_closed(self) -> None:
        token = self.fixture["vectors"][0]["token"]
        with self.assertRaises(CursorError):
            decode(token, bytes([255] * 32), self.now)


class CursorPaginationTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.key = bytes(range(32))
        cls.now = 1760000100
        cls.ids = make_ids(512)
        cls.q = query()

    def page(self, items, cursor="", page_size=64, **kwargs):
        return paginate(
            items,
            key=self.key,
            now=self.now,
            kind=kwargs.get("kind", self.q["kind"]),
            query_hash=kwargs.get("query_hash", self.q["query_hash"]),
            set_fingerprint=kwargs.get("set_fingerprint", self.q["set_fingerprint"]),
            epoch=kwargs.get("epoch", self.q["epoch"]),
            page_size=page_size,
            cursor=cursor,
        )

    def test_empty_set(self) -> None:
        page = self.page([])
        self.assertEqual(page.items, [])
        self.assertFalse(page.has_more)
        self.assertTrue(page.exhausted)
        self.assertIsNone(page.next_cursor)
        self.assertEqual(page.total, 0)

    def test_one_item(self) -> None:
        page = self.page(["only"], page_size=1)
        self.assertEqual(page.items, ["only"])
        self.assertTrue(page.exhausted)
        self.assertIsNone(page.next_cursor)

    def test_many_pages_no_overlap_no_gap_for_512(self) -> None:
        collected = collect(
            self.ids,
            key=self.key,
            now=self.now,
            kind=self.q["kind"],
            query_hash=self.q["query_hash"],
            set_fingerprint=self.q["set_fingerprint"],
            epoch=self.q["epoch"],
            page_size=64,
        )
        self.assertEqual(collected, self.ids)
        self.assertEqual(len(collected), 512)
        self.assertEqual(len(set(collected)), 512)

        seen = []
        page = self.page(self.ids, page_size=64)
        while True:
            self.assertFalse(set(page.items) & set(seen))
            seen.extend(page.items)
            if not page.next_cursor:
                self.assertTrue(page.exhausted)
                self.assertFalse(page.has_more)
                break
            page = self.page(self.ids, cursor=page.next_cursor)
        self.assertEqual(seen, self.ids)

    def test_last_page_is_exhausted(self) -> None:
        page = self.page(self.ids, cursor=self.page(self.ids, page_size=64).next_cursor, page_size=0)
        # second page still has a cursor; walk to the end
        while page.next_cursor:
            page = self.page(self.ids, cursor=page.next_cursor)
        self.assertTrue(page.exhausted)
        self.assertEqual(page.offset + len(page.items), 512)
        self.assertEqual(page.items[-1], self.ids[-1])

    def test_page_size_bounds(self) -> None:
        with self.assertRaises(CursorError):
            self.page(self.ids, page_size=257)
        self.assertEqual(len(self.page(self.ids, page_size=1).items), 1)
        self.assertEqual(len(self.page(self.ids, page_size=256).items), 256)

    def test_replayed_cursor_is_idempotent(self) -> None:
        first = self.page(self.ids, page_size=64)
        second = self.page(self.ids, cursor=first.next_cursor)
        replay = self.page(self.ids, cursor=first.next_cursor)
        self.assertEqual(second.items, replay.items)
        self.assertEqual(second.next_cursor, replay.next_cursor)

    def test_query_shape_change_is_rejected(self) -> None:
        cursor = self.page(self.ids, page_size=64).next_cursor
        with self.assertRaises(CursorError) as context:
            self.page(self.ids, cursor=cursor, query_hash="sha256:" + "9" * 16)
        self.assertIn("does not match this query", context.exception.message)

    def test_query_kind_change_is_rejected(self) -> None:
        cursor = self.page(self.ids, page_size=64).next_cursor
        with self.assertRaises(CursorError):
            self.page(self.ids, cursor=cursor, kind="context")

    def test_stale_fingerprint_is_rejected(self) -> None:
        cursor = self.page(self.ids, page_size=64).next_cursor
        with self.assertRaises(CursorError) as context:
            self.page(self.ids, cursor=cursor, set_fingerprint="sha256:" + "8" * 16)
        self.assertEqual(context.exception.code, "native-gate-revision-conflict")

    def test_stale_epoch_is_rejected(self) -> None:
        cursor = self.page(self.ids, page_size=64).next_cursor
        with self.assertRaises(CursorError) as context:
            self.page(self.ids, cursor=cursor, epoch="sha256:" + "7" * 16)
        self.assertEqual(context.exception.code, "native-gate-revision-conflict")

    def test_page_size_mismatch_is_rejected(self) -> None:
        cursor = self.page(self.ids, page_size=64).next_cursor
        with self.assertRaises(CursorError):
            self.page(self.ids, cursor=cursor, page_size=32)

    def test_expired_cursor_is_rejected(self) -> None:
        cursor = self.page(self.ids, page_size=64).next_cursor
        with self.assertRaises(CursorError):
            paginate(
                self.ids,
                key=self.key,
                now=self.now + CURSOR_TTL_SECONDS + 1,
                kind=self.q["kind"],
                query_hash=self.q["query_hash"],
                set_fingerprint=self.q["set_fingerprint"],
                epoch=self.q["epoch"],
                page_size=64,
                cursor=cursor,
            )

    def test_cursor_binds_total(self) -> None:
        cursor = self.page(self.ids, page_size=64).next_cursor
        with self.assertRaises(CursorError) as context:
            self.page(self.ids[:511], cursor=cursor)
        self.assertEqual(context.exception.code, "native-gate-revision-conflict")


class RealEvidencePagingTests(unittest.TestCase):
    def test_512_exported_ids_page_without_gap(self) -> None:
        export = ROOT / "Build" / "r1-native" / "Evidence" / "catalog-export.json"
        if not export.exists():
            self.skipTest("no catalog export from the UE run")
        with export.open("r", encoding="utf-8-sig") as handle:
            payload = json.load(handle)
        ids = sorted(descriptor["actionId"] for descriptor in payload["descriptors"])
        self.assertEqual(len(ids), 512)
        key = bytes(range(32))
        now = 1760000100
        q = "sha256:" + "1" * 16
        s = "sha256:" + "2" * 16
        e = "sha256:" + "3" * 16
        collected = collect(
            ids,
            key=key,
            now=now,
            kind="catalog",
            query_hash=q,
            set_fingerprint=s,
            epoch=e,
            page_size=64,
        )
        self.assertEqual(collected, ids)
        self.assertEqual(len(set(collected)), 512)


class CursorSourceGuardTests(unittest.TestCase):
    def test_cursor_code_has_no_io_or_eval(self) -> None:
        files = [NATIVE_SOURCE / "NativeCursorCodec.cpp", NATIVE_SOURCE / "NativeCursorCodec.h"]
        forbidden = re.compile(r"(FPaths::|IFileManager|CreateProcess|system\s*\(|eval\s*\(|LoadObject|FSoftObjectPath)")
        for path in files:
            with self.subTest(file=path.name):
                self.assertTrue(path.exists(), path)
                for line_number, line in enumerate(path.read_text(encoding="utf-8").splitlines(), 1):
                    if line.strip().startswith("//"):
                        continue
                    self.assertIsNone(
                        forbidden.search(line),
                        f"{path.name}:{line_number} contains a forbidden IO/eval API",
                    )

    def test_cpp_constants_match_reference(self) -> None:
        header = (NATIVE_SOURCE / "NativeCursorCodec.h").read_text(encoding="utf-8")
        self.assertIn("CursorVersion = 1", header)
        self.assertIn("MaxPageSize = 256", header)
        self.assertIn("CursorTtlSeconds = 900", header)
        self.assertIn("MaxCursorLength = 4096", header)
        implementation = (NATIVE_SOURCE / "NativeCursorCodec.cpp").read_text(encoding="utf-8")
        self.assertIn('TEXT("v1.")', implementation)
        self.assertIn("HmacSha256", implementation)


if __name__ == "__main__":
    unittest.main()
