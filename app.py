import streamlit as st
import hmac
import pandas as pd
import requests
from datetime import datetime, timedelta
from pybaseball import statcast_batter, playerid_lookup  # added playerid_lookup
from get_lineups import get_players_and_pitchers, get_matchup_data
from get_bvp import get_bvp_dataframe, get_pitcher_hr_split_display
from get_pitchmix import display_pitchmix_for_matchup
from get_spray_chart import display_spray_chart_tab
from bs4 import BeautifulSoup
from PIL import Image

# 🔧 extra imports for robust ID resolving
import re, json, unicodedata, os
from pathlib import Path
from difflib import get_close_matches

# --- CONFIG ---
st.set_page_config(page_title="Hib's Batter Data Tool", layout="wide")


def require_password():
    """Block the app until the shared password is entered."""
    if st.session_state.get("authenticated", False):
        return

    st.title("⚾ Hib's Batter Data Tool")
    entered_password = st.text_input(
        "Enter password",
        type="password",
        key="password_input",
    )

    if st.button("Log in", use_container_width=True):
        try:
            correct_password = str(st.secrets["app_password"])
        except KeyError:
            st.error('Missing Streamlit secret: app_password')
            st.stop()

        if hmac.compare_digest(entered_password, correct_password):
            st.session_state["authenticated"] = True
            st.rerun()
        else:
            st.error("Incorrect password.")

    st.stop()


require_password()

# --- TEAM & STADIUM MAPS ---
TEAM_NAME_MAP_REV = {
    "Arizona Diamondbacks": "ARI", "Atlanta Braves": "ATL", "Baltimore Orioles": "BAL",
    "Boston Red Sox": "BOS", "Chicago Cubs": "CHC", "Chicago White Sox": "CHW",
    "Cincinnati Reds": "CIN", "Cleveland Guardians": "CLE", "Colorado Rockies": "COL",
    "Detroit Tigers": "DET", "Houston Astros": "HOU", "Kansas City Royals": "KCR",
    "Los Angeles Angels": "LAA", "Los Angeles Dodgers": "LAD", "Miami Marlins": "MIA",
    "Milwaukee Brewers": "MIL", "Minnesota Twins": "MIN", "New York Mets": "NYM",
    "New York Yankees": "NYY", "Oakland Athletics": "OAK", "Athletics": "OAK",
    "Philadelphia Phillies": "PHI", "Pittsburgh Pirates": "PIT", "San Diego Padres": "SDP",
    "Seattle Mariners": "SEA", "San Francisco Giants": "SFG", "St. Louis Cardinals": "STL",
    "Tampa Bay Rays": "TBR", "Texas Rangers": "TEX", "Toronto Blue Jays": "TOR",
    "Washington Nationals": "WSH"
}

STADIUM_KEYWORDS = {
    "ARI": "chase field", "ATL": "truist park", "BAL": "camden yards",
    "BOS": "fenway park", "CHC": "wrigley field", "CHW": "guaranteed rate field",
    "CIN": "great american ball park", "CLE": "progressive field", "COL": "coors field",
    "DET": "comerica park", "HOU": "minute maid park", "KCR": "kauffman stadium",
    "LAA": "angel stadium", "LAD": "dodger stadium", "MIA": "loandepot park",
    "MIL": "american family field", "MIN": "target field", "NYM": "citi field",
    "NYY": "yankee stadium", "OAK": "sutter health park", "PHI": "citizens bank park",
    "PIT": "pnc park", "SDP": "petco park", "SEA": "t-mobile park", "SFG": "oracle park",
    "STL": "busch stadium", "TBR": "tropicana field", "TEX": "globe life field",
    "TOR": "rogers centre", "WSH": "nationals park"
}

# --- PLAYER ID MAP ---
id_map = pd.read_csv("player_id_map.csv")

# =========================
# Robust player ID resolver
# =========================

ID_CACHE_PATH = Path("id_cache.json")
_id_cache = {}
if ID_CACHE_PATH.exists():
    try:
        _id_cache = json.loads(ID_CACHE_PATH.read_text())
    except Exception:
        _id_cache = {}

