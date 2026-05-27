from pathlib import Path

from hermes_cli import sdk_patches


def test_patch_openai_responses_output_none_applies_once(tmp_path, monkeypatch):
    parser = tmp_path / "openai" / "lib" / "_parsing" / "_responses.py"
    parser.parent.mkdir(parents=True)
    parser.write_text(
        "def parse(response):\n"
        "    for output in response.output:\n"
        "        pass\n",
        encoding="utf-8",
    )
    monkeypatch.setattr(sdk_patches, "_openai_responses_parser_path", lambda: parser)

    result = sdk_patches.patch_openai_responses_output_none()
    assert result.status == "applied"
    assert "for output in (response.output or []):" in parser.read_text(encoding="utf-8")

    result = sdk_patches.patch_openai_responses_output_none()
    assert result.status == "already_applied"


def test_patch_openai_responses_output_none_reports_pattern_drift(tmp_path, monkeypatch):
    parser = tmp_path / "openai" / "lib" / "_parsing" / "_responses.py"
    parser.parent.mkdir(parents=True)
    parser.write_text("def parse(response):\n    return response\n", encoding="utf-8")
    monkeypatch.setattr(sdk_patches, "_openai_responses_parser_path", lambda: parser)

    result = sdk_patches.patch_openai_responses_output_none()
    assert result.status == "pattern_not_found"


def test_patch_openai_responses_output_none_skips_missing_sdk(monkeypatch):
    monkeypatch.setattr(sdk_patches, "_openai_responses_parser_path", lambda: None)

    result = sdk_patches.patch_openai_responses_output_none()
    assert result.status == "missing_sdk"
    assert result.ok
