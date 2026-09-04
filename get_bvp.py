import pandas as pd
import requests
from functools import lru_cache
from datetime import datetime, timedelta
from pybaseball import statcast_pitcher

BVP_COLUMNS = ["Batter", "AB", "H", "2B", "3B", "HR"]


def _extract_stat(payload):
    for stat_group in payload.get("stats", []):
        splits = stat_group.get("splits", [])
        if splits:
            return splits[0].get("stat", {})
    return {}



def _season_from_slate_date(slate_date_iso):
    """Use the slate year so archived/future slate dates query the correct season."""
    try:
        return int(str(slate_date_iso)[:4])
    except (TypeError, ValueError):
        from datetime import datetime
        return datetime.now().year


def _extract_home_runs(payload):
    stat = _extract_stat(payload)
    return int(stat.get("homeRuns", 0) or 0)


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

    # Safe fallback that excludes nearly all spring-training games.
    return f"{int(season)}-03-20"


@lru_cache(maxsize=128)
def _fetch_pitcher_hr_splits_statcast(pitcher_id, slate_date_iso):
    """Count regular-season HR allowed through the day before the slate date."""
    season = _season_from_slate_date(slate_date_iso)
    start_date = _regular_season_start_date(season)

    slate_date = datetime.strptime(str(slate_date_iso)[:10], "%Y-%m-%d")
    end_date = (slate_date - timedelta(days=1)).strftime("%Y-%m-%d")

    if end_date < start_date:
        return 0, 0

    data = statcast_pitcher(start_date, end_date, int(pitcher_id))
    if data is None or data.empty:
        return 0, 0

    home_runs = data[data["events"].eq("home_run")].copy()
    if home_runs.empty:
        return 0, 0

    vs_lhh = int(home_runs["stand"].eq("L").sum())
    vs_rhh = int(home_runs["stand"].eq("R").sum())
    return vs_lhh, vs_rhh

def get_pitcher_hr_splits(pitcher, slate_date_iso):
    """Return current-season HR allowed split by opposing batter handedness."""
    pitcher_id = pitcher.get("id") if isinstance(pitcher, dict) else None
    pitcher_name = pitcher.get("name", "TBD") if isinstance(pitcher, dict) else str(pitcher)

    if not pitcher_id or pitcher_name == "TBD":
        return None

    try:
        vs_lhh, vs_rhh = _fetch_pitcher_hr_splits_statcast(
            int(pitcher_id),
            str(slate_date_iso)[:10],
        )
        return {
            "total_hr": vs_lhh + vs_rhh,
            "vs_lhh": vs_lhh,
            "vs_rhh": vs_rhh,
        }
    except Exception:
        return None


def get_pitcher_hr_split_display(pitcher, slate_date_iso):
    """Format HR splits using the app's established 9L / 9R / 7:7 convention."""
    splits = get_pitcher_hr_splits(pitcher, slate_date_iso)
    if not splits:
        return "HR splits unavailable"

    total = splits["total_hr"]
    vs_lhh = splits["vs_lhh"]
    vs_rhh = splits["vs_rhh"]

    if vs_lhh == vs_rhh:
        split_label = f"{vs_lhh}:{vs_rhh}"
    elif vs_lhh > vs_rhh:
        split_label = f"{vs_lhh}L"
    else:
        split_label = f"{vs_rhh}R"

    return f"{total} HR allowed ({split_label})"


@lru_cache(maxsize=4096)
def _fetch_single_bvp(batter_id, pitcher_id):
    """Fetch and cache one career batter-vs-pitcher matchup in memory."""
    url = f"https://statsapi.mlb.com/api/v1/people/{int(batter_id)}/stats"
    params = {
        "stats": "vsPlayer",
        "group": "hitting",
        "opposingPlayerId": int(pitcher_id),
    }
    response = requests.get(url, params=params, timeout=10)
    response.raise_for_status()
    stat = _extract_stat(response.json())

    return (
        int(stat.get("atBats", 0) or 0),
        int(stat.get("hits", 0) or 0),
        int(stat.get("doubles", 0) or 0),
        int(stat.get("triples", 0) or 0),
        int(stat.get("homeRuns", 0) or 0),
    )

def get_bvp_dataframe(batters, pitcher):
    """Fetch career MLB batter-vs-pitcher totals from MLB StatsAPI."""
    pitcher_id = pitcher.get("id") if isinstance(pitcher, dict) else None
    pitcher_name = pitcher.get("name", "TBD") if isinstance(pitcher, dict) else str(pitcher)

    if not pitcher_id or pitcher_name == "TBD":
        return pd.DataFrame(columns=BVP_COLUMNS), []

    rows = []
    errors = []

    for batter in batters:
        batter_id = batter.get("id") if isinstance(batter, dict) else None
        batter_name = batter.get("name") if isinstance(batter, dict) else str(batter)
        if not batter_id:
            errors.append(f"Missing MLB ID: {batter_name}")
            continue

        try:
            at_bats, hits, doubles, triples, home_runs = _fetch_single_bvp(
                int(batter_id),
                int(pitcher_id),
            )
            if at_bats < 1:
                continue

            rows.append({
                "Batter": batter_name,
                "AB": at_bats,
                "H": hits,
                "2B": doubles,
                "3B": triples,
                "HR": home_runs,
            })
        except Exception as error:
            errors.append(f"{batter_name}: {error}")

    dataframe = pd.DataFrame(rows, columns=BVP_COLUMNS)
    if not dataframe.empty:
        dataframe = dataframe.sort_values(["AB", "H"], ascending=[False, False]).reset_index(drop=True)
    return dataframe, errors
