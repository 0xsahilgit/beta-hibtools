from datetime import datetime

import matplotlib.pyplot as plt
import numpy as np
import pandas as pd
import streamlit as st
from pybaseball import statcast_batter, statcast_pitcher

from get_lineups import get_matchup_data


@st.cache_data(ttl=60 * 60 * 6, show_spinner=False)
def _batter_home_runs(player_id: int, start_date: str, end_date: str) -> pd.DataFrame:
    """Return current-season Statcast home runs for one batter."""
    data = statcast_batter(start_date, end_date, int(player_id))
    if data is None or data.empty or "events" not in data.columns:
        return pd.DataFrame(columns=["hc_x", "hc_y"])

    home_runs = data[data["events"].eq("home_run")].copy()
    return home_runs.dropna(subset=["hc_x", "hc_y"])


@st.cache_data(ttl=60 * 60 * 6, show_spinner=False)
def _pitcher_home_runs_allowed(player_id: int, start_date: str, end_date: str) -> pd.DataFrame:
    """Return current-season Statcast home runs allowed by one pitcher."""
    data = statcast_pitcher(start_date, end_date, int(player_id))
    if data is None or data.empty or "events" not in data.columns:
        return pd.DataFrame(columns=["hc_x", "hc_y"])

    home_runs = data[data["events"].eq("home_run")].copy()
    return home_runs.dropna(subset=["hc_x", "hc_y"])


def _spray_coordinates(data: pd.DataFrame) -> tuple[np.ndarray, np.ndarray]:
    """Convert Baseball Savant hit coordinates to a home-plate-centered field."""
    if data.empty:
        return np.array([]), np.array([])

    x = pd.to_numeric(data["hc_x"], errors="coerce") - 125.42
    y = 198.27 - pd.to_numeric(data["hc_y"], errors="coerce")
    valid = x.notna() & y.notna()
    return x[valid].to_numpy(), y[valid].to_numpy()


def _draw_generic_field(ax) -> None:
    """Draw a simple generic baseball field behind the Statcast locations."""
    home = np.array([0.0, 0.0])
    first = np.array([45.0, 45.0])
    second = np.array([0.0, 90.0])
    third = np.array([-45.0, 45.0])

    # Foul lines and outfield fence.
    foul_corner = 113.14
    ax.plot([0, -foul_corner], [0, foul_corner], linewidth=1.5)
    ax.plot([0, foul_corner], [0, foul_corner], linewidth=1.5)

    theta = np.linspace(np.pi / 4, 3 * np.pi / 4, 300)
    radius = 160
    ax.plot(radius * np.cos(theta), radius * np.sin(theta), linewidth=1.5)

    # Infield diamond.
    diamond = np.vstack([home, first, second, third, home])
    ax.plot(diamond[:, 0], diamond[:, 1], linewidth=1.2)

    ax.set_xlim(-165, 165)
    ax.set_ylim(-8, 170)
    ax.set_aspect("equal", adjustable="box")
    ax.axis("off")


def build_spray_chart(
    batter_home_runs: pd.DataFrame,
    pitcher_home_runs: pd.DataFrame,
    batter_name: str,
    pitcher_name: str,
    season_year: int,
):
    """Build the generic-field overlay chart."""
    figure, axis = plt.subplots(figsize=(8, 7))
    _draw_generic_field(axis)

    batter_x, batter_y = _spray_coordinates(batter_home_runs)
    pitcher_x, pitcher_y = _spray_coordinates(pitcher_home_runs)

    if len(batter_x):
        axis.scatter(
            batter_x,
            batter_y,
            s=58,
            alpha=0.72,
            c="#8B1A1A",
            edgecolors="none",
            label=f"{batter_name}: {len(batter_x)} HR",
            zorder=3,
        )

    if len(pitcher_x):
        axis.scatter(
            pitcher_x,
            pitcher_y,
            s=58,
            alpha=0.72,
            c="#173F73",
            edgecolors="none",
            label=f"{pitcher_name}: {len(pitcher_x)} HR allowed",
            zorder=3,
        )

    axis.set_title(
        f"{batter_name} HRs vs. {pitcher_name} HRs Allowed — {season_year}",
        pad=14,
        fontsize=13,
    )

    if len(batter_x) or len(pitcher_x):
        axis.legend(loc="upper right", frameon=False)

    figure.tight_layout()
    return figure


