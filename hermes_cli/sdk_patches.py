"""Local third-party SDK compatibility patches.

These patches are intentionally small and idempotent.  They run after Hermes
installs or updates dependencies so a virtualenv rebuild does not silently
remove local compatibility fixes needed by the gateway runtime.
"""
from __future__ import annotations

import argparse
import importlib.util
from dataclasses import dataclass
from pathlib import Path
from typing import Optional


_OPENAI_RESPONSES_ORIGINAL = "    for output in response.output:\n"
_OPENAI_RESPONSES_PATCHED = "    for output in (response.output or []):\n"


@dataclass(frozen=True)
class PatchResult:
    name: str
    status: str
    path: Optional[Path]
    message: str

    @property
    def ok(self) -> bool:
        return self.status in {"applied", "already_applied", "missing_sdk"}


def _openai_responses_parser_path() -> Optional[Path]:
    """Return OpenAI SDK Responses parser path for the active interpreter."""
    spec = importlib.util.find_spec("openai")
    if spec is None:
        return None

    package_dir: Optional[Path] = None
    locations = getattr(spec, "submodule_search_locations", None)
    if locations:
        package_dir = Path(next(iter(locations)))
    elif spec.origin:
        package_dir = Path(spec.origin).parent

    if package_dir is None:
        return None

    return package_dir / "lib" / "_parsing" / "_responses.py"


def patch_openai_responses_output_none() -> PatchResult:
    """Patch OpenAI SDK parsing to tolerate ``response.output is None``.

    Some Codex Responses API calls have returned a response object with
    ``output=None``.  OpenAI SDK versions that iterate ``response.output``
    directly raise ``TypeError: 'NoneType' object is not iterable`` before
    Hermes can handle the malformed upstream response.  Treating ``None`` as
    an empty list preserves normal parsing for valid responses and prevents the
    SDK crash path.
    """
    name = "OpenAI Responses parser output=None guard"
    path = _openai_responses_parser_path()
    if path is None:
        return PatchResult(name, "missing_sdk", None, "OpenAI SDK not installed; skipped")
    if not path.exists():
        return PatchResult(name, "missing_file", path, f"OpenAI parser file not found: {path}")

    try:
        text = path.read_text(encoding="utf-8")
    except OSError as exc:
        return PatchResult(name, "error", path, f"Could not read {path}: {exc}")

    if _OPENAI_RESPONSES_PATCHED in text:
        return PatchResult(name, "already_applied", path, "OpenAI Responses parser patch already applied")

    if _OPENAI_RESPONSES_ORIGINAL not in text:
        return PatchResult(
            name,
            "pattern_not_found",
            path,
            "OpenAI Responses parser pattern not found; SDK may already differ upstream",
        )

    try:
        path.write_text(
            text.replace(_OPENAI_RESPONSES_ORIGINAL, _OPENAI_RESPONSES_PATCHED, 1),
            encoding="utf-8",
        )
    except OSError as exc:
        return PatchResult(name, "error", path, f"Could not write {path}: {exc}")

    return PatchResult(name, "applied", path, "OpenAI Responses parser patch applied")


def apply_local_sdk_patches(*, quiet: bool = False) -> list[PatchResult]:
    """Apply all local SDK compatibility patches for the active interpreter."""
    results = [patch_openai_responses_output_none()]

    if not quiet:
        for result in results:
            if result.status in {"applied", "already_applied"}:
                print(f"  ✓ {result.message}")
            elif result.status == "missing_sdk":
                print(f"  ℹ {result.message}")
            else:
                print(f"  ⚠ {result.message}")

    return results


def main(argv: Optional[list[str]] = None) -> int:
    parser = argparse.ArgumentParser(description="Apply Hermes local SDK compatibility patches")
    parser.add_argument("--quiet", action="store_true", help="suppress status output")
    args = parser.parse_args(argv)

    results = apply_local_sdk_patches(quiet=args.quiet)
    return 0 if all(result.ok for result in results) else 1


if __name__ == "__main__":
    raise SystemExit(main())
