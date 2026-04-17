import os
import sys

from core.base_mailbox import create_mailbox
from core.base_platform import RegisterConfig
from platforms.fotor.plugin import FotorPlatform


def build_config(provider: str) -> RegisterConfig:
    extra = {
        "mail_provider": provider,
        "fotor_ref_link": "https://www.fotor.com/referrer/ce1yh8e7",
        "headless": False,
    }

    if provider == "duckmail":
        extra.update(
            {
                "duckmail_api_url": os.getenv("DUCKMAIL_API_URL", "https://www.duckmail.sbs"),
                "duckmail_provider_url": os.getenv("DUCKMAIL_PROVIDER_URL", "https://api.duckmail.sbs"),
                "duckmail_bearer": os.getenv("DUCKMAIL_BEARER", "kevin273945"),
                "duckmail_api_key": os.getenv("DUCKMAIL_API_KEY", ""),
                "duckmail_domain": os.getenv("DUCKMAIL_DOMAIN", ""),
            }
        )

    return RegisterConfig(executor_type="headed", extra=extra)


def main() -> None:
    provider = (sys.argv[1] if len(sys.argv) > 1 else "duckmail").strip().lower()
    config = build_config(provider)
    mailbox = None if provider == "tempmail" else create_mailbox(provider, extra=config.extra, proxy=config.proxy)
    platform = FotorPlatform(config=config, mailbox=mailbox)

    print(f"[TEST] Provider: {provider}")
    print(f"[TEST] Config extra: {config.extra}")

    result = platform.create_account(
        context=None,
        email=None,
        mailbox=mailbox,
        password="Aa1!TestFlow2026",
        headless=False,
        config=config,
    )
    print("[TEST] CREATE_ACCOUNT SUCCESS")
    print(result)


if __name__ == "__main__":
    main()
