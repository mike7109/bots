"""normalize: GitLab webhook payload -> botkit Event. Only issue events;
maps open/reopen/close/update; drops non-issue kinds. (`update` is mapped but
the webhook handler routes it only to watched-issue rooms — see app.py.)"""
from __future__ import annotations

from normalize import _mentions, normalize


def _payload(action="open", changes=None, **attr_extra):
    attr = {"iid": 7, "title": "Fix it", "url": "https://gl/x/-/issues/7",
            "state": "opened", "action": action, "due_date": "2026-06-10"}
    attr.update(attr_extra)
    payload = {
        "object_kind": "issue",
        "object_attributes": attr,
        "project": {"path_with_namespace": "fakspro/infra"},
        "user": {"username": "alice"},
        "assignees": [{"username": "bob"}, {"username": "carol"}, {"no_name": 1}],
        "labels": [{"title": "bug"}, {"title": "workflow::in progress"}],
    }
    if changes is not None:
        payload["changes"] = changes                 # top-level, as GitLab sends it
    return payload


def test_normalize_non_issue_returns_none():
    assert normalize({"object_kind": "merge_request"}) is None
    assert normalize({}) is None


def test_normalize_maps_three_actions():
    for gl_action, expected in [("open", "open"), ("reopen", "reopen"), ("close", "close")]:
        ev = normalize(_payload(gl_action))
        assert ev is not None and ev.action == expected


def test_normalize_maps_update():
    ev = normalize(_payload("update"))               # mapped (routed to watched rooms in app.py)
    assert ev is not None and ev.action == "update"


def test_normalize_drops_unknown_action():
    assert normalize(_payload("approved")) is None


def test_normalize_extracts_fields():
    ev = normalize(_payload("open"))
    assert ev.kind == "issue"
    assert ev.project == "fakspro/infra"
    assert ev.iid == "7"                              # stringified
    assert ev.title == "Fix it"
    assert ev.url == "https://gl/x/-/issues/7"
    assert ev.state == "opened"
    assert ev.author == "alice"
    assert ev.due == "2026-06-10"


def test_normalize_extracts_assignees_and_labels():
    ev = normalize(_payload("open"))
    assert ev.assignees == ["bob", "carol"]           # entries without username dropped
    assert ev.labels == ["bug", "workflow::in progress"]


def test_normalize_handles_missing_optional_fields():
    payload = {"object_kind": "issue", "object_attributes": {"action": "open"}}
    ev = normalize(payload)
    assert ev is not None
    assert ev.iid == "" and ev.project == "" and ev.author is None
    assert ev.assignees == [] and ev.labels == []
    assert ev.description == "" and ev.changes == {}


def test_normalize_extracts_description():
    ev = normalize(_payload("open", description="  Полное описание задачи  "))
    assert ev.description == "Полное описание задачи"      # stripped


def test_normalize_non_update_has_no_changes():
    # `changes` only rides update webhooks; open/close/reopen carry an empty diff
    ev = normalize(_payload("open", changes={"due_date": {"previous": None, "current": "2026-07-01"}}))
    assert ev.changes == {}


def test_normalize_update_parses_assignee_and_due_changes():
    changes = {
        "assignees": {"previous": [{"username": "bob"}], "current": [{"username": "carol"}]},
        "due_date": {"previous": "2026-06-01", "current": "2026-07-01"},
        "labels": {"previous": [], "current": [{"title": "security"}]},   # dropped
    }
    ev = normalize(_payload("update", changes=changes))
    assert ev.changes == {
        "assignees": {"previous": ["bob"], "current": ["carol"]},
        "due_date": {"previous": "2026-06-01", "current": "2026-07-01"},
    }


def test_normalize_update_parses_rename():
    # a rename rides `changes.title`; app.py uses it to EDIT the thread root
    # (no thread message) — so it must survive distillation.
    changes = {"title": {"previous": "старое имя", "current": "новое имя"}}
    ev = normalize(_payload("update", changes=changes))
    assert ev.changes == {"title": {"previous": "старое имя", "current": "новое имя"}}