_SUFFIXES = {"jr", "sr", "ii", "iii", "iv", "v"}

# Common nicknames → formal names (extend as needed)
NICKNAME_MAP = {
    "gio": ["giovanni"],
    "mike": ["michael"],
    "tony": ["anthony"],
    "jim": ["james"],
    "jimmy": ["james"],
    "joe": ["joseph"],
    "joey": ["joseph"],
    "johnny": ["john"],
    "nick": ["nicholas"],
    "alex": ["alexander", "alejandro"],
    "andy": ["andrew"],
    "drew": ["andrew"],
    "frankie": ["francisco"],
    "fran": ["francisco", "francis"],
    "pepe": ["jose"],
    "javy": ["javier"],
    "eddy": ["edward", "eduardo"],
    "eddie": ["edward", "eduardo"],
    "nate": ["nathan", "nathaniel"],
    "jake": ["jacob"],
    "zach": ["zachary"],
    # initial-style first names
    "j.t.": ["jt", "john thomas"],
    "jj": ["jj", "jeffrey joseph", "jeffery joseph", "joseph james", "james joseph", "john joseph"],
}

def _strip_accents(s: str) -> str:
    return "".join(c for c in unicodedata.normalize("NFD", s) if unicodedata.category(c) != "Mn")

def _normalize_name(name: str) -> str:
    s = name.strip().replace(",", " ")
    s = s.replace("’", "'").replace(".", "")
    s = re.sub(r"\s+", " ", s)
    parts = [p for p in s.split() if p.lower().strip(".") not in _SUFFIXES]
    s = " ".join(parts)
    s = s.replace("-", " ")
    s = re.sub(r"[^\w\s']", " ", s)
    s = re.sub(r"\s+", " ", s).strip()
    return s

def _variants(full_name: str):
    """Yield reasonable first/last variants (nicknames, initials, deaccented)."""
    base = _normalize_name(full_name)
    yield base

    na = base.replace("'", "")
    if na != base:
        yield na

    deacc = _strip_accents(base)
    if deacc != base:
        yield deacc
    deacc_na = deacc.replace("'", "")
    if deacc_na != deacc:
        yield deacc_na

    toks = base.split()
    if len(toks) >= 2:
        first, last = toks[0], " ".join(toks[1:])
        # first + last only
        fl = f"{first} {last.split()[-1]}"
        if fl != base:
            yield fl

        # nickname expansions
        lf = first.lower()
        if lf in NICKNAME_MAP:
            for exp in NICKNAME_MAP[lf]:
                yield f"{exp.title()} {last}"

        # “JJ” style initials
        if re.fullmatch(r"[A-Za-z]{1,2}", first) and first.isupper():
            if len(first) == 2:
                yield f"{first[0]} {first[1]} {last}"
                if first == "JJ" and "jj" in NICKNAME_MAP:
                    for exp in NICKNAME_MAP["jj"]:
                        yield f"{exp.title()} {last}"

def _save_cache():
    try:
        ID_CACHE_PATH.write_text(json.dumps(_id_cache, indent=2))
    except Exception:
        pass

def _fuzzy_lastname_candidates(last: str, cutoff=0.86):
    """Tiny-typo repair for last names (e.g., 'Ursela' → 'Urshela')."""
    try:
        last_pool = id_map['LASTNAME'].astype(str).str.lower().unique().tolist()
        return get_close_matches(last.lower(), last_pool, n=3, cutoff=cutoff)
    except Exception:
        return []

def _search_statsapi_person_id(name: str):
    """Last-resort: MLB StatsAPI fuzzy search by name."""
    try:
        q = requests.utils.quote(name)
        url = f"https://statsapi.mlb.com/api/v1/people/search?names={q}"
        r = requests.get(url, timeout=6)
        if r.status_code == 200:
            data = r.json()
            people = data.get("people", [])
            if people:
                return int(people[0]["id"])
    except Exception:
        return None
    return None

