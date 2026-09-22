import argparse
import csv
import json
from pathlib import Path

import matplotlib.pyplot as plt
import numpy as np


COLORS = {
    "blue": "#2878B5",
    "orange": "#F28E2B",
    "green": "#3A9D5D",
    "red": "#D9534F",
    "gray": "#8A8A8A",
    "purple": "#7E57C2",
}


def read_csv(path: Path) -> list[dict[str, str]]:
    with path.open(encoding="utf-8") as stream:
        return list(csv.DictReader(stream))


def values(rows: list[dict[str, str]], key: str) -> np.ndarray:
    return np.asarray([float(row[key]) for row in rows], dtype=float)


def save(fig: plt.Figure, output_dir: Path, stem: str) -> None:
    fig.savefig(output_dir / f"{stem}.png", dpi=220, bbox_inches="tight")
    fig.savefig(output_dir / f"{stem}.pdf", bbox_inches="tight")
    plt.close(fig)


def annotate_bars(ax: plt.Axes, bars, suffix: str = "%", decimals: int = 1) -> None:
    for bar in bars:
        height = bar.get_height()
        va = "bottom" if height >= 0 else "top"
        offset = 1.5 if height >= 0 else -1.5
        ax.text(
            bar.get_x() + bar.get_width() / 2,
            height + offset,
            f"{height:.{decimals}f}{suffix}",
            ha="center",
            va=va,
            fontsize=9,
            fontweight="bold",
        )


