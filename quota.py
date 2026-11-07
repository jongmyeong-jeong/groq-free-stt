"""Conservative local request accounting for one sequential transcription worker."""

import json
import math
import os
import tempfile
import time
from pathlib import Path


class QuotaLedger:
    # Rolling windows avoid assuming Groq's unpublished reset algorithm.
    LIMITS = ((60, 20, None), (86400, 2000, None),
              (3600, None, 7200), (86400, None, 28800))

    def __init__(self, path, clock=time.time):
        self.path = Path(path)
        self.clock = clock
        self.events = json.loads(self.path.read_text()) if self.path.exists() else []

    def reserve(self, duration):
        if not math.isfinite(duration) or duration <= 0 or duration > 7200:
            raise ValueError('invalid audio duration')
        now = self.clock()
        seconds = max(10, math.ceil(duration))
        self.events = [event for event in self.events if event['time'] > now - 86400]
        ready = now
        for window, requests, audio in self.LIMITS:
            active = [event for event in self.events if event['time'] > now - window]
            while active and ((requests is not None and len(active) + 1 > requests) or
                              (audio is not None and sum(e['seconds'] for e in active) + seconds > audio)):
                earliest = min(active, key=lambda event: event['time'])
                ready = max(ready, earliest['time'] + window)
                active.remove(earliest)
        if ready > now:
            return ready
        self.events.append({'time': now, 'seconds': seconds})
        self._save()
        return None

    def _save(self):
        self.path.parent.mkdir(parents=True, exist_ok=True)
        fd, name = tempfile.mkstemp(dir=self.path.parent, prefix='.quota-')
        try:
            with os.fdopen(fd, 'w') as stream:
                json.dump(self.events, stream)
                stream.flush()
                os.fsync(stream.fileno())
            os.replace(name, self.path)
        finally:
            if os.path.exists(name):
                os.unlink(name)


class RetryPolicy:
    """Persistent 429 gate; the caller must honor current() before sending."""

    def __init__(self, path, clock=time.time):
        self.path = Path(path)
        self.clock = clock
        self.state = json.loads(self.path.read_text()) if self.path.exists() else {
            'consecutive_429': 0, 'until': None, 'stopped': False,
        }

    def current(self):
        if self.state['stopped']:
            return ('stop', None)
        until = self.state['until']
        if until is not None and until > self.clock():
            return ('wait', until)
        return ('ready', None)

    def record_429(self, retry_after):
        from email.utils import parsedate_to_datetime

        now = self.clock()
        self.state['consecutive_429'] += 1
        if self.state['consecutive_429'] >= 3:
            self.state['stopped'] = True
            self.state['until'] = None
        else:
            try:
                delay = float(retry_after)
                if not math.isfinite(delay) or delay < 0:
                    raise ValueError
            except (ValueError, TypeError):
                try:
                    date = parsedate_to_datetime(retry_after)
                    if date.tzinfo is None:
                        raise ValueError
                    delay = date.timestamp() - now
                    if delay < 0:
                        raise ValueError
                except (ValueError, TypeError, OverflowError):
                    delay = 86400
            self.state['until'] = now + delay
        self._save()
        return self.current()

    def record_success(self):
        self.state = {'consecutive_429': 0, 'transient': 0,
                      'until': None, 'stopped': False}
        self._save()

    def record_transient(self):
        self.state['consecutive_429'] = 0
        self.state['until'] = None
        self.state['transient'] = self.state.get('transient', 0) + 1
        self.state['stopped'] = self.state['transient'] >= 3
        self._save()
        return self.current()

    def _save(self):
        self.path.parent.mkdir(parents=True, exist_ok=True)
        fd, name = tempfile.mkstemp(dir=self.path.parent, prefix='.retry-')
        try:
            with os.fdopen(fd, 'w') as stream:
                json.dump(self.state, stream)
                stream.flush()
                os.fsync(stream.fileno())
            os.replace(name, self.path)
        finally:
            if os.path.exists(name):
                os.unlink(name)


class AuthenticationFailed(Exception):
    pass


class WaitForLimit(Exception):
    def __init__(self, until):
        self.until = until


class RetryExhausted(Exception):
    pass


class RequestCancelled(Exception):
    pass


class RequestController:
    def __init__(self, client, ledger, policy, cancelled=lambda: False):
        self.client, self.ledger, self.policy = client, ledger, policy
        self.cancelled = cancelled

    def send(self, path, duration):
        while True:
            if self.cancelled():
                raise RequestCancelled()
            action, until = self.policy.current()
            if action == 'stop':
                raise RetryExhausted()
            if action == 'wait':
                raise WaitForLimit(until)
            until = self.ledger.reserve(duration)
            if until is not None:
                raise WaitForLimit(until)
            if self.cancelled():
                raise RequestCancelled()
            try:
                result = self.client.transcribe(path)
            except Exception as error:
                if self.cancelled():
                    raise RequestCancelled() from error
                status = getattr(error, 'status', None)
                if status == 401:
                    raise AuthenticationFailed() from error
                if status == 429:
                    headers = getattr(error, 'headers', {})
                    retry_after = next((value for key, value in headers.items()
                                        if key.lower() == 'retry-after'), None)
                    action, until = self.policy.record_429(retry_after)
                    if action == 'stop':
                        raise RetryExhausted() from error
                    if action == 'wait':
                        raise WaitForLimit(until) from error
                    continue
                if isinstance(error, (OSError, TimeoutError)) or (status is not None and 500 <= status <= 599):
                    if self.policy.record_transient()[0] == 'stop':
                        raise RetryExhausted() from error
                    continue
                raise
            self.policy.record_success()
            return result
