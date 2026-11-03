"""Export source and compact immutable evidence into a separate Git checkout."""

import argparse
import hashlib
import json
import re
import shutil
import subprocess
from pathlib import Path
from zipfile import ZipFile

from verification.package_submission import safe


def read(path):
    return json.loads(path.read_text(encoding="utf-8"))


def digest(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def copy_file(source, destination):
    destination.parent.mkdir(parents=True, exist_ok=True)
    shutil.copyfile(source, destination)


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--destination", type=Path, required=True)
    parser.add_argument("--completion", type=Path, required=True)
    parser.add_argument("--deployed", type=Path, required=True)
    args = parser.parse_args()
    source = Path(__file__).resolve().parents[1]
    target = args.destination.resolve()
    assert target != source and (target / ".git").exists(), "Use a separate existing Git clone"
    tracked = subprocess.check_output(["git", "ls-files", "-z"], cwd=source).decode().split("\0")
    names = {name for name in tracked if name and safe(Path(name))}
    for folder in ("ai_eda", "content", "verification"):
        names.update(p.relative_to(source).as_posix() for p in (source / folder).rglob("*")
                     if p.is_file() and safe(p.relative_to(source)))
    names.add("README.md")
    for name in sorted(names):
        if (source / name).is_file():
            copy_file(source / name, target / name)
    for entry in args.completion.rglob("*"):
        relative = entry.relative_to(args.completion)
        if entry.is_file() and safe(relative):
            copy_file(entry, target / "completion" / relative)
    completion = read(args.completion / "completion.json")
    for name, expected in completion["teacher_sha256"].items():
        assert digest(target / name) == expected, "Teacher file changed: " + name
    assert completion["report"]["passed"] and completion["report"]["ppa_passed"]
    assert completion["independent_retest"]["passed"]
    run = args.deployed / "runs/agent" / completion["selected_run"]
    attempt = int(completion["selected_tester"].split("-")[1])
    manifest = read(run / f"artifacts/tested_manifest_{attempt}.json")
    for entry in manifest["files"]:
        relative = Path(entry["path"])
        assert relative.parts[0] == "rtl" and ".." not in relative.parts
        origin = run / entry["artifact"]
        assert digest(origin) == entry["sha256"]
        copy_file(origin, target / "submission/l3" / relative)
    copy_file(run / f"artifacts/tested_manifest_{attempt}.json", target / "submission/l3/source_manifest.json")
    copy_file(run / completion["selected_tester"] / "test_report.json", target / "submission/l3/test_report.json")
    copy_file(run / completion["independent_report"], target / "submission/l3/independent_test_report.json")
    copy_file(run / "artifacts/candidate_selection.json", target / "submission/l3/candidate_selection.json")
    aes = args.deployed / "runs/prompt/homework_aes128/hw-20261027T152441661029"
    metadata = read(aes / "metadata.json")
    assert digest(aes / "generated.sv") == metadata["rtl_sha256"]
    for name in ("generated.sv", "prompt.md", "metadata.json", "test_report.json", "run_summary.json"):
        copy_file(aes / name, target / "submission/aes" / name)
    for entry in (aes / "oracle").iterdir():
        if entry.is_file():
            copy_file(entry, target / "submission/aes/oracle" / entry.name)
    credential = re.compile(rb"sk-[A-Za-z0-9_-]{20,}|-----BEGIN (?:RSA |OPENSSH |EC )?PRIVATE KEY-----")
    exported = {}
    for entry in sorted(target.rglob("*")):
        relative = entry.relative_to(target)
        if not entry.is_file() or ".git" in relative.parts or not safe(relative):
            continue
        content = entry.read_bytes()
        assert len(content) < 95 * 1024 * 1024, "File too large for normal Git: " + str(relative)
        assert not credential.search(content), "Credential-like content: " + str(relative)
        if entry.suffix == ".docx":
            with ZipFile(entry) as archive:
                assert not any(credential.search(archive.read(p)) for p in archive.namelist() if p.endswith(".xml"))
        exported[relative.as_posix()] = hashlib.sha256(content).hexdigest()
    print(json.dumps({"destination": str(target), "files": len(exported),
                      "credential_scan": "passed", "teacher_hashes": "unchanged",
                      "l3_modules": len(manifest["files"]), "aes_hash": metadata["rtl_sha256"]}))


if __name__ == "__main__":
    main()
