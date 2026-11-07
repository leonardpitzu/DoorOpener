"""The keypad with several doors and guests, and what Home Assistant hears about it."""
from datetime import datetime, timedelta
from unittest.mock import MagicMock

import pytest

import config
from ha_client import ACCESS_REFUSED_EVENT, DOOR_OPENED_EVENT

WED_NOON = datetime(2026, 11, 11, 12, 0, tzinfo=config.TIMEZONE)
BARRIER = config.Door("barrier", "Barrier", "cover.barrier", icon="barrier")
DOOR = config.Door("door", "Door", "button.intercom_open", battery_entity="sensor.door_battery")


@pytest.fixture
def clock():
    return {"now": WED_NOON}


@pytest.fixture
def app_module(monkeypatch, clock):
    import app
    monkeypatch.setattr(app, "get_current_time", lambda: clock["now"])
    monkeypatch.setattr(config, "doors", (BARRIER, DOOR))
    monkeypatch.setattr(config, "_doors_by_id", {"barrier": BARRIER, "door": DOOR})
    return app


@pytest.fixture
def keypad(app_module):
    """One phone: a client that keeps its cookies."""
    return app_module.app.test_client()


def _open(client, pin, doors=None):
    body = {"pin": pin}
    if doors is not None:
        body["doors"] = doors
    return client.post("/open-door", json=body)


def _last_audit(app_module):
    return app_module.audit_log.tail()[-1]


# ---------------------------------------------------------------------------
# Which doors a PIN opens
# ---------------------------------------------------------------------------
def test_a_pin_for_one_door_opens_it_at_once(app_module, keypad):
    app_module.users_store.create_user("kid", "2468", doors=["door"])
    r = _open(keypad, "2468")
    assert r.status_code == 200
    assert r.get_json() == {"status": "success", "door": "door", "message": "Door is opening (test mode)"}
    assert _last_audit(app_module)["details"] == "Door opened (TEST MODE)"


def test_the_keypad_never_says_whose_pin_it_was(app_module, keypad):
    app_module.users_store.create_user("Popescu", "2468", doors=["door"])
    assert b"Popescu" not in _open(keypad, "2468").data
    assert _last_audit(app_module)["user"] == "Popescu"  # the log still does


def test_a_pin_for_several_doors_offers_them_and_the_tile_opens_one(app_module, keypad):
    app_module.users_store.create_user("leo", "1357", doors=["barrier", "door"])
    logged = len(app_module.audit_log.tail())
    r = _open(keypad, "1357")
    data = r.get_json()
    assert r.status_code == 200 and data["status"] == "choose" and data["expires_in"] == 30
    assert data["doors"] == [{"id": "barrier", "name": "Barrier", "icon": "barrier", "battery": False},
                             {"id": "door", "name": "Door", "icon": "door", "battery": True}]
    assert len(app_module.audit_log.tail()) == logged  # nothing opened yet
    chosen = keypad.post("/open-door/choice", json={"pass": data["pass"], "door": "barrier"})
    assert chosen.status_code == 200 and chosen.get_json()["door"] == "barrier"
    assert (_last_audit(app_module)["user"], _last_audit(app_module)["details"]) == ("leo", "Barrier opened (TEST MODE)")
    # The pass is spent: the door needs the PIN again.
    again = keypad.post("/open-door/choice", json={"pass": data["pass"], "door": "door"})
    assert again.status_code == 410


def test_a_pass_works_only_from_the_browser_it_was_given_to(app_module, keypad):
    app_module.users_store.create_user("leo", "1357", doors=["barrier", "door"])
    token = _open(keypad, "1357").get_json()["pass"]
    stranger = app_module.app.test_client()
    assert stranger.post("/open-door/choice", json={"pass": token, "door": "door"}).status_code == 410


def test_a_pass_opens_only_the_doors_it_offered(app_module, keypad):
    app_module.users_store.create_user("leo", "1357", doors=["barrier", "door"])
    token = _open(keypad, "1357", ["barrier"]).get_json()  # a shortcut: one door, opened at once
    assert token["status"] == "success" and token["door"] == "barrier"
    app_module.users_store.create_user("ana", "2468", doors=["barrier", "door"])
    data = _open(keypad, "2468").get_json()
    assert keypad.post("/open-door/choice", json={"pass": data["pass"], "door": "garage"}).status_code == 410


