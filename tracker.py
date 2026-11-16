"""Demone: campiona ogni poll_secs e scrive in SQLite. Solo stdlib."""
import os
import signal
import time

from collect import WEIGHT_SECS, collect_sample, init_db, write_samples

DB_DEFAULT = os.path.expanduser("~/.usage/usage.db")
BACKOFF_SECS = 60

stop = False


def _handle_sigterm(signum, frame):
    global stop
    stop = True


def should_query_browser(state, now):
    return now >= state.get("until", 0)


def note_denied(state, now, secs=BACKOFF_SECS):
    state["until"] = now + secs


def main(poll_secs=WEIGHT_SECS, db=DB_DEFAULT, max_samples=None):
    signal.signal(signal.SIGTERM, _handle_sigterm)
    signal.signal(signal.SIGINT, _handle_sigterm)
    init_db(db)
    state = {"until": 0}
    n = 0
    while not stop:
        now = int(time.time())
        try:
            s = collect_sample(now, allow_browser=should_query_browser(state, now))
            if s["success"] == "denied_or_error" and s["kind"] == "browser":
                note_denied(state, now)
            write_samples(db, [s])
        except Exception as e:  # shortcut: il loop non muore mai per un campione
            print(f"tracker: sample failed: {e}")
        n += 1
        if max_samples is not None and n >= max_samples:
            break
        time.sleep(poll_secs)


if __name__ == "__main__":
    import sys

    times = int(sys.argv[1]) if len(sys.argv) > 1 else None
    main(max_samples=times)
