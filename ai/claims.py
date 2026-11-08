from ai.schemas import CLAIM_FIELDS, Claim


def iter_claims(structured: object) -> list[tuple[str, Claim]]:
    rows: list[tuple[str, Claim]] = []
    for field_name in CLAIM_FIELDS:
        for claim in getattr(structured, field_name, []):
            rows.append((field_name, claim))
    return rows
