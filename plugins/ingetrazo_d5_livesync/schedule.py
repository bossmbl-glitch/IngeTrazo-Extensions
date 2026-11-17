# SPDX-License-Identifier: GPL-3.0-or-later
"""Pure scheduling state. Publication and acknowledgement are separate commits."""
class SyncSchedule:
    def __init__(self, paused=False):
        self.paused = paused
        self.generation = 0
        self.published_generation = -1
        self.changed_at = float('-inf')
        self.manual = False
        self.retry_at = 0.0
        self.failures = 0

    @property
    def dirty(self):
        return self.generation > self.published_generation

    def changed(self, now):
        self.generation += 1
        self.changed_at = now

    def request(self):
        # Multiple clicks coalesce to one request; they never create snapshots.
        self.manual = True
        self.retry_at = 0.0

    def ready(self, now, delay, pending=False):
        return (not pending and now >= self.retry_at and
                (self.manual or (not self.paused and self.dirty and
                                 now - self.changed_at >= delay)))

    def begin(self):
        token = (self.generation, self.manual)
        self.manual = False
        return token

    def published(self, token):
        self.published_generation = token[0]
        self.failures = 0
        self.retry_at = 0.0

    def failed(self, token, now):
        self.manual = self.manual or token[1]
        self.failures += 1
        self.retry_at = now + min(30.0, 2.0 ** min(self.failures, 5))


class RetryGate:
    """Bounded automatic retry; explicit request permits a new retry cycle."""
    def __init__(self):
        self.revision = None
        self.failures = 0
        self.retry_at = 0.0
        self.request_id = 0

    def ready(self, revision, now, request_id=0):
        if revision != self.revision or request_id != self.request_id:
            self.revision = revision
            self.request_id = request_id
            self.failures = 0
            self.retry_at = 0.0
        return self.failures < 4 and now >= self.retry_at

    def failed(self, now):
        self.failures += 1
        self.retry_at = now + min(30.0, 2.0 ** self.failures)


class RecoveryGate:
    """A failed native send needs stable project readiness before reconnect."""
    def __init__(self):self.ready_since=None;self.started=None
    def reset(self):self.ready_since=None;self.started=None
    def check(self,ready,now):
        if self.started is None:self.started=now
        if not ready:self.ready_since=None;return False
        if self.ready_since is None:self.ready_since=now
        return now-self.ready_since>=1.
    def expired(self,now):return self.started is not None and now-self.started>=30.
