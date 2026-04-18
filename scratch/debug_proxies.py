from sqlmodel import Session, create_engine, select
from core.db import ProxyModel
import os
from pathlib import Path

APP_ROOT = Path("f:/vibecode/auto_reg")
DATABASE_FILE = APP_ROOT / "account_manager.db"
DATABASE_URL = f"sqlite:///{DATABASE_FILE.as_posix()}"
engine = create_engine(DATABASE_URL)

def check_proxies():
    with Session(engine) as session:
        all_proxies = session.exec(select(ProxyModel)).all()
        print(f"Total proxies: {len(all_proxies)}")
        active = [p for p in all_proxies if p.is_active]
        disabled = [p for p in all_proxies if not p.is_active]
        print(f"Active: {len(active)}")
        print(f"Disabled: {len(disabled)}")
        
        # Check a few disabled proxies
        for i, p in enumerate(disabled[:5]):
            print(f"Disabled {i}: id={p.id}, active={p.is_active}, url={p.url[:30]}...")

if __name__ == "__main__":
    check_proxies()
