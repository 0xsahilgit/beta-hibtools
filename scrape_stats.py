from get_lineups import get_players_and_pitchers
import pandas as pd
import numpy as np
import re

def clean_name(name):
    return re.sub(r"[^\w\s]", "", name).lower().strip()

def load_csvs():
    expected_batters = pd.read_csv("expected_batters.csv")
    exit_batters = pd.read_csv("exit_batters.csv")
    expected_pitchers = pd.read_csv("expected_pitchers.csv")
    exit_pitchers = pd.read_csv("exit_pitchers.csv")
    batted_ball = pd.read_csv("batted_ball.csv")

    batted_ball['name_clean'] = batted_ball['name'].apply(clean_name)
    return expected_batters, exit_batters, expected_pitchers, exit_pitchers, batted_ball

def run_scrape(team1, team2):
    expected_batters, exit_batters, expected_pitchers, exit_pitchers, batted_ball = load_csvs()

    team_names = [team1.strip().upper(), team2.strip().upper()]
    batter_rows = exit_batters[exit_batters["Team"].isin(team_names)]
    pitcher_rows = exit_pitchers[exit_pitchers["Team"].isin(team_names)]

    print("Batter Stats:")
    for _, row in batter_rows.iterrows():
        name = row["Name"]
        ev_row = expected_batters[expected_batters["Name"] == name]
        xSLG = float(ev_row["xSLG"]) if not ev_row.empty else "n/a"
        barrel = float(ev_row["Barrel %"]) if not ev_row.empty else "n/a"

        batted_row = batted_ball[batted_ball["name_clean"] == clean_name(name)]
        pull_air = float(batted_row["pull_air_rate"]) if not batted_row.empty else "n/a"
        oppo_air = float(batted_row["oppo_air_rate"]) if not batted_row.empty else "n/a"
        fb = float(batted_row["fb_rate"]) if not batted_row.empty else "n/a"

        EV = row["EV"]
        print(f"{name} | EV: {EV} | Barrel %: {barrel} | xSLG: {xSLG} | PullAir %: {pull_air} | OppoAir %: {oppo_air} | FB %: {fb}")

    print("\nPitcher Stats:")
    for _, row in pitcher_rows.iterrows():
        name = row["Name"]
        ev_row = expected_pitchers[expected_pitchers["Name"] == name]
        barrel_allowed = float(ev_row["Barrel %"]) if not ev_row.empty else "n/a"
        hh = float(ev_row["HardHit %"]) if not ev_row.empty else "n/a"
        print(f"{name} | Hard-Hit %: {hh} | Barrel % Allowed: {barrel_allowed}")

def clean_name(name):
    return re.sub(r"[^\w\s]", "", name).lower().strip()

def format_to_last_first(name):
    parts = name.strip().split()
    if len(parts) < 2:
        return None
    first = parts[0]
    last = " ".join(parts[1:])
    return f"{last}, {first}"

def get_batter_stats(name, exit_batters, expected_batters, batted_ball):
    last_first = format_to_last_first(name)
    if not last_first:
        return {
            "Name": name,
            "EV": "n/a",
            "Barrel %": "n/a",
            "xSLG": "n/a",
            "PullAir %": "n/a",
            "OppoAir %": "n/a",
            "FB %": "n/a"
        }

    ev_row = exit_batters[exit_batters['last_name, first_name'].str.lower() == last_first.lower()]
    exp_row = expected_batters[expected_batters['last_name, first_name'].str.lower() == last_first.lower()]
    cleaned_last_first = clean_name(last_first)
    ball_row = batted_ball[batted_ball['name_clean'] == cleaned_last_first]

    ev = ev_row.iloc[0]['avg_hit_speed'] if not ev_row.empty else 87.0
    barrel = ev_row.iloc[0]['brl_percent'] if not ev_row.empty else 3.0
    xslg = exp_row.iloc[0]['est_slg'] if not exp_row.empty else 0.34
    pull_air = ball_row.iloc[0]['pull_air_rate'] if not ball_row.empty else "n/a"
    oppo_air = ball_row.iloc[0]['oppo_air_rate'] if not ball_row.empty else "n/a"
    fb_rate = ball_row.iloc[0]['fb_rate'] if not ball_row.empty else "n/a"

    return {
        "Name": name,
        "EV": ev,
        "Barrel %": barrel,
        "xSLG": xslg,
        "PullAir %": pull_air,
        "OppoAir %": oppo_air,
        "FB %": fb_rate
    }

def get_pitcher_stats(name, exit_pitchers, expected_pitchers):
    last_first = format_to_last_first(name)
    if not last_first:
        return {
            "Name": name,
            "Hard-Hit %": "n/a",
            "Barrel % Allowed": "n/a"
        }

    ev_row = expected_pitchers[expected_pitchers['last_name, first_name'].str.lower() == last_first.lower()]
    exit_row = exit_pitchers[exit_pitchers['last_name, first_name'].str.lower() == last_first.lower()]

    hh = ev_row.iloc[0]['ev95percent'] if not ev_row.empty else 40.0
    brl_allowed = exit_row.iloc[0]['brl_percent'] if not exit_row.empty else 6.0
    return {
        "Name": name,
        "Hard-Hit %": hh,
        "Barrel % Allowed": brl_allowed
    }

def run_scrape(team1, team2):
    expected_batters, exit_batters, expected_pitchers, exit_pitchers, batted_ball = load_csvs()
    lineup_data = get_players_and_pitchers(team1, team2)
    if not lineup_data:
        return f"Error: No {team1.upper()} vs {team2.upper()} matchup today or lineups unavailable."

    batters, pitchers = lineup_data

    output = "Batter Stats:\n"
    for name in batters:
        stats = get_batter_stats(name, exit_batters, expected_batters, batted_ball)
        output += (
            f"{stats['Name']} | EV: {stats['EV']} | Barrel %: {stats['Barrel %']} | xSLG: {stats['xSLG']} | "
            f"PullAir %: {stats['PullAir %']} | OppoAir %: {stats['OppoAir %']} | FB %: {stats['FB %']}\n"
        )

    output += "\nPitcher Stats:\n"
    for name in pitchers:
        stats = get_pitcher_stats(name, exit_pitchers, expected_pitchers)
        output += (
            f"{stats['Name']} | Hard-Hit %: {stats['Hard-Hit %']} | Barrel % Allowed: {stats['Barrel % Allowed']}\n"
        )

    return output
