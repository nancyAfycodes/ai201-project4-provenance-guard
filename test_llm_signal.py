"""
test_llm_signal.py

Standalone test for Signal 1 (Groq LLM detector), run BEFORE wiring it
into the Flask endpoint — per Milestone 3 checklist and the AI Tool Plan
in planning.md.

Run with:
    python test_llm_signal.py

Requires GROQ_API_KEY to be set in your environment / .env file.
"""

from dotenv import load_dotenv
load_dotenv()

from signals.llm_signal import get_llm_score

TEST_CASES = [
    (
        "clearly_ai_leaning",
        "In conclusion, it is important to note that artificial "
        "intelligence has become an increasingly significant technology "
        "in today's fast-paced digital landscape. As we move forward, it "
        "is crucial to consider the various implications and "
        "opportunities that this presents for individuals and "
        "organizations alike.",
    ),
    (
        "clearly_human_leaning",
        "the coffee went cold an hour ago and i still haven't touched it. "
        "my sister called twice, didn't pick up either time. there's a "
        "crack in the ceiling shaped like Florida if you squint, my ex "
        "used to point it out every single morning like it was some kind "
        "of omen.",
    ),
    (
        "short_poem",
        "Rain on the tin roof,\nold dog sighs at my cold feet,\nMarch will "
        "not hurry.",
    ),
    (
        "empty",
        "",
    ),
]

if __name__ == "__main__":
    print(f"{'Case':<22} {'llm_score':<12} {'error':<20} reasoning")
    print("-" * 90)
    for name, text in TEST_CASES:
        result = get_llm_score(text)
        print(
            f"{name:<22} {result['llm_score']:<12.3f} "
            f"{str(result['error']):<20} {result['reasoning']}"
        )
