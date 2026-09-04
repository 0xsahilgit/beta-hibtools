from datetime import datetime, timedelta
from functools import lru_cache

import pandas as pd
import requests
import streamlit as st
from pybaseball import statcast_batter, statcast_pitcher


PITCH_NAMES = {
    "FF": "4-Seam",
    "SI": "Sinker",
    "FC": "Cutter",
    "SL": "Slider",
    "ST": "Sweeper",
    "CU": "Curve",
    "KC": "Knuckle Curve",
    "CH": "Changeup",
    "FS": "Splitter",
    "FO": "Forkball",
    "SC": "Screwball",
    "KN": "Knuckleball",
    "EP": "Eephus",
    "SV": "Slurve",
    "CS": "Slow Curve",
}

# Ignore one-off tracking classifications while still showing every real pitch.
MIN_PITCH_USAGE_PCT = 1.0


def _season_from_slate_date(slate_date_iso):
    try:
        return int(str(slate_date_iso)[:4])
    except (TypeError, ValueError):
        return datetime.now().year


@lru_cache(maxsize=16)
def _regular_season_start_date(season):
    """Return MLB's official regular-season start date for the requested year."""
    try:
        url = f"https://statsapi.mlb.com/api/v1/seasons/{int(season)}"
        response = requests.get(url, params={"sportId": 1}, timeout=10)
        response.raise_for_status()
        seasons = response.json().get("seasons", [])
        if seasons:
            start_date = seasons[0].get("regularSeasonStartDate")
            if start_date:
                return str(start_date)[:10]
    except Exception:
        pass

    return f"{int(season)}-03-20"


def _statcast_date_range(slate_date_iso):
    season = _season_from_slate_date(slate_date_iso)
    start_date = _regular_season_start_date(season)
    slate_date = datetime.strptime(str(slate_date_iso)[:10], "%Y-%m-%d")
    end_date = (slate_date - timedelta(days=1)).strftime("%Y-%m-%d")
    return start_date, end_date


@lru_cache(maxsize=128)
def _fetch_pitcher_statcast(pitcher_id, start_date, end_date):
    """Cache one pitcher's season-to-date Statcast rows in memory."""
    if end_date < start_date:
        return pd.DataFrame()
    data = statcast_pitcher(start_date, end_date, int(pitcher_id))
    return data.copy() if data is not None else pd.DataFrame()


@lru_cache(maxsize=4096)
def _fetch_batter_statcast(batter_id, start_date, end_date):
    """Cache one batter's season-to-date Statcast rows in memory."""
    if end_date < start_date:
        return pd.DataFrame()
    data = statcast_batter(start_date, end_date, int(batter_id))
    return data.copy() if data is not None else pd.DataFrame()


@lru_cache(maxsize=256)
def _fetch_pitcher_hand(pitcher_id):
    try:
        response = requests.get(
            f"https://statsapi.mlb.com/api/v1/people/{int(pitcher_id)}",
            timeout=10,
        )
        response.raise_for_status()
        people = response.json().get("people", [])
        if people:
            return people[0].get("pitchHand", {}).get("code", "")
    except Exception:
        pass
    return ""


def _pitch_mix_dataframe(pitcher_id, slate_date_iso):
    start_date, end_date = _statcast_date_range(slate_date_iso)
    data = _fetch_pitcher_statcast(int(pitcher_id), start_date, end_date)

    if data.empty or "pitch_type" not in data.columns:
        return pd.DataFrame(columns=["Pitch", "Usage", "Pitches"]), []

    pitch_types = data["pitch_type"].dropna().astype(str)
    if pitch_types.empty:
        return pd.DataFrame(columns=["Pitch", "Usage", "Pitches"]), []

    counts = pitch_types.value_counts()
    total = int(counts.sum())
    rows = []

    for pitch_code, count in counts.items():
        usage_pct = (int(count) / total) * 100 if total else 0.0
        if usage_pct < MIN_PITCH_USAGE_PCT:
            continue
        rows.append({
            "Pitch Code": pitch_code,
            "Pitch": PITCH_NAMES.get(pitch_code, pitch_code),
            "Usage Value": usage_pct,
            "Usage": f"{usage_pct:.1f}%",
            "Pitches": int(count),
        })

    rows.sort(key=lambda row: row["Usage Value"], reverse=True)
    dataframe = pd.DataFrame(rows)
    if dataframe.empty:
        return pd.DataFrame(columns=["Pitch", "Usage", "Pitches"]), []

    pitch_codes = dataframe["Pitch Code"].tolist()
    return dataframe[["Pitch", "Usage", "Pitches"]], pitch_codes


