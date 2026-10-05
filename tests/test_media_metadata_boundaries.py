import ast
from pathlib import Path


def test_services_use_media_metadata_view_for_media_request_fields():
    forbidden = {
        "temporary_files",
        "image_source_urls",
        "image_download_headers",
        "video_download_headers",
        "video_download_host_suffixes",
    }
    project_root = Path(__file__).parents[1]
    violations = []
    for path in (project_root / "services").rglob("*.py"):
        tree = ast.parse(path.read_text(encoding="utf-8"))
        for node in ast.walk(tree):
            if not isinstance(node, ast.Attribute) or node.attr not in forbidden:
                continue
            owner = node.value
            if isinstance(owner, ast.Attribute) and owner.attr == "media_metadata":
                continue
            violations.append(f"{path.relative_to(project_root)}:{node.lineno}")

    assert violations == []
