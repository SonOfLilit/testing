import random
from dotenv import load_dotenv
import pandas as pd
from pydantic import BaseModel, Field
from pydantic_ai import Agent
import logfire
import re
from pydantic_evals import Case, Dataset
from pydantic_evals.evaluators import Evaluator, EvaluatorContext
from typing import Any

logfire.configure(scrubbing=False)
logfire.instrument_pydantic_ai()
logfire.instrument_httpx(capture_request_body=True, capture_response_body=True)

# Set up your API key
load_dotenv()
# os.environ['GOOGLE_API_KEY'] = 'your-api-key-here'

TOPICS = [
    "rabbits in the rain",
    "rubik's cube",
    "asmodeus",
    "midnight coffee",
    "dancing shadows",
    "crystal mountains",
    "forgotten dreams",
    "ocean waves",
    "starlight journey",
    "autumn leaves",
    "silver moon",
    "golden sunrise",
    "winter storms",
    "summer breeze",
    "ancient ruins",
    "mystical forest",
    "frozen lake",
    "burning candle",
    "quiet library",
    "busy marketplace",
]
random.shuffle(TOPICS)
N = 10
assert len(TOPICS) == 2 * N
TRAIN_TOPICS = TOPICS[:N]
TEST_TOPICS = TOPICS[N:]


class PalindromeGeneration(BaseModel):
    palindrome: str = Field(description="The generated palindrome")


class PalindromeEvaluation(BaseModel):
    sense_score: int = Field(description="How much sense it makes (1-10)", ge=1, le=10)
    topic_score: int = Field(description="How on topic it is (1-10)", ge=1, le=10)
    germanic_count: int = Field(
        description="Number of Germanic words with 4+ letters", ge=0
    )
    romance_count: int = Field(
        description="Number of Romance words with 4+ letters", ge=0
    )
    reasoning: str = Field(description="Brief explanation of the evaluation")


EMPTY_GENERATION = PalindromeGeneration(palindrome="XY")

EMPTY_EVALUATION = PalindromeEvaluation(
    sense_score=1, topic_score=1, germanic_count=0, romance_count=0, reasoning=""
)

# Agent for generating palindromes
# TODO: maybe we could do some Chain of Thought or other kind of reasoning?
generator_agent = Agent(
    model="google-gla:gemini-2.0-flash-lite",
    output_type=PalindromeGeneration,
    system_prompt="""You are a creative palindrome generator. Create palindromes that:
1. Are related to the given topic
2. Make grammatical and semantic sense
3. Use a balanced mix of Germanic and Romance language words of 4+ letters
""",
)

# Agent for evaluating palindromes
evaluator_agent = Agent(
    model="google-gla:gemini-2.0-flash-lite",
    output_type=PalindromeEvaluation,
    system_prompt="""You are a creative writing teacher. Evaluate this short text on these criteria:

1. Sense (1-10): How grammatically correct and semantically meaningful is it?
2. Topic relevance (1-10): How well does it relate to the assigned topic?
3. Germanic word count: Count words with 4+ letters that come from Germanic origins
4. Romance word count: Count words with 4+ letters that come from Romance origins
""",
)


def is_palindrome(text: str) -> bool:
    """Check if text is a palindrome (ignoring spaces, punctuation, case)"""
    cleaned = re.sub(r"[^a-zA-Z0-9]", "", text.lower())
    return cleaned == cleaned[::-1]


def calculate_balance_difference(germanic_count: int, romance_count: int) -> int:
    """Calculate the absolute difference between Germanic and Romance word counts"""
    return abs(germanic_count - romance_count)


class PalindromeAccuracyEvaluator(Evaluator[str, dict]):
    def evaluate(self, ctx: EvaluatorContext[str, dict]) -> dict[str, Any]:
        if not ctx.output:
            return {
                "palindrome_score": 0.0,
                "average_score": 0.0,
            }

        return ctx.output


@logfire.instrument
def generate_palindrome(topic: str) -> PalindromeGeneration:
    prompt = f"Create a palindrome related to the topic: {topic}"

    with logfire.span("palindrome_generation", topic=topic):
        try:
            result = generator_agent.run_sync(prompt)
            logfire.info(
                f"Generated palindrome for topic '{topic}': {result.output.palindrome}"
            )
            return result.output
        except Exception as e:
            logfire.exception("Palindrome generation failed", error=str(e), topic=topic)
            return EMPTY_GENERATION


@logfire.instrument
def evaluate_palindrome(topic: str, palindrome: str) -> PalindromeEvaluation:
    prompt = f"Evaluate this palindrome for the topic '{topic}': {palindrome}"

    with logfire.span("palindrome_evaluation", topic=topic, palindrome=palindrome):
        try:
            result = evaluator_agent.run_sync(prompt)
            logfire.info(
                f"Evaluated palindrome: scores {result.output.sense_score}/{result.output.topic_score}"
            )
            return result.output
        except Exception as e:
            logfire.exception("Palindrome evaluation failed", error=str(e))
            return EMPTY_EVALUATION