def lookup_player_id(name: str):
    """
    Cache -> CSV exact -> CSV variants (incl. nickname & fuzzy last name) -> pybaseball -> StatsAPI.
    """
    if not name:
        return None

    # cache
    if name in _id_cache:
        return _id_cache[name]

    # quick-access lowercase columns from CSV
    try:
        player_lower = id_map['PLAYERNAME'].astype(str).str.lower()
        first = id_map['FIRSTNAME'].astype(str).str.strip()
        last = id_map['LASTNAME'].astype(str).str.strip()
        full_lower = (first + ' ' + last).str.lower()
    except Exception:
        player_lower = pd.Series(dtype=str)
        full_lower = pd.Series(dtype=str)

    # 1) CSV exact
    try:
        row = id_map[player_lower == name.lower()]
        if not row.empty:
            pid = int(row['MLBID'].values[0]); _id_cache[name] = pid; _save_cache(); return pid
        row = id_map[full_lower == name.lower()]
        if not row.empty:
            pid = int(row['MLBID'].values[0]); _id_cache[name] = pid; _save_cache(); return pid
    except Exception:
        pass

    # 2) CSV variants (nicknames, initials, deaccented, + fuzzy last-name repair)
    try:
        for v in _variants(name):
            lv = v.lower()
            row = id_map[(player_lower == lv) | (full_lower == lv)]
            if not row.empty:
                pid = int(row['MLBID'].values[0]); _id_cache[name] = pid; _save_cache(); return pid

        # fuzzy last name try
        norm = _normalize_name(name)
        toks = norm.split()
        if len(toks) >= 2:
            f, l = toks[0], toks[-1]
            for lfix in _fuzzy_lastname_candidates(l):
                v2 = f"{f} {lfix}"
                row = id_map[(player_lower == v2.lower()) | (full_lower == v2.lower())]
                if not row.empty:
                    pid = int(row['MLBID'].values[0]); _id_cache[name] = pid; _save_cache(); return pid
                lf = f.lower()
                if lf in NICKNAME_MAP:
                    for exp in NICKNAME_MAP[lf]:
                        v3 = f"{exp.title()} {lfix}"
                        row = id_map[(player_lower == v3.lower()) | (full_lower == v3.lower())]
                        if not row.empty:
                            pid = int(row['MLBID'].values[0]); _id_cache[name] = pid; _save_cache(); return pid
    except Exception:
        pass

    # 3) pybaseball fallback on variants
    try:
        for v in _variants(name):
            toks = v.split()
            if len(toks) >= 2:
                f, l = toks[0], " ".join(toks[1:])
                df = playerid_lookup(l, f)
                if df is not None and not df.empty:
                    if 'mlb_played_last' in df.columns:
                        df = df.sort_values(by='mlb_played_last', ascending=False)
                    pid = int(df.iloc[0]['key_mlbam'])
                    _id_cache[name] = pid; _save_cache(); return pid
    except Exception:
        pass

    # 4) StatsAPI last-resort
    pid = _search_statsapi_person_id(name)
    if pid:
        _id_cache[name] = pid; _save_cache(); return pid

    return None

# =====================================
# 11-day Statcast helpers
# =====================================

@st.cache_data(ttl=60 * 60 * 6, show_spinner=False)
def get_cached_11_day_statcast(player_id, start_date, end_date):
    """Fetch and cache one batter's Statcast data for six hours."""
    return statcast_batter(start_date, end_date, player_id)


