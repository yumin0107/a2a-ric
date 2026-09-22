import argparse
import csv
import json
from pathlib import Path

import matplotlib.pyplot as plt
import numpy as np


COLORS = {
    "rule": "#2878B5",
    "llm": "#F28E2B",
    "adaptive": "#7E57C2",
    "control": "#3A9D5D",
    "baseline": "#8A8A8A",
    "latency": "#D9534F",
}


def read_csv(path: Path) -> list[dict[str, str]]:
    with path.open(encoding="utf-8") as stream:
        return list(csv.DictReader(stream))


def numbers(rows: list[dict[str, str]], key: str) -> np.ndarray:
    return np.asarray([float(row[key]) for row in rows], dtype=float)


def save_figure(fig: plt.Figure, output_dir: Path, name: str) -> None:
    fig.savefig(output_dir / f"{name}.png", dpi=220, bbox_inches="tight")
    fig.savefig(output_dir / f"{name}.pdf", bbox_inches="tight")
    plt.close(fig)


def label_bars(ax: plt.Axes, bars, suffix: str = "%", offset: float = 1.5) -> None:
    for bar in bars:
        value = bar.get_height()
        ax.text(
            bar.get_x() + bar.get_width() / 2,
            value + (offset if value >= 0 else -offset),
            f"{value:.1f}{suffix}",
            ha="center",
            va="bottom" if value >= 0 else "top",
            fontsize=9,
            fontweight="bold",
        )


def plot_latency_accuracy(
    result_dir: Path,
    output_dir: Path,
    summary: dict,
    semantic_rows: list[dict[str, str]],
) -> None:
    semantic = summary["semantic_decision"]
    families = semantic["by_family"]
    modes = ["rule_only", "llm_rule", "adaptive_hybrid"]
    labels = ["Rule only", "LLM always", "Adaptive hybrid"]
    colors = [COLORS["rule"], COLORS["llm"], COLORS["adaptive"]]

    fig, axes = plt.subplots(1, 3, figsize=(15.5, 4.7))
    x = np.arange(2)
    width = 0.24
    for index, (mode, label, color) in enumerate(zip(modes, labels, colors)):
        scores = [
            families["registered_action"][mode]["routing_accuracy_pct"],
            families["open_vocabulary_holdout"][mode]["routing_accuracy_pct"],
        ]
        bars = axes[0].bar(
            x + (index - 1) * width,
            scores,
            width,
            label=label,
            color=color,
        )
        label_bars(axes[0], bars)
    axes[0].set_xticks(x, ["Registered\naction", "Open-vocabulary\nhold-out"])
    axes[0].set_ylim(0, 112)
    axes[0].set_ylabel("Routing accuracy (%)")
    axes[0].set_title("A. Accuracy by policy family", loc="left", fontweight="bold")
    axes[0].legend(loc="lower left", fontsize=8)

    distributions = [
        numbers([row for row in semantic_rows if row["mode"] == mode], "latency_ms")
        for mode in modes
    ]
    axes[1].boxplot(distributions, tick_labels=["Rule", "LLM always", "Adaptive"], showmeans=True)
    axes[1].set_yscale("log")
    axes[1].set_ylabel("Semantic decision latency (ms, log)")
    axes[1].set_title("B. Decision-time distribution", loc="left", fontweight="bold")
    for index, data in enumerate(distributions, 1):
        axes[1].text(
            index,
            max(data) * 1.25,
            f"mean {np.mean(data):.3f} ms",
            ha="center",
            fontsize=8,
        )

    overall_accuracy = [semantic[mode]["routing_accuracy_pct"] for mode in modes]
    overall_latency = [semantic[mode]["latency_ms"]["mean"] for mode in modes]
    llm_call_rates = [0, 100, 50]
    annotation_styles = {
        "Rule only": {"xytext": (8, -5), "ha": "left", "va": "center"},
        "LLM always": {"xytext": (-10, 12), "ha": "right", "va": "bottom"},
        "Adaptive hybrid": {"xytext": (-10, -12), "ha": "right", "va": "top"},
    }
    for accuracy, latency, call_rate, label, color in zip(
        overall_accuracy, overall_latency, llm_call_rates, labels, colors
    ):
        axes[2].scatter(
            latency,
            accuracy,
            s=90 + call_rate * 2,
            color=color,
            edgecolor="black",
            linewidth=0.6,
            zorder=3,
        )
        axes[2].annotate(
            f"{label}\n{accuracy:.1f}%, {latency:.1f} ms\nLLM calls {call_rate}%",
            (latency, accuracy),
            xytext=annotation_styles[label]["xytext"],
            textcoords="offset points",
            ha=annotation_styles[label]["ha"],
            va=annotation_styles[label]["va"],
            fontsize=8,
        )
    axes[2].set_xscale("symlog", linthresh=0.1)
    axes[2].set_ylim(45, 100)
    axes[2].set_xlabel("Mean decision latency (ms, symlog)")
    axes[2].set_ylabel("Overall routing accuracy (%)")
    axes[2].set_title("C. System-level trade-off", loc="left", fontweight="bold")
    axes[2].text(
        0.98,
        0.04,
        "Marker size = LLM invocation rate",
        transform=axes[2].transAxes,
        ha="right",
        fontsize=8,
    )

    fig.suptitle(
        "Communication control intelligence: latency–accuracy trade-off",
        fontsize=15,
        fontweight="bold",
    )
    fig.tight_layout()
    save_figure(fig, output_dir, "fig1_latency_accuracy_tradeoff")