def test_a_pass_expires(app_module, keypad, monkeypatch):
    from passes import Passes
    tick = {"t": 0.0}
    monkeypatch.setattr(app_module, "passes", Passes(clock=lambda: tick["t"]))
    app_module.users_store.create_user("leo", "1357", doors=["barrier", "door"])
    token = _open(keypad, "1357").get_json()["pass"]
    tick["t"] = 30.0
    assert keypad.post("/open-door/choice", json={"pass": token, "door": "door"}).status_code == 410


def test_a_shortcut_opens_its_door_for_a_pin_that_may(app_module, keypad):
    app_module.users_store.create_user("leo", "1357", doors=["barrier", "door"])
    r = _open(keypad, "1357", ["door"])
    assert r.get_json()["door"] == "door"
    both = _open(keypad, "1357", ["barrier", "door"]).get_json()
    assert both["status"] == "choose" and [d["id"] for d in both["doors"]] == ["barrier", "door"]


def test_a_shortcut_to_two_doors_opens_the_one_a_pin_may(app_module, keypad):
    app_module.users_store.create_user("kid", "2468", doors=["door"])
    assert _open(keypad, "2468", ["barrier", "door"]).get_json()["door"] == "door"


def test_a_pin_on_a_shortcut_it_may_not_open_is_a_wrong_pin(app_module, keypad):
    app_module.users_store.create_user("kid", "2468", doors=["door"])
    r = _open(keypad, "2468", ["barrier"])
    assert r.status_code == 401 and r.get_json()["message"] == "Invalid PIN"
    entry = _last_audit(app_module)
    assert (entry["user"], entry["status"], entry["details"]) == ("kid", "NOT_ALLOWED", "Invalid PIN (Barrier)")


def test_a_user_with_no_doors_opens_nothing(app_module, keypad):
    app_module.users_store.create_user("nobody", "2468", doors=[])
    assert _open(keypad, "2468").status_code == 401


def test_an_unknown_door_is_refused(app_module, keypad):
    app_module.users_store.create_user("leo", "1357", doors=["barrier", "door"])
    assert _open(keypad, "1357", ["garage"]).status_code == 400
    assert _open(keypad, "1357", "door").status_code == 400


# ---------------------------------------------------------------------------
# Refusals: vague to strangers, plain to the user's own phone
# ---------------------------------------------------------------------------
KIDS = {"schedule": [{"days": [0, 1, 2, 3, 4], "hours": ["07:00", "20:00"]}]}


@pytest.mark.parametrize("rules, status", [
    ({"valid_until": "2026-11-10T18:00"}, "EXPIRED"),
    ({"valid_from": "2026-11-12T08:00", "valid_until": "2026-11-14T08:00"}, "NOT_YET_VALID"),
    ({"schedule": [{"days": [5, 6], "hours": None}]}, "OUTSIDE_SCHEDULE"),
    ({"schedule": [{"days": None, "hours": ["14:00", "18:00"]}]}, "OUTSIDE_SCHEDULE"),
])
def test_a_pin_outside_its_window_is_just_a_wrong_pin(app_module, keypad, rules, status):
    app_module.users_store.create_user("guest", "2468", access_rules=rules, doors=["door"])
    r = _open(keypad, "2468")
    assert r.status_code == 401
    assert r.get_json()["message"] == "Invalid PIN"  # nothing tells a stranger the PIN exists
    entry = _last_audit(app_module)
    assert (entry["user"], entry["status"]) == ("guest", status)


def test_a_pin_inside_its_window_opens(app_module, keypad):
    app_module.users_store.create_user(
        "cleaner", "2468", access_rules={"schedule": [{"days": [0, 1, 2, 3, 4], "hours": ["08:00", "18:00"]}]},
        doors=["door"])
    assert _open(keypad, "2468").status_code == 200


