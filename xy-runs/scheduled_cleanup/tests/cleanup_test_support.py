def stop_process(process):
    if process.poll() is None:
        process.terminate()
    process.wait(timeout=5)
    for stream in (process.stdin, process.stdout, process.stderr):
        stream.close()
