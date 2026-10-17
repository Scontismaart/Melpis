"""Static release-container checks that do not require Docker or network access."""

from __future__ import annotations

import re
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]


def _read(relative_path: str) -> str:
    return (ROOT / relative_path).read_text(encoding="utf-8")


def _job(workflow: str, name: str) -> str:
    match = re.search(
        rf"^  {re.escape(name)}:\n(?P<body>.*?)(?=^  [a-zA-Z0-9_-]+:\n|\Z)",
        workflow,
        flags=re.MULTILINE | re.DOTALL,
    )
    assert match, f"missing workflow job: {name}"
    return match.group("body")


def _compose_service(compose: str, name: str) -> str:
    match = re.search(
        rf"^  {re.escape(name)}:\n(?P<body>.*?)(?=^  [a-zA-Z0-9_-]+:\n|^networks:\n|^volumes:\n|\Z)",
        compose,
        flags=re.MULTILINE | re.DOTALL,
    )
    assert match, f"missing compose service: {name}"
    return match.group("body")


def test_validation_gates_all_image_builds_and_prs_never_publish() -> None:
    workflow = _read(".github/workflows/ci.yml")
    validation = _job(workflow, "test")
    build = _job(workflow, "build_images")
    publish = _job(workflow, "publish_images")
    manifest = _job(workflow, "publish_manifests")

    for expected in (
        "ruff check",
        "npm run lint:web",
        "npm run test:web",
        "python scripts/check_tenant_scoping.py",
        "pytest -v --tb=short",
        "docker compose",
        "test_oracle_vm_bootstrap.sh",
    ):
        assert expected in validation

    assert "needs: test" in build
    assert "needs: test" in publish
    assert "needs: [test, publish_images]" in manifest
    assert "github.event_name == 'pull_request'" in build
    assert "push: false" in build
    assert "docker/login-action" not in build

    assert "github.event_name == 'push'" in publish
    assert "github.ref == 'refs/heads/main'" in publish
    assert "permissions:" in publish
    assert "packages: write" in publish
    assert "docker/login-action" in publish
    assert "push: true" in publish
    assert "github.event_name == 'push'" in manifest
    assert "packages: write" in manifest
    assert "docker/login-action" in manifest


def test_ci_prepares_non_secret_legal_env_for_compose_syntax_check() -> None:
    workflow = _read(".github/workflows/ci.yml")
    validation = _job(workflow, "test")
    prepare = validation.index("mkdir -p .runtime")
    empty_env = validation.index(": > .runtime/legal.env")
    compose_check = validation.index("docker compose -f compose.production.yml config --quiet")

    assert prepare < empty_env < compose_check


def test_ci_does_not_duplicate_branch_push_and_pull_request_runs() -> None:
    workflow = _read(".github/workflows/ci.yml")
    trigger = workflow.split("permissions:", 1)[0]

    assert re.search(
        r"pull_request:\n(?:    #[^\n]*\n)*    branches: \[main, 'codex/\*\*'\]", trigger
    )
    assert "push:\n    branches: [main]" in trigger
    assert 'branches: ["**"]' not in trigger


def test_buildx_distributes_api_and_web_across_native_amd64_and_arm64_runners() -> None:
    workflow = _read(".github/workflows/ci.yml")

    for job_name in ("build_images", "publish_images"):
        job = _job(workflow, job_name)
        assert "docker/setup-qemu-action" not in job
        assert "docker/setup-buildx-action" in job
        assert "buildkitd-config-inline" in job
        assert "max-parallelism = 1" in job
        assert "docker/build-push-action" in job
        assert job.count("platform: linux/amd64") == 2
        assert job.count("platform: linux/arm64") == 2
        assert job.count("runner: ubuntu-22.04") == 2
        assert job.count("runner: ubuntu-24.04-arm") == 2
        assert "runs-on: ${{ matrix.runner }}" in job
        assert "platforms: ${{ matrix.platform }}" in job
        assert "linux/amd64,linux/arm64" not in job
        assert "./Dockerfile" in job
        assert "./web/Dockerfile" in job
        assert "context: ." in job
        assert "context: ./web" in job

    publish = _job(workflow, "publish_images")
    assert ":sha-${{ github.sha }}-${{ matrix.arch }}" in publish
    assert "id: build" in publish
    assert "provenance: mode=max" in publish
    assert "sbom: true" in publish

    manifest = _job(workflow, "publish_manifests")
    assert "docker buildx imagetools create" in manifest
    assert ":sha-${GITHUB_SHA}-amd64" in manifest
    assert ":sha-${GITHUB_SHA}-arm64" in manifest
    assert "docker buildx imagetools inspect" in manifest
    assert "index(\"amd64\")" in manifest
    assert "index(\"arm64\")" in manifest
    assert "MELPIS_API_IMAGE_REF" in manifest
    assert "MELPIS_WEB_IMAGE_REF" in manifest
    assert "GITHUB_STEP_SUMMARY" in manifest
    assert ":latest" not in workflow
    assert "type=ref" not in workflow