def _known_phone(app_module, clock, user, pin, rules, doors=("door",)):
    """A phone that opened with *pin* an hour before noon (so no visit is running at
    noon); the rules then change to *rules*."""
    app_module.users_store.create_user(user, pin, doors=list(doors))
    phone = app_module.app.test_client()
    clock["now"] = WED_NOON - timedelta(hours=1)
    assert _open(phone, pin, [doors[0]]).status_code == 200
    clock["now"] = WED_NOON
    app_module.users_store.update_user(user, access_rules=rules)
    return phone


def test_the_owners_phone_is_told_why_and_not_counted(app_module, clock):
    phone = _known_phone(app_module, clock, "kid", "2468", KIDS)
    clock["now"] = WED_NOON.replace(hour=22)
    for _ in range(10):  # more than any block allows: none of these count
        r = _open(phone, "2468")
        assert r.status_code == 403
        assert r.get_json() == {"status": "refused", "message": "Not now. You can open again tomorrow at 07:00."}
    entry = _last_audit(app_module)
    assert entry["status"] == "OUTSIDE_SCHEDULE" and "told to a phone that opened before" in entry["details"]
    # Wrong PINs from that same phone still count.
    assert _open(phone, "9999").status_code == 401


def test_another_phone_hears_only_invalid_pin(app_module, clock):
    _known_phone(app_module, clock, "kid", "2468", KIDS)
    clock["now"] = WED_NOON.replace(hour=22)
    stranger = app_module.app.test_client()
    r = _open(stranger, "2468")
    assert r.status_code == 401 and r.get_json()["message"] == "Invalid PIN"


def test_a_phone_that_knew_another_user_is_not_told(app_module, clock):
    phone = _known_phone(app_module, clock, "dad", "1357", None)
    app_module.users_store.create_user("kid", "2468", doors=["door"], access_rules=KIDS)
    clock["now"] = WED_NOON.replace(hour=22)
    assert _open(phone, "2468").status_code == 401


def test_a_new_pin_forgets_the_phone(app_module, clock):
    phone = _known_phone(app_module, clock, "kid", "2468", KIDS)
    app_module.users_store.update_user("kid", pin="8642")
    clock["now"] = WED_NOON.replace(hour=22)
    assert _open(phone, "8642").status_code == 401


def test_a_forged_phone_cookie_is_ignored(app_module, clock):
    app_module.users_store.create_user("kid", "2468", doors=["door"], access_rules=KIDS)
    clock["now"] = WED_NOON.replace(hour=22)
    stranger = app_module.app.test_client()
    stranger.set_cookie("dooropener_phone", '[["kid","0000000000000000"]]')
    assert _open(stranger, "2468").status_code == 401


@pytest.mark.parametrize("rules, doors, message", [
    ({"valid_until": "2026-11-11T11:00"}, None, "This PIN expired on Wed 11 Nov."),
    ({"valid_from": "2026-11-12T08:00", "valid_until": "2026-11-14T08:00"}, None,
     "Not yet. This PIN works from tomorrow at 08:00."),
    ({"schedule": [{"days": [0], "hours": ["08:00", "09:00"]}]}, None, "Not now. You can open again on Monday at 08:00."),
    ({"valid_until": "2026-11-11T23:59", "schedule": [{"days": [0], "hours": ["08:00", "09:00"]}]}, None,
     "Not now, and this PIN has no times left to open."),
    (None, ["barrier"], "This PIN can't open Barrier."),
])
def test_what_the_owner_is_told(app_module, clock, rules, doors, message):
    phone = _known_phone(app_module, clock, "guest", "2468", rules)
    assert _open(phone, "2468", doors).get_json()["message"] == message


def test_a_disabled_pin_on_its_phone(app_module, clock):
    phone = _known_phone(app_module, clock, "guest", "2468", None)
    app_module.users_store.update_user("guest", active=False)
    assert _open(phone, "2468").get_json()["message"] == "This PIN is switched off."


