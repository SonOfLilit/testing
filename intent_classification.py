from decimal import Decimal
import itertools
import math
import traceback
from dotenv import load_dotenv
import pandas as pd
import numpy as np
from enum import Enum
from pydantic import BaseModel, Field
from pydantic_ai import Agent
import logfire
from datasets import load_dataset, IterableDataset
import random
from sklearn.metrics import (
    accuracy_score,
    precision_recall_fscore_support,
)
from pydantic_evals import Case, Dataset
from pydantic_evals.evaluators import Evaluator, EvaluatorContext
from typing import Any

logfire.configure(scrubbing=False)
logfire.instrument_pydantic_ai()
logfire.instrument_httpx()

# Set up your API key
load_dotenv()
# os.environ['GOOGLE_API_KEY'] = 'your-api-key-here'


class IntentLabel(str, Enum):
    BALANCE = "balance"
    BILL_BALANCE = "bill_balance"
    BILL_DUE = "bill_due"
    BOOK_FLIGHT = "book_flight"
    BOOK_HOTEL = "book_hotel"
    CANCEL = "cancel"
    CANCEL_RESERVATION = "cancel_reservation"
    CARD_DECLINED = "card_declined"
    CREDIT_LIMIT = "credit_limit"
    CREDIT_SCORE = "credit_score"
    DIRECTIONS = "directions"
    FLIGHT_STATUS = "flight_status"
    PAY_BILL = "pay_bill"
    REPORT_LOST_CARD = "report_lost_card"
    RESTAURANT_RESERVATION = "restaurant_reservation"
    ORDER = "order"
    DATE = "date"
    TIME = "time"
    CHANGE_LANGUAGE = "change_language"
    SPELLING = "spelling"
    TEXT = "text"
    # last one is a distractor, there will not be any data for it
    DIRECT_DEPOSIT = "direct_deposit"


class SingleIntentClassification(BaseModel):
    reasoning: str = Field(description="Brief explanation")
    predicted_intent: IntentLabel | None = Field(description="The most likely intent")
    confidence: Decimal = Field(
        description="Confidence score between 0 and 1", ge=0, le=1, decimal_places=2
    )


EMPTY_CLASSIFICATION = SingleIntentClassification(
    reasoning="", predicted_intent=None, confidence=Decimal("0.01")
)


class BatchIntentClassification(BaseModel):
    classifications: list[SingleIntentClassification]


agent = Agent(
    model="google-gla:gemini-2.0-flash-lite",
    output_type=BatchIntentClassification,
    system_prompt=f"""Classify user queries into one of these intents:
{"\n".join(x.value for x in IntentLabel)}""",
)


class AccuracyEvaluator(Evaluator[str, SingleIntentClassification]):
    def evaluate(
        self, ctx: EvaluatorContext[str, SingleIntentClassification]
    ) -> dict[str, Any]:
        if not ctx.output or not ctx.expected_output:
            return {"accuracy": 0.0}

        true_intent = ctx.expected_output.predicted_intent
        pred_intent = ctx.output.predicted_intent

        prob = float(ctx.output.confidence) if ctx.output else 0.0
        logprob = math.log(prob)
        return {
            "accuracy": 1.0 if true_intent == pred_intent else 0.0,
            "confidence": prob,
            "log_confidence": logprob,
            "predicted_intent": pred_intent,
            "true_intent": true_intent,
        }


class ConfidenceEvaluator(Evaluator[str, SingleIntentClassification]):
    def evaluate(
        self, ctx: EvaluatorContext[str, SingleIntentClassification]
    ) -> dict[str, Any]:
        if not ctx.output:
            return {"confidence_score": 0.0}

        true_intent = (
            ctx.expected_output.predicted_intent if ctx.expected_output else None
        )
        pred_intent = ctx.output.predicted_intent
        is_correct = true_intent == pred_intent

        return {
            "confidence_score": float(ctx.output.confidence),
            "confidence_calibration": 1.0 if is_correct else 0.0,
        }


