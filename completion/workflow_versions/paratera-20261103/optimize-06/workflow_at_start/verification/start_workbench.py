"""Start an existing installation detached, with logs on F: rather than C:."""
import argparse
import subprocess
import sys
from pathlib import Path

def main():
    parser=argparse.ArgumentParser()
    parser.add_argument("--port",type=int,default=8000)
    args=parser.parse_args()
    root=Path(__file__).resolve().parents[1]
    log=Path(f"/mnt/f/AI_EDA/workbench-{args.port}.log")
    log.parent.mkdir(parents=True,exist_ok=True)
    with log.open("a") as output:
        child=subprocess.Popen([sys.executable,"-m","ai_eda","serve","--host","127.0.0.1","--port",str(args.port)],
            cwd=root,stdout=output,stderr=subprocess.STDOUT,stdin=subprocess.DEVNULL,start_new_session=True)
    print(child.pid)

if __name__=="__main__":main()