# ---------------------------------------------------------------------------
# Guests: their uses, and switching off by themselves
# ---------------------------------------------------------------------------
def test_a_courier_gets_barrier_door_and_barrier_out_for_one_use(app_module, keypad, clock):
    app_module.users_store.create_user("courier", "8642", doors=["barrier", "door"], access_rules={
        "valid_until": "2026-11-11T23:59", "uses": 1, "schedule": [{"days": [2], "hours": ["10:00", "13:30"]}]})
    for minutes, door in [(0, "barrier"), (3, "door"), (9, "barrier")]:
        clock["now"] = WED_NOON + timedelta(minutes=minutes)
        assert _open(keypad, "8642", [door]).status_code == 200
    assert "use 1 of 1" in app_module.audit_log.tail()[-3]["details"]
    clock["now"] = WED_NOON + timedelta(minutes=16)
    r = _open(keypad, "8642", ["door"])  # the courier's own phone is told why
    assert r.status_code == 403 and r.get_json()["message"] == "This PIN has been used up."
    assert _open(app_module.app.test_client(), "8642").status_code == 401  # anyone else: a wrong PIN
    statuses = [entry["status"] for entry in app_module.audit_log.tail()[-3:]]
    assert statuses == ["SWITCHED_OFF", "USED_UP", "USED_UP"]
    (user,) = app_module.users_store.list_users()["users"]
    assert user["active"] is False and user["finished"] == "USED_UP"


def test_a_friend_is_switched_off_after_the_last_day(app_module, keypad, clock):
    app_module.users_store.create_user("friend", "2468", doors=["door"],
                                       access_rules={"valid_until": "2026-11-13T23:59"})
    for day in range(3):
        clock["now"] = WED_NOON + timedelta(days=day)
        assert _open(keypad, "2468").status_code == 200  # as often as needed
        assert _open(keypad, "2468").status_code == 200
    clock["now"] = datetime(2026, 11, 14, 8, 0, tzinfo=config.TIMEZONE)
    assert _open(keypad, "2468").get_json()["message"] == "This PIN expired on Fri 13 Nov."
    assert app_module.users_store.list_users()["users"][0]["finished"] == "EXPIRED"


def test_the_admin_list_switches_off_finished_guests(client, app_module, clock):
    _admin(client)
    app_module.users_store.create_user("friend", "2468", doors=["door"],
                                       access_rules={"valid_until": "2026-11-11T08:00"})
    (user,) = client.get("/admin/users").get_json()["users"]
    assert (user["active"], user["finished"]) == (False, "EXPIRED")
    assert _last_audit(app_module)["details"] == "Guest switched off: end date passed"


# ---------------------------------------------------------------------------
# The admin API
# ---------------------------------------------------------------------------
def _admin(client):
    with client.session_transaction() as s:
        s["admin_authenticated"] = True
        s["admin_login_time"] = WED_NOON.isoformat()


def test_users_and_their_doors_through_the_admin_api(client, app_module):
    _admin(client)
    rules = {"valid_until": "2026-11-12T18:00", "uses": 2, "schedule": [{"days": [0], "hours": ["10:00", "13:30"]}]}
    created = client.post("/admin/users", json={"username": "guest", "pin": "2468", "access": rules,
                                                "doors": ["barrier"]})
    assert created.status_code == 201
    data = client.get("/admin/users").get_json()
    assert [d["id"] for d in data["doors"]] == ["barrier", "door"]
    (listed,) = data["users"]
    assert listed["doors"] == ["barrier"] and listed["access"]["uses"] == 2 and listed["visits"] == 0

    bad = client.put("/admin/users/guest", json={"access": {"schedule": [{"hours": ["08:00"]}]}})
    assert bad.status_code == 400 and "hours" in bad.get_json()["message"]
    unknown = client.put("/admin/users/guest", json={"doors": ["garage"]})
    assert unknown.status_code == 400 and "garage" in unknown.get_json()["message"]
    assert client.put("/admin/users/guest", json={"pin": "1357"}).status_code == 200  # rules and doors kept
    (listed,) = client.get("/admin/users").get_json()["users"]
    assert listed["access"]["uses"] == 2 and listed["doors"] == ["barrier"]
    assert client.put("/admin/users/guest", json={"doors": ["barrier", "door"], "access": None}).status_code == 200
    (listed,) = client.get("/admin/users").get_json()["users"]
    assert listed["access"] is None and listed["doors"] == ["barrier", "door"]


