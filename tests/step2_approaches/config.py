#!/usr/bin/env python3
"""
共用配置和工具函数。
"""
import os

# 路径
PROJECT_ROOT = "/Users/richard/projects/read-tailor-cli"
EPUB_PATH = os.path.join(PROJECT_ROOT, "查拉图斯特拉如是说.epub")
EPUB_TEXT_DIR = "/tmp/epub_extract/OEBPS/Text"
RAW_HTML = os.path.join(PROJECT_ROOT, "dist/查拉图斯特拉如是说/work/查拉图斯特拉如是说.raw.html")
TEST_OUTPUT_DIR = os.path.join(PROJECT_ROOT, "tests/step2_approaches/outputs")

# AI 配置（从环境变量读取，或使用默认值）
AI_BASE_URL = os.environ.get("AI_BASE_URL", "")
AI_API_KEY = os.environ.get("AI_API_KEY", "")
AI_MODEL = os.environ.get("AI_MODEL", "")

# 结果文件
RESULTS_FILE = os.path.join(PROJECT_ROOT, "tests/step2_approaches/results.json")

os.makedirs(TEST_OUTPUT_DIR, exist_ok=True)
