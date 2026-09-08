"""Measure native NVDA-driver replacement speech at a fixed navigation cadence.

Uses the repository's NVDA test environment with the real engine host. Reports
first nonquiet PCM delivered to the simulated player, not physical sound.
No NVDA settings are changed. Engine data stays in the caller's local tree.
"""
import argparse
import importlib
import importlib.util
import json
import os
from pathlib import Path
import threading
import time

ROOT = Path(__file__).resolve().parent.parent


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--generation", choices=["leopard", "lion"], required=True)
    parser.add_argument("--tree", type=Path, required=True)
    parser.add_argument("--host", type=Path,
                        help="Use an isolated host executable without replacing the staged build")
    parser.add_argument("--interval-ms", type=float, default=150)
    parser.add_argument("--requests", type=int, default=16)
    parser.add_argument("--posts", type=Path,
                        default=ROOT / "tools/fixtures/navigation-posts.json")
    parser.add_argument("--host-timing", action="store_true")
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    if args.interval_ms <= 0 or args.requests < 3:
        parser.error("use a positive interval and at least three requests")
    os.environ[args.generation.upper() + "_TREE"] = str(args.tree.resolve())
    if args.host_timing:
        os.environ["TIGER_CANCEL_TRACE"] = "1"
    spec = importlib.util.spec_from_file_location("navigation_nvda_fakes", ROOT / "panthera/tests/conftest.py")
    fakes = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(fakes)
    module = importlib.import_module("synthDrivers." + args.generation + "speech")
    if args.host:
        host = args.host.resolve(strict=True)
        module.SynthDriver.TREE.HOST_EXE = str(host)
    driver = module.SynthDriver()
    records = []
    lock = threading.Lock()
    epochs = {}
    events = []
    origin = time.perf_counter()
    original_feed = driver._player.feed
    original_render = driver._render

    def event(kind, **details):
        with lock:
            events.append(dict(event=kind, at_ms=(time.perf_counter() - origin) * 1000,
                               epoch=driver._epoch, **details))

    def render(text, *args, **kwargs):
        epoch = driver._epoch
        event("render_start", render_epoch=epoch, characters=len(text))
        try:
            return original_render(text, *args, **kwargs)
        finally:
            event("render_end", render_epoch=epoch)

    def feed(data):
        # The production feeder holds its player lock and rechecks the epoch
        # before this call. Cancellation cannot relabel stale audio here.
        import array
        samples = array.array("h", data)
        if any(abs(v) > 128 for v in samples):
            now = time.perf_counter()
            with lock:
                row = epochs.get(driver._epoch)
                if row is not None and row["first_pcm_ms"] is None:
                    row["first_pcm_ms"] = (now - row["sent"]) * 1000
        original_feed(data)

    try:
        voice = next(v[0] for v in driver._voices if v[0].lower().startswith("alex"))
        driver._set_voice(voice)
        driver._set_rate(50)
        driver._player.feed = feed
        driver._render = render
        posts = json.loads(args.posts.read_text(encoding="utf8"))
        if not isinstance(posts, list) or len(posts) < 2 or not all(
                isinstance(post, str) and post.strip() for post in posts):
            raise ValueError("Posts must be a JSON array of at least two nonempty strings")
        driver.speak(["Seven"])
        deadline = time.monotonic() + 20
        while driver._player.fed == 0 and time.monotonic() < deadline:
            time.sleep(.005)
        if not driver._player.fed:
            raise RuntimeError("Warm-up produced no audio")
        driver.cancel()
        time.sleep(.5)
        for i in range(args.requests):
            started = time.perf_counter()
            driver.cancel()
            event("cancel", request=i)
            row = {"request": i, "epoch": driver._epoch, "sent": started,
                   "post": i % len(posts), "characters": len(posts[i % len(posts)]),
                   "cancel_ms": (time.perf_counter() - started) * 1000,
                   "first_pcm_ms": None}
            with lock:
                epochs[driver._epoch] = row
                records.append(row)
            driver.speak([posts[i % len(posts)]])
            remaining = args.interval_ms / 1000 - (time.perf_counter() - started)
            if remaining > 0:
                time.sleep(remaining)
        deadline = time.monotonic() + 10
        while records[-1]["first_pcm_ms"] is None and time.monotonic() < deadline:
            time.sleep(.005)
    finally:
        driver.cancel()
        driver.terminate()
        for row in records:
            row.pop("sent", None)
            row["before_next_request"] = (row["first_pcm_ms"] is not None
                                           and row["first_pcm_ms"] < args.interval_ms)
        result = {"generation": args.generation, "interval_ms": args.interval_ms,
                  "host": str(Path(driver.TREE.HOST_EXE).resolve()),
                  "posts": str(args.posts), "host_mode": "library" if driver._useLibrary() else "process",
                  "measurement": "first nonquiet PCM at simulated player; not acoustic latency",
                  "requests": records, "events": events}
        if args.host_timing:
            from logHandler import log
            result["host_timings"] = [message for _, message in log.messages
                                      if "[cancel]" in message]
        args.output.parent.mkdir(parents=True, exist_ok=True)
        args.output.write_text(json.dumps(result, indent=2), encoding="utf8")
        print(json.dumps(result, indent=2))
    if not records or records[-1]["first_pcm_ms"] is None:
        raise RuntimeError("Final replacement produced no audio")


if __name__ == "__main__":
    main()