def create_logfire_dataset(data: list[dict]) -> Dataset[str, dict]:
    """Convert data to Logfire evals Dataset"""
    cases = [
        Case(
            name=item["topic"],
            inputs=item["topic"],
            expected_output={},  # No expected output for generative task
        )
        for i, item in enumerate(data)
    ]
    return Dataset(
        cases=cases,
        evaluators=[PalindromeAccuracyEvaluator()],
    )


@logfire.instrument
def process_topics(topics: list[str]) -> list[dict]:
    results = []

    for topic in topics:
        print(f"Processing topic: {topic}")

        generation = generate_palindrome(topic)
        evaluation = evaluate_palindrome(topic, generation.palindrome)
        is_valid_palindrome = is_palindrome(generation.palindrome)
        palindrome_score = 10 * is_valid_palindrome

        sense_score = evaluation.sense_score
        topic_score = evaluation.topic_score
        germanic_count = evaluation.germanic_count
        romance_count = evaluation.romance_count

        balance_diff = calculate_balance_difference(germanic_count, romance_count)
        balance_score = 10 / (balance_diff + 1)
        average_score = (
            palindrome_score + sense_score + topic_score + balance_score
        ) / 4

        result = {
            "topic": topic,
            "palindrome": generation.palindrome,
            "average_score": average_score,
            "palindrome_score": palindrome_score,
            "sense_score": evaluation.sense_score,
            "topic_score": evaluation.topic_score,
            "balance_score": balance_score,
            "germanic_count": evaluation.germanic_count,
            "romance_count": evaluation.romance_count,
            "evaluation_reasoning": evaluation.reasoning,
            "balance_difference": balance_diff,
        }

        results.append(result)

    return results


def find_best_palindromes(results: list[dict], top_k: int = 5) -> list[dict]:
    """Find the best palindromes based on overall quality"""
    valid_palindromes = list(results)
    valid_palindromes.sort(key=lambda x: x["average_score"], reverse=True)
    return valid_palindromes[:top_k]


def find_worst_palindromes(results: list[dict], top_k: int = 5) -> list[dict]:
    """Find the worst results for analysis"""
    valid_palindromes = list(results)
    valid_palindromes.sort(key=lambda x: x["average_score"], reverse=True)
    return valid_palindromes[:top_k]


def display_best_palindromes(palindromes: list[dict]):
    """Display the best palindromes"""
    if not palindromes:
        print("🚨 No valid palindromes found!")
        return

    print(f"\n🏆 BEST PALINDROMES (Top {len(palindromes)})")
    for i, p in enumerate(palindromes, 1):
        print(f"\n{i}. Topic: {p['topic']}")
        print(f'   Palindrome: "{p["palindrome"]}"')
        print(f"   Scores: Sense {p['sense_score']}/10, Topic {p['topic_score']}/10")
        print(
            f"   Words: Germanic {p['germanic_count']}, Romance {p['romance_count']} (diff: {p['balance_difference']})"
        )
        print(f"   Reasoning: {p['evaluation_reasoning']}")


def display_worst_results(results: list[dict]):
    """Display the worst results for debugging"""
    if not results:
        return

    print(f"\n🚨 AREAS FOR IMPROVEMENT (Bottom {len(results)})")
    for i, r in enumerate(results, 1):
        print(f"\n{i}. Topic: {r['topic']}")
        print(f'   Result: "{r["palindrome"]}"')
        print(f"   Valid palindrome: {r['palindrome_score'] > 0}")
        print(f"   Scores: Sense {r['sense_score']}/10, Topic {r['topic_score']}/10")
        print(f"   Issue: {r['evaluation_reasoning']}")


def main(topics: list[str]):
    """Main execution function"""
    print(f"📝 Processing {len(topics)} topics")
    results = process_topics(topics)

    df = pd.DataFrame(results)
    filename = "palindrome_results.csv"
    df.to_csv(filename, index=False)
    print(f"\n💾 Saved results to {filename}")

    # Show best and worst examples
    best_palindromes = find_best_palindromes(results)
    display_best_palindromes(best_palindromes)

    worst_results = find_worst_palindromes(results)
    display_worst_results(worst_results)

    # Logfire evaluation
    results_lookup = {r["topic"]: r for r in results}
    logfire_dataset = create_logfire_dataset(results)
    eval_report = logfire_dataset.evaluate_sync(
        lambda topic: results_lookup.get(topic, {})
    )
    eval_report.print(include_input=True, include_output=False)


if __name__ == "__main__":
    import sys

    _, chosen_split = sys.argv
    topics = {"train": TRAIN_TOPICS, "test": TEST_TOPICS}
    main(topics[chosen_split])
