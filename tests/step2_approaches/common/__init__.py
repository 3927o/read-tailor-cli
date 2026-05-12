"""Shared infrastructure for the 6-plan Step 2 normalization experiment.

This package replaces the legacy plan_a..plan_f exploratory scripts. It exposes
three context-builder modules (full / trimmed outline + regex facts), an
intermediate-representation schema and engine for the "AI outputs fixed
structure" approaches, a script runner for the "AI generates script"
approaches, and a generic structure summarizer.

All modules MUST stay book-agnostic: never reference specific book titles,
chapter texts, preface wording, or EPUB internal filenames.
"""