def test_normalize_update_drops_unchanged_and_noise():
    changes = {
        "assignees": {"previous": [{"username": "bob"}], "current": [{"username": "bob"}]},  # no change
        "labels": {"previous": [], "current": [{"title": "x"}]},
        "updated_at": {"previous": "t1", "current": "t2"},
        "title": {"previous": "same", "current": "same"},                 # no change
    }
    ev = normalize(_payload("update", changes=changes))
    assert ev.changes == {}                                # nothing actually changed


def test_normalize_update_unassign_to_empty():
    changes = {"assignees": {"previous": [{"username": "bob"}], "current": []}}
    ev = normalize(_payload("update", changes=changes))
    assert ev.changes == {"assignees": {"previous": ["bob"], "current": []}}


# --- note (comment) events --------------------------------------------
def _note_payload(note="Смотри @m.bahmutskij", *, labels=("security",),
                  action="create", system=False, noteable="Issue"):
    return {
        "object_kind": "note",
        "object_attributes": {
            "noteable_type": noteable, "action": action, "system": system,
            "note": note, "url": "https://gl/x/-/issues/7#note_1241",
        },
        "issue": {"iid": 7, "title": "Fix it", "labels": [{"title": t} for t in labels]},
        "project": {"path_with_namespace": "fakspro/infra"},
        "user": {"username": "d.nikulin"},
    }


def test_normalize_note_new_comment():
    ev = normalize(_note_payload("Готово, гляньте"))
    assert ev is not None
    assert ev.kind == "note" and ev.action == "create"
    assert ev.iid == "7" and ev.title == "Fix it"
    assert ev.project == "fakspro/infra"
    assert ev.url == "https://gl/x/-/issues/7#note_1241"     # deep-link to the comment
    assert ev.author == "d.nikulin"                          # the commenter
    assert ev.comment == "Готово, гляньте"
    assert ev.labels == ["security"]                         # labels read from issue.labels


def test_normalize_note_parses_mentions():
    ev = normalize(_note_payload("cc @m.bahmutskij и @d.nikulin"))
    assert ev.mention_logins == ["m.bahmutskij", "d.nikulin"]


def test_normalize_note_system_note_dropped():
    assert normalize(_note_payload("changed label", system=True)) is None


def test_normalize_note_edit_dropped():
    # comment EDIT re-fires with action=update -> not a new comment
    assert normalize(_note_payload("edited", action="update")) is None


def test_normalize_note_non_issue_dropped():
    assert normalize(_note_payload("on an MR", noteable="MergeRequest")) is None


def test_normalize_note_empty_body_dropped():
    assert normalize(_note_payload("   ")) is None            # whitespace-only -> nothing


def test_normalize_note_no_labels():
    ev = normalize(_note_payload("hi", labels=()))
    assert ev is not None and ev.labels == []                # watched-detect finds no match


def test_normalize_note_extracts_description_and_due_for_lazy_root():
    # a comment may have to LAZILY create the thread root, so it carries the issue
    # body + due (best-effort) from the note payload's `issue` object.
    payload = _note_payload("hi")
    payload["issue"]["description"] = "  тело задачи  "
    payload["issue"]["due_date"] = "2026-08-01"
    ev = normalize(payload)
    assert ev.description == "тело задачи" and ev.due == "2026-08-01"


# --- @-mention parser -------------------------------------------------
def test_mentions_dotted_login_not_truncated():
    assert _mentions("ping @m.bahmutskij") == ["m.bahmutskij"]


def test_mentions_skips_emails_and_paths():
    assert _mentions("mail user@host.com and path a/@b") == []


def test_mentions_skips_group_specials():
    assert _mentions("@all @here @everyone hurry") == []


def test_mentions_strips_trailing_punctuation():
    assert _mentions("thanks @user. and @dev-") == ["user", "dev"]


def test_mentions_dedupes_preserving_order():
    assert _mentions("@a @b @a @c") == ["a", "b", "c"]


def test_mentions_caps_fanout():
    text = " ".join(f"@u{i}" for i in range(30))
    assert len(_mentions(text)) == 20                         # bounded fan-out
