"""
Automated test suite for multi-media attachments, temp media flow,
confidence-based intent detection, and user management.
"""
import sys, os, sqlite3
sys.path.insert(0, '/Volumes/Ahmad/Reminder-Bot App')

import database
import config

PASS = "\033[92m✅ PASS\033[0m"
FAIL = "\033[91m❌ FAIL\033[0m"
results = []

def test(name, condition, detail=""):
    status = PASS if condition else FAIL
    results.append(condition)
    print(f"{status}  {name}" + (f"  ({detail})" if detail else ""))

def section(title):
    print(f"\n{'─'*60}\n  {title}\n{'─'*60}")

TEST_PHONE = "92000000001"

# ─────────────────────────────────────────────────────────────────────────────
section("1. DATABASE — Table creation")
# ─────────────────────────────────────────────────────────────────────────────

database.init_db()

conn = sqlite3.connect(config.DB_PATH)
tables = {r[0] for r in conn.execute("SELECT name FROM sqlite_master WHERE type='table'").fetchall()}
conn.close()

test("attachments table exists",   "attachments" in tables)
test("temp_media table exists",    "temp_media" in tables)
test("allowed_users table exists", "allowed_users" in tables)
test("otp_tokens table exists",    "otp_tokens" in tables)

# ─────────────────────────────────────────────────────────────────────────────
section("2. DATABASE — temp_media CRUD")
# ─────────────────────────────────────────────────────────────────────────────

conn = sqlite3.connect(config.DB_PATH)
conn.execute("DELETE FROM temp_media WHERE user_phone = ?", (TEST_PHONE,))
conn.execute("DELETE FROM attachments WHERE user_phone = ?", (TEST_PHONE,))
conn.commit(); conn.close()

tmp_id = database.add_temp_media(TEST_PHONE, "temp_media/test.jpg", "image", "test.jpg")
test("add_temp_media returns ID > 0", tmp_id > 0, f"id={tmp_id}")

row = database.get_pending_temp_media(TEST_PHONE)
test("get_pending_temp_media finds row", row is not None)
test("correct file_path stored", row["file_path"] == "temp_media/test.jpg" if row else False)

expiring = database.get_expiring_temp_media()
test("Fresh row NOT in expiring list", not any(r["id"] == tmp_id for r in expiring))

database.mark_temp_media_warning_sent(tmp_id)
conn = sqlite3.connect(config.DB_PATH)
conn.row_factory = sqlite3.Row
r = conn.execute("SELECT warning_sent, expires_at FROM temp_media WHERE id=?", (tmp_id,)).fetchone()
conn.close()
test("warning_sent set to 1",          r["warning_sent"] == 1 if r else False)
test("expires_at populated",           r["expires_at"] is not None if r else False)

fetched = database.get_temp_media_by_id(tmp_id, TEST_PHONE)
test("get_temp_media_by_id works",     fetched is not None)

database.delete_temp_media(tmp_id)
test("delete_temp_media removes row",  database.get_temp_media_by_id(tmp_id, TEST_PHONE) is None)

# ─────────────────────────────────────────────────────────────────────────────
section("3. DATABASE — attachments CRUD")
# ─────────────────────────────────────────────────────────────────────────────

conn = sqlite3.connect(config.DB_PATH)
conn.execute("INSERT OR IGNORE INTO allowed_users (phone,label,is_admin,added_at) VALUES (?,?,0,datetime('now'))", (TEST_PHONE,"Test"))
idea_id = conn.execute("INSERT INTO ideas (user_phone,subject,description,created_at) VALUES (?,'Test','Desc',datetime('now'))", (TEST_PHONE,)).lastrowid
conn.commit(); conn.close()

att_id = database.add_attachment("idea", idea_id, TEST_PHONE, "image", "ideas_media/x.jpg", "x.jpg")
test("add_attachment returns ID > 0", att_id > 0, f"id={att_id}")

