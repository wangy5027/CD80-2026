from decimal import Decimal

def run(payload):
    total = sum((Decimal(value) for value in payload["values"]), Decimal("0"))
    return {"total": format(total, "f")}