def _batter_hr_dataframe(batters, pitch_codes, slate_date_iso):
    start_date, end_date = _statcast_date_range(slate_date_iso)
    rows = []
    errors = []

    for batter in batters:
        batter_id = batter.get("id") if isinstance(batter, dict) else None
        batter_name = batter.get("name") if isinstance(batter, dict) else str(batter)

        if not batter_id:
            errors.append(f"Missing MLB ID: {batter_name}")
            continue

        try:
            data = _fetch_batter_statcast(int(batter_id), start_date, end_date)
            home_runs = pd.DataFrame()
            if (
                not data.empty
                and "events" in data.columns
                and "pitch_type" in data.columns
            ):
                home_runs = data[data["events"].eq("home_run")]

            row = {"Batter": batter_name}
            for pitch_code in pitch_codes:
                count = 0
                if not home_runs.empty:
                    count = int(home_runs["pitch_type"].eq(pitch_code).sum())
                row[PITCH_NAMES.get(pitch_code, pitch_code)] = count
            rows.append(row)
        except Exception as error:
            errors.append(f"{batter_name}: {error}")

    columns = ["Batter"] + [PITCH_NAMES.get(code, code) for code in pitch_codes]
    return pd.DataFrame(rows, columns=columns), errors


def _render_pitcher_matchup(team_abbr, batters, pitcher, slate_date_iso):
    pitcher_id = pitcher.get("id") if isinstance(pitcher, dict) else None
    pitcher_name = pitcher.get("name", "TBD") if isinstance(pitcher, dict) else str(pitcher)

    if not pitcher_id or pitcher_name == "TBD":
        st.info(f"{team_abbr} pitch-mix matchup unavailable because the probable pitcher is TBD.")
        return

    hand = pitcher.get("hand", "") if isinstance(pitcher, dict) else ""
    if not hand:
        hand = _fetch_pitcher_hand(int(pitcher_id))
    hand_label = f" ({hand}HP)" if hand in {"R", "L"} else ""

    st.markdown(f"**{team_abbr} hitters vs {pitcher_name}{hand_label}**")

    try:
        pitch_mix_df, pitch_codes = _pitch_mix_dataframe(pitcher_id, slate_date_iso)
    except Exception as error:
        st.error(f"Could not load {pitcher_name}'s pitch mix: {error}")
        return

    if pitch_mix_df.empty or not pitch_codes:
        st.caption("No season pitch-mix data found.")
        return

    st.caption("Pitch usage — current regular season through the day before this slate")
    st.dataframe(pitch_mix_df, use_container_width=True, hide_index=True)

    with st.spinner(f"Loading hitter HR totals by pitch type vs {pitcher_name}..."):
        hr_df, errors = _batter_hr_dataframe(batters, pitch_codes, slate_date_iso)

    st.caption("Hitter home runs by pitch type — current regular season")
    if hr_df.empty:
        st.caption("No hitter pitch-type data found.")
    else:
        st.dataframe(hr_df, use_container_width=True, hide_index=True)

    if errors:
        with st.expander(f"⚠️ Pitch-mix lookup issues ({len(errors)})", expanded=False):
            st.code("\n".join(errors))


def display_pitchmix_for_matchup(away_team, home_team, slate_date_iso):
    """Render each starter's pitch mix and opposing hitters' HR totals by pitch type."""
    # Imported here to keep the module independent and avoid circular imports.
    from get_lineups import get_matchup_data

    matchup_data = get_matchup_data(away_team, home_team, slate_date_iso)
    away_pitcher = matchup_data["away_pitcher"]
    home_pitcher = matchup_data["home_pitcher"]

    away_name = away_pitcher.get("name", "TBD") if isinstance(away_pitcher, dict) else str(away_pitcher)
    home_name = home_pitcher.get("name", "TBD") if isinstance(home_pitcher, dict) else str(home_pitcher)

    if away_name == "TBD" and home_name == "TBD":
        st.info("Pitch mix unavailable because probable pitchers have not been announced.")
        return

    if home_name != "TBD":
        _render_pitcher_matchup(
            matchup_data["away_abbr"],
            matchup_data["away_batters"],
            home_pitcher,
            slate_date_iso,
        )

    if home_name != "TBD" and away_name != "TBD":
        st.divider()

    if away_name != "TBD":
        _render_pitcher_matchup(
            matchup_data["home_abbr"],
            matchup_data["home_batters"],
            away_pitcher,
            slate_date_iso,
        )
