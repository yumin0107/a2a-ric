import argparse
import csv
import json
from pathlib import Path

import matplotlib.pyplot as plt
import numpy as np


COLORS = ["#0072B2", "#D55E00", "#009E73"]


def read_csv(path: Path) -> list[dict[str, str]]:
    with path.open(newline="", encoding="utf-8") as stream:
        return list(csv.DictReader(stream))


def add_distribution(
    axis: plt.Axes,
    values: list[list[float]],
    labels: list[str],
    colors: list[str],
) -> None:
    boxes = axis.boxplot(
        values,
        patch_artist=True,
        widths=0.55,
        showmeans=True,
        meanprops={"marker": "D", "markerfacecolor": "white", "markeredgecolor": "#222222", "markersize": 5},
        medianprops={"color": "#202020", "linewidth": 1.8},
        whiskerprops={"color": "#555555", "linewidth": 1.2},
        capprops={"color": "#555555", "linewidth": 1.2},
        flierprops={"marker": "o", "markersize": 3, "alpha": 0.45},
    )
    for patch, color in zip(boxes["boxes"], colors):
        patch.set_facecolor(color)
        patch.set_alpha(0.72)

    random = np.random.default_rng(42)
    for index, (series, color) in enumerate(zip(values, colors), 1):
        jitter = random.normal(index, 0.045, len(series))
        axis.scatter(jitter, series, s=11, color=color, alpha=0.34, linewidths=0, zorder=1)
        mean = float(np.mean(series))
        axis.annotate(
            f"{mean:.2f}",
            (index, max(series)),
            xytext=(0, 7),
            textcoords="offset points",
            ha="center",
            va="bottom",
            fontsize=8.5,
            fontweight="bold",
            color=color,
        )
    axis.set_xticks(range(1, len(labels) + 1), labels)


def create_plot(result_dir: Path, output_stem: str) -> tuple[Path, Path]:
    latency = read_csv(result_dir / "latency_trials.csv")
    kpi = read_csv(result_dir / "kpi_effect.csv")
    routing = read_csv(result_dir / "routing_scalability.csv")
    summary = json.loads((result_dir / "summary.json").read_text(encoding="utf-8"))

    direct = [float(row["end_to_end_ms"]) for row in latency if row["path"] == "direct_rest"]
    a2a = [float(row["end_to_end_ms"]) for row in latency if row["path"] == "a2a"]
    full = [float(row["full_cycle_ms"]) for row in latency if row["path"] == "a2a"]
    before = [float(row["prb_usage"]) for row in kpi if row["phase"] == "before_control"]
    after = [float(row["prb_usage"]) for row in kpi if row["phase"] == "after_control"]
    card_counts = [int(row["card_count"]) for row in routing]
    route_mean = [float(row["mean_us"]) for row in routing]
    route_p95 = [float(row["p95_us"]) for row in routing]

    plt.rcParams.update(
        {
            "font.family": "DejaVu Sans",
            "font.size": 9,
            "axes.titlesize": 11,
            "axes.labelsize": 9.5,
            "axes.spines.top": False,
            "axes.spines.right": False,
            "axes.grid": True,
            "grid.alpha": 0.22,
            "grid.linewidth": 0.7,
        }
    )
    figure, axes = plt.subplots(1, 3, figsize=(12.8, 4.8))
    figure.subplots_adjust(left=0.06, right=0.985, bottom=0.20, top=0.76, wspace=0.24)

    add_distribution(
        axes[0],
        [direct, a2a, full],
        ["Direct REST\n→ control", "A2A decision\n→ control", "Policy creation\n→ control"],
        COLORS,
    )
    axes[0].set_title("(a) End-to-end latency (30 runs)", loc="left", fontweight="bold")
    axes[0].set_ylabel("Latency (ms)")
    axes[0].set_ylim(bottom=0)

    add_distribution(
        axes[1],
        [before, after],
        ["Before control", "After control"],
        [COLORS[0], COLORS[2]],
    )
    reduction = summary["closed_loop_prb_usage"]["mean_reduction_pct"]
    axes[1].annotate(
        f"Mean reduction: {reduction:.1f}%",
        xy=(1.5, 0.91),
        ha="center",
        va="center",
        fontsize=9,
        fontweight="bold",
        color="#333333",
        bbox={"boxstyle": "round,pad=0.28", "facecolor": "white", "edgecolor": "#BBBBBB"},
    )
    axes[1].set_title("(b) Closed-loop synthetic PRB effect", loc="left", fontweight="bold")
    axes[1].set_ylabel("Normalized PRB usage")
    axes[1].set_ylim(0, 1.08)

    positions = np.arange(len(card_counts))
    axes[2].plot(positions, route_mean, marker="o", markersize=6, linewidth=2, color=COLORS[0], label="Mean")
    axes[2].plot(positions, route_p95, marker="s", markersize=5, linewidth=1.7, linestyle="--", color=COLORS[1], label="p95")
    for x_value, y_value in zip(positions, route_mean):
        axes[2].annotate(
            f"{y_value:.2f}",
            (x_value, y_value),
            xytext=(0, 7),
            textcoords="offset points",
            ha="center",
            fontsize=8.5,
            fontweight="bold",
            color=COLORS[0],
        )
    axes[2].set_xticks(positions, [str(value) for value in card_counts])
    axes[2].set_title("(c) Agent Card routing scalability", loc="left", fontweight="bold")
    axes[2].set_xlabel("Number of registered Agent Cards")
    axes[2].set_ylabel("Linear lookup time (μs)")
    axes[2].set_ylim(bottom=0)
    axes[2].legend(frameon=False, loc="upper left")

    e2_mean = summary["e2_indication_latency_us"]["mean"]
    e2_p95 = summary["e2_indication_latency_us"]["p95"]
    success = summary["success_rate_pct"]["a2a"]
    figure.suptitle(
        "FlexRIC A2A Prototype Evaluation",
        fontsize=15,
        fontweight="bold",
        y=0.97,
    )
    figure.text(
        0.5,
        0.895,
        f"E2 indication: mean {e2_mean:.1f} μs, p95 {e2_p95:.1f} μs   |   E2 control success: {success:.0f}%",
        ha="center",
        va="top",
        fontsize=9.5,
        color="#444444",
    )
    figure.text(
        0.5,
        0.035,
        "FlexRIC built-in gNB emulator on one host; PRB response is synthetic. Error distributions show 30 measured runs (3 warm-ups excluded).",
        ha="center",
        va="bottom",
        fontsize=8,
        color="#555555",
    )

    png_path = result_dir / f"{output_stem}.png"
    pdf_path = result_dir / f"{output_stem}.pdf"
    figure.savefig(png_path, dpi=300, bbox_inches="tight", facecolor="white")
    figure.savefig(pdf_path, bbox_inches="tight", facecolor="white")
    plt.close(figure)
    return png_path, pdf_path


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--result-dir", type=Path, required=True)
    parser.add_argument("--output-stem", default="flexric_a2a_evaluation")
    args = parser.parse_args()
    png_path, pdf_path = create_plot(args.result_dir.resolve(), args.output_stem)
    print(png_path)
    print(pdf_path)


if __name__ == "__main__":
    main()