def plot_radio_qos(
    output_dir: Path, summary: dict, qos_rows: list[dict[str, str]]
) -> None:
    no_control = [row for row in qos_rows if row["condition"] == "no_control"]
    control = [row for row in qos_rows if row["condition"] == "control"]
    effect = summary["closed_loop_effect"]["control"]
    definitions = [
        (
            "throughput_before",
            "throughput_after",
            "Throughput proxy",
            "Higher is better",
            COLORS["rule"],
            effect["mean_throughput_change_pct"],
            "+",
        ),
        (
            "queue_delay_before_ms",
            "queue_delay_after_ms",
            "Queue-delay proxy",
            "Lower is better",
            COLORS["llm"],
            effect["mean_queue_delay_reduction_pct"],
            "−",
        ),
        (
            "retransmission_before",
            "retransmission_after",
            "Retransmission proxy",
            "Lower is better",
            COLORS["adaptive"],
            effect["mean_retransmission_reduction_pct"],
            "−",
        ),
        (
            "before_mean",
            "after_mean",
            "Normalized PRB usage",
            "Lower is better",
            COLORS["control"],
            effect["mean_reduction_pct"],
            "−",
        ),
    ]

    fig, axes = plt.subplots(2, 2, figsize=(11.5, 8.7))
    for ax, (before_key, after_key, title, direction, color, improvement, sign) in zip(
        axes.flat, definitions
    ):
        for rows, line_color, alpha, x_offset in (
            (no_control, COLORS["baseline"], 0.30, -0.025),
            (control, color, 0.48, 0.025),
        ):
            for row in rows:
                ax.plot(
                    [x_offset, 1 + x_offset],
                    [float(row[before_key]), float(row[after_key])],
                    color=line_color,
                    alpha=alpha,
                    linewidth=1,
                )
        for rows, line_color, label, x_offset in (
            (no_control, COLORS["baseline"], "No control mean", -0.025),
            (control, color, "E2 control mean", 0.025),
        ):
            mean_values = [numbers(rows, before_key).mean(), numbers(rows, after_key).mean()]
            ax.plot(
                [x_offset, 1 + x_offset],
                mean_values,
                color=line_color,
                marker="o",
                linewidth=3,
                label=label,
            )
        ax.set_xticks([0, 1], ["Before", "After"])
        ax.set_title(
            f"{title}: {sign}{improvement:.1f}%\n{direction}",
            fontweight="bold",
        )
        ax.legend(fontsize=8)
    fig.suptitle(
        "Radio-resource and QoS proxy effects (10 episodes per condition)",
        fontsize=15,
        fontweight="bold",
    )
    fig.text(
        0.5,
        0.01,
        "FlexRIC gNB-emulator proxies: normalized TBS, BSR-derived queue delay, retransmission PRB, and PRB usage",
        ha="center",
        fontsize=8,
    )
    fig.tight_layout(rect=(0, 0.03, 1, 1))
    save_figure(fig, output_dir, "fig2_radio_qos_effect")


