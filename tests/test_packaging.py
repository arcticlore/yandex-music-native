"""Release gates for the repository layout: no legacy code, consistent packaging.

Step 4 removed the ``yamusic`` package. These checks keep the repository in the
state a v1.0.0 tag expects: one application stack, packaging scripts that ship
the same files the Makefile installs, and no references to a repository or an
AppStream id that no longer exists.

Run: python -m pytest -q tests/test_packaging.py
"""

from __future__ import annotations

import os
import re
import subprocess
import sys
import tomllib
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

LEGACY = "yamusic"
SCANNED_SUFFIXES = (".py", ".toml", ".txt", ".sh", ".spec", ".service", ".desktop", ".xml")
SKIP_DIRS = {".git", ".venv", "dist", "build", "__pycache__", ".pytest_cache", ".ruff_cache"}
SPEC = "packaging/rpm/yandex-music-native.spec"
RPM_SCRIPT = "scripts/build-rpm.sh"


def tracked_files() -> list[Path]:
    files: list[Path] = []
    for path in ROOT.rglob("*"):
        if any(part in SKIP_DIRS for part in path.relative_to(ROOT).parts):
            continue
        if path.is_file() and path.suffix in SCANNED_SUFFIXES:
            files.append(path)
    return sorted(files)


def text(path: str) -> str:
    return (ROOT / path).read_text(encoding="utf-8")


@pytest.fixture(scope="module")
def pyproject() -> dict:
    return tomllib.loads(text("pyproject.toml"))


# -- the legacy package is gone ---------------------------------------------


def test_legacy_package_is_removed() -> None:
    assert list((ROOT / "src" / LEGACY).rglob("*")) == []
    assert list((ROOT / "data").rglob("*")) == []


def test_no_legacy_imports_anywhere() -> None:
    import_re = re.compile(r"^\s*(?:from|import)\s+.*" + LEGACY, re.MULTILINE)
    offenders = [
        str(path.relative_to(ROOT)) for path in tracked_files() if import_re.search(path.read_text("utf-8"))
    ]
    assert offenders == [], f"legacy imports remain: {offenders}"


def test_legacy_fallback_entry_point_is_gone() -> None:
    assert "YML_LEGACY" not in text("src/main.py")
    assert "ui.app" in text("src/main.py")


def test_only_core_and_ui_are_shipped(pyproject: dict) -> None:
    include = pyproject["tool"]["setuptools"]["packages"]["find"]["include"]
    assert include == ["core*", "ui*"]
    assert LEGACY not in text("pyproject.toml")
    assert LEGACY not in text("Makefile")
    assert not (ROOT / "src" / LEGACY).exists()


# -- one application stack -----------------------------------------------------


def test_entry_points_start_the_new_stack(pyproject: dict) -> None:
    assert pyproject["project"]["scripts"] == {
        "yandex-music-native": "ui.app:main",
        "yandex-music-linux": "ui.app:main",
    }


def test_dependencies_match_the_imports(pyproject: dict) -> None:
    requirements = {
        line.split(">=")[0].strip().lower() for line in text("requirements.txt").splitlines() if line
    }
    declared = {
        re.split(r"[><=!]", item, maxsplit=1)[0].strip().lower()
        for item in pyproject["project"]["dependencies"]
    }
    assert requirements == declared
    for unused in ("dbus-next", "aiohttp"):
        assert unused not in requirements, f"{unused} is not imported by src/core or src/ui"
    source = "".join(
        path.read_text("utf-8") for path in tracked_files() if path.suffix == ".py" and "src" in path.parts
    )
    for package in ("PySide6", "numpy", "mpv", "yandex_music", "keyring"):
        assert re.search(rf"(?:^|\n)\s*(?:from|import)\s+{package}\b", source), (
            f"{package} is declared but unused"
        )


def test_version_is_release_ready(pyproject: dict) -> None:
    assert pyproject["project"]["version"] == "1.0.0"
    assert "pkgver=1.0.0" in text("PKGBUILD")


# -- repository links ----------------------------------------------------------


def test_urls_point_at_the_current_repository(pyproject: dict) -> None:
    repo = "https://github.com/arcticlore/yandex-music-native"
    for name, url in pyproject["project"]["urls"].items():
        assert url.startswith(repo), f"{name} points outside {repo}"
    readme = text("README.md")
    for stale in ("yandex-music-linux/yandex-music-linux", "yandex-music-native/yandex-music-native"):
        assert stale not in readme, f"README still links {stale}"
    assert 'url="https://github.com/arcticlore/yandex-music-native"' in text("PKGBUILD")
    assert "https://github.com/arcticlore/yandex-music-native" in text("packaging/debian/build-deb.sh")
    assert "https://github.com/arcticlore/yandex-music-native" in text(
        "packaging/yandex-music-native.metainfo.xml"
    )