def plot_core_dashboard(
    result_dir: Path,
    summary: dict,
    conflicts: list[dict[str, str]],
    qos: list[dict[str, str]],
) -> None:
    fig, axes = plt.subplots(1, 3, figsize=(16, 4.8))

    # Separate action-table lookup from open-vocabulary generalization.
    semantic = summary["semantic_decision"]
    families = semantic["by_family"]
    family_keys = ["registered_action", "open_vocabulary_holdout"]
    family_labels = ["Registered\naction", "Open-vocabulary\nhold-out"]
    rule_accuracy = [
        families[key]["rule_only"]["routing_accuracy_pct"] for key in family_keys
    ]
    llm_accuracy = [
        families[key]["llm_rule"]["routing_accuracy_pct"] for key in family_keys
    ]
    adaptive_accuracy = [
        families[key]["adaptive_hybrid"]["routing_accuracy_pct"]
        for key in family_keys
    ]
    x = np.arange(2)
    width = 0.25
    rule_bars = axes[0].bar(
        x - width, rule_accuracy, width, label="Rule only", color=COLORS["blue"]
    )
    llm_bars = axes[0].bar(
        x, llm_accuracy, width, label="LLM always", color=COLORS["orange"]
    )
    adaptive_bars = axes[0].bar(
        x + width,
        adaptive_accuracy,
        width,
        label="Adaptive hybrid",
        color=COLORS["purple"],
    )
    axes[0].set_ylim(0, 112)
    axes[0].set_ylabel("Routing accuracy (%)")
    axes[0].set_xticks(x, family_labels)
    axes[0].set_title("A. LLM generalization benefit", loc="left", fontweight="bold")
    axes[0].legend(loc="lower left", fontsize=8)
    annotate_bars(axes[0], rule_bars, "%")
    annotate_bars(axes[0], llm_bars, "%")
    annotate_bars(axes[0], adaptive_bars, "%")
    axes[0].text(
        0.98,
        0.08,
        f"Mean latency: rule {semantic['rule_only']['latency_ms']['mean']:.3f} ms\n"
        f"LLM always {semantic['llm_rule']['latency_ms']['mean']:.1f} ms\n"
        f"Adaptive {semantic['adaptive_hybrid']['latency_ms']['mean']:.1f} ms",
        transform=axes[0].transAxes,
        ha="right",
        va="bottom",
        fontsize=9,
        bbox={"boxstyle": "round,pad=0.3", "facecolor": "white", "alpha": 0.9},
    )

    # Actual two-process before/after comparison.
    integrated = summary["integrated_multi_xapp_conflict"]
    before = integrated["before_no_arbitration"]
    after = integrated["after_priority_mutex"]
    outcome_labels = ["Conflict\nrate", "Loser\nexecuted", "Correct single\nwinner"]
    before_values = [
        before["collision_rate_pct"],
        before["loser_execution_rate_pct"],
        before["correct_exclusive_winner_pct"],
    ]
    after_values = [
        after["collision_rate_pct"],
        after["loser_execution_rate_pct"],
        after["correct_exclusive_winner_pct"],
    ]
    x = np.arange(3)
    before_bars = axes[1].bar(
        x - width / 2, before_values, width, label="Before: no arbiter", color=COLORS["gray"]
    )
    after_bars = axes[1].bar(
        x + width / 2, after_values, width, label="After: priority + mutex", color=COLORS["green"]
    )
    axes[1].set_ylim(0, 108)
    axes[1].set_ylabel("Rate (%)")
    axes[1].set_xticks(x, outcome_labels)
    axes[1].set_title("B. Conflict mitigation before vs after", loc="left", fontweight="bold")
    axes[1].legend(loc="center", fontsize=8)
    annotate_bars(axes[1], before_bars)
    annotate_bars(axes[1], after_bars)

    # Positive means improvement for all metrics in this panel.
    metrics = [
        ("Throughput", "throughput_change_pct"),
        ("Queue delay", "queue_delay_reduction_pct"),
        ("Retransmission", "retransmission_reduction_pct"),
        ("PRB usage", "reduction_pct"),
    ]
    no_control = [row for row in qos if row["condition"] == "no_control"]
    control = [row for row in qos if row["condition"] == "control"]
    baseline_means = [values(no_control, key).mean() for _, key in metrics]
    control_means = [values(control, key).mean() for _, key in metrics]
    x = np.arange(len(metrics))
    width = 0.36
    baseline_bars = axes[2].bar(
        x - width / 2,
        baseline_means,
        width,
        label="No control",
        color=COLORS["gray"],
    )
    control_bars = axes[2].bar(
        x + width / 2,
        control_means,
        width,
        label="E2 control",
        color=COLORS["green"],
    )
    axes[2].axhline(0, color="black", linewidth=0.8)
    axes[2].set_xticks(x, [name.replace(" ", "\n") for name, _ in metrics])
    axes[2].set_ylabel("Mean episode improvement (%)")
    axes[2].set_title("C. QoS proxy improvement", loc="left", fontweight="bold")
    axes[2].legend(loc="upper left")
    annotate_bars(axes[2], baseline_bars)
    annotate_bars(axes[2], control_bars)
    axes[2].text(
        0.98,
        0.05,
        "Positive = better\n10 episodes / condition",
        transform=axes[2].transAxes,
        ha="right",
        va="bottom",
        fontsize=9,
    )

    fig.suptitle("Core outcomes: performance gains and costs", fontsize=16, fontweight="bold")
    fig.tight_layout()
    save(fig, result_dir, "core_results_dashboard")


