#!/usr/bin/env python3
"""Keep the three private demo interfaces available on localhost."""
import signal
import socket
import subprocess
import sys
import time

from common import KUBE

INTERFACES = [
    ("Chat", "agents", "open-webui", 3000, 8080),
    ("Grafana", "observability", "monitoring-grafana", 3001, 80),
    ("Gateway", "intentlatch-system", "intentlatch-gateway", 8080, 8080),
]


def main():
    processes = []
    try:
        for _, _, _, port, _ in INTERFACES:
            with socket.socket() as sock:
                sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
                sock.bind(("127.0.0.1", port))
        for name, namespace, service, port, remote in INTERFACES:
            process = subprocess.Popen([
                *KUBE, "-n", namespace, "port-forward", "--address", "127.0.0.1",
                "svc/" + service, f"{port}:{remote}",
            ], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
            processes.append(process)
            for _ in range(100):
                if process.poll() is not None:
                    raise RuntimeError(f"{name} port-forward exited")
                try:
                    with socket.create_connection(("127.0.0.1", port), timeout=0.2):
                        break
                except OSError:
                    time.sleep(0.2)
            else:
                raise RuntimeError(f"{name} port-forward did not become ready")
            print(f"{name}: http://localhost:{port}", flush=True)
        print("Keep this process running. Ctrl+C closes all three forwards.", flush=True)
        while all(process.poll() is None for process in processes):
            time.sleep(1)
        raise RuntimeError("A port-forward disconnected")
    finally:
        for process in processes:
            if process.poll() is None:
                process.terminate()
        for process in processes:
            try:
                process.wait(timeout=5)
            except subprocess.TimeoutExpired:
                process.kill()
                process.wait()


if __name__ == "__main__":
    signal.signal(signal.SIGTERM, lambda *_: sys.exit(0))
    try:
        while True:
            try:
                main()
            except RuntimeError as error:
                print(f"{error}. Reconnecting in 3 seconds.", flush=True)
                time.sleep(3)
    except KeyboardInterrupt:
        pass
    except OSError as error:
        sys.exit(str(error))
