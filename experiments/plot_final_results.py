import argparse
import csv
import json
from pathlib import Path

import matplotlib.pyplot as plt
import numpy as np


def read_csv(path: Path) -> list[dict[str, str]]:
    with path.open(encoding="utf-8") as stream:
        return list(csv.DictReader(stream))


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--result-dir", type=Path, required=True)
    args = parser.parse_args()
    result_dir = args.result_dir
    summary = json.loads((result_dir / "summary.json").read_text(encoding="utf-8"))
    latency = read_csv(result_dir / "latency_modes.csv")
    effects = read_csv(result_dir / "prb_effect_episodes.csv")
    integrated = read_csv(result_dir / "integrated_multi_xapp_conflicts.csv")

    plt.style.use("seaborn-v0_8-whitegrid")
    fig, axes = plt.subplots(3, 2, figsize=(12, 12))

    modes = ["direct_rest", "rule_only_a2a", "llm_rule_a2a"]
    labels = ["Direct REST", "Rule A2A", "LLM+Rule A2A"]
    values = [
        [float(row["state_to_control_ms"]) for row in latency if row["mode"] == mode]
        for mode in modes
    ]
    axes[0, 0].boxplot(values, tick_labels=labels, showmeans=True)
    axes[0, 0].set_yscale("log")
    axes[0, 0].set_ylabel("Latency (ms, log scale)")
    axes[0, 0].set_title("Control-path latency")

    semantic = summary["semantic_decision"]
    x = np.arange(2)
    width = 0.34
    axes[0, 1].bar(
        x - width / 2,
        [semantic["rule_only"]["routing_accuracy_pct"], semantic["llm_rule"]["routing_accuracy_pct"]],
        width,
        label="Routing",
    )
    axes[0, 1].bar(
        x + width / 2,
        [semantic["rule_only"]["trigger_accuracy_pct"], semantic["llm_rule"]["trigger_accuracy_pct"]],
        width,
        label="Trigger",
    )
    axes[0, 1].set_xticks(x, ["Rule", "LLM+Rule"])
    axes[0, 1].set_ylim(0, 105)
    axes[0, 1].set_ylabel("Accuracy (%)")
    axes[0, 1].set_title("Semantic decision accuracy")
    axes[0, 1].legend()

    scaling = summary["routing_scalability"]
    axes[1, 0].plot(
        [item["card_count"] for item in scaling],
        [item["lookup_mean_us"] for item in scaling],
        marker="o",
    )
    axes[1, 0].set_xlabel("Agent Card count")
    axes[1, 0].set_ylabel("Mean lookup time (μs)")
    axes[1, 0].set_title("Agent Card routing scalability")

    integrated_after = [
        row for row in integrated if row["condition"] == "after_priority_mutex"
    ]
    conflict_success = (
        100
        * sum(row["correct_exclusive_winner"] == "True" for row in integrated_after)
        / len(integrated_after)
    )
    axes[1, 1].bar(["Integrated\n2-xApp conflict"], [conflict_success], color="#55a868")
    axes[1, 1].set_ylim(0, 105)
    axes[1, 1].set_ylabel("Correct mitigation (%)")
    axes[1, 1].set_title("Multi-xApp conflict mitigation")

    conditions = ["no_control", "control"]
    x = np.arange(2)
    for column, ylabel, title, axis in (
        (
            "throughput",
            "Normalized throughput proxy",
            "Emulator throughput proxy",
            axes[2, 0],
        ),
        (
            "queue_delay",
            "Queue-delay proxy (ms)",
            "Emulator queue-delay proxy",
            axes[2, 1],
        ),
    ):
        before = [
            [
                float(row[f"{column}_before" + ("_ms" if column == "queue_delay" else "")])
                for row in effects
                if row["condition"] == condition
            ]
            for condition in conditions
        ]
        after = [
            [
                float(row[f"{column}_after" + ("_ms" if column == "queue_delay" else "")])
                for row in effects
                if row["condition"] == condition
            ]
            for condition in conditions
        ]
        axis.bar(x - width / 2, [np.mean(item) for item in before], width, label="Before")
        axis.bar(x + width / 2, [np.mean(item) for item in after], width, label="After")
        axis.set_xticks(x, ["No control", "E2 control"])
        axis.set_ylabel(ylabel)
        axis.set_title(title)
        axis.legend()

    fig.suptitle("A2A-based FlexRIC final evaluation", fontsize=15)
    fig.tight_layout()
    png = result_dir / "final_evaluation.png"
    pdf = result_dir / "final_evaluation.pdf"
    fig.savefig(png, dpi=180, bbox_inches="tight")
    fig.savefig(pdf, bbox_inches="tight")
    print(png)
    print(pdf)


if __name__ == "__main__":
    main()