def plot_llm_tradeoff(
    result_dir: Path,
    summary: dict,
    semantic_rows: list[dict[str, str]],
    latency_rows: list[dict[str, str]],
) -> None:
    fig, axes = plt.subplots(1, 3, figsize=(15, 4.5))
    semantic = summary["semantic_decision"]

    family_keys = ["registered_action", "open_vocabulary_holdout"]
    family_labels = ["Registered action", "Open-vocabulary hold-out"]
    families = semantic["by_family"]
    rule_scores = [
        families[key]["rule_only"]["routing_accuracy_pct"] for key in family_keys
    ]
    llm_scores = [
        families[key]["llm_rule"]["routing_accuracy_pct"] for key in family_keys
    ]
    adaptive_scores = [
        families[key]["adaptive_hybrid"]["routing_accuracy_pct"]
        for key in family_keys
    ]
    x = np.arange(2)
    width = 0.25
    rule_bars = axes[0].bar(
        x - width, rule_scores, width, label="Rule only", color=COLORS["blue"]
    )
    llm_bars = axes[0].bar(
        x, llm_scores, width, label="LLM always", color=COLORS["orange"]
    )
    adaptive_bars = axes[0].bar(
        x + width,
        adaptive_scores,
        width,
        label="Adaptive hybrid",
        color=COLORS["purple"],
    )
    axes[0].set_ylim(0, 112)
    axes[0].set_ylabel("Routing accuracy (%)")
    axes[0].set_title("A. Accuracy by test family", loc="left", fontweight="bold")
    axes[0].set_xticks(x, family_labels, rotation=10)
    axes[0].legend()
    annotate_bars(axes[0], rule_bars)
    annotate_bars(axes[0], llm_bars)
    annotate_bars(axes[0], adaptive_bars)

    rule_decisions = values([row for row in semantic_rows if row["mode"] == "rule_only"], "latency_ms")
    llm_decisions = values([row for row in semantic_rows if row["mode"] == "llm_rule"], "latency_ms")
    adaptive_decisions = values(
        [row for row in semantic_rows if row["mode"] == "adaptive_hybrid"],
        "latency_ms",
    )
    axes[1].boxplot(
        [rule_decisions, llm_decisions, adaptive_decisions],
        tick_labels=["Rule", "LLM always", "Adaptive"],
        showmeans=True,
    )
    axes[1].set_yscale("log")
    axes[1].set_ylabel("Decision latency (ms, log)")
    axes[1].set_title("B. Semantic decision cost", loc="left", fontweight="bold")
    for index, data in enumerate((rule_decisions, llm_decisions, adaptive_decisions), 1):
        axes[1].text(index, np.max(data) * 1.35, f"mean {np.mean(data):.3f} ms", ha="center", fontsize=9)

    modes = ["direct_rest", "rule_only_a2a", "llm_rule_a2a"]
    mode_labels = ["Direct REST", "Rule A2A", "LLM + rule A2A"]
    mode_values = [values([row for row in latency_rows if row["mode"] == mode], "state_to_control_ms") for mode in modes]
    axes[2].boxplot(mode_values, tick_labels=mode_labels, showmeans=True)
    axes[2].set_yscale("log")
    axes[2].set_ylabel("State-to-control latency (ms, log)")
    axes[2].set_title("C. End-to-end control cost", loc="left", fontweight="bold")
    axes[2].tick_params(axis="x", rotation=15)
    for index, data in enumerate(mode_values, 1):
        axes[2].text(index, np.max(data) * 1.18, f"{np.mean(data):.1f} ms", ha="center", fontsize=9)

    fig.suptitle("LLM performance: generalization benefit and latency trade-off", fontsize=15, fontweight="bold")
    fig.tight_layout()
    save(fig, result_dir, "llm_performance_tradeoff")