# -- packaging stays consistent -------------------------------------------------


def test_metainfo_id_matches_installed_file_name() -> None:
    identifier = re.search(r"<id>([^<]+)</id>", text("packaging/yandex-music-native.metainfo.xml")).group(1)
    assert identifier == "org.arcticlore.YandexMusicNative"
    for script in (
        "Makefile",
        "packaging/debian/build-deb.sh",
        "scripts/build-appimage.sh",
        "PKGBUILD",
        SPEC,
    ):
        assert f"{identifier}.metainfo.xml" in text(script), script


def test_dbus_service_name_and_exec() -> None:
    service = text("packaging/dbus/yandex-music-native.service")
    assert "Name=org.arcticlore.YandexMusicNative" in service
    assert "Exec=/usr/local/bin/yandex-music-native" in service


@pytest.mark.parametrize("script", ("packaging/debian/build-deb.sh", "scripts/build-appimage.sh", "PKGBUILD"))
def test_packaging_ships_the_new_stack(script: str) -> None:
    body = text(script)
    assert LEGACY not in body
    assert "src/main.py" in body
    assert "core" in body and "ui" in body


def test_system_launcher_targets_the_new_stack() -> None:
    launcher = text("packaging/yandex-music-native.sh")
    assert LEGACY not in launcher
    assert "main.py" in launcher
    assert "ui.app" in launcher
    assert "PYTHONPATH" in launcher
    # deb/AUR install into <prefix>/lib, the RPM keeps a noarch tree in share
    assert 'LIBRARY="${PREFIX}/lib/${NAME}"' in launcher
    assert 'SHARED_LIBRARY="${PREFIX}/share/${NAME}"' in launcher


def makefile_variables() -> dict[str, str]:
    return dict(re.findall(r"^([A-Z_]+)\s*:?=\s*(\S+)\s*$", text("Makefile"), re.MULTILINE))


def resolve_makefile() -> str:
    body = text("Makefile")
    variables = makefile_variables()
    for _ in range(len(variables) + 1):
        expanded = body
        for name, value in variables.items():
            expanded = expanded.replace(f"$({name})", value)
        if expanded == body:
            return expanded
        body = expanded
    return body


def test_makefile_targets_reference_existing_files() -> None:
    body = resolve_makefile()
    for path in (
        "src/core",
        "src/ui",
        "src/main.py",
        "packaging/yandex-music-native.desktop",
        "packaging/yandex-music-native.sh",
        "packaging/yandex-music-native.metainfo.xml",
        "packaging/dbus/yandex-music-native.service",
        "packaging/icons/hicolor/scalable/apps/yandex-music-native.svg",
    ):
        assert path in body, f"Makefile does not mention {path}"
    tokens = set(re.findall(r"(?:src|packaging|tests|scripts)/[A-Za-z0-9_./-]+", body))
    assert tokens
    missing = sorted(token for token in tokens if not list(ROOT.glob(token)))
    assert missing == [], f"Makefile points at missing paths: {missing}"
    assert "tests/audio_pipeline.py" not in body
    assert "tests/mpris_selftest.py" in body
    assert "ruff format --check" in body


def test_tests_only_use_the_new_stack() -> None:
    for path in sorted((ROOT / "tests").glob("*.py")):
        source = path.read_text("utf-8")
        assert re.search(r"^\s*(?:from|import)\s+.*" + LEGACY, source, re.MULTILINE) is None, path.name
    assert not (ROOT / "tests" / "audio_pipeline.py").exists()
    assert not (ROOT / "tests" / "smoke.py").exists()


# -- native RPM (Fedora / RHEL / openSUSE) ------------------------------------


@pytest.fixture(scope="module")
def spec() -> str:
    return text(SPEC)


def test_rpm_spec_is_packaged_and_versioned(spec: str, pyproject: dict) -> None:
    assert (ROOT / SPEC).is_file()
    assert "Name:           yandex-music-native" in spec
    assert f"Version:        {pyproject['project']['version']}" in spec
    assert "License:        MIT" in spec
    assert "URL:            https://github.com/arcticlore/yandex-music-native" in spec
    assert "BuildArch:      noarch" in spec, "the application is pure Python"
    assert "Release:" in spec and "%{?dist}" in spec
    assert "%changelog" in spec


