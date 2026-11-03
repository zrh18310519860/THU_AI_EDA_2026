"""Package the verified fresh experiment, report and source without installed software."""

import argparse
import hashlib
import json
import re
import subprocess
from pathlib import Path
from zipfile import ZIP_DEFLATED, ZipFile

from verification.package_submission import safe


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--completion", type=Path, required=True)
    parser.add_argument("--report", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    data = json.loads((args.completion / "completion.json").read_text())
    selected = next(item for item in data["records"] if item["run_id"] == data["selected_run"])
    assert selected["status"] == "passed" and selected["generation_mode"] == "fresh"
    assert data["independent_retest"]["passed"] and data["independent_retest"]["ppa_passed"]
    source = Path("/mnt/d/大三/人工智能赋能数字电路设计/THU_AI_EDA_2026")
    deployed = Path("/home/eda/projects/THU_AI_EDA_2026")
    files = {}
    tracked = subprocess.check_output(["git", "ls-files", "-z"], cwd=source).decode().split("\0")
    for name in tracked:
        path = source / name
        if name and path.is_file() and safe(Path(name)):
            files["THU_AI_EDA_2026/" + name] = path
    for prefix in ("ai_eda", "verification", "content"):
        for path in (source / prefix).rglob("*"):
            relative = path.relative_to(source)
            if path.is_file() and safe(relative):
                files["THU_AI_EDA_2026/" + relative.as_posix()] = path
    for item in data["records"]:
        root = deployed / "runs/agent" / item["run_id"]
        for path in root.rglob("*"):
            relative = path.relative_to(deployed)
            if path.is_file() and safe(relative):
                files["THU_AI_EDA_2026/" + relative.as_posix()] = path
    aes_runs = deployed / "runs/prompt/homework_aes128"
    for path in aes_runs.rglob("*"):
        relative = path.relative_to(deployed)
        if path.is_file() and safe(relative):
            files["THU_AI_EDA_2026/" + relative.as_posix()] = path
    for folder, prefix in (("report-evidence-20261027", "AES-original-evidence"),
                           ("stability-evidence-20261027", "AES-optimization-and-historical-evidence")):
        aes_evidence = Path("/mnt/f/AI_EDA") / folder
        for path in aes_evidence.rglob("*"):
            relative = path.relative_to(aes_evidence)
            if path.is_file() and safe(relative) and "report-qa" not in relative.parts:
                files[prefix + "/" + relative.as_posix()] = path
    for path in args.completion.rglob("*"):
        relative = path.relative_to(args.completion)
        if path.is_file() and safe(relative) and "report-qa" not in relative.parts:
            files["completion/" + relative.as_posix()] = path
    files["report/" + args.report.name] = args.report
    credential = re.compile(rb"sk-[A-Za-z0-9_-]{20,}")
    manifest = {}
    for name, path in sorted(files.items()):
        content = path.read_bytes()
        assert not credential.search(content), "Credential-like content in " + name
        if path.suffix.lower() == ".docx":
            with ZipFile(path) as document:
                assert not any(credential.search(document.read(part)) for part in document.namelist()
                               if part.endswith(".xml")), "Credential-like document content"
        manifest[name] = hashlib.sha256(content).hexdigest()
    args.output.parent.mkdir(parents=True, exist_ok=True)
    with ZipFile(args.output, "w", compression=ZIP_DEFLATED, compresslevel=6) as archive:
        for name, path in sorted(files.items()):
            archive.write(path, name)
        archive.writestr("manifest-sha256.json", json.dumps(manifest, ensure_ascii=False, indent=2))
    with ZipFile(args.output) as archive:
        assert archive.testzip() is None
        for name, expected in manifest.items():
            value = hashlib.sha256()
            with archive.open(name) as stream:
                for block in iter(lambda: stream.read(1024 * 1024), b""):
                    value.update(block)
            assert value.hexdigest() == expected, name
    print(json.dumps({"output": str(args.output), "files": len(files), "bytes": args.output.stat().st_size}))


if __name__ == "__main__":
    main()
