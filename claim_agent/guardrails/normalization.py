import re
from datetime import datetime
from typing import Any, Dict, Optional


def normalize_name(value: Optional[str]) -> Optional[str]:
    if not value:
        return None
    return " ".join(value.strip().split())


def normalize_date(value: Optional[str]) -> Optional[str]:
    if not value:
        return None

    raw = re.sub(r"(?<=\d)(?:st|nd|rd|th)\b", "", value.strip(), flags=re.I)
    for pattern in (
        "%Y-%m-%d", "%Y/%m/%d", "%B %d, %Y", "%b %d, %Y",
        "%B %d %Y", "%b %d %Y", "%d %B %Y", "%d %b %Y",
    ):
        try:
            return datetime.strptime(raw, pattern).strftime("%Y-%m-%d")
        except ValueError:
            continue

    # Accept common single-digit month/day forms such as 1985-3-15.
    match = re.fullmatch(r"(\d{4})[-/](\d{1,2})[-/](\d{1,2})", raw)
    if match:
        try:
            return datetime(
                int(match.group(1)), int(match.group(2)), int(match.group(3))
            ).strftime("%Y-%m-%d")
        except ValueError:
            return None
    return None


def normalize_phone(value: Optional[str]) -> Optional[str]:
    if not value:
        return None

    digits = re.sub(r"\D", "", value)
    if len(digits) == 10:
        return "+1" + digits
    if len(digits) == 11 and digits.startswith("1"):
        return "+" + digits
    return "+" + digits if digits else None


def normalize_last4(value: Optional[str]) -> Optional[str]:
    if not value:
        return None
    digits = re.sub(r"\D", "", value)
    return digits[-4:] if len(digits) >= 4 else None


def normalize_policy_number(value: Optional[str]) -> Optional[str]:
    if not value:
        return None

    raw = value.strip().upper().replace(" ", "")
    match = re.fullmatch(r"POL-?(\d+)", raw)
    if match:
        return f"POL-{match.group(1)}"
    return raw


def normalize_pii(pii: Dict[str, Any]) -> Dict[str, Any]:
    """Convert extracted identity fields into the application's canonical form."""
    result = dict(pii)

    if result.get("name"):
        result["name"] = normalize_name(result["name"])
    if result.get("dob"):
        result["dob"] = normalize_date(result["dob"])
    if result.get("phone"):
        result["phone"] = normalize_phone(result["phone"])
    if result.get("email"):
        result["email"] = result["email"].strip().lower()
    if result.get("id_last4"):
        result["id_last4"] = normalize_last4(result["id_last4"])
    if result.get("policy_number"):
        result["policy_number"] = normalize_policy_number(result["policy_number"])
    if result.get("id_type"):
        result["id_type"] = result["id_type"].strip().lower()

    return {key: value for key, value in result.items() if value not in (None, "")}


def normalize_hints(hints: Dict[str, Any]) -> Dict[str, Any]:
    result = dict(hints)
    if result.get("case_id"):
        result["case_id"] = result["case_id"].strip().upper()
    for key in ("case_type", "status"):
        if result.get(key):
            result[key] = result[key].strip().lower()
    if result.get("month"):
        result["month"] = result["month"].strip().capitalize()
    if result.get("year"):
        result["year"] = str(result["year"]).strip()
    return {key: value for key, value in result.items() if value not in (None, "")}


def validate_pii_formats(pii: Dict[str, Any]) -> Dict[str, str]:
    """Return user-correctable format errors without checking identity ownership."""
    errors: Dict[str, str] = {}

    if pii.get("name"):
        name = str(pii["name"]).strip()
        if len(re.findall(r"[A-Za-zÀ-ÖØ-öø-ÿ]", name)) < 2 or re.search(r"\d", name):
            errors["name"] = "use a real full name, such as Margaret Chen"

    if pii.get("dob") and normalize_date(str(pii["dob"])) is None:
        errors["dob"] = "use a valid date in YYYY-MM-DD format"

    if pii.get("phone"):
        digits = re.sub(r"\D", "", str(pii["phone"]))
        if len(digits) not in (10, 11) or (len(digits) == 11 and not digits.startswith("1")):
            errors["phone"] = "use a valid 10-digit phone number"

    if pii.get("email"):
        email = str(pii["email"]).strip()
        if not re.fullmatch(r"[^\s@]+@[^\s@]+\.[^\s@]+", email):
            errors["email"] = "use an email such as name@example.com"

    if pii.get("id_last4"):
        digits = re.sub(r"\D", "", str(pii["id_last4"]))
        if not re.fullmatch(r"\d{4}", digits):
            errors["id_last4"] = "provide exactly four digits"

    if pii.get("policy_number"):
        if not re.fullmatch(r"POL-?\d+", str(pii["policy_number"]).strip(), re.IGNORECASE):
            errors["policy_number"] = "use a policy number such as POL-9921"

    return errors


def find_pii_conflicts(existing: Dict[str, Any], incoming: Dict[str, Any]) -> Dict[str, str]:
    """Find fields whose new value contradicts an already collected value."""
    old = normalize_pii(existing)
    new = normalize_pii(incoming)
    conflicts: Dict[str, str] = {}
    for field in ("name", "dob", "phone", "email", "id_last4", "policy_number"):
        if old.get(field) and new.get(field) and old[field] != new[field]:
            conflicts[field] = f"received both {old[field]} and {new[field]}"
    return conflicts