def test_switching_an_expired_guest_back_on_asks_for_a_new_end_date(client, app_module):
    _admin(client)
    client.post("/admin/users", json={"username": "friend", "pin": "2468", "doors": ["door"],
                                      "access": {"valid_until": "2026-11-11T08:00"}})
    client.get("/admin/users")  # switched off
    r = client.put("/admin/users/friend", json={"active": True})
    assert r.status_code == 400 and "end date has passed" in r.get_json()["message"]
    r = client.put("/admin/users/friend", json={"active": True, "access": {"valid_until": "2026-11-15T23:59"}})
    assert r.status_code == 200
    assert client.get("/admin/users").get_json()["users"][0]["active"] is True


# ---------------------------------------------------------------------------
# Home Assistant hears who came in, and whose PIN was refused
# ---------------------------------------------------------------------------
@pytest.fixture
def posts(app_module, monkeypatch):
    """Every POST to Home Assistant, with background calls run inline."""
    monkeypatch.setattr(config, "test_mode", False)
    monkeypatch.setattr(config, "report_to_ha", True)
    monkeypatch.setattr(app_module, "_in_background", lambda fn, *args: fn(*args))
    post = MagicMock(return_value=MagicMock(status_code=200, raise_for_status=lambda: None))
    monkeypatch.setattr(app_module.ha_client.session, "post", post)
    return post


def _paths(post):
    return [call.args[0].removeprefix(config.ha_url) for call in post.call_args_list]


def test_an_opening_is_an_event_and_a_logbook_entry(app_module, keypad, posts):
    app_module.users_store.create_user("alice", "1357", doors=["barrier"])
    assert _open(keypad, "1357").status_code == 200
    assert _paths(posts) == ["/api/services/cover/open_cover", f"/api/events/{DOOR_OPENED_EVENT}",
                             "/api/services/logbook/log"]
    assert posts.call_args_list[1].kwargs["json"] == {"user": "alice", "door": "barrier", "door_name": "Barrier",
                                                      "entity_id": "cover.barrier"}
    assert posts.call_args_list[2].kwargs["json"] == {"name": "Barrier", "message": "opened by alice",
                                                      "entity_id": "cover.barrier"}


def test_a_refused_own_pin_is_an_event(app_module, keypad, posts, clock):
    app_module.users_store.create_user("kid", "2468", doors=["door"], access_rules=KIDS)
    clock["now"] = WED_NOON.replace(hour=22)
    _open(keypad, "2468", ["door"])
    assert _paths(posts) == [f"/api/events/{ACCESS_REFUSED_EVENT}"]
    assert posts.call_args.kwargs["json"] == {"user": "kid", "reason": "outside_schedule", "doors": ["door"]}
    posts.reset_mock()
    _open(keypad, "9999")  # a PIN nobody has: the brute-force alert's business, not an event
    assert posts.call_count == 0


def test_report_to_ha_off_sends_no_events_or_logbook(app_module, keypad, posts, monkeypatch, clock):
    monkeypatch.setattr(config, "report_to_ha", False)
    app_module.users_store.create_user("alice", "1357", doors=["door"], access_rules=KIDS)
    assert _open(keypad, "1357").status_code == 200
    assert _paths(posts) == ["/api/services/button/press"]  # the door still opens
    clock["now"] = WED_NOON.replace(hour=22)
    _open(keypad, "1357")
    assert _paths(posts) == ["/api/services/button/press"]


def test_a_failing_event_does_not_fail_the_opening(app_module, keypad, posts):
    import requests
    app_module.users_store.create_user("alice", "1357", doors=["door"])
    posts.side_effect = [MagicMock(raise_for_status=lambda: None), requests.ConnectionError("down"),
                         requests.ConnectionError("down")]
    assert _open(keypad, "1357").status_code == 200


def test_test_mode_tells_home_assistant_nothing(app_module, keypad, posts, monkeypatch, clock):
    monkeypatch.setattr(config, "test_mode", True)
    app_module.users_store.create_user("alice", "1357", doors=["door"], access_rules=KIDS)
    assert _open(keypad, "1357").status_code == 200
    clock["now"] = WED_NOON.replace(hour=22)
    _open(keypad, "1357")
    assert posts.call_count == 0