atts = database.get_attachments("idea", idea_id, TEST_PHONE)
test("get_attachments returns 1 row",       len(atts) == 1)
test("attachment.section == 'idea'",        atts[0]["section"] == "idea" if atts else False)
test("attachment.entry_id correct",         atts[0]["entry_id"] == idea_id if atts else False)

deleted = database.delete_attachments_for_entry("idea", idea_id, TEST_PHONE)
test("delete_attachments_for_entry count=1", deleted == 1)
test("attachments empty after delete",       len(database.get_attachments("idea", idea_id, TEST_PHONE)) == 0)

# ─────────────────────────────────────────────────────────────────────────────
section("4. DATABASE — User Management (backend database.py)")
# ─────────────────────────────────────────────────────────────────────────────

conn = sqlite3.connect(config.DB_PATH)
conn.execute("DELETE FROM allowed_users WHERE phone = ?", (TEST_PHONE,))
conn.commit(); conn.close()

ok = database.add_allowed_user(TEST_PHONE, "Test User")
test("add_allowed_user returns True",            ok is True)

ok2 = database.add_allowed_user(TEST_PHONE, "Dup")
test("add_allowed_user duplicate returns False", ok2 is False)

test("is_phone_allowed True for added user",     database.is_phone_allowed(TEST_PHONE))
test("is_phone_allowed False for unknown",       not database.is_phone_allowed("92999999999"))

users = database.get_all_allowed_users()
test("get_all_allowed_users is non-empty list",  isinstance(users, list) and len(users) > 0)
phones = [u["phone"] for u in users]
test("Admin phone in list",                      config.ALLOWED_PHONE_NUMBER in phones)

ok3 = database.remove_allowed_user(TEST_PHONE)
test("remove_allowed_user returns True",         ok3 is True)
test("is_phone_allowed False after removal",     not database.is_phone_allowed(TEST_PHONE))

ok_admin = database.remove_allowed_user(config.ALLOWED_PHONE_NUMBER)
test("Admin cannot be removed (returns False)",  ok_admin is False)

# ─────────────────────────────────────────────────────────────────────────────
section("5. DATABASE — OTP flow")
# ─────────────────────────────────────────────────────────────────────────────

otp = database.create_otp()
test("create_otp is 6-digit string", len(otp) == 6 and otp.isdigit(), f"otp={otp}")
test("wrong OTP fails",              not database.verify_otp("000000"))
test("correct OTP passes",           database.verify_otp(otp))
test("same OTP fails (used)",        not database.verify_otp(otp))

# ─────────────────────────────────────────────────────────────────────────────
section("6. NLP — process_media_caption (regex gate)")
# ─────────────────────────────────────────────────────────────────────────────

from nlp_parser import process_media_caption

r = process_media_caption("")
test("Empty caption → unclear+low", r["intent"]=="unclear" and r["confidence"]=="low")

r = process_media_caption("check this out lol")
test("No keywords → unclear+low (no LLM)", r["confidence"]=="low")

r = process_media_caption("fyi")
test("Generic word → unclear+low",  r["confidence"]=="low")

# ─────────────────────────────────────────────────────────────────────────────
section("7. NLP — process_media_caption (LLM, may take a few seconds)")
# ─────────────────────────────────────────────────────────────────────────────

print("  [Calling Gemini LLM…]")

r = process_media_caption("save as idea")
test("'save as idea' → intent=new_idea",        r.get("intent") == "new_idea",          f"got {r.get('intent')}")
test("'save as idea' → confidence=high",        r.get("confidence") == "high",          f"got {r.get('confidence')}")
test("'save as idea' → section=idea",           r.get("section") == "idea",             f"got {r.get('section')}")

r = process_media_caption("attach to resource 3")
test("'attach to resource 3' → attach_to_existing", r.get("intent") == "attach_to_existing", f"got {r.get('intent')}")
test("'attach to resource 3' → entry_id=3",         r.get("entry_id") == 3,                  f"got {r.get('entry_id')}")
test("'attach to resource 3' → confidence=high",     r.get("confidence") == "high",           f"got {r.get('confidence')}")

