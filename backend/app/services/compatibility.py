from app.schemas.compatibility import CompatibilityScore, MatchCandidate


def calculate_score(user_a_id: int, user_b_id: int) -> CompatibilityScore:
    # TODO: Replace with real rule-based saju compatibility logic
    # Basic placeholder: fixed base score, no real calculation
    score = 72
    return CompatibilityScore(
        user_a_id=user_a_id,
        user_b_id=user_b_id,
        score=score,
        summary=None,  # Populated by knowledge retrieval layer later
    )


def get_placeholder_matches(user_id: int) -> list[MatchCandidate]:
    # TODO: Query real users from DB, score each, return top candidates
    return [
        MatchCandidate(user_id=2, score=72, is_blinded=True),
        MatchCandidate(user_id=3, score=65, is_blinded=True),
    ]