def plot_multi_xapp(
    result_dir: Path, conflicts: list[dict[str, str]]
) -> None:
    fig, axes = plt.subplots(1, 2, figsize=(11, 4.3))
    before = [
        row for row in conflicts if row["condition"] == "before_no_arbitration"
    ]
    after = [
        row for row in conflicts if row["condition"] == "after_priority_mutex"
    ]
    labels = ["Conflict rate", "Loser executed", "Correct single winner"]
    keys = ["collision", "loser_executed", "correct_exclusive_winner"]
    before_rates = [
        100 * sum(row[key] == "True" for row in before) / len(before) for key in keys
    ]
    after_rates = [
        100 * sum(row[key] == "True" for row in after) / len(after) for key in keys
    ]
    x = np.arange(3)
    width = 0.36
    before_bars = axes[0].bar(
        x - width / 2,
        before_rates,
        width,
        label="Before: no arbiter",
        color=COLORS["gray"],
    )
    after_bars = axes[0].bar(
        x + width / 2,
        after_rates,
        width,
        label="After: priority + mutex",
        color=COLORS["green"],
    )
    axes[0].set_xticks(x, [label.replace(" ", "\n") for label in labels])
    axes[0].set_ylim(0, 110)
    axes[0].set_ylabel("Rate (%)")
    axes[0].set_title("A. Conflict outcomes", loc="left", fontweight="bold")
    axes[0].legend(fontsize=8, loc="center")
    annotate_bars(axes[0], before_bars)
    annotate_bars(axes[0], after_bars)

    action_means = [
        values(before, "actions_per_state").mean(),
        values(after, "actions_per_state").mean(),
    ]
    latency_means = [
        values(before, "latency_ms").mean(),
        values(after, "latency_ms").mean(),
    ]
    x = np.arange(2)
    action_bars = axes[1].bar(
        x,
        action_means,
        width=0.5,
        color=[COLORS["gray"], COLORS["green"]],
    )
    axes[1].set_xticks(x, ["Before\nno arbiter", "After\npriority + mutex"])
    axes[1].set_ylabel("Mean actions executed per state")
    axes[1].set_ylim(0, max(action_means) * 1.35)
    axes[1].set_title("B. Execution cost and latency", loc="left", fontweight="bold")
    for bar, value in zip(action_bars, action_means):
        axes[1].text(
            bar.get_x() + bar.get_width() / 2,
            value + 0.05,
            f"{value:.1f}",
            ha="center",
            fontweight="bold",
        )
    latency_axis = axes[1].twinx()
    latency_axis.plot(x, latency_means, color=COLORS["red"], marker="o", linewidth=2.2)
    latency_axis.set_ylabel("Mean state-to-completion latency (ms)", color=COLORS["red"])
    latency_axis.tick_params(axis="y", labelcolor=COLORS["red"])
    for xi, value in zip(x, latency_means):
        latency_axis.annotate(
            f"{value:.1f} ms",
            (xi, value),
            xytext=(0, 8),
            textcoords="offset points",
            ha="center",
            color=COLORS["red"],
        )

    fig.text(
        0.5,
        0.02,
        "Deterministic rules: radio_load > 0.7; same radio_resource mutex group; priority 10 vs 1; LLM disabled",
        ha="center",
        fontsize=9,
    )
    fig.suptitle(
        "Multi-xApp conflict mitigation: paired before/after analysis",
        fontsize=15,
        fontweight="bold",
    )
    fig.tight_layout(rect=(0, 0.07, 1, 1))
    save(fig, result_dir, "multi_xapp_conflict_performance")


