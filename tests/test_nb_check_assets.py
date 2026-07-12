#!/usr/bin/env python3
from __future__ import annotations

import hashlib
import sys
import tempfile
import unittest
from collections import Counter
from pathlib import Path

from bs4 import BeautifulSoup

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "tools"))

from nb_check import AssetChecker, FidelityChecker


class FakeBaseline:
    def __init__(self, image_bytes: bytes):
        self.image_hash = hashlib.md5(image_bytes).hexdigest()

    def image_refs(self):
        return Counter({self.image_hash: 1}), {self.image_hash: ["chapter.xhtml"]}


def soup_with_image(src: str) -> BeautifulSoup:
    return BeautifulSoup(f'<html><body><img src="{src}"></body></html>', "html.parser")


class AssetCheckerTests(unittest.TestCase):
    def test_existing_asset_passes(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            (root / "assets").mkdir()
            (root / "assets" / "a.png").write_bytes(b"image")
            product = root / "book.normalized.html"
            product.write_text("", encoding="utf-8")

            checker = AssetChecker(soup_with_image("assets/a.png"), str(product))
            checker.run()

            self.assertEqual(checker.errors, [])
            self.assertEqual(checker.metrics["asset_references"], 1)
            self.assertEqual(checker.metrics["asset_files"], 1)

    def test_missing_asset_fails(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            product = Path(tmp) / "book.normalized.html"
            product.write_text("", encoding="utf-8")

            checker = AssetChecker(soup_with_image("assets/missing.png"), str(product))
            checker.run()

            self.assertTrue(any("[资源缺失]" in error for error in checker.errors))

    def test_image_hash_matches_epub_baseline(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            (root / "assets").mkdir()
            image_bytes = b"same-image"
            (root / "assets" / "a.png").write_bytes(image_bytes)

            checker = FidelityChecker(
                soup_with_image("assets/a.png"),
                FakeBaseline(image_bytes),
                root,
            )
            checker.check_image_recall()

            self.assertEqual(checker.errors, [])
            self.assertEqual(checker.warnings, [])

    def test_image_hash_mismatch_fails(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            (root / "assets").mkdir()
            (root / "assets" / "a.png").write_bytes(b"different-image")

            checker = FidelityChecker(
                soup_with_image("assets/a.png"),
                FakeBaseline(b"source-image"),
                root,
            )
            checker.check_image_recall()

            self.assertTrue(any("[图片丢失]" in error for error in checker.errors))


if __name__ == "__main__":
    unittest.main()