def load_clinc150_subset(split_choice: str, target_intents: list[str]) -> list[dict]:
    print("📥 Loading CLINC150 dataset from HuggingFace...")
    intents: IterableDataset = load_dataset("DeepPavlov/clinc150", "intents")["intents"]  # type: ignore
    intent_to_label = {intent["name"]: intent["id"] for intent in intents}
    label_to_intent = {intent["id"]: intent["name"] for intent in intents}
    target_labels = [intent_to_label[intent] for intent in target_intents]

    dataset: IterableDataset = load_dataset("DeepPavlov/clinc150", split=split_choice)  # type: ignore
    results = []
    for item in dataset:  # type: ignore
        if item["label"] in target_labels:
            results.append(
                {"text": item["utterance"], "intent": label_to_intent[item["label"]]}
            )
    print(f"✅ Loaded {len(results)} rows")
    return results


def create_logfire_dataset(
    data: list[dict],
) -> Dataset[str, SingleIntentClassification]:
    """Convert existing data format to Logfire evals Dataset"""
    cases = [
        Case(
            name=f"intent_classification_{i}",
            inputs=item["text"],
            expected_output=SingleIntentClassification(
                reasoning="", predicted_intent=item["intent"], confidence=Decimal(1)
            ),
        )
        for i, item in enumerate(data)
    ]
    return Dataset(cases=cases, evaluators=[AccuracyEvaluator(), ConfidenceEvaluator()])


def create_result_lookup(results: list[dict]) -> dict[str, SingleIntentClassification]:
    """Create a lookup dictionary from text to classification results"""
    lookup = {}
    for result in results:
        classification = SingleIntentClassification(
            reasoning=result.get("reasoning", ""),
            predicted_intent=result.get("predicted_intent"),
            confidence=Decimal(str(result.get("confidence", 0.01))),
        )
        lookup[result["text"]] = classification
    return lookup


@logfire.instrument
def batch_classify_queries(
    queries: list[str],
) -> list[SingleIntentClassification | None]:
    batch_prompt = "Classify these queries:\n\n" + "\n---\n".join(queries)

    with logfire.span("llm_batch_classification", batch_size=len(queries)):
        try:
            result = agent.run_sync(batch_prompt)

            classifications = result.output.classifications
            logfire.info(
                f"Got {len(classifications)} results for {len(queries)} queries"
            )
            return (classifications + [None] * len(queries))[: len(queries)]

        except Exception as e:
            logfire.exception(
                "Batch classification failed", error=str(e), batch_size=len(queries)
            )
            traceback.print_exc()
            return [None] * len(queries)


@logfire.instrument
def process_dataset(data: list[dict], batch_size: int) -> list[dict]:
    results = []

    for batch in itertools.batched(data, batch_size):
        queries = [item["text"] for item in batch]
        print("Processing batch...")
        classifications = batch_classify_queries(queries)
        for source, classification in zip(batch, classifications):
            results.append(
                {
                    "text": source["text"],
                    "true_intent": source["intent"],
                    **(classification or EMPTY_CLASSIFICATION).model_dump(),
                }
            )
    return results


def calculate_evaluation_metrics(results: list[dict]) -> dict[str, float]:
    """Calculate comprehensive evaluation metrics"""
    true_labels = [r["true_intent"] for r in results]
    pred_labels = [r["predicted_intent"] for r in results]
    confidences = [r["confidence"] for r in results]

    # Overall metrics
    accuracy = accuracy_score(true_labels, pred_labels)
    precision, recall, f1, _ = precision_recall_fscore_support(
        true_labels, pred_labels, average="weighted", zero_division=1
    )

    # Per-class metrics
    precision_per_class, recall_per_class, f1_per_class, support_per_class = (
        precision_recall_fscore_support(
            true_labels,
            pred_labels,
            average=None,
            labels=["account_details", "balance", "spending_history", "transactions"],
        )
    )

    # Confidence statistics
    avg_confidence = np.mean(confidences)
    correct_predictions = [
        i
        for i, (true, pred) in enumerate(zip(true_labels, pred_labels))
        if true == pred
    ]
    incorrect_predictions = [
        i
        for i, (true, pred) in enumerate(zip(true_labels, pred_labels))
        if true != pred
    ]

    avg_confidence_correct = (
        np.mean([confidences[i] for i in correct_predictions])
        if correct_predictions
        else 0
    )
    avg_confidence_incorrect = (
        np.mean([confidences[i] for i in incorrect_predictions])
        if incorrect_predictions
        else 0
    )

    metrics = {
        "accuracy": accuracy,
        "precision": precision,
        "recall": recall,
        "f1_score": f1,
        "avg_confidence": avg_confidence,
        "avg_confidence_correct": avg_confidence_correct,
        "avg_confidence_incorrect": avg_confidence_incorrect,
        "total_samples": len(results),
    }

    # Add per-class metrics
    class_names = ["account_details", "balance", "spending_history", "transactions"]
    for i, class_name in enumerate(class_names):
        if i < len(precision_per_class):
            metrics[f"{class_name}_precision"] = precision_per_class[i]
            metrics[f"{class_name}_recall"] = recall_per_class[i]
            metrics[f"{class_name}_f1"] = f1_per_class[i]
            metrics[f"{class_name}_support"] = support_per_class[i]

    return metrics


