#!/usr/bin/env python3
"""Offline sdist -> wheel -> isolated non-editable install acceptance.

Run with the project's dependency environment. Uses a separate system-site build
venv for already-installed setuptools/wheel, and a fresh runtime venv. Runtime
third-party distributions are copied from this interpreter's installed metadata
(no dependency download/resolution). Never install/uninstall in the working venv.
Outputs are retained under out/r2-hardening for audit; no source cleanup/deletion.
"""
from __future__ import annotations

from datetime import datetime, timezone
import hashlib
from importlib import metadata
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
import tarfile
import zipfile

ROOT = Path(__file__).resolve().parents[1]


def main() -> int:
    run_id = datetime.now(timezone.utc).strftime("package-%Y%m%dT%H%M%S%fZ")
    out = ROOT / "out" / "r2-hardening" / run_id
    out.mkdir(parents=True, exist_ok=False)
    log_path = out / "check.log"
    with log_path.open("w", encoding="utf-8") as log:
        def run(args, *, cwd=None, env=None, timeout=180):
            log.write("$ " + " ".join(map(str, args)) + "\n")
            log.flush()
            p = subprocess.run(list(map(str, args)), cwd=cwd, env=env,
                               stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
                               text=True, timeout=timeout)
            log.write(p.stdout)
            log.write(f"\n[exit code: {p.returncode}]\n")
            log.flush()
            if p.returncode:
                raise RuntimeError(f"offline install command failed; see {log_path}")
            return p.stdout

        snapshot = out / "source"
        snapshot.mkdir()
        shutil.copytree(ROOT / "qswarm", snapshot / "qswarm",
                        ignore=shutil.ignore_patterns("__pycache__", "*.pyc"))
        for name in ("pyproject.toml", "README.md"):
            shutil.copy2(ROOT / name, snapshot / name)
        source_hashes = {str(p.relative_to(snapshot)): hashlib.sha256(p.read_bytes()).hexdigest()
                         for p in sorted(snapshot.rglob("*")) if p.is_file()}
        build_env, runtime_env = out / "build-env", out / "runtime-env"
        run(["/usr/bin/python3", "-m", "venv", "--system-site-packages", build_env])
        run(["/usr/bin/python3", "-m", "venv", runtime_env])
        build_py, runtime_py = build_env / "bin/python", runtime_env / "bin/python"
        runtime_site = Path(run([runtime_py, "-c", "import sysconfig; print(sysconfig.get_paths()['purelib'])"]).strip())
        if not runtime_site.resolve().is_relative_to(runtime_env.resolve()):
            raise RuntimeError("runtime dependency destination escaped isolated environment")
        dependencies = {}
        for name in ("pydantic", "pydantic_core", "annotated_types", "typing_extensions", "typing_inspection", "PyYAML"):
            dist = metadata.distribution(name)
            dependencies[name] = dist.version
            for entry in dist.files or ():
                rel = Path(str(entry))
                if rel.is_absolute() or ".." in rel.parts or rel.suffix == ".pyc":
                    continue
                src = Path(dist.locate_file(entry))
                if src.is_symlink():
                    raise RuntimeError(f"dependency symlink not supported: {name}")
                if not src.is_file():
                    continue
                target = runtime_site / rel
                target.parent.mkdir(parents=True, exist_ok=True)
                shutil.copy2(src, target)
        dist_dir = out / "dist"
        dist_dir.mkdir()
        run([build_py, "-c", f"from setuptools import build_meta; print(build_meta.build_sdist({str(dist_dir)!r}))"], cwd=snapshot)
        sdists = list(dist_dir.glob("*.tar.gz"))
        if len(sdists) != 1:
            raise RuntimeError("expected exactly one sdist")
        extracted = out / "sdist-source"
        extracted.mkdir()
        with tarfile.open(sdists[0]) as archive:
            archive.extractall(extracted, filter="data")
        roots = [p for p in extracted.iterdir() if p.is_dir()]
        if len(roots) != 1:
            raise RuntimeError("unexpected sdist layout")
        run([build_py, "-c", f"from setuptools import build_meta; print(build_meta.build_wheel({str(dist_dir)!r}))"], cwd=roots[0])
        wheels = list(dist_dir.glob("*.whl"))
        if len(wheels) != 1:
            raise RuntimeError("expected exactly one wheel")
        with zipfile.ZipFile(wheels[0]) as wheel:
            names = wheel.namelist()
            for required in ("qswarm/cli.py", "qswarm/__main__.py", "qswarm/kernel/trust.py",
                             "qswarm/exams/store.py", "qswarm/sandbox/runner.py",
                             "qswarm/defaults/params.yaml", "qswarm/defaults/semgrep_rules.yaml",
                             "qswarm/contracts/schema.sql"):
                if required not in names:
                    raise RuntimeError(f"wheel missing runtime resource: {required}")
            forbidden = [n for n in names if any(part in {"out", "seeds", "tests", "__pycache__", "experiments"}
                                                 for part in Path(n).parts)
                         or n.endswith((".db", ".sqlite", ".pem", ".key")) or ".zhipu_key" in n]
            if forbidden:
                raise RuntimeError("private/internal artifacts in wheel")
            entry_points = [n for n in names if n.endswith(".dist-info/entry_points.txt")]
            if len(entry_points) != 1 or b"qswarm = qswarm.cli:main" not in wheel.read(entry_points[0]):
                raise RuntimeError("qswarm console entry point missing")
        run([runtime_py, "-m", "pip", "install", "--no-index", "--no-deps", wheels[0]])
        unrelated = out / "unrelated-cwd"
        unrelated.mkdir()
        env = {k: v for k, v in os.environ.items() if k not in {"PYTHONPATH", "PYTHONHOME"}}
        smoke = '''
from pathlib import Path
import qswarm
assert Path(qswarm.__file__).resolve().is_relative_to(Path(RUNTIME).resolve())
import qswarm.exams, qswarm.prompts, qswarm.supply, qswarm.sandbox, qswarm.kernel
from qswarm.contracts import load_params, load_taxonomy, schema_sql
assert load_params().sandbox.tier == 'process'
assert load_taxonomy() is not None
assert 'CREATE TABLE' in schema_sql()
from qswarm.sandbox.fixtures import FIXTURES_ROOT
assert FIXTURES_ROOT.is_dir()
from qswarm.sandbox import FakeRunner
assert FakeRunner(script=True).execute('print(1)', repo=FIXTURES_ROOT, deps=[], seed='installed').ok
print('NON_EDITABLE_RUNTIME_SMOKE_PASS', qswarm.__file__)
'''
        run([runtime_py, "-c", "RUNTIME=" + repr(str(runtime_env)) + "\n" + smoke], cwd=unrelated, env=env)
        doctor = json.loads(run([runtime_env / "bin/qswarm", "doctor"], cwd=unrelated, env=env))
        if doctor.get("model_calls") != 0:
            raise RuntimeError("installed doctor did not preserve offline behavior")
        manifest = {
            "status": "PASS", "source_hashes": source_hashes,
            "runtime_dependencies": dependencies,
            "dependency_strategy": "offline copy of installed third-party distribution files; no fresh resolution",
            "sdist_sha256": hashlib.sha256(sdists[0].read_bytes()).hexdigest(),
            "wheel_sha256": hashlib.sha256(wheels[0].read_bytes()).hexdigest(),
            "wheel": str(wheels[0].relative_to(ROOT)),
            "model_calls": 0, "primary_venv_modified": False,
        }
        (out / "manifest.json").write_text(json.dumps(manifest, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
        log.write("PASS: isolated non-editable offline install\n")
    print(f"PASS: {out.relative_to(ROOT)}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
