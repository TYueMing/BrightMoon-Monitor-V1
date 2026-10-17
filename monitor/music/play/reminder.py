"""Cancellable three-play reminders, serialized independently of completion cues."""
from concurrent.futures import ThreadPoolExecutor
from threading import Event, Lock
import os

from .catalog import sound_files
from .preview import SoundPreview


class ReminderSoundPlayer:
    def __init__(self):
        self.pool = ThreadPoolExecutor(max_workers=1, thread_name_prefix="solo-reminder-sound")
        self.events = {}
        self.lock = Lock()
        self.closed = False

    def play(self, key, sound):
        path = sound_files().get(sound)
        with self.lock:
            if self.closed or key in self.events or not path:
                return False
            event = self.events[key] = Event()
            self.pool.submit(self._play, key, path, event)
        return True

    def _play(self, key, path, cancel):
        backend = SoundPreview()
        try:
            for index in range(3):
                if cancel.is_set():
                    break
                if os.name == "nt":
                    backend._windows(path, cancel)
                else:
                    backend._external(path, cancel)
                if index < 2 and cancel.wait(.2):
                    break
        except (OSError, RuntimeError, AttributeError):
            pass  # Audio failure must not affect a saved reminder or the UI.
        finally:
            with self.lock:
                self.events.pop(key, None)

    def retain(self, keys):
        with self.lock:
            for key, event in self.events.items():
                if key not in keys:
                    event.set()

    def close(self):
        with self.lock:
            self.closed = True
            for event in self.events.values():
                event.set()
        self.pool.shutdown(wait=False, cancel_futures=True)
