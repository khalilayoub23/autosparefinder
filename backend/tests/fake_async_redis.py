"""Minimal in-memory asyncio Redis for the owner-digest tests (strings, hashes, lists; TTLs honoured by wall clock)."""
import time


class FakeAsyncRedis:
    def __init__(self):
        self.d, self.exp = {}, {}

    def _alive(self, k):
        if k in self.exp and self.exp[k] <= time.time():
            self.d.pop(k, None); self.exp.pop(k, None)
        return k in self.d

    async def get(self, k):
        return self.d.get(k) if self._alive(k) else None

    async def set(self, k, v, ex=None, nx=False):
        if nx and self._alive(k):
            return None
        self.d[k] = v if isinstance(v, (str, bytes)) else str(v)
        if ex:
            self.exp[k] = time.time() + ex
        else:
            self.exp.pop(k, None)
        return True

    async def exists(self, k):
        return 1 if self._alive(k) else 0

    async def delete(self, *ks):
        for k in ks:
            self.d.pop(k, None); self.exp.pop(k, None)

    async def hget(self, k, f):
        return (self.d.get(k) or {}).get(f) if self._alive(k) else None

    async def hset(self, k, f, v):
        self.d.setdefault(k, {})[f] = v

    async def hdel(self, k, f):
        (self.d.get(k) or {}).pop(f, None)

    async def hgetall(self, k):
        return dict(self.d.get(k) or {}) if self._alive(k) else {}

    async def hincrby(self, k, f, n=1):
        h = self.d.setdefault(k, {}); h[f] = int(h.get(f, 0)) + n

    async def lpush(self, k, v):
        self.d.setdefault(k, []).insert(0, v)

    async def rpop(self, k):
        l = self.d.get(k) or []
        return l.pop() if l else None

    async def ltrim(self, k, a, b):
        l = self.d.get(k) or []
        self.d[k] = l[a:b + 1]

    async def llen(self, k):
        return len(self.d.get(k) or [])

    async def lrange(self, k, a, b):
        l = self.d.get(k) or []
        return l[a:(len(l) if b == -1 else b + 1)]
