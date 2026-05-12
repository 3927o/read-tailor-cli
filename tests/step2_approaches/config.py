#!/usr/bin/env python3
"""
共用配置和工具函数。
"""
import os

# 路径
PROJECT_ROOT = "/Users/richard/projects/read-tailor-cli"
BOOK_NAME = os.environ.get("BOOK_NAME", "查拉图斯特拉如是说")
EPUB_PATH = os.path.join(PROJECT_ROOT, f"{BOOK_NAME}.epub")
EPUB_TEXT_DIR = "/tmp/epub_extract/OEBPS/Text"
RAW_HTML = os.path.join(PROJECT_ROOT, f"dist/{BOOK_NAME}/work/{BOOK_NAME}.raw.html")
_outputs_suffix = "" if BOOK_NAME == "查拉图斯特拉如是说" else f"_{BOOK_NAME}"
TEST_OUTPUT_DIR = os.path.join(PROJECT_ROOT, f"tests/step2_approaches/outputs{_outputs_suffix}")

# AI 配置（从环境变量读取，或使用默认值）
AI_BASE_URL = os.environ.get("AI_BASE_URL", "")
AI_API_KEY = os.environ.get("AI_API_KEY", "")
AI_MODEL = os.environ.get("AI_MODEL", "")

# 结果文件
RESULTS_FILE = os.path.join(PROJECT_ROOT, f"tests/step2_approaches/results{_outputs_suffix}.json")

os.makedirs(TEST_OUTPUT_DIR, exist_ok=True)