def display_spray_chart_tab(matchups: list[str], slate_date_iso: str, slate_date_label: str, show_heading: bool = True) -> None:
    """Render the Spray Charts interface."""
    if show_heading:
        st.markdown(f"### ⚾ Spray Charts ({slate_date_label} Slate)")

    if not matchups:
        st.info(f"No MLB matchups are available for the {slate_date_label} slate.")
        return

    selected_matchup = st.selectbox(
        f"Select Matchup ({slate_date_label} Slate)",
        matchups,
        key="spray_matchup",
    )
    away_team, home_team = selected_matchup.split(" @ ")

    try:
        matchup = get_matchup_data(away_team, home_team, slate_date_iso)
    except Exception as error:
        st.error(f"Could not load matchup data: {error}")
        return

    pitcher_choices = []
    if matchup["away_pitcher"].get("id"):
        pitcher_choices.append({
            "label": f"{matchup['away_pitcher']['name']} ({matchup['away_abbr']})",
            "pitcher": matchup["away_pitcher"],
            "batters": matchup["home_batters"],
            "opponent": matchup["home_abbr"],
        })
    if matchup["home_pitcher"].get("id"):
        pitcher_choices.append({
            "label": f"{matchup['home_pitcher']['name']} ({matchup['home_abbr']})",
            "pitcher": matchup["home_pitcher"],
            "batters": matchup["away_batters"],
            "opponent": matchup["away_abbr"],
        })

    if not pitcher_choices:
        st.info("Spray charts are unavailable because probable pitchers have not been announced.")
        return

    selected_pitcher_label = st.selectbox(
        "Pitcher",
        [choice["label"] for choice in pitcher_choices],
        key="spray_pitcher",
    )
    selected_choice = next(
        choice for choice in pitcher_choices if choice["label"] == selected_pitcher_label
    )

    opposing_batters = [b for b in selected_choice["batters"] if b.get("id")]
    if not opposing_batters:
        st.info(
            f"The {selected_choice['opponent']} lineup is not available yet. "
            "Check again after the lineup is posted."
        )
        return

    batter_labels = [batter["name"] for batter in opposing_batters]
    selected_batter_name = st.selectbox(
        f"Hitter ({selected_choice['opponent']})",
        batter_labels,
        key="spray_batter",
    )
    selected_batter = next(
        batter for batter in opposing_batters if batter["name"] == selected_batter_name
    )

    run_chart = st.button(
        "Run Spray Chart",
        key="run_spray_chart",
        use_container_width=True,
    )
    if not run_chart:
        return

    slate_date = datetime.strptime(slate_date_iso, "%Y-%m-%d")
    season_year = slate_date.year
    start_date = f"{season_year}-03-01"
    end_date = slate_date_iso

    with st.spinner(
        f"Loading {season_year} home-run locations for "
        f"{selected_batter_name} and {selected_choice['pitcher']['name']}..."
    ):
        try:
            batter_home_runs = _batter_home_runs(
                selected_batter["id"], start_date, end_date
            )
            pitcher_home_runs = _pitcher_home_runs_allowed(
                selected_choice["pitcher"]["id"], start_date, end_date
            )
        except Exception as error:
            st.error(f"Could not load Statcast spray-chart data: {error}")
            return

    if batter_home_runs.empty:
        st.info(f"{selected_batter_name} has no chartable {season_year} home runs through {slate_date_label}.")
    if pitcher_home_runs.empty:
        st.info(
            f"{selected_choice['pitcher']['name']} has no chartable {season_year} "
            f"home runs allowed through {slate_date_label}."
        )

    chart = build_spray_chart(
        batter_home_runs,
        pitcher_home_runs,
        selected_batter_name,
        selected_choice["pitcher"]["name"],
        season_year,
    )
    st.pyplot(chart, use_container_width=True)
    plt.close(chart)