def test_production_compose_uses_release_images_and_only_caddy_publishes() -> None:
    compose = _read("compose.production.yml")
    assert "${MELPIS_API_IMAGE_REF:?" in compose
    assert "${MELPIS_WEB_IMAGE_REF:?" in compose
    assert "MELPIS_IMAGE_TAG" not in compose
    assert "MELPIS_API_IMAGE:?" not in compose
    assert "MELPIS_WEB_IMAGE:?" not in compose
    assert "CADDY_SITE_MODE:?" in compose
    assert "./Caddyfile.${CADDY_SITE_MODE" in compose
    assert ":latest" not in compose
    assert "build:" not in compose

    services = ("caddy", "web", "api", "worker-inbound", "worker-retry", "supervisor", "valkey")
    assert 'ports: ["80:80", "443:443"]' in _compose_service(compose, "caddy")
    for service in services[1:]:
        assert "ports:" not in _compose_service(compose, service)


def test_caddy_keeps_temporary_tls_separate_from_final_canonical_hosts() -> None:
    temporary = _read("Caddyfile.temporary")
    final = _read("Caddyfile.final")
    assert "{$PUBLIC_HOST}" in temporary
    assert "melpis.it" not in temporary
    assert "app.melpis.it" not in temporary
    assert "PUBLIC_HOST" not in final
    assert "melpis.it" in final
    assert re.search(
        r"app\.melpis\.it\s*\{.*?redir\s+https://melpis\.it/app/\s+308.*?\}",
        final,
        flags=re.DOTALL,
    )
    for caddyfile in (temporary, final):
        assert "reverse_proxy @backend api:8000" in caddyfile
        assert "reverse_proxy web:80" in caddyfile


def test_application_dockerfiles_are_architecture_neutral() -> None:
    for relative_path in ("Dockerfile", "web/Dockerfile"):
        dockerfile = _read(relative_path)
        assert "--platform=linux/amd64" not in dockerfile
        assert "--platform=linux/arm64" not in dockerfile
        assert "TARGETARCH=amd64" not in dockerfile


def test_web_build_normalizes_windows_entrypoint_before_execution() -> None:
    dockerfile = _read("web/Dockerfile")
    normalize = "sed -i 's/\\r$//' /docker-entrypoint.d/50-render-legal-pages.sh"
    assert normalize in dockerfile
    assert dockerfile.index(normalize) < dockerfile.index("chmod 0755 /docker-entrypoint.d/50-render-legal-pages.sh")


def test_api_build_context_excludes_local_runtime_and_agent_artifacts() -> None:
    ignored = {
        line.strip()
        for line in _read(".dockerignore").splitlines()
        if line.strip() and not line.lstrip().startswith("#")
    }

    for pattern in (
        ".pytest-*-tmp/",
        ".runtime/",
        ".agent/",
        ".agents/",
        ".claude/",
        ".codex/",
        ".opencode/",
        ".playwright-mcp/",
        ".qwen/",
        ".ruff_cache/",
        ".superpowers/",
        ".zcode/",
        "agent/",
        "graphify-out/",
        "node_modules/",
        "review/",
        "scratch/",
        "~/",
        "*.png",
        "*.jpg",
        "*.jpeg",
        "*.gif",
    ):
        assert pattern in ignored
