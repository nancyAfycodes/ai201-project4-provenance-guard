"""
test_combined_scoring.py

Milestone 4 checkpoint test: runs both signals + confidence scoring on
4 deliberately chosen inputs (1 clearly AI, 1 clearly human, 2 borderline)
and prints everything needed to judge whether the combined score matches
intuition — per the Milestone 4 checklist.

Run with:
    python test_combined_scoring.py

Requires GROQ_API_KEY to be set (Signal 1 needs real network access).
"""

from dotenv import load_dotenv
load_dotenv()

from signals.llm_signal import get_llm_score
from signals.stylometric_signal import get_stylometric_score
from confidence_scoring import combine_scores, determine_attribution_result, determine_direction

TEST_CASES = {
    "clearly_ai": (
        "Artificial intelligence represents a transformative paradigm shift "
        "in modern society. It is important to note that while the benefits "
        "of AI are numerous, it is equally essential to consider the "
        "ethical implications. Furthermore, stakeholders across various "
        "sectors must collaborate to ensure responsible deployment."
    ),
    "clearly_human": (
        "ok so i finally tried that new ramen place downtown and honestly? "
        "underwhelming. the broth was fine but they put WAY too much "
        "sodium in it and i was thirsty for like three hours after. my "
        "friend got the spicy version and said it was better. probably "
        "won't go back unless someone drags me there"
    ),
    "borderline_formal_human": (
        "The relationship between monetary policy and asset price "
        "inflation has been extensively studied in the literature. "
        "Central banks face a fundamental tension between their mandate "
        "for price stability and the unintended consequences of prolonged "
        "low interest rates on equity and real estate valuations."
    ),
    "borderline_edited_ai": (
        "I've been thinking a lot about remote work lately. There are "
        "genuine tradeoffs — flexibility and no commute on one side, "
        "isolation and blurred work-life boundaries on the other. Studies "
        "show productivity varies widely by individual and role type."
    ),
}

if __name__ == "__main__":
    header = (
        f"{'case':<25} {'llm':<6} {'stylo':<6} {'spread':<7} "
        f"{'agree':<6} {'combined':<9} {'result':<10} direction"
    )
    print(header)
    print("-" * len(header))

    for name, text in TEST_CASES.items():
        llm_result = get_llm_score(text)
        stylo_result = get_stylometric_score(text)

        llm_score = llm_result["llm_score"]
        stylo_score = stylo_result["stylo_score"]

        scoring = combine_scores(llm_score, stylo_score)
        combined = scoring["combined_score"]
        result = determine_attribution_result(combined)
        direction = determine_direction(combined) if result == "uncertain" else "—"

        print(
            f"{name:<25} {llm_score:<6.2f} {stylo_score:<6.2f} "
            f"{scoring['spread']:<7.2f} {str(scoring['signals_agree']):<6} "
            f"{combined:<9.3f} {result:<10} {direction}"
        )

        if llm_result["error"]:
            print(f"    ! signal_1 error: {llm_result['error']}")
        if not stylo_result["reliable"]:
            print(f"    ! signal_2 reliability warning: text may be too short for stable stylometric stats")
