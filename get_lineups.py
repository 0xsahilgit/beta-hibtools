import requests
from datetime import datetime
from functools import lru_cache

TEAM_NAME_MAP = {
    "ARI": "Arizona Diamondbacks", "ATL": "Atlanta Braves", "BAL": "Baltimore Orioles",
    "BOS": "Boston Red Sox", "CHC": "Chicago Cubs", "CHW": "Chicago White Sox",
    "CIN": "Cincinnati Reds", "CLE": "Cleveland Guardians", "COL": "Colorado Rockies",
    "DET": "Detroit Tigers", "HOU": "Houston Astros", "KCR": "Kansas City Royals",
    "LAA": "Los Angeles Angels", "LAD": "Los Angeles Dodgers", "MIA": "Miami Marlins",
    "MIL": "Milwaukee Brewers", "MIN": "Minnesota Twins", "NYM": "New York Mets",
    "NYY": "New York Yankees", "OAK": "Oakland Athletics", "PHI": "Philadelphia Phillies",
    "PIT": "Pittsburgh Pirates", "SDP": "San Diego Padres", "SEA": "Seattle Mariners",
    "SFG": "San Francisco Giants", "STL": "St. Louis Cardinals", "TBR": "Tampa Bay Rays",
    "TEX": "Texas Rangers", "TOR": "Toronto Blue Jays", "WSH": "Washington Nationals"
}

TEAM_NAME_ALIASES = {
    "OAK": "Athletics"
}


def _team_name(team_abbr):
    abbreviation = team_abbr.upper()
    return TEAM_NAME_ALIASES.get(abbreviation, TEAM_NAME_MAP[abbreviation])


def _ordered_batters(team_box):
    """Return the team's hitters in batting-order order when available."""
    lineup = []
    for player in team_box.get("players", {}).values():
        person = player.get("person", {})
        name = person.get("fullName")
        player_id = person.get("id")
        if not name:
            continue

        if "battingOrder" in player:
            lineup.append((int(player["battingOrder"]), name, player_id))
        elif "stats" in player or "position" in player:
            lineup.append((999, name, player_id))

    lineup.sort(key=lambda item: item[0])
    return [{"name": name, "id": player_id} for _, name, player_id in lineup]



@lru_cache(maxsize=128)
def get_pitching_hand(player_id):
    if not player_id:
        return ""
    try:
        url = f"https://statsapi.mlb.com/api/v1/people/{player_id}"
        r = requests.get(url, timeout=10)
        r.raise_for_status()
        people = r.json().get("people", [])
        if not people:
            return ""
        code = people[0].get("pitchHand", {}).get("code", "")
        return f"{code}HP" if code in ("R","L") else ""
    except Exception:
        return ""


def get_matchup_data(team1_abbr, team2_abbr, slate_date_iso=None):
    """Return team-specific hitters and probable pitchers for one matchup."""
    requested_date = slate_date_iso or datetime.now().strftime("%Y-%m-%d")
    schedule_url = (
        "https://statsapi.mlb.com/api/v1/schedule"
        f"?sportId=1&date={requested_date}&hydrate=probablePitcher"
    )
    response = requests.get(schedule_url, timeout=10)
    response.raise_for_status()
    schedule = response.json()

    team1_name = _team_name(team1_abbr)
    team2_name = _team_name(team2_abbr)

    for date_block in schedule.get("dates", []):
        for game in date_block.get("games", []):
            away_name = game["teams"]["away"]["team"]["name"]
            home_name = game["teams"]["home"]["team"]["name"]

            if {away_name, home_name} != {team1_name, team2_name}:
                continue

            game_id = game["gamePk"]
            box_url = f"https://statsapi.mlb.com/api/v1/game/{game_id}/boxscore"
            box_response = requests.get(box_url, timeout=10)
            box_response.raise_for_status()
            box = box_response.json()

            away_batters = _ordered_batters(box.get("teams", {}).get("away", {}))
            home_batters = _ordered_batters(box.get("teams", {}).get("home", {}))

            away_pitcher = game["teams"]["away"].get("probablePitcher", {})
            home_pitcher = game["teams"]["home"].get("probablePitcher", {})

            return {
                "game_id": game_id,
                "away_abbr": team1_abbr if away_name == team1_name else team2_abbr,
                "home_abbr": team2_abbr if home_name == team2_name else team1_abbr,
                "away_batters": away_batters,
                "home_batters": home_batters,
                "away_pitcher": {
                    "name": away_pitcher.get("fullName", "TBD"),
                    "id": away_pitcher.get("id"),
                    "hand": get_pitching_hand(away_pitcher.get("id")),
                },
                "home_pitcher": {
                    "name": home_pitcher.get("fullName", "TBD"),
                    "id": home_pitcher.get("id"),
                    "hand": get_pitching_hand(home_pitcher.get("id")),
                },
            }

    return {
        "game_id": None,
        "away_abbr": team1_abbr,
        "home_abbr": team2_abbr,
        "away_batters": [],
        "home_batters": [],
        "away_pitcher": {"name": "TBD", "id": None, "hand": ""},
        "home_pitcher": {"name": "TBD", "id": None, "hand": ""},
    }


def get_players_and_pitchers(team1_abbr, team2_abbr, slate_date_iso=None):
    """Backward-compatible combined batter and pitcher lists."""
    matchup = get_matchup_data(team1_abbr, team2_abbr, slate_date_iso)
    batters = [p["name"] for p in matchup["home_batters"]]
    batters.extend(p["name"] for p in matchup["away_batters"])
    pitchers = [
        matchup["away_pitcher"]["name"],
        matchup["home_pitcher"]["name"],
    ]
    return batters, pitchers


if __name__ == "__main__":
    import sys

    if len(sys.argv) not in (3, 4):
        print("Usage: python3 get_lineups.py TEAM1 TEAM2 [YYYY-MM-DD]")
    else:
        date_arg = sys.argv[3] if len(sys.argv) == 4 else None
        batters, pitchers = get_players_and_pitchers(sys.argv[1], sys.argv[2], date_arg)
        print("Batters:")
        for batter in batters:
            print("-", batter)
        print("\nPitchers:")
        for pitcher in pitchers:
            print("-", pitcher)
