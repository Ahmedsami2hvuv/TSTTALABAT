import re


_ARABIC_DIGITS = str.maketrans("٠١٢٣٤٥٦٧٨٩", "0123456789")


def normalize_text(s: str) -> str:
    if not s:
        return ""
    s = s.translate(_ARABIC_DIGITS)
    s = s.replace("ـ", "")
    s = re.sub(r"\s+", " ", s).strip()
    return s


def parse_quantity_kg(text: str) -> float | None:
    """
    يحاول يستخرج الكمية بالكيلو من النص:
    - ربع/نص/ثلاث ارباع
    - كيلو/كيلوين/3ك/2 ك/1.5
    - كيلو ونص / 1ك ونص / 1 ك ونص
    يرجع None إذا ما لقى شيء.
    """
    t = normalize_text(text)
    if not t:
        return None

    # كلمات الكسور
    if re.search(r"\bربع\b", t):
        return 0.25
    if re.search(r"\bنص\b", t) or re.search(r"\bنصف\b", t):
        # لاحقاً قد يجي "كيلو ونص" (يعالج تحت)
        pass
    if re.search(r"ثلاث\s*ارباع|3\s*ارباع", t):
        return 0.75

    # "كيلو ونص" وأشباهها
    if re.search(r"(كيلو|ك)\s*و\s*نص", t):
        return 1.5
    if re.search(r"\b1\s*(كيلو|ك)\s*و\s*نص\b", t):
        return 1.5

    # "كيلوين/كيلوَين"
    if re.search(r"\bكيلوين\b", t):
        return 2.0
    if re.search(r"\bثلاث\s*كيلو\b", t):
        return 3.0

    # أرقام صريحة (1.5, 2, 3) مع ك/كيلو
    m = re.search(r"(\d+(?:\.\d+)?)\s*(?:ك|كغم|كيلو)\b", t)
    if m:
        return float(m.group(1))

    # رقم لوحده داخل النص (آخر حل)
    m2 = re.search(r"\b(\d+(?:\.\d+)?)\b", t)
    if m2:
        return float(m2.group(1))

    # "نص" لوحدها
    if re.search(r"\bنص\b|\bنصف\b", t):
        return 0.5

    return None


def _match_meat_base(text: str) -> str | None:
    t = normalize_text(text)
    if not t:
        return None

    # أولوية المطابقة
    if "ضلوع" in t:
        return "ضلوع"
    if "مثروم" in t or "مفروم" in t:
        return "مثروم"
    if "شرح" in t or "شرائح" in t:
        return "شرح"
    # لحم عظم: إذا موجود "عظم" أو "بعظم"
    if "عظم" in t:
        return "لحم عظم"
    # إذا مجرد "لحم" بدون توصيف، نعتبره لحم عظم كافتراضي
    if "لحم" in t:
        return "لحم عظم"
    return None


DEFAULT_MEAT_PRICES_PER_KG = {
    "لحم عظم": {"buy": 13.0, "sell": 16.0},
    "شرح": {"buy": 14.0, "sell": 18.0},
    "مثروم": {"buy": 14.0, "sell": 18.0},
    "ضلوع": {"buy": 12.0, "sell": 14.0},
}


def suggest_fixed_prices(product_text: str, price_table: dict | None = None) -> dict | None:
    """
    يرجع اقتراح:
    {
      base: str,
      qty_kg: float,
      buy_total: float,
      sell_total: float
    }
    أو None إذا المنتج مو ضمن الجدول.
    """
    base = _match_meat_base(product_text)
    if not base:
        return None

    table = price_table or DEFAULT_MEAT_PRICES_PER_KG
    if base not in table:
        return None

    qty = parse_quantity_kg(product_text)
    if qty is None:
        qty = 1.0

    buy_per = float(table[base]["buy"])
    sell_per = float(table[base]["sell"])
    return {
        "base": base,
        "qty_kg": float(qty),
        "buy_total": buy_per * float(qty),
        "sell_total": sell_per * float(qty),
    }

