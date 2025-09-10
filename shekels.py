import re
from typing import Union, List, Optional


def normalize_shekel_string(string: str) -> str:
    """
    Apply transformations that make strings match despite some unimportant variations.
    Examples:
    - שנים עשר שקלים
    - שתים עשרה שקל
    - שניים-עשר שקלים חדשים
    """
    # Remove non-Hebrew characters and spaces
    result = re.sub(r"[^א-ת ]", "", string)

    # Remove currency terms
    result = re.sub(r'(שקל חדש|שקלים חדשים|שקלים|שקל|שח|ש"ח|אגורה|אגורות)', "", result)

    # Remove "new" qualifiers
    result = re.sub(r" חדשים| חדש", "", result)

    # Male to female conversions
    result = re.sub(r"ה ", " ", result)
    result = re.sub(r"יי", "י", result)
    result = re.sub(r"שתי", "שני", result)
    result = re.sub(r"אחת", "אחד", result)
    result = re.sub(r"חמש", "חמיש", result)
    result = re.sub(r"שש", "שיש", result)
    result = re.sub(r"מליון", "מיליון", result)
    result = re.sub(r"(מליארד|מילירד)", "מיליארד", result)

    # Remove conjunctions
    result = re.sub(r"ו", "", result)

    # Normalize whitespace
    result = re.sub(r"\s+", " ", result)

    return result.strip()


def amount_to_shekels_in_hebrew(amount: Union[str, int, float, None]) -> str:
    """Convert a numeric amount to Hebrew shekel representation."""
    num_string = amount_to_num_string(amount)

    if num_string == "":
        return ""
    if num_string is None:
        return "לא הוקש סכום תקין"

    components = num_string.split(".")
    integer_part = shekels_to_hebrew(int(components[0]))
    cents_part = ""

    if len(components) > 1:
        _, cents_string = components
        if len(cents_string) == 1:
            cents_string += "0"
        cents_part = cents_to_hebrew(int(cents_string))

    return hebrew_join([integer_part, cents_part])


def hebrew_join(components: List[str]) -> str:
    """Join Hebrew components with proper conjunctions."""
    components = [x for x in components if x]

    if len(components) < 2:
        return " ".join(components)

    last = components.pop()
    return f"{' '.join(components)} ו{last}"


def amount_to_num_string(amount: Union[str, int, float, None]) -> Optional[str]:
    """Convert amount to a valid number string or return None if invalid."""
    if not amount and amount != 0:
        return ""

    try:
        num = float(amount)
    except (ValueError, TypeError):
        return None

    num_string = str(num)

    # Check if it matches the expected pattern (up to 12 digits, max 2 decimal places)
    if not re.match(r"^\d{1,12}(\.\d{1,2})?$", num_string):
        return None

    return num_string


def hebrew_concat(a: str, b: str) -> str:
    """Concatenate Hebrew strings with proper spacing."""
    return " ".join([a, b])


def shekel_integer_to_hebrew(amount: int) -> str:
    """Convert integer amount to Hebrew shekel representation."""
    amount_str = str(amount)
    component_names = ["", "אלף", "מיליון", "מיליארד"]
    int_components = []

    i = len(amount_str)
    while i > 0:
        i -= 3
        component = int(amount_str[max(0, i) : i + 3])
        int_components.append(component)

    hebrew_components = []
    for idx, component in enumerate(int_components):
        component_name = component_names[idx] if idx < len(component_names) else ""
        hebrew_components.extend(
            component_to_hebrew_components(component, component_name)
        )

    hebrew_components.reverse()
    result = hebrew_join(hebrew_components)
    return hebrew_concat(result, "שקלים")


