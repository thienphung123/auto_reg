import re
from core.base_platform import RegisterConfig
from platforms.fotor.plugin import FotorPlatform


def extract_domain_from_exception(exc_msg: str) -> str:
    """Extract email domain from timeout error message"""
    # Format: "[TEMP-MAILO] Timeout (15s) - Fotor chưa gửi OTP đến {domain}..."
    match = re.search(r"đến\s+([a-zA-Z0-9\-\.]+\.[a-zA-Z]{2,})", exc_msg)
    if match:
        return match.group(1)
    return ""


def main() -> None:
    config = RegisterConfig(
        executor_type="headed",
        extra={
            "fotor_ref_link": "https://www.fotor.com/referrer/ce1yh8e7",
        },
    )
    platform = FotorPlatform(config=config)
    
    blacklisted_domains = set()
    max_retries = 3
    
    for attempt in range(1, max_retries + 1):
        try:
            print(f"\n[TEST] === Attempt {attempt}/{max_retries} ===")
            if blacklisted_domains:
                print(f"[TEST] Blacklisted domains: {blacklisted_domains}")
            
            result = platform.create_account(
                email=None,
                password="Aa1!TestFlow2026",
                headless=False,
            )
            
            print("[TEST] ✓✓✓ CREATE_ACCOUNT SUCCESS!")
            print("[TEST] create_account result:")
            print(result)
            return  # Success, exit
            
        except Exception as exc:
            exc_msg = str(exc)
            print(f"[TEST] ✗ Attempt {attempt} failed: {exc_msg}")
            
            # Check if this is a timeout from temp-mailo (mail domain blocked)
            if "Timeout" in exc_msg and "temp-mailo" in exc_msg.lower():
                domain = extract_domain_from_exception(exc_msg)
                if domain:
                    blacklisted_domains.add(domain)
                    print(f"[TEST] 🚫 Blacklisted domain: {domain}")
                
                if attempt < max_retries:
                    print(f"[TEST] Retrying with new email address...")
                    continue
                else:
                    print(f"[TEST] ✗ All {max_retries} attempts failed!")
                    print(f"[TEST] Final blacklist: {blacklisted_domains}")
                    raise RuntimeError(f"Failed to create account after {max_retries} attempts with domains: {blacklisted_domains}")
            else:
                # Other errors, re-raise
                raise


if __name__ == "__main__":
    main()