def plot_conflict_mitigation(
    output_dir: Path, summary: dict, conflict_rows: list[dict[str, str]]
) -> None:
    conflict = summary["integrated_multi_xapp_conflict"]
    before = conflict["before_no_arbitration"]
    after = conflict["after_priority_mutex"]
    fig, axes = plt.subplots(1, 2, figsize=(11.5, 4.8))

    labels = ["Conflict\nrate", "Loser\nexecuted", "Correct single\nwinner"]
    before_rates = [
        before["collision_rate_pct"],
        before["loser_execution_rate_pct"],
        before["correct_exclusive_winner_pct"],
    ]
    after_rates = [
        after["collision_rate_pct"],
        after["loser_execution_rate_pct"],
        after["correct_exclusive_winner_pct"],
    ]
    x = np.arange(3)
    width = 0.36
    before_bars = axes[0].bar(
        x - width / 2,
        before_rates,
        width,
        label="Before: no arbiter",
        color=COLORS["baseline"],
    )
    after_bars = axes[0].bar(
        x + width / 2,
        after_rates,
        width,
        label="After: priority + mutex",
        color=COLORS["control"],
    )
    axes[0].set_xticks(x, labels)
    axes[0].set_ylim(0, 110)
    axes[0].set_ylabel("Rate (%)")
    axes[0].set_title("A. Conflict outcomes", loc="left", fontweight="bold")
    axes[0].legend(loc="center", fontsize=8)
    label_bars(axes[0], before_bars)
    label_bars(axes[0], after_bars)

    actions = [before["mean_actions_per_state"], after["mean_actions_per_state"]]
    latency = [before["latency_ms"]["mean"], after["latency_ms"]["mean"]]
    x = np.arange(2)
    action_bars = axes[1].bar(
        x,
        actions,
        width=0.52,
        color=[COLORS["baseline"], COLORS["control"]],
    )
    axes[1].set_xticks(x, ["Before\nno arbiter", "After\npriority + mutex"])
    axes[1].set_ylim(0, 2.7)
    axes[1].set_ylabel("Mean actions executed per state")
    axes[1].set_title("B. Control-path overhead", loc="left", fontweight="bold")
    for bar, value in zip(action_bars, actions):
        axes[1].text(
            bar.get_x() + bar.get_width() / 2,
            value + 0.06,
            f"{value:.1f}",
            ha="center",
            fontweight="bold",
        )
    latency_axis = axes[1].twinx()
    latency_axis.plot(x, latency, color=COLORS["latency"], marker="o", linewidth=2.5)
    latency_axis.set_ylabel("Mean state-to-completion latency (ms)", color=COLORS["latency"])
    latency_axis.tick_params(axis="y", labelcolor=COLORS["latency"])
    for xi, value in zip(x, latency):
        latency_axis.annotate(
            f"{value:.1f} ms",
            (xi, value),
            xytext=(0, 8),
            textcoords="offset points",
            ha="center",
            color=COLORS["latency"],
        )

    fig.suptitle(
        "Rule-based multi-xApp conflict mitigation (20 paired trials, LLM disabled)",
        fontsize=15,
        fontweight="bold",
    )
    fig.text(
        0.5,
        0.01,
        "Both policies trigger at radio_load > 0.7; radio_resource mutex group; priorities 10 vs 1",
        ha="center",
        fontsize=8,
    )
    fig.tight_layout(rect=(0, 0.04, 1, 1))
    save_figure(fig, output_dir, "fig3_xapp_conflict_mitigation")