def find_egregious_mistakes(results: list[dict], top_k: int = 10) -> list[dict]:
    mistakes = [x for x in results if x["true_intent"] != x["predicted_intent"]]
    mistakes.sort(key=lambda x: x["confidence"], reverse=True)
    return mistakes[:top_k]


def display_egregious_mistakes(mistakes: list[dict]):
    if not mistakes:
        print("🎉 No mistakes found!")
        return

    print(f"\n🚨 MOST EGREGIOUS MISTAKES (Top {len(mistakes)})")

    for mistake in mistakes:
        print(f'\n   Query: "{mistake["text"]}"')
        print(f"   Reasoning: {mistake['reasoning']}")
        print(
            f"   {mistake['true_intent']} != {mistake['predicted_intent']} (confidence: {mistake['confidence']})"
        )


def main(chosen_split: str = "train"):
    dataset = load_clinc150_subset(chosen_split, [x.value for x in IntentLabel])
    random.shuffle(dataset)
    subset = dataset[:80]

    results = process_dataset(subset, batch_size=32)
    df = pd.DataFrame(results)
    filename = f"{chosen_split}_results.csv"
    df.to_csv(filename, index=False)
    print(f"{filename}: {len(results)} predictions")

    metrics = calculate_evaluation_metrics(results)
    filename = f"{chosen_split}_metrics.csv"
    pd.DataFrame(metrics.items(), columns=["Metric", "Score"]).to_csv(
        filename, index=False
    )
    print(f"{filename}: metrics")
    print(f"Overall Accuracy: {metrics['accuracy']:.3f}")
    print(f"Overall F1-Score: {metrics['f1_score']:.3f}")
    print(f"Overall Precision: {metrics['precision']:.3f}")
    print(f"Overall Recall: {metrics['recall']:.3f}")
    print("\nConfidence Statistics:")
    print(f"  Average Confidence: {metrics['avg_confidence']:.3f}")
    print(f"  Correct Predictions: {metrics['avg_confidence_correct']:.3f}")
    print(f"  Incorrect Predictions: {metrics['avg_confidence_incorrect']:.3f}")

    print("\nPer-Class Results:")
    class_names = ["account_details", "balance", "spending_history", "transactions"]
    for class_name in class_names:
        if f"{class_name}_f1" in metrics:
            print(f"  {class_name}:")
            print(f"    F1: {metrics[f'{class_name}_f1']:.3f}")
            print(f"    Precision: {metrics[f'{class_name}_precision']:.3f}")
            print(f"    Recall: {metrics[f'{class_name}_recall']:.3f}")
            print(f"    Support: {int(metrics[f'{class_name}_support'])}")

    mistakes = find_egregious_mistakes(results)
    display_egregious_mistakes(mistakes)

    results_lookup = create_result_lookup(results)
    logfire_dataset = create_logfire_dataset(subset)
    eval_report = logfire_dataset.evaluate_sync(
        lambda x: results_lookup.get(x, EMPTY_CLASSIFICATION)
    )
    eval_report.print(include_input=True, include_output=False)


if __name__ == "__main__":
    import sys

    _, chosen_split = sys.argv
    main(chosen_split)