# ---------------------------------------------------------------------------
# No Home Assistant at all: everything else keeps working
# ---------------------------------------------------------------------------
@pytest.fixture
def no_home_assistant(app_module, monkeypatch):
    monkeypatch.setattr(config, "ha_token", "")
    monkeypatch.setattr(config, "test_mode", False)
    post = MagicMock()
    monkeypatch.setattr(app_module.ha_client.session, "post", post)
    monkeypatch.setattr(app_module.ha_client.session, "get", post)
    return post


def test_without_home_assistant_the_keypad_answers_and_calls_nobody(app_module, keypad, no_home_assistant, caplog):
    app_module.users_store.create_user("alice", "1357", doors=["door"])
    r = _open(keypad, "1357")
    assert r.status_code == 502 and r.get_json()["status"] == "error"
    assert _last_audit(app_module)["details"] == "Door: No Home Assistant configured"
    assert _open(keypad, "9999").status_code == 401
    assert keypad.get("/battery?door=door").get_json() == {"level": None}
    app_module.ha_client.notify("t", "m")
    app_module.ha_client.door_opened("alice", DOOR)
    app_module.ha_client.access_refused("alice", "EXPIRED", [])
    app_module.ha_client.check_connection()
    assert no_home_assistant.call_count == 0
    assert "No Home Assistant configured" in caplog.text


@pytest.mark.parametrize("status, logged", [(200, "Connected to Home Assistant"),
                                             (401, "refused the access token"),
                                             (500, "API error: 500")])
def test_the_startup_check_says_what_home_assistant_answered(app_module, monkeypatch, caplog, status, logged):
    import logging
    monkeypatch.setattr(app_module.ha_client.session, "get", MagicMock(return_value=MagicMock(status_code=status)))
    with caplog.at_level(logging.INFO, logger="dooropener"):
        app_module.ha_client.check_connection()
    assert logged in caplog.text


# ---------------------------------------------------------------------------
# Pages: shortcut links, the manifest, batteries
# ---------------------------------------------------------------------------
def test_shortcut_pages_name_their_doors(app_module, keypad):
    page = keypad.get("/barrier+door")
    assert page.status_code == 200
    assert b'data-doors="barrier+door"' in page.data and b"<title>Barrier &amp; Door</title>" in page.data
    assert b'data-battery-door=""' in page.data  # two doors: the tiles show batteries
    single = keypad.get("/door")
    assert b'data-battery-door="door"' in single.data
    assert b"manifest.webmanifest?to=door" in single.data
    main = keypad.get("/")
    assert b'data-doors=""' in main.data and b"<title>DoorOpener</title>" in main.data
    for missing in ("/garage", "/barrier+garage", "/favicon.ico", "/door+"):
        assert keypad.get(missing).status_code == 404


def test_each_shortcut_has_a_manifest_of_its_own(app_module, keypad):
    own = keypad.get("/manifest.webmanifest?to=barrier").get_json(force=True)
    assert (own["id"], own["start_url"], own["name"]) == ("/barrier", "/barrier", "Barrier")
    main = keypad.get("/manifest.webmanifest").get_json(force=True)
    assert main["start_url"] == "/" and main["shortcuts"] == [{"name": "Barrier", "url": "/barrier"},
                                                             {"name": "Door", "url": "/door"}]
    assert keypad.get("/manifest.webmanifest?to=garage").status_code == 404


def test_battery_per_door(app_module, keypad, monkeypatch):
    get = MagicMock(return_value=MagicMock(status_code=200, json=lambda: {"state": "64"}))
    monkeypatch.setattr(app_module.ha_client.session, "get", get)
    assert keypad.get("/battery?door=door").get_json() == {"level": 64}
    assert get.call_args.args[0].endswith("/api/states/sensor.door_battery")
    assert keypad.get("/battery?door=barrier").get_json() == {"level": None}  # no sensor
    assert keypad.get("/battery").get_json() == {"level": None}  # two doors: which one?
