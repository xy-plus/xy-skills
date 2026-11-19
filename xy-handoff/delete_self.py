"""claude rm 先让守护进程停会话，再删登记。会话退出会杀掉工具命令的整棵子孙进程树。
双重 fork 让删除孙进程先被 init 收养；结果经管道返回，读到末尾说明孙进程已结束。
删成功时本会话连同本脚本先被停掉，不会有输出。脚本还能输出，就是没删成（或已删但会话还没停）。
"""

import json
import os
import signal
import subprocess
import sys
import time


def ancestor_process_ids():
    ancestor_ids = set()
    process_id = os.getpid()
    while process_id > 1:
        with open(f"/proc/{process_id}/stat") as stat_file:
            process_id = int(stat_file.read().rsplit(")", 1)[1].split()[1])
        ancestor_ids.add(process_id)
    return ancestor_ids


def identify_session():
    agent_result = subprocess.run(
        ["claude", "agents", "--json"],
        capture_output=True,
        text=True,
        check=True,
    )
    agent_rows = json.loads(agent_result.stdout)
    matching_rows = [
        row for row in agent_rows
        if row.get("kind") == "background"
        and row.get("pid") in ancestor_process_ids()
    ]
    if len(matching_rows) != 1:
        raise ValueError(f"匹配到 {len(matching_rows)} 个本会话")
    return matching_rows[0]["id"]


def run_removal_grandchild(write_end, session_id):
    try:
        os.setsid()
        intermediate_pid = os.getpid()
        if os.fork() != 0:
            os._exit(0)
    except OSError:
        os._exit(1)

    while os.getppid() == intermediate_pid:
        time.sleep(0.01)
    null_descriptor = os.open(os.devnull, os.O_RDWR)
    for descriptor in (0, 1, 2):
        os.dup2(null_descriptor, descriptor)
    signal.signal(signal.SIGPIPE, signal.SIG_IGN)
    removal = subprocess.run(
        ["claude", "rm", session_id],
        stdin=subprocess.DEVNULL,
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
    )
    try:
        result = f"{removal.returncode}\n".encode() + removal.stdout
        os.write(write_end, result)
    except OSError:
        pass
    os._exit(0)


def read_and_report(read_end, child_pid, session_id):
    os.waitpid(child_pid, 0)
    chunks = []
    while True:
        chunk = os.read(read_end, 65536)
        if not chunk:
            break
        chunks.append(chunk)
    data = b"".join(chunks)
    if not data:
        print(f"claude rm {session_id} 没送回结果，不知道删没删成", file=sys.stderr)
        return 1

    return_code, _, output = data.partition(b"\n")
    sys.stdout.write(output.decode("utf-8", "replace"))
    if return_code == b"0":
        return 0
    print(
        f"claude rm {session_id} 没删成，退出码 {return_code.decode('ascii', 'replace')}",
        file=sys.stderr,
    )
    return 1


def delete_session(session_id):
    read_end, write_end = os.pipe()
    try:
        child_pid = os.fork()
    except OSError as error:
        os.close(read_end)
        os.close(write_end)
        print(f"无法启动脱离进程：{error}", file=sys.stderr)
        return 1

    if child_pid == 0:
        os.close(read_end)
        run_removal_grandchild(write_end, session_id)
    os.close(write_end)
    return read_and_report(read_end, child_pid, session_id)


def main():
    if len(sys.argv) != 1:
        print("delete_self.py 不接受参数", file=sys.stderr)
        return 2
    try:
        session_id = identify_session()
    except Exception as error:
        print(f"无法确认本会话，未运行 claude rm：{error}", file=sys.stderr)
        return 2
    return delete_session(session_id)


if __name__ == "__main__":
    raise SystemExit(main())
