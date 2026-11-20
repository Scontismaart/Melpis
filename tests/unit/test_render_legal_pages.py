import importlib.util
from pathlib import Path

import pytest


_SPEC = importlib.util.spec_from_file_location(
    "render_legal_pages", Path("web/render_legal_pages.py")
)
_MODULE = importlib.util.module_from_spec(_SPEC)
_SPEC.loader.exec_module(_MODULE)
_RUNTIME_ENV_SPEC = importlib.util.spec_from_file_location(
    "write_legal_runtime_env", Path("scripts/write_legal_runtime_env.py")
)
_RUNTIME_ENV_MODULE = importlib.util.module_from_spec(_RUNTIME_ENV_SPEC)
_RUNTIME_ENV_SPEC.loader.exec_module(_RUNTIME_ENV_MODULE)


def reviewed_values():
    return {
        "LEGAL_ENTITY_NAME": 'Melpis & Partners "S.r.l."',
        "LEGAL_ENTITY_REGISTERED_OFFICE": 'Via "Roma" 1',
        "LEGAL_ENTITY_VAT_NUMBER": "IT12345678901",
        "LEGAL_PRIVACY_CONTACT_EMAIL": "privacy@melpis.invalid",
        "LEGAL_FORUM": "Roma",
        "LEGAL_DOCUMENT_EFFECTIVE_DATE": "2026-09-21",
        # Must never appear in output: the renderer reads no arbitrary env key.
        "DATABASE_URL": "postgresql://secret:secret@db/private",
    }


def test_renderer_writes_only_rendered_public_legal_pages(tmp_path):
    template_dir = Path("web/landing")
    output_dir = tmp_path / "runtime-legal"
    _MODULE.render_tree(template_dir, output_dir, reviewed_values())

    outputs = sorted(path.relative_to(output_dir).as_posix() for path in output_dir.rglob("*.html"))
    assert outputs == sorted(_MODULE.TEMPLATE_PATHS)
    for path in output_dir.rglob("*.html"):
        content = path.read_text(encoding="utf-8")
        assert "{{LEGAL_" not in content
        assert "postgresql://secret" not in content
        if not any("cookie" in part for part in path.relative_to(output_dir).parts):
            assert "2026-09-21" in content
    privacy = (output_dir / "privacy.html").read_text(encoding="utf-8")
    assert "Melpis &amp; Partners &quot;S.r.l.&quot;" in privacy
    assert "Via &quot;Roma&quot; 1" in privacy


def test_renderer_fails_closed_for_missing_reviewed_legal_value(tmp_path):
    values = reviewed_values()
    del values["LEGAL_FORUM"]
    with pytest.raises(ValueError, match="LEGAL_FORUM"):
        _MODULE.render_tree(Path("web/landing"), tmp_path / "runtime-legal", values)


def test_runtime_env_exposes_only_allowlisted_public_legal_values(tmp_path):
    source = tmp_path / "production.env"
    source.write_text("\n".join(f"{name}={value}" for name, value in reviewed_values().items()), encoding="utf-8")
    output = tmp_path / "runtime" / "legal.env"
    _RUNTIME_ENV_MODULE.write_runtime_env(source, output)
    content = output.read_text(encoding="utf-8")
    assert "DATABASE_URL" not in content
    assert set(line.split("=", 1)[0] for line in content.splitlines()) == set(_MODULE.LEGAL_FIELDS)