def plot_control_plane_scalability(output_dir: Path, summary: dict) -> None:
    scaling = summary["routing_scalability"]
    latency = summary["latency"]
    fig, axes = plt.subplots(1, 2, figsize=(11.5, 4.8))

    card_counts = np.asarray([item["card_count"] for item in scaling])
    lookup_us = np.asarray([item["lookup_mean_us"] for item in scaling])
    request_ms = np.asarray([item["request_ms"]["mean"] for item in scaling])
    request_std = np.asarray([item["request_ms"]["stdev"] for item in scaling])
    axes[0].plot(
        card_counts,
        lookup_us,
        color=COLORS["rule"],
        marker="o",
        linewidth=2.2,
        label="Internal lookup",
    )
    axes[0].set_xlabel("Agent Card count")
    axes[0].set_ylabel("Internal lookup time (μs)", color=COLORS["rule"])
    axes[0].tick_params(axis="y", labelcolor=COLORS["rule"])
    axes[0].set_title("A. Agent Card routing scalability", loc="left", fontweight="bold")
    request_axis = axes[0].twinx()
    request_axis.errorbar(
        card_counts,
        request_ms,
        yerr=request_std,
        color=COLORS["llm"],
        marker="s",
        linewidth=2,
        capsize=3,
        label="End-to-end API",
    )
    request_axis.set_ylabel("Routing API latency (ms)", color=COLORS["llm"])
    request_axis.tick_params(axis="y", labelcolor=COLORS["llm"])
    for count, value in zip(card_counts, lookup_us):
        axes[0].annotate(f"{value:.2f} μs", (count, value), xytext=(0, 7), textcoords="offset points", ha="center", fontsize=8)
    axes[0].text(
        0.03,
        0.92,
        "Selection accuracy: 100% at all sizes",
        transform=axes[0].transAxes,
        fontsize=8,
    )

    modes = ["direct_rest", "rule_only_a2a", "llm_rule_a2a"]
    mode_labels = ["Direct REST", "Rule A2A", "LLM A2A"]
    mean_latency = [latency[mode]["state_to_control_ms"]["mean"] for mode in modes]
    p95_latency = [latency[mode]["state_to_control_ms"]["p95"] for mode in modes]
    x = np.arange(3)
    bars = axes[1].bar(
        x,
        mean_latency,
        width=0.58,
        color=[COLORS["baseline"], COLORS["rule"], COLORS["llm"]],
        label="Mean",
    )
    axes[1].plot(x, p95_latency, color=COLORS["latency"], marker="D", linewidth=1.8, label="p95")
    axes[1].set_yscale("log")
    axes[1].set_xticks(x, mode_labels)
    axes[1].set_ylabel("State-to-control latency (ms, log)")
    axes[1].set_title("B. Control-plane orchestration cost", loc="left", fontweight="bold")
    axes[1].legend()
    for bar, mean, p95 in zip(bars, mean_latency, p95_latency):
        axes[1].text(
            bar.get_x() + bar.get_width() / 2,
            p95 * 1.18,
            f"{mean:.1f} / {p95:.1f} ms\nmean / p95",
            ha="center",
            fontsize=8,
        )
    axes[1].text(
        0.03,
        0.93,
        "Execution success: 100%",
        transform=axes[1].transAxes,
        fontsize=8,
    )

    fig.suptitle(
        "Control-plane scalability and orchestration overhead",
        fontsize=15,
        fontweight="bold",
    )
    fig.tight_layout()
    save_figure(fig, output_dir, "fig4_control_plane_scalability")


