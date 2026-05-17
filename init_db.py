from __future__ import annotations

from db import db_counts, seed_all


if __name__ == "__main__":
    seeded = seed_all()
    counts = db_counts()
    print("Seeded:", seeded)
    print("Counts:", counts)