def calculate_11_day_scores(team1, team2, weights, handedness_dict, start_date, end_date):
    """Return one ranked 11-day dataframe for a single matchup."""
    batters, _ = get_players_and_pitchers(team1, team2, end_date)
    results = []
    errors = []

    for name in batters:
        player_id = lookup_player_id(name)

        # Preserve the fallback behavior from the original 11-day tab.
        if player_id is None:
            name_parts = name.split(" ")
            if len(name_parts) > 1:
                player_id = lookup_player_id(name_parts[0])

        if player_id is None:
            errors.append(f"Could not resolve player ID: {name}")
            continue

        try:
            data = get_cached_11_day_statcast(player_id, start_date, end_date)
            if data is None or data.empty:
                continue

            avg_ev = data["launch_speed"].mean(skipna=True)
            if pd.isna(avg_ev):
                continue

            barrel_events = data[data["launch_speed"] > 95]
            barrel_pct = len(barrel_events) / len(data) if len(data) > 0 else 0
            fb_pct = len(data[data["launch_angle"] >= 25]) / len(data) if len(data) > 0 else 0

            side = handedness_dict.get(name.lower().strip(), "")
            label = f"{name} ({side})" if side else name

            values = [avg_ev, barrel_pct * 100, fb_pct * 100]
            score = sum(weight * value for weight, value in zip(weights, values))
            score *= 2.34
            results.append((label, score))

        except Exception as error:
            errors.append(f"{name}: {error}")

    results.sort(key=lambda item: item[1], reverse=True)
    dataframe = pd.DataFrame(results, columns=["Player", "Score"])
    return dataframe, errors



def display_bvp_for_matchup(away_team, home_team, slate_date_iso):
    """Render career BvP tables plus each starter's current-season HR splits."""
    matchup_data = get_matchup_data(away_team, home_team, slate_date_iso)
    away_pitcher = matchup_data["away_pitcher"]
    home_pitcher = matchup_data["home_pitcher"]

    if away_pitcher["name"] == "TBD" and home_pitcher["name"] == "TBD":
        st.info("BvP unavailable because probable pitchers have not been announced.")
        return

    if home_pitcher["name"] != "TBD":
        home_hr_display = get_pitcher_hr_split_display(home_pitcher, slate_date_iso)
        st.markdown(
            f"**{matchup_data['away_abbr']} hitters vs {home_pitcher['name']} ({home_pitcher.get('hand','')})"
            f" — {home_hr_display}**"
        )
        away_bvp, away_errors = get_bvp_dataframe(matchup_data["away_batters"], home_pitcher)
        if away_bvp.empty:
            st.caption("No career BvP plate appearances found for the listed hitters.")
        else:
            st.dataframe(away_bvp, use_container_width=True, hide_index=True)
        if away_errors:
            with st.expander(f"⚠️ BvP lookup issues ({len(away_errors)})", expanded=False):
                st.code("\n".join(away_errors))

    if away_pitcher["name"] != "TBD":
        away_hr_display = get_pitcher_hr_split_display(away_pitcher, slate_date_iso)
        st.markdown(
            f"**{matchup_data['home_abbr']} hitters vs {away_pitcher['name']} ({away_pitcher.get('hand','')})"
            f" — {away_hr_display}**"
        )
        home_bvp, home_errors = get_bvp_dataframe(matchup_data["home_batters"], away_pitcher)
        if home_bvp.empty:
            st.caption("No career BvP plate appearances found for the listed hitters.")
        else:
            st.dataframe(home_bvp, use_container_width=True, hide_index=True)
        if home_errors:
            with st.expander(f"⚠️ BvP lookup issues ({len(home_errors)})", expanded=False):
                st.code("\n".join(home_errors))

# --- GET MATCHUPS AND SLATE DATE ---
def get_today_matchups():
    requested_date = datetime.now().strftime("%Y-%m-%d")
    url = f"https://statsapi.mlb.com/api/v1/schedule?sportId=1&date={requested_date}"
    response = requests.get(url).json()

    matchups = []
    slate_date_iso = requested_date

    dates = response.get("dates", [])
    if dates:
        slate_date_iso = dates[0].get("date", requested_date)

    for date_block in dates:
        for game in date_block.get("games", []):
            away = game["teams"]["away"]["team"]["name"]
            home = game["teams"]["home"]["team"]["name"]
            if away in TEAM_NAME_MAP_REV and home in TEAM_NAME_MAP_REV:
                matchups.append(f"{TEAM_NAME_MAP_REV[away]} @ {TEAM_NAME_MAP_REV[home]}")

    slate_date = datetime.strptime(slate_date_iso, "%Y-%m-%d")
    slate_date_label = slate_date.strftime("%B %d").replace(" 0", " ")
    return matchups, slate_date_iso, slate_date_label

# --- UI ---
# --- UI ---

