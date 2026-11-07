"""Phones that have opened a door before, so the keypad may tell their owner why not.

To a stranger, a PIN refused for its schedule, its end date or its door looks
like any wrong PIN: anything else would say the PIN exists. Its owner, though,
deserves a plain answer ("Not now. You can open again tomorrow at 07:00").

After a successful opening the browser gets a signed cookie naming the user and
the user's phone key. A later refusal of that same user's PIN, from a browser
holding that cookie, gets the plain answer and does not count as a wrong try. A
new PIN, or deleting the user, changes the key and so forgets every phone.
"""

from itsdangerous import BadSignature, URLSafeSerializer

COOKIE = "dooropener_phone"
MAX_AGE = 400 * 24 * 3600  # the longest browsers keep a cookie; renewed on every opening
MAX_USERS = 4  # people sharing one phone or tablet


class KnownPhones:
    def __init__(self, secret_key: str):
        self._serializer = URLSafeSerializer(secret_key, salt="dooropener-known-phone")

    def users(self, cookie: str | None) -> dict[str, str]:
        """``{username: phone key}`` from a cookie; empty for none or a forged one."""
        if not cookie:
            return {}
        try:
            data = self._serializer.loads(cookie)
        except BadSignature:
            return {}
        if not isinstance(data, list):
            return {}
        return {pair[0]: pair[1] for pair in data
                if isinstance(pair, list) and len(pair) == 2 and all(isinstance(v, str) for v in pair)}

    def knows(self, cookie: str | None, user: str, key: str) -> bool:
        return bool(key) and self.users(cookie).get(user) == key

    def remember(self, cookie: str | None, user: str, key: str) -> str:
        """The cookie value with *user* added (most recent last, at most ``MAX_USERS``)."""
        known = {u: k for u, k in self.users(cookie).items() if u != user}
        known[user] = key
        pairs = [[u, k] for u, k in known.items()][-MAX_USERS:]
        return self._serializer.dumps(pairs)