def test_rpm_requires_the_runtime_dependencies(spec: str) -> None:
    required = re.findall(r"^Requires:\s+(.+)$", spec, re.MULTILINE)
    for package in ("python3-pyside6", "python3-numpy", "python3-requests", "pulseaudio-utils"):
        assert any(package in line for line in required), f"{package} is not required"
    assert any("python3 >=" in line for line in required), "the interpreter floor is not required"
    libmpv = [line for line in required if "mpv-libs" in line]
    assert libmpv, "libmpv is not required"
    assert " or " in libmpv[0] and "libmpv" in libmpv[0], "libmpv must resolve on Fedora and openSUSE"
    # nothing in the core/ui stack may be a hard build requirement: it is not compiled
    for line in re.findall(r"^BuildRequires:\s+(.+)$", spec, re.MULTILINE):
        assert "python3" in line, f"unexpected build dependency: {line}"
    assert "gcc" not in spec and "cmake" not in spec and "make" not in spec


def test_rpm_sections_install_the_freedesktop_layout(spec: str) -> None:
    for section in ("%prep", "%install", "%files", "%check"):
        assert f"\n{section}" in spec, f"{section} is missing"
    assert "%autosetup -n %{name}-%{version}" in spec
    for path in (
        "%{buildroot}%{_datadir}/%{name}",
        "%{buildroot}%{_bindir}",
        "%{_datadir}/applications/%{name}.desktop",
        "%{_datadir}/icons/hicolor/scalable/apps/%{name}.svg",
        "%{buildroot}%{_datadir}/dbus-1/services",
        "%{buildroot}%{_datadir}/metainfo",
    ):
        assert path in spec, f"{path} is not installed"
    for path in ("%{_bindir}/%{name}", "%{_datadir}/%{name}/main.py", "%license LICENSE", "%doc README.md"):
        assert path in spec, f"{path} is not packaged"
    # a noarch package must not write into /usr/lib
    assert "%{buildroot}%{_libdir}" not in spec
    assert "%{python3_sitelib}" not in spec
    # the D-Bus activation has to point at the installed launcher
    assert "s|/usr/local/bin/%{name}|%{_bindir}/%{name}|" in spec
    assert "__pycache__" in spec, "byte-code caches must be pruned"


def test_rpm_sources_exist_in_the_repository(spec: str) -> None:
    referenced = set(re.findall(r"(?:src|packaging)/[A-Za-z0-9_./-]+", spec))
    assert referenced
    missing = sorted(path for path in referenced if not list(ROOT.glob(path)))
    assert missing == [], f"the spec references missing sources: {missing}"
    assert "/home/" not in spec and "samsa" not in spec


def test_rpm_build_script_is_runnable() -> None:
    script = ROOT / RPM_SCRIPT
    assert script.is_file()
    assert os.access(script, os.X_OK), "scripts/build-rpm.sh is not executable"
    body = script.read_text("utf-8")
    assert body.startswith("#!/usr/bin/env bash")
    assert "rpmbuild" in body
    assert "packaging/rpm" in body, "the script must find the spec"
    assert '"${ROOT}/src"' in body and '"${ROOT}/packaging"' in body, "the tree is staged as it is"
    assert "__pycache__" in body, "the source tarball must not carry byte-code caches"
    result = subprocess.run(["bash", "-n", str(script)], capture_output=True, text=True)
    assert result.returncode == 0, result.stderr
    assert "build-rpm" in resolve_makefile()


def workflow_job(name: str) -> str:
    body = text(".github/workflows/release.yml")
    match = re.search(rf"^  {re.escape(name)}:\n.*?(?=^  [a-z_-]+:\n|\Z)", body, re.MULTILINE | re.DOTALL)
    assert match, f"release.yml has no {name} job"
    return match.group(0)


def test_rpm_is_built_and_attached_by_the_release_workflow() -> None:
    rpm = workflow_job("rpm")
    assert "needs: test" in rpm
    assert re.search(r"^\s+container: fedora", rpm, re.MULTILINE), "rpmbuild needs an RPM distribution"
    assert "rpm-build" in rpm, "the container must install the rpm tooling"
    assert RPM_SCRIPT in rpm
    assert re.search(r"name: rpm-package\s+path: dist/\*\.rpm", rpm), rpm
    release = workflow_job("release")
    needs = re.search(r"needs: \[([^\]]+)\]", release)
    assert needs and "rpm" in [item.strip() for item in needs.group(1).split(",")]
    assert "*.rpm" in release, "the RPM never reaches the GitHub Release"


def test_readme_documents_the_rpm_channel() -> None:
    for readme in ("README.md", "README.en.md"):
        body = text(readme)
        assert "make build-rpm" in body, readme
        assert "dnf install ./yandex-music-native" in body, readme
        assert "zypper install" in body, readme
    assert "packaging/rpm/yandex-music-native.spec" in text("README.md")
    assert "packaging/rpm/yandex-music-native.spec" in text("README.en.md")
