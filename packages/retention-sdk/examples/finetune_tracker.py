#!/usr/bin/env python3
"""Fine-Tuning Pipeline Demo — shows retention.sh for ML training workflows.

Simulates a typical fine-tuning pipeline where an agent:
1. Collects training data (scrapes examples from multiple sources)
2. Preprocesses data (tokenize, format, validate)
3. Kicks off fine-tuning run
4. Evaluates on test set

Runs 3 iterations to show how retention.sh detects:
- Data collection re-fetching the same sources (cacheable)
- Preprocessing running identical tokenizer config (deduplicable)
- Evaluation running same test set (replayable)

Usage:
    pip install retention-sh
    python finetune_tracker.py
"""

import time
import random

from retention_sh import track, observe
track(project="finetune-pipeline-demo")


# ── Stage 1: Data Collection ──

@observe(name="fetch_training_source")
def fetch_training_source(source_url: str, format: str = "jsonl") -> dict:
    """Fetch training examples from a data source."""
    time.sleep(0.08)
    return {
        "source": source_url,
        "examples": random.randint(80, 150),
        "format": format,
    }


@observe(name="validate_examples")
def validate_examples(count: int, schema: str = "instruction-response") -> dict:
    """Validate training examples against schema."""
    time.sleep(0.03)
    valid = int(count * random.uniform(0.92, 0.99))
    return {"valid": valid, "invalid": count - valid, "schema": schema}


# ── Stage 2: Preprocessing ──

@observe(name="tokenize_dataset")
def tokenize_dataset(example_count: int, tokenizer: str = "cl100k_base", max_length: int = 2048) -> dict:
    """Tokenize all examples with the specified tokenizer."""
    time.sleep(0.1)
    return {
        "tokenizer": tokenizer,
        "examples_tokenized": example_count,
        "avg_tokens": random.randint(180, 350),
        "max_length": max_length,
    }


@observe(name="format_for_training")
def format_for_training(example_count: int, format: str = "chat_ml") -> dict:
    """Format tokenized examples into training format."""
    time.sleep(0.05)
    return {"format": format, "formatted": example_count}


# ── Stage 3: Fine-Tuning ──

@observe(name="start_finetune_run")
def start_finetune_run(
    model_base: str = "meta-llama/Llama-3.2-3B",
    learning_rate: float = 2e-5,
    epochs: int = 3,
    batch_size: int = 4,
    example_count: int = 0,
) -> dict:
    """Kick off a fine-tuning run."""
    time.sleep(0.2)
    run_id = f"ft-{int(time.time())}"
    return {
        "run_id": run_id,
        "model": model_base,
        "lr": learning_rate,
        "epochs": epochs,
        "status": "completed",
        "final_loss": round(random.uniform(0.8, 1.4), 3),
    }


# ── Stage 4: Evaluation ──

@observe(name="run_eval_suite")
def run_eval_suite(model_path: str, test_set: str = "eval_v1.jsonl", num_samples: int = 50) -> dict:
    """Run evaluation suite on the fine-tuned model."""
    time.sleep(0.15)
    accuracy = round(random.uniform(0.72, 0.89), 3)
    return {
        "model": model_path,
        "test_set": test_set,
        "samples": num_samples,
        "accuracy": accuracy,
        "f1": round(accuracy * random.uniform(0.95, 1.02), 3),
    }


@observe(name="compare_baseline")
def compare_baseline(current_accuracy: float, baseline: float = 0.75) -> dict:
    """Compare fine-tuned model against baseline."""
    time.sleep(0.02)
    delta = current_accuracy - baseline
    return {
        "current": current_accuracy,
        "baseline": baseline,
        "delta": round(delta, 3),
        "improved": delta > 0,
    }


# ── Pipeline ──

TRAINING_SOURCES = [
    "https://huggingface.co/datasets/squad/train",
    "https://huggingface.co/datasets/alpaca/train",
    "s3://internal-data/customer-support-v3.jsonl",
]


def run_pipeline(iteration: int) -> None:
    print(f"\n{'='*60}")
    print(f"  Fine-Tuning Pipeline — Iteration {iteration}")
    print(f"{'='*60}")

    # Stage 1: Collect data
    print("\n  [Stage 1] Collecting training data...")
    total_examples = 0
    for source in TRAINING_SOURCES:
        result = fetch_training_source(source_url=source, format="jsonl")
        valid = validate_examples(count=result["examples"])
        total_examples += valid["valid"]
        print(f"    {source.split('/')[-1]}: {valid['valid']} valid examples")

    # Stage 2: Preprocess
    print(f"\n  [Stage 2] Preprocessing {total_examples} examples...")
    tokenize_dataset(example_count=total_examples, tokenizer="cl100k_base")
    format_for_training(example_count=total_examples, format="chat_ml")
    print(f"    Tokenized and formatted {total_examples} examples")

    # Stage 3: Fine-tune
    print("\n  [Stage 3] Fine-tuning...")
    ft_result = start_finetune_run(
        model_base="meta-llama/Llama-3.2-3B",
        learning_rate=2e-5,
        epochs=3,
        example_count=total_examples,
    )
    print(f"    Run {ft_result['run_id']}: loss={ft_result['final_loss']}")

    # Stage 4: Evaluate
    print("\n  [Stage 4] Evaluating...")
    eval_result = run_eval_suite(model_path=ft_result["run_id"], test_set="eval_v1.jsonl")
    comparison = compare_baseline(current_accuracy=eval_result["accuracy"])
    delta_str = f"+{comparison['delta']}" if comparison['delta'] > 0 else str(comparison['delta'])
    print(f"    Accuracy: {eval_result['accuracy']} ({delta_str} vs baseline)")


def main():
    print("retention.sh Fine-Tuning Pipeline Demo")
    print("=" * 60)
    print("Simulating 3 fine-tuning iterations with identical sources")
    print("retention.sh will detect: duplicate data fetches, repeated tokenization,")
    print("and identical eval suite runs across iterations.")
    print()

    for i in range(1, 4):
        run_pipeline(i)

    # Summary
    total_calls = 3 * (len(TRAINING_SOURCES) * 2 + 2 + 1 + 2)  # per iteration
    duplicate_fetches = len(TRAINING_SOURCES) * 2  # sources re-fetched in iterations 2+3
    duplicate_evals = 2  # eval suite re-run in iterations 2+3

    print(f"\n{'='*60}")
    print(f"  retention.sh Summary")
    print(f"{'='*60}")
    print(f"  Total tool calls:          {total_calls}")
    print(f"  Duplicate data fetches:    {duplicate_fetches} (same sources, iterations 2+3)")
    print(f"  Duplicate eval runs:       {duplicate_evals} (same test set)")
    print(f"  Duplicate tokenization:    2 (same config)")
    print(f"  Cacheable calls:           {duplicate_fetches + duplicate_evals + 2}")
    print(f"  Potential savings:         {(duplicate_fetches + duplicate_evals + 2) / total_calls * 100:.0f}%")
    print()
    print("  View analytics: http://localhost:5173/memory?tab=analytics")


if __name__ == "__main__":
    main()