# Clean single-page presentation. This intentionally changes layout only;
# all existing data, calculations, and feature behavior remain the same.
st.markdown("""
<style>
    .block-container {
        max-width: 1320px;
        padding-top: 2.2rem;
        padding-bottom: 4rem;
    }

    h1, h2, h3 {
        letter-spacing: -0.025em;
    }

    [data-testid="stAppViewContainer"] {
        background: #0b0f14;
    }

    [data-testid="stHeader"] {
        background: rgba(11, 15, 20, 0.92);
    }

    .hibs-hero {
        padding: 0.25rem 0 1.35rem 0;
        border-bottom: 1px solid rgba(255,255,255,0.10);
        margin-bottom: 1.6rem;
    }

    .hibs-hero-title {
        font-size: 2.15rem;
        font-weight: 760;
        line-height: 1.05;
        margin: 0;
    }

    .hibs-hero-subtitle {
        margin-top: 0.45rem;
        color: rgba(255,255,255,0.62);
        font-size: 0.95rem;
    }

    .hibs-section {
        margin-top: 2.15rem;
        margin-bottom: 0.75rem;
    }

    .hibs-section-kicker {
        color: rgba(255,255,255,0.50);
        font-size: 0.76rem;
        font-weight: 700;
        letter-spacing: 0.12em;
        text-transform: uppercase;
        margin-bottom: 0.28rem;
    }

    .hibs-section-title {
        font-size: 1.45rem;
        font-weight: 720;
        line-height: 1.2;
        margin: 0;
    }

    .hibs-section-note {
        color: rgba(255,255,255,0.56);
        margin-top: 0.35rem;
        font-size: 0.9rem;
    }

    [data-testid="stExpander"] {
        border: 1px solid rgba(255,255,255,0.10);
        border-radius: 12px;
        overflow: hidden;
        background: rgba(255,255,255,0.025);
    }

    [data-testid="stDataFrame"] {
        border: 1px solid rgba(255,255,255,0.08);
        border-radius: 10px;
        overflow: hidden;
    }

    .stButton > button {
        border-radius: 10px;
        min-height: 2.75rem;
        font-weight: 650;
    }

    div[data-baseweb="select"] > div,
    div[data-testid="stNumberInput"] input {
        border-radius: 9px;
    }

    hr {
        border-color: rgba(255,255,255,0.09);
        margin: 2.25rem 0;
    }
</style>
""", unsafe_allow_html=True)

matchups, slate_date_iso, slate_date_label = get_today_matchups()

# Replace the default title with a compact dashboard header.
st.markdown(
    f"""
    <div class="hibs-hero">
        <div class="hibs-hero-title">⚾ Hib's Batter Data Tool</div>
        <div class="hibs-hero-subtitle">{datetime.strptime(slate_date_iso, '%Y-%m-%d').strftime('%A, %B %d').replace(' 0', ' ')} MLB slate</div>
    </div>
    """,
    unsafe_allow_html=True,
)


def section_header(kicker, title, note=None):
    note_html = f'<div class="hibs-section-note">{note}</div>' if note else ''
    st.markdown(
        f"""
        <div class="hibs-section">
            <div class="hibs-section-kicker">{kicker}</div>
            <div class="hibs-section-title">{title}</div>
            {note_html}
        </div>
        """,
        unsafe_allow_html=True,
    )


# =========================
# 11-DAY STATS
# =========================
section_header("11-Day Stats", f"Recent batter rankings — {slate_date_label} slate")

with st.expander("How to use 11-Day Stats", expanded=False):
    st.markdown("""
    1. Set the EV, Barrel %, and FB % weights.
    2. Use **Run Selected Game** for one matchup.
    3. Use **Run All Games** to calculate every matchup and keep each game's rankings separate.
    """)

available_7d_stats = ["EV", "Barrel %", "FB %"]
default_weights_7d = [0.42735, 0.42735, 0.14530]

