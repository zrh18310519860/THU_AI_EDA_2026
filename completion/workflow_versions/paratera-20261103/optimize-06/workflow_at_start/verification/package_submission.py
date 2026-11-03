"""Package source and saved evidence without credentials or installed tools."""

import hashlib
import argparse
import json
import re
import subprocess
from pathlib import Path
from zipfile import ZIP_DEFLATED, ZipFile


SOURCE = Path("/mnt/d/大三/人工智能赋能数字电路设计/THU_AI_EDA_2026")
DEPLOYED = Path("/home/eda/projects/THU_AI_EDA_2026")
OUTPUT = Path("/mnt/f/AI_EDA/AI_EDA_记录功能与AES补跑_20261027.zip")
EVIDENCE = OUTPUT.parent / "report-evidence-20261027"
REPORT = SOURCE.parent / "作业提交要求与报告模板_AES128补跑记录.docx"


def safe(path):
    return not any(
        part in {".git", ".venv", "__pycache__", "node_modules", ".api_key"}
        or part.startswith((".env", "~$"))
        or part.endswith((".pyc", ".pem", ".key"))
        for part in path.parts
    )


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--l3", action="store_true")
    parser.add_argument("--stability", action="store_true")
    args = parser.parse_args()
    output = OUTPUT.parent / "AI_EDA_L3智能体与实验记录_20261027.zip" if args.l3 else OUTPUT
    report = SOURCE.parent / "作业提交要求与报告模板_L3实验记录.docx" if args.l3 else REPORT
    if args.stability:
        output = OUTPUT.parent / "AI_EDA_稳定性复核与PPA记录_20261027.zip"
        report = SOURCE.parent / "作业提交要求与报告模板_稳定性复核版.docx"
    tracked = subprocess.check_output(["git", "ls-files", "-z"], cwd=SOURCE).decode().split("\0")
    files = {}
    for name in tracked:
        if name and safe(Path(name)) and (SOURCE / name).is_file():
            files["THU_AI_EDA_2026/" + name] = SOURCE / name
    for prefix in ("ai_eda", "verification", "content"):
        target = SOURCE / prefix
        for path in target.rglob("*") if target.is_dir() else [target]:
            relative = path.relative_to(SOURCE)
            if path.is_file() and safe(relative):
                files["THU_AI_EDA_2026/" + relative.as_posix()] = path
    for prefix in ("runs/prompt/homework_aes128", "runs/agent"):
        for path in (DEPLOYED / prefix).rglob("*"):
            relative = path.relative_to(DEPLOYED)
            if path.is_file() and safe(relative):
                files["THU_AI_EDA_2026/" + relative.as_posix()] = path
    for path in EVIDENCE.rglob("*"):
        if path.is_file() and safe(path.relative_to(EVIDENCE)):
            files["evidence/" + path.relative_to(EVIDENCE).as_posix()] = path
    if args.l3:
        for path in (OUTPUT.parent / "L3-evidence-20261027").rglob("*"):
            if path.is_file() and 'report-qa' not in path.relative_to(OUTPUT.parent).parts and safe(path.relative_to(OUTPUT.parent)):
                files["L3-evidence/" + path.relative_to(OUTPUT.parent / "L3-evidence-20261027").as_posix()] = path
    if args.stability:
        evidence = OUTPUT.parent / "stability-evidence-20261027"
        for path in evidence.rglob('*'):
            relative = path.relative_to(evidence)
            if path.is_file() and 'report-qa' not in relative.parts and safe(relative):
                files['stability-evidence/' + relative.as_posix()] = path
    files["report/" + report.name] = report
    if args.stability:
        for name in ('作业提交要求与报告模板_AES128补跑记录.docx','作业提交要求与报告模板_L3实验记录.docx'):
            files['historical-reports/'+name]=SOURCE.parent/name

    # Detect accidental embedded credentials without printing any matched text.
    for name, path in files.items():
        if re.search(rb"sk-[A-Za-z0-9_-]{20,}", path.read_bytes()):
            raise RuntimeError("Credential-like value detected in " + name)
        if path.suffix.lower() == '.docx':
            with ZipFile(path) as document:
                for part in document.namelist():
                    if part.endswith('.xml') and re.search(rb"sk-[A-Za-z0-9_-]{20,}", document.read(part)):
                        raise RuntimeError("Credential-like value detected in " + name)
    manifest = {name: hashlib.sha256(path.read_bytes()).hexdigest() for name, path in sorted(files.items())}
    with ZipFile(output, "w", compression=ZIP_DEFLATED, compresslevel=6) as archive:
        for name, path in sorted(files.items()):
            archive.write(path, name)
        archive.writestr("manifest-sha256.json", json.dumps(manifest, ensure_ascii=False, indent=2) + "\n")
    with ZipFile(output) as archive:
        assert archive.testzip() is None
        assert all(safe(Path(name)) for name in archive.namelist())
        for name, expected in manifest.items():
            digest = hashlib.sha256()
            with archive.open(name) as stream:
                for block in iter(lambda: stream.read(1024 * 1024), b''):
                    digest.update(block)
            assert digest.hexdigest() == expected, name
    print(json.dumps({"archive": str(output), "files": len(files), "bytes": output.stat().st_size}, ensure_ascii=False))


if __name__ == "__main__":
    main()
