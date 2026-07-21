"""Shared pytest fixtures for the epub2md regression harness.

Two kinds of end-to-end coverage:

* A **synthetic EPUB** built on the fly (`synthetic_epub` fixture). It exercises
  the real pandoc → cleanup pipeline and runs in CI (no copyrighted content
  committed), so it is the auto-merge gate's teeth.
* The **local corpus** under ``sample-epubs-for-testing/`` (gitignored, real
  books). Those tests are skipped when the EPUBs aren't present (e.g. in CI).
"""

import json
import zipfile
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parent.parent
BASELINES_PATH = Path(__file__).resolve().parent / "baselines.json"
CORPUS_DIR = REPO_ROOT / "sample-epubs-for-testing"


# --------------------------------------------------------------------------- #
# Synthetic EPUB builder (committed-content-free end-to-end fixture)
# --------------------------------------------------------------------------- #

_CONTAINER_XML = """<?xml version="1.0"?>
<container version="1.0" xmlns="urn:oasis:names:tc:opendocument:xmlns:container">
  <rootfiles>
    <rootfile full-path="OEBPS/content.opf" media-type="application/oebps-package+xml"/>
  </rootfiles>
</container>"""

_CHAPTERS = [
    ("Introduction", [
        "This is the introduction paragraph one, with enough words to look real.",
        "A second introductory paragraph that elaborates on the opening idea in detail.",
    ]),
    ("Chapter One", [
        "Chapter one opens with a clear topic sentence about business strategy.",
        "It continues with a supporting paragraph that develops the argument further.",
    ]),
    ("Chapter Two", [
        "Chapter two begins here and introduces a distinct second theme for the book.",
        "More explanatory prose follows so the converter has real content to process.",
    ]),
]


def _chapter_xhtml(title: str, paras: list) -> str:
    body = "\n".join(f"<p>{p}</p>" for p in paras)
    return (
        '<?xml version="1.0" encoding="utf-8"?>\n'
        "<!DOCTYPE html>\n"
        '<html xmlns="http://www.w3.org/1999/xhtml">\n'
        f"<head><title>{title}</title></head>\n"
        f"<body>\n<h1>{title}</h1>\n{body}\n</body>\n</html>"
    )


def build_synthetic_epub(path: str) -> str:
    """Write a minimal but valid EPUB with 3 chaptered <h1> sections to ``path``."""
    manifest, spine, files = [], [], {}
    for i, (title, paras) in enumerate(_CHAPTERS, 1):
        cid, href = f"ch{i}", f"ch{i}.xhtml"
        files[f"OEBPS/{href}"] = _chapter_xhtml(title, paras)
        manifest.append(f'<item id="{cid}" href="{href}" media-type="application/xhtml+xml"/>')
        spine.append(f'<itemref idref="{cid}"/>')

    files["OEBPS/content.opf"] = (
        '<?xml version="1.0" encoding="utf-8"?>\n'
        '<package xmlns="http://www.idpf.org/2007/opf" version="3.0" unique-identifier="bookid">\n'
        '  <metadata xmlns:dc="http://purl.org/dc/elements/1.1/">\n'
        '    <dc:identifier id="bookid">urn:uuid:synthetic-epub-0001</dc:identifier>\n'
        "    <dc:title>Synthetic Test Book</dc:title>\n"
        "    <dc:creator>Test Author</dc:creator>\n"
        "    <dc:language>en</dc:language>\n"
        "    <dc:date>2020</dc:date>\n"
        "  </metadata>\n"
        f"  <manifest>\n    {''.join(manifest)}\n  </manifest>\n"
        f"  <spine>\n    {''.join(spine)}\n  </spine>\n"
        "</package>"
    )
    files["META-INF/container.xml"] = _CONTAINER_XML

    with zipfile.ZipFile(path, "w", zipfile.ZIP_DEFLATED) as z:
        # Per spec the mimetype entry must be first and stored uncompressed.
        z.writestr("mimetype", "application/epub+zip", compress_type=zipfile.ZIP_STORED)
        for name, content in files.items():
            z.writestr(name, content)
    return path


@pytest.fixture(scope="session")
def synthetic_epub(tmp_path_factory):
    """A converted synthetic EPUB: returns (epub_path, md_path)."""
    from epub_to_md_converter import process_folder

    src = tmp_path_factory.mktemp("synthetic_src")
    out = tmp_path_factory.mktemp("synthetic_out")
    epub = src / "Synthetic Test Book - Test Author 2020.epub"
    build_synthetic_epub(str(epub))
    pairs = process_folder(str(src), str(out))
    assert pairs, "synthetic EPUB failed to convert"
    return pairs[0]


# --------------------------------------------------------------------------- #
# Baselines + corpus discovery
# --------------------------------------------------------------------------- #

@pytest.fixture(scope="session")
def baselines() -> dict:
    with open(BASELINES_PATH, encoding="utf-8") as f:
        return json.load(f)


def find_corpus_epub(filename_glob: str):
    """Return the local corpus EPUB matching the glob, or None if absent."""
    if not CORPUS_DIR.is_dir():
        return None
    matches = sorted(CORPUS_DIR.glob(filename_glob))
    return matches[0] if matches else None


def pytest_addoption(parser):
    parser.addoption(
        "--regen-baselines",
        action="store_true",
        default=False,
        help="Reconvert the local corpus and rewrite tests/baselines.json (local only).",
    )


def pytest_configure(config):
    """When --regen-baselines is passed, recompute floors/ceilings from current output."""
    if not config.getoption("--regen-baselines"):
        return
    import shutil
    import tempfile

    from epub_to_md_converter import collect_quality_signals, process_folder

    with open(BASELINES_PATH, encoding="utf-8") as f:
        baselines = json.load(f)

    for key, spec in baselines.items():
        epub = find_corpus_epub(spec["filename_glob"])
        if epub is None:
            print(f"[regen] skipping {key}: corpus EPUB not present")
            continue
        work = tempfile.mkdtemp(prefix=f"regen_{key}_")
        out = tempfile.mkdtemp(prefix=f"regen_out_{key}_")
        shutil.copy(str(epub), work)
        pairs = process_folder(work, out)
        sig = collect_quality_signals(*pairs[0])
        spec["min_optimization_score"] = round(sig["optimization_score"] * 0.92, 1)
        spec["min_heading_count"] = int(sig["heading_count"] * 0.9)
        spec["min_md_chars"] = int(sig["md_char_count"] * 0.9)
        spec["max_artifacts"] = {
            k: int(v * 1.25) + 5 for k, v in sig["artifacts"].items() if k != "line_count"
        }
        print(f"[regen] {key}: {json.dumps(spec)}")

    with open(BASELINES_PATH, "w", encoding="utf-8") as f:
        json.dump(baselines, f, indent=2)
        f.write("\n")
    pytest.exit("Baselines regenerated; re-run pytest without --regen-baselines.", returncode=0)