st.markdown("**Weights**")
weight_cols = st.columns(3)
weight_inputs_7d = []
for i, stat in enumerate(available_7d_stats):
    with weight_cols[i]:
        weight = st.number_input(
            stat,
            min_value=0.0,
            max_value=1.0,
            value=default_weights_7d[i],
            step=0.01,
            key=f"7d_weight_{i}"
        )
        weight_inputs_7d.append(weight)

total_weight_7d = sum(weight_inputs_7d)
if abs(total_weight_7d - 1.0) > 0.001:
    st.warning(
        f"Your weights currently total {total_weight_7d:.2f}. "
        "They do not have to equal 1.00, but that changes the score scale."
    )

if not matchups:
    st.info(f"No MLB matchups are available for the {slate_date_label} slate.")
else:
    selected_matchup_7d = st.selectbox(
        f"Matchup ({slate_date_label} Slate)",
        matchups,
        key="7d_matchup"
    )
    team1_7d, team2_7d = selected_matchup_7d.split(" @ ")

    selected_col, all_col = st.columns(2)
    run_selected_7d = selected_col.button(
        "Run Selected Game",
        key="run_selected_11_day",
        use_container_width=True
    )
    run_all_7d = all_col.button(
        "Run All Games",
        key="run_all_11_day",
        use_container_width=True
    )

    today_7d = slate_date_iso
    slate_datetime_7d = datetime.strptime(slate_date_iso, "%Y-%m-%d")
    eleven_days_ago_7d = (slate_datetime_7d - timedelta(days=11)).strftime("%Y-%m-%d")

    handedness_df_7d = pd.read_csv("handedness.csv")
    handedness_dict_7d = dict(zip(
        handedness_df_7d["Name"].str.lower().str.strip(),
        handedness_df_7d["Side"]
    ))

    if run_selected_7d:
        with st.spinner(f"Fetching 11-day player data for {selected_matchup_7d}..."):
            df_7d, selected_errors = calculate_11_day_scores(
                team1_7d,
                team2_7d,
                weight_inputs_7d,
                handedness_dict_7d,
                eleven_days_ago_7d,
                today_7d
            )

        st.markdown(f"### {selected_matchup_7d}")
        with st.expander("Batter Scores", expanded=True):
            if df_7d.empty:
                st.error("No data found for the selected players.")
            else:
                st.dataframe(df_7d, use_container_width=True, hide_index=True)

            if selected_errors:
                st.markdown(f"**⚠️ Skipped players ({len(selected_errors)})**")
                st.code("\n".join(selected_errors))

        detail_left, detail_right = st.columns(2)
        with detail_left:
            with st.expander("Career Batter vs Pitcher", expanded=False):
                display_bvp_for_matchup(team1_7d, team2_7d, slate_date_iso)
        with detail_right:
            with st.expander("Pitch Mix Matchups", expanded=False):
                display_pitchmix_for_matchup(team1_7d, team2_7d, slate_date_iso)

    if run_all_7d:
        st.markdown("### All Games — 11-Day Rankings")
        progress_bar = st.progress(0)
        status_text = st.empty()
        total_matchups = len(matchups)
        successful_games = 0

        for matchup_number, matchup in enumerate(matchups, start=1):
            status_text.write(
                f"Processing game {matchup_number} of {total_matchups}: {matchup}"
            )

            try:
                away_team, home_team = matchup.split(" @ ")
                game_df, game_errors = calculate_11_day_scores(
                    away_team,
                    home_team,
                    weight_inputs_7d,
                    handedness_dict_7d,
                    eleven_days_ago_7d,
                    today_7d
                )

                st.markdown(f"### {matchup}")
                with st.expander("Batter Scores", expanded=False):
                    if game_df.empty:
                        st.warning("No 11-day data found for this game.")
                    else:
                        st.dataframe(
                            game_df,
                            use_container_width=True,
                            hide_index=True
                        )
                        successful_games += 1

                    if game_errors:
                        st.markdown(f"**⚠️ Skipped players ({len(game_errors)})**")
                        st.code("\n".join(game_errors))

                game_detail_left, game_detail_right = st.columns(2)
                with game_detail_left:
                    with st.expander("Career Batter vs Pitcher", expanded=False):
                        display_bvp_for_matchup(away_team, home_team, slate_date_iso)
                with game_detail_right:
                    with st.expander("Pitch Mix Matchups", expanded=False):
                        display_pitchmix_for_matchup(away_team, home_team, slate_date_iso)

            except Exception as error:
                with st.expander(matchup, expanded=True):
                    st.error(f"Could not process this game: {error}")

            progress_bar.progress(matchup_number / total_matchups)

        status_text.success(
            f"Finished. Produced rankings for {successful_games} of {total_matchups} games."
        )