r = process_media_caption("discard")
test("'discard' → intent=discard",    r.get("intent") == "discard",   f"got {r.get('intent')}")
test("'discard' → confidence=high",   r.get("confidence") == "high",  f"got {r.get('confidence')}")

r = process_media_caption("new note: meeting summary")
test("'new note: …' → section=note",  r.get("section") == "note",    f"got {r.get('section')}")

# ─────────────────────────────────────────────────────────────────────────────
section("8. SCHEDULER — Jobs registered")
# ─────────────────────────────────────────────────────────────────────────────

import reminder_scheduler
sched = reminder_scheduler.start_scheduler()
job_funcs = {j.func.__name__ for j in sched.get_jobs()}
test("check_and_send_reminders registered",   "check_and_send_reminders" in job_funcs)
test("warn_expiring_temp_media registered",   "warn_expiring_temp_media" in job_funcs)
test("discard_expired_temp_media registered", "discard_expired_temp_media" in job_funcs)
sched.shutdown(wait=False)

# ─────────────────────────────────────────────────────────────────────────────
section("9. MESSAGE HANDLER — attributes and helpers")
# ─────────────────────────────────────────────────────────────────────────────

import message_handler as mh

for attr in ["TEMP_MEDIA_DIR","SECTION_META","_handle_incoming_media",
             "_execute_media_intent","handle_awaiting_media_intent_state",
             "handle_awaiting_section_confirmation_state",
             "_move_temp_to_section","_save_new_section_entry","_discard_temp_media"]:
    test(f"{attr} defined", hasattr(mh, attr))

# SECTION_META keys
test("SECTION_META has idea",     "idea"     in mh.SECTION_META)
test("SECTION_META has note",     "note"     in mh.SECTION_META)
test("SECTION_META has resource", "resource" in mh.SECTION_META)
test("SECTION_META has dump",     "dump"     in mh.SECTION_META)

# ─────────────────────────────────────────────────────────────────────────────
section("10. MESSAGE HANDLER — _save_new_section_entry writes to DB")
# ─────────────────────────────────────────────────────────────────────────────

database.add_allowed_user(TEST_PHONE, "Test")

for section_name, label in [("idea","idea"),("note","note"),("resource","resource"),("dump","dump")]:
    eid = mh._save_new_section_entry(TEST_PHONE, section_name, f"Test {label.capitalize()}", None, None, None, None)
    test(f"_save_new_section_entry({section_name}) → id>0", eid > 0, f"id={eid}")

# ─────────────────────────────────────────────────────────────────────────────
section("11. FRONTEND — database_queries user management")
# ─────────────────────────────────────────────────────────────────────────────

sys.path.insert(0, '/Volumes/Ahmad/Reminder-Bot App/frontend')
import database_queries as dq

df = dq.get_all_allowed_users()
test("get_all_allowed_users returns DataFrame",   hasattr(df, "columns"))
test("admin phone in DataFrame",                  config.ALLOWED_PHONE_NUMBER in df["phone"].values if not df.empty else False)

ok_f, msg_f = dq.add_allowed_user("92123456780", "FrontendTest")
test(f"dq.add_allowed_user succeeds",            ok_f, msg_f)

ok_d, msg_d = dq.remove_allowed_user("92123456780")
test(f"dq.remove_allowed_user succeeds",         ok_d, msg_d)

ok_a, _ = dq.remove_allowed_user(config.ALLOWED_PHONE_NUMBER)
test("dq blocks admin removal",                  not ok_a)

# ─────────────────────────────────────────────────────────────────────────────
# SUMMARY
# ─────────────────────────────────────────────────────────────────────────────
print(f"\n{'═'*60}")
passed = sum(results)
total  = len(results)
failed = total - passed
color  = "\033[92m" if failed == 0 else ("\033[93m" if failed <= 2 else "\033[91m")
print(f"{color}  RESULTS: {passed}/{total} passed, {failed} failed\033[0m")
print(f"{'═'*60}\n")
sys.exit(0 if failed == 0 else 1)
