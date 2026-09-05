"""
migrate_media_paths.py
──────────────────────
One-time migration script: converts absolute media file paths stored in the
database to relative paths (relative to the project root).

Run once after upgrading to the relative-path system:
    conda run -n reminderBot python migrate_media_paths.py

Safe to run multiple times — already-relative paths are left untouched.
"""

import os
import sqlite3

# ── Paths ─────────────────────────────────────────────────────────────────────
BASE_DIR = os.path.dirname(os.path.abspath(__file__))
DB_PATH  = os.path.join(BASE_DIR, "reminder_bot.db")

# Tables and their media columns
TARGETS = [
    ("ideas",     "media_path"),
    ("notes",     "media_path"),
    ("resources", "media_path"),
    ("dumps",     "media_path"),
]

def migrate():
    if not os.path.exists(DB_PATH):
        print(f"❌ Database not found at: {DB_PATH}")
        return

    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    cursor = conn.cursor()

    total_updated = 0

    for table, col in TARGETS:
        # Fetch all rows that have a non-null media path
        cursor.execute(f"SELECT id, {col} FROM {table} WHERE {col} IS NOT NULL AND {col} != ''")
        rows = cursor.fetchall()

        updated = 0
        for row in rows:
            old_path = row[col]

            # Skip if already relative
            if not os.path.isabs(old_path):
                continue

            # Convert to relative
            try:
                new_path = os.path.relpath(old_path, BASE_DIR)
            except ValueError:
                # On Windows, relpath can fail across drives — skip
                print(f"  ⚠️  [{table}] id={row['id']} — could not make relative (skipping): {old_path}")
                continue

            cursor.execute(
                f"UPDATE {table} SET {col} = ? WHERE id = ?",
                (new_path, row["id"])
            )
            updated += 1
            print(f"  ✅ [{table}] id={row['id']}: converted\n"
                  f"       FROM: {old_path}\n"
                  f"       TO:   {new_path}")

        print(f"[{table}] {updated} path(s) updated.")
        total_updated += updated

    conn.commit()
    conn.close()

    print(f"\n🎉 Migration complete. {total_updated} total path(s) updated.")

if __name__ == "__main__":
    migrate()
