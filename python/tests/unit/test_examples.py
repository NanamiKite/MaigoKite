from __future__ import annotations

import pytest
from examples.code_analysis import analyze_python
from examples.code_review import review_file
from examples.document_summary import summarize_document
from examples.search_summary import search_and_summarize

from tests.fixtures.chat_model import FakeModel


def test_code_analysis_example():
    result = analyze_python("import os\nclass Demo:\n    def run(self):\n        return os.getcwd()\n")
    assert result["classes"] == ["Demo"]
    assert result["functions"] == ["run"]
    assert result["import_count"] == 1


@pytest.mark.asyncio
async def test_search_summary_example():
    async def search(_: str):
        return [{"title": "Source", "snippet": "Evidence"}]
    result = await search_and_summarize("topic", search, FakeModel())
    assert "Source: Evidence" in result


@pytest.mark.asyncio
async def test_phase_one_code_review_workflow(tmp_path):
    source = tmp_path / "sample.py"
    source.write_text("def answer():\n    return 42\n", encoding="utf-8")
    result = await review_file(source, FakeModel())
    assert "sample.py" in result and "return 42" in result


@pytest.mark.asyncio
async def test_phase_one_document_summary_workflow(tmp_path):
    document = tmp_path / "decision.md"
    document.write_text("# Decision\nUse SQLite in phase one.\n", encoding="utf-8")
    result = await summarize_document(document, FakeModel())
    assert "decision.md" in result and "Use SQLite" in result
