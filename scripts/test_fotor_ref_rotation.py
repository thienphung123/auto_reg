from __future__ import annotations

import tempfile
from pathlib import Path

from sqlmodel import Session, create_engine, select

import core.db as db
from core.db import AccountModel


def main() -> None:
    original_engine = db.engine
    original_url = db.DATABASE_URL
    temp_dir = tempfile.TemporaryDirectory()
    db_path = Path(temp_dir.name) / "fotor_ref_rotation.db"
    temp_engine = None

    try:
        db.DATABASE_URL = f"sqlite:///{db_path.as_posix()}"
        temp_engine = create_engine(db.DATABASE_URL)
        db.engine = temp_engine
        db.init_db()

        with Session(db.engine) as session:
            session.add(
                AccountModel(
                    platform="fotor",
                    email="parent-a@example.com",
                    password="x",
                    ref_link="https://www.fotor.com/referrer/aaa111",
                    parent_email="MASTER",
                    referred_count=19,
                )
            )
            session.add(
                AccountModel(
                    platform="fotor",
                    email="parent-b@example.com",
                    password="x",
                    ref_link="https://www.fotor.com/referrer/bbb222",
                    parent_email="MASTER",
                    referred_count=20,
                )
            )
            session.commit()

        parent_email, ref_link = db.get_fotor_ref_parent("https://www.fotor.com/referrer/master000")
        print(f"[TEST] selected parent: {parent_email}")
        print(f"[TEST] selected ref_link: {ref_link}")

        db.increment_referral_count(parent_email)
        parent_email_2, ref_link_2 = db.get_fotor_ref_parent("https://www.fotor.com/referrer/master000")
        print(f"[TEST] after increment parent: {parent_email_2}")
        print(f"[TEST] after increment ref_link: {ref_link_2}")

        with Session(db.engine) as session:
            rows = session.exec(select(AccountModel).where(AccountModel.platform == "fotor")).all()
            print("[TEST] current referred_count snapshot:")
            for row in rows:
                print(
                    {
                        "email": row.email,
                        "ref_link": row.ref_link,
                        "parent_email": row.parent_email,
                        "referred_count": row.referred_count,
                    }
                )
    finally:
        if temp_engine is not None:
            temp_engine.dispose()
        db.engine = original_engine
        db.DATABASE_URL = original_url
        temp_dir.cleanup()


if __name__ == "__main__":
    main()