def plot_qos_tradeoff(result_dir: Path, qos: list[dict[str, str]]) -> None:
    no_control = [row for row in qos if row["condition"] == "no_control"]
    control = [row for row in qos if row["condition"] == "control"]
    definitions = [
        ("throughput", "Throughput proxy", "Higher is better", COLORS["blue"]),
        ("queue_delay", "Queue-delay proxy (ms)", "Lower is better", COLORS["orange"]),
        ("retransmission", "Retransmission proxy", "Lower is better", COLORS["purple"]),
        ("prb", "Normalized PRB usage", "Lower is better", COLORS["green"]),
    ]
    keys = {
        "throughput": ("throughput_before", "throughput_after"),
        "queue_delay": ("queue_delay_before_ms", "queue_delay_after_ms"),
        "retransmission": ("retransmission_before", "retransmission_after"),
        "prb": ("before_mean", "after_mean"),
    }
    fig, axes = plt.subplots(2, 2, figsize=(11, 8.5))
    for ax, (metric, ylabel, direction, color) in zip(axes.flat, definitions):
        before_key, after_key = keys[metric]
        for rows, line_color, alpha, offset in (
            (no_control, COLORS["gray"], 0.35, -0.025),
            (control, color, 0.5, 0.025),
        ):
            for row in rows:
                ax.plot(
                    [0 + offset, 1 + offset],
                    [float(row[before_key]), float(row[after_key])],
                    color=line_color,
                    alpha=alpha,
                    linewidth=1.0,
                )
        for rows, line_color, label, offset in (
            (no_control, COLORS["gray"], "No control mean", -0.025),
            (control, color, "E2 control mean", 0.025),
        ):
            means = [values(rows, before_key).mean(), values(rows, after_key).mean()]
            ax.plot([0 + offset, 1 + offset], means, color=line_color, marker="o", linewidth=3, label=label)
        ax.set_xticks([0, 1], ["Before", "After"])
        ax.set_ylabel(ylabel)
        ax.set_title(f"{ylabel}\n{direction}", fontweight="bold")
        ax.legend(fontsize=8)
    fig.suptitle("QoS proxy effect by episode (n=10 per condition)", fontsize=15, fontweight="bold")
    fig.tight_layout()
    save(fig, result_dir, "qos_episode_effects")

    # Each point is one control episode; upper-right means both throughput and delay improve.
    throughput = values(control, "throughput_change_pct")
    delay = values(control, "queue_delay_reduction_pct")
    retrans = values(control, "retransmission_reduction_pct")
    prb = values(control, "reduction_pct")
    fig, ax = plt.subplots(figsize=(7.5, 5.8))
    scatter = ax.scatter(
        throughput,
        delay,
        c=retrans,
        s=35 + prb * 2.2,
        cmap="viridis",
        edgecolor="black",
        linewidth=0.5,
    )
    ax.axvline(0, color="black", linewidth=0.8)
    ax.axhline(0, color="black", linewidth=0.8)
    for index, (x, y) in enumerate(zip(throughput, delay), 1):
        ax.annotate(str(index), (x, y), xytext=(5, 4), textcoords="offset points", fontsize=8)
    mean_x = throughput.mean()
    mean_y = delay.mean()
    ax.scatter(mean_x, mean_y, marker="*", s=260, color=COLORS["red"], edgecolor="black")
    ax.annotate("Mean", (mean_x, mean_y), xytext=(10, -3), textcoords="offset points", va="center")
    ax.set_xlabel("Throughput improvement (%)")
    ax.set_ylabel("Queue-delay reduction (%)")
    ax.set_title("QoS relationship across E2-control episodes", fontsize=14, fontweight="bold")
    colorbar = fig.colorbar(scatter, ax=ax)
    colorbar.set_label("Retransmission reduction (%)")
    ax.text(
        0.02,
        0.98,
        "Marker size = PRB reduction\nUpper-right = jointly improved",
        transform=ax.transAxes,
        ha="left",
        va="top",
        fontsize=9,
        bbox={"boxstyle": "round,pad=0.3", "facecolor": "white", "alpha": 0.9},
    )
    fig.tight_layout()
    save(fig, result_dir, "qos_tradeoff_relationship")


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--result-dir", type=Path, required=True)
    args = parser.parse_args()
    result_dir = args.result_dir
    summary = json.loads((result_dir / "summary.json").read_text(encoding="utf-8"))
    semantic = read_csv(result_dir / "semantic_cases.csv")
    latency = read_csv(result_dir / "latency_modes.csv")
    conflicts = read_csv(result_dir / "integrated_multi_xapp_conflicts.csv")
    qos = read_csv(result_dir / "prb_effect_episodes.csv")

    plt.style.use("seaborn-v0_8-whitegrid")
    plt.rcParams.update({"font.family": "DejaVu Sans", "axes.titlepad": 10})
    plot_core_dashboard(result_dir, summary, conflicts, qos)
    plot_llm_tradeoff(result_dir, summary, semantic, latency)
    plot_multi_xapp(result_dir, conflicts)
    plot_qos_tradeoff(result_dir, qos)

    for stem in (
        "core_results_dashboard",
        "llm_performance_tradeoff",
        "multi_xapp_conflict_performance",
        "qos_episode_effects",
        "qos_tradeoff_relationship",
    ):
        print(result_dir / f"{stem}.png")


if __name__ == "__main__":
    main()