st.divider()

# =========================
# WEATHER
# =========================
section_header("Weather", f"Game conditions — {slate_date_label} slate")

if not matchups:
    st.info(f"No MLB matchups are available for the {slate_date_label} slate.")
else:
    selected_weather_matchup = st.selectbox(
        f"Matchup ({slate_date_label} Slate)",
        matchups,
        key="weather_matchup",
    )
    team1, team2 = selected_weather_matchup.split(" @ ")
    keywords = [STADIUM_KEYWORDS.get(team1, "").lower(), STADIUM_KEYWORDS.get(team2, "").lower()]

    try:
        url = "https://rotogrinders.com/weather/mlb"
        response = requests.get(url, headers={"User-Agent": "Mozilla/5.0"})
        soup = BeautifulSoup(response.text, "html.parser")
        blocks = soup.find_all("div", class_="module")

        found = False
        all_locations = []

        for block in blocks:
            location_div = block.find("span", class_="game-weather-stadium")
            if not location_div:
                continue

            location = location_div.get_text()[2:].strip().lower()
            all_locations.append(location)
            weather_data = block.find_all("div", class_="weather-gametime-set")

            if len(weather_data) == 0:
                if any(k in location or location in k for k in keywords if k):
                    st.success(f"Location match: `{location}`")
                    st.markdown("Game is played inside a dome")
                    found = True
                continue

            temp = weather_data[0].find_all("span", recursive=False)[-2].find("span", class_="weather-gametime-value bold").get_text()
            precipitation = weather_data[0].find_all("span", recursive=False)[-1].find("span", class_="weather-gametime-value bold").get_text()
            wind_dir = weather_data[1].find_all("span", recursive=False)[-2].find("span", class_="weather-gametime-value bold").get_text()
            wind_speed = weather_data[1].find_all("span", recursive=False)[-1].find("span", class_="weather-gametime-value bold").get_text()

            paths = block.find_all("span", class_="weather-gametime-icon")[-1].find('svg').find_all('path')
            target_path = paths[2]
            style = target_path.get('style')
            style_dict = dict(item.strip().split(':') for item in style.split(';') if item)
            transform = style_dict.get('transform', '')
            rotation_match = re.search(r'rotate\(([\d.]+)deg\)', transform)
            rotation_angle = float(rotation_match.group(1)) if rotation_match else 0.0

            if any(k in location or location in k for k in keywords if k):
                st.success(f"Location match: `{location}`")

                weather_text, weather_arrow = st.columns([2, 1])
                with weather_text:
                    weather_rows = st.columns(3)
                    weather_rows[0].metric("Wind Speed", f"{wind_speed} MPH")
                    weather_rows[1].metric("Precipitation", precipitation)
                    weather_rows[2].metric("Temperature", temp)
                    st.markdown(f"**Wind Direction:** {wind_dir}")
                with weather_arrow:
                    img = Image.open("arrow.png")
                    img = img.rotate(360 - rotation_angle, expand=True)
                    st.image(img, caption="Wind direction")
                found = True
                break

        if not found:
            st.warning("No wind data found for this matchup.")
            with st.expander("All locations found on RotoGrinders", expanded=False):
                st.code("\n".join(all_locations))

    except Exception as e:
        st.error(f"Error loading weather data: {e}")

st.divider()

# =========================
# SPRAY CHARTS
# =========================
section_header("Spray Charts", f"Hitter HRs and pitcher HRs allowed — {slate_date_label} slate")
display_spray_chart_tab(matchups, slate_date_iso, slate_date_label, show_heading=False)