def component_to_hebrew_components(value: int, component_name: str) -> List[str]:
    """Convert a 3-digit component to Hebrew components."""
    if not isinstance(value, int) or value < 0 or value > 999:
        raise ValueError(f"Logical error: {value} is not a 3 digit number")

    if value == 0:
        return []

    units = {
        1: "אחד",
        2: "שניים",
        3: "שלושה",
        4: "ארבעה",
        5: "חמישה",
        6: "שישה",
        7: "שבעה",
        8: "שמונה",
        9: "תשעה",
    }

    teens = {
        11: "אחד עשר",
        12: "שנים עשר",
        13: "שלושה עשר",
        14: "ארבעה עשר",
        15: "חמישה עשר",
        16: "שישה עשר",
        17: "שבעה עשר",
        18: "שמונה עשר",
        19: "תשעה עשר",
    }

    tens = {
        1: "עשרה",
        2: "עשרים",
        3: "שלושים",
        4: "ארבעים",
        5: "חמישים",
        6: "שישים",
        7: "שבעים",
        8: "שמונים",
        9: "תשעים",
    }

    hundreds = {
        1: "מאה",
        2: "מאתיים",
        3: "שלוש מאות",
        4: "ארבע מאות",
        5: "חמש מאות",
        6: "שש מאות",
        7: "שבע מאות",
        8: "שמונה מאות",
        9: "תשע מאות",
    }

    thousands = {
        1: "אלף",
        2: "אלפיים",
        3: "שלושת אלפים",
        4: "ארבעת אלפים",
        5: "חמשת אלפים",
        6: "ששת אלפים",
        7: "שבעת אלפים",
        8: "שמונת אלפים",
        9: "תשעת אלפים",
        10: "עשרת אלפים",
    }

    hebrew_components = []

    if component_name == "אלף" and value in thousands:
        component_name = thousands[value]
    elif value == 1 and component_name:
        pass  # Special case for "one thousand", etc.
    elif value == 2:
        hebrew_components.append("שני")
    elif value in teens:
        hebrew_components.append(teens[value])
    else:
        u = value % 10  # units
        t = (value % 100) // 10  # tens
        h = value // 100  # hundreds

        if 10 * t + u in teens:
            hebrew_components.append(teens[10 * t + u])
        else:
            if u in units:
                hebrew_components.append(units[u])
            if t in tens:
                hebrew_components.append(tens[t])

        if h in hundreds:
            hebrew_components.append(hundreds[h])

    if component_name:
        # Join components and add component name
        hebrew = hebrew_join(list(reversed(hebrew_components)))
        return [hebrew_concat(hebrew, component_name)]

    return hebrew_components


def shekels_to_hebrew(amount: int) -> str:
    """Convert shekel amount to Hebrew."""
    if amount:
        if amount == 1:
            return "שקל אחד"
        else:
            return shekel_integer_to_hebrew(amount)
    return ""


def cents_to_hebrew(value: int) -> str:
    """Convert cents (agorot) to Hebrew."""
    if not value:
        return ""

    if value == 1:
        return "אגורה"

    units = {
        1: "אחת",
        2: "שתיים",
        3: "שלוש",
        4: "ארבע",
        5: "חמש",
        6: "שש",
        7: "שבע",
        8: "שמונה",
        9: "תשע",
    }

    teens = {
        11: "אחת עשרה",
        12: "שתים עשרה",
        13: "שלוש עשרה",
        14: "ארבע עשרה",
        15: "חמש עשרה",
        16: "שש עשרה",
        17: "שבע עשרה",
        18: "שמונה עשרה",
        19: "תשע עשרה",
    }

    tens = {
        1: "עשר",
        2: "עשרים",
        3: "שלושים",
        4: "ארבעים",
        5: "חמישים",
        6: "שישים",
        7: "שבעים",
        8: "שמונים",
        9: "תשעים",
    }

    u = value % 10
    t = value // 10

    hebrew_components = []

    if value == 2:
        hebrew_components.append("שתי")
    elif value in teens:
        hebrew_components.append(teens[value])
    else:
        if 10 * t + u in teens:
            hebrew_components.append(teens[10 * t + u])
        else:
            if u in units:
                hebrew_components.append(units[u])
            if t in tens:
                hebrew_components.append(tens[t])

    hebrew = hebrew_join(list(reversed(hebrew_components)))
    return hebrew_concat(hebrew, "אגורות")