def plot_summary(output_dir: Path, summary: dict) -> None:
    semantic = summary["semantic_decision"]
    conflict = summary["integrated_multi_xapp_conflict"]
    qos = summary["closed_loop_effect"]
    scaling = summary["routing_scalability"]
    fig, axes = plt.subplots(2, 2, figsize=(12, 9))

    modes = ["rule_only", "llm_rule", "adaptive_hybrid"]
    labels = ["Rule", "LLM always", "Adaptive"]
    colors = [COLORS["rule"], COLORS["llm"], COLORS["adaptive"]]
    label_styles = {
        "Rule": {"xytext": (7, 5), "ha": "left", "va": "bottom"},
        "LLM always": {"xytext": (-7, 7), "ha": "right", "va": "bottom"},
        "Adaptive": {"xytext": (-7, -8), "ha": "right", "va": "top"},
    }
    for mode, label, color in zip(modes, labels, colors):
        axes[0, 0].scatter(
            semantic[mode]["latency_ms"]["mean"],
            semantic[mode]["routing_accuracy_pct"],
            s=150,
            color=color,
            edgecolor="black",
        )
        axes[0, 0].annotate(
            label,
            (
                semantic[mode]["latency_ms"]["mean"],
                semantic[mode]["routing_accuracy_pct"],
            ),
            xytext=label_styles[label]["xytext"],
            textcoords="offset points",
            ha=label_styles[label]["ha"],
            va=label_styles[label]["va"],
        )
    axes[0, 0].set_xscale("symlog", linthresh=0.1)
    axes[0, 0].set_xlabel("Mean decision latency (ms, symlog)")
    axes[0, 0].set_ylabel("Routing accuracy (%)")
    axes[0, 0].set_title("(a)", loc="left", fontweight="bold")

    metrics = ["Throughput", "Queue delay", "Retransmission", "PRB usage"]
    no_control = [
        qos["no_control"]["mean_throughput_change_pct"],
        qos["no_control"]["mean_queue_delay_reduction_pct"],
        qos["no_control"]["mean_retransmission_reduction_pct"],
        qos["no_control"]["mean_reduction_pct"],
    ]
    control = [
        qos["control"]["mean_throughput_change_pct"],
        qos["control"]["mean_queue_delay_reduction_pct"],
        qos["control"]["mean_retransmission_reduction_pct"],
        qos["control"]["mean_reduction_pct"],
    ]
    x = np.arange(4)
    width = 0.36
    axes[0, 1].bar(x - width / 2, no_control, width, label="No control", color=COLORS["baseline"])
    axes[0, 1].bar(x + width / 2, control, width, label="E2 control", color=COLORS["control"])
    axes[0, 1].axhline(0, color="black", linewidth=0.8)
    axes[0, 1].set_xticks(x, [item.replace(" ", "\n") for item in metrics])
    axes[0, 1].set_ylabel("Mean improvement (%)")
    axes[0, 1].set_title("(b)", loc="left", fontweight="bold")
    axes[0, 1].legend()

    before = conflict["before_no_arbitration"]
    after = conflict["after_priority_mutex"]
    outcome_labels = ["Conflict", "Loser executed", "Correct winner"]
    before_rates = [before["collision_rate_pct"], before["loser_execution_rate_pct"], before["correct_exclusive_winner_pct"]]
    after_rates = [after["collision_rate_pct"], after["loser_execution_rate_pct"], after["correct_exclusive_winner_pct"]]
    x = np.arange(3)
    axes[1, 0].bar(x - width / 2, before_rates, width, label="Before", color=COLORS["baseline"])
    axes[1, 0].bar(x + width / 2, after_rates, width, label="After", color=COLORS["control"])
    axes[1, 0].set_xticks(x, [item.replace(" ", "\n") for item in outcome_labels])
    axes[1, 0].set_ylim(0, 108)
    axes[1, 0].set_ylabel("Rate (%)")
    axes[1, 0].set_title("(c)", loc="left", fontweight="bold")
    axes[1, 0].legend()

    axes[1, 1].plot(
        [item["card_count"] for item in scaling],
        [item["lookup_mean_us"] for item in scaling],
        color=COLORS["rule"],
        marker="o",
        linewidth=2.2,
    )
    axes[1, 1].set_xlabel("Agent Card count")
    axes[1, 1].set_ylabel("Mean internal lookup (μs)")
    axes[1, 1].set_title("(d)", loc="left", fontweight="bold")
    axes[1, 1].text(0.04, 0.90, "Selection accuracy: 100%", transform=axes[1, 1].transAxes)

    fig.suptitle(
        "Communication-centric evaluation summary",
        fontsize=16,
        fontweight="bold",
    )
    fig.tight_layout()
    save_figure(fig, output_dir, "fig5_communication_summary")


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--result-dir", type=Path, required=True)
    parser.add_argument("--output-dir", type=Path, required=True)
    args = parser.parse_args()
    args.output_dir.mkdir(parents=True, exist_ok=False)

    summary = json.loads((args.result_dir / "summary.json").read_text(encoding="utf-8"))
    semantic = read_csv(args.result_dir / "semantic_cases.csv")
    qos = read_csv(args.result_dir / "prb_effect_episodes.csv")
    conflicts = read_csv(args.result_dir / "integrated_multi_xapp_conflicts.csv")

    plt.style.use("seaborn-v0_8-whitegrid")
    plt.rcParams.update({"font.family": "DejaVu Sans", "axes.titlepad": 10})
    plot_latency_accuracy(args.result_dir, args.output_dir, summary, semantic)
    plot_radio_qos(args.output_dir, summary, qos)
    plot_conflict_mitigation(args.output_dir, summary, conflicts)
    plot_control_plane_scalability(args.output_dir, summary)
    plot_summary(args.output_dir, summary)


if __name__ == "__main__":
    main()
