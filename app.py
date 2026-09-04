import streamlit as st
import pandas as pd
import requests
from datetime import datetime, timedelta
from pybaseball import statcast_batter, playerid_lookup  # added playerid_lookup
from get_lineups import get_players_and_pitchers
from bs4 import BeautifulSoup
from PIL import Image

# 🔧 extra imports for robust ID resolving
import re, json, unicodedata, os
from pathlib import Path
from difflib import get_close_matches

# --- CONFIG ---
st.set_page_config(page_title="Hib's Batter Data Tool", layout="wide")
st.title("⚾ Hib's Batter Data Tool")

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
    batters, _ = get_players_and_pitchers(team1, team2)
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
            results.append((label, score))

        except Exception as error:
            errors.append(f"{name}: {error}")

    results.sort(key=lambda item: item[1], reverse=True)
    dataframe = pd.DataFrame(results, columns=["Player", "Score"])
    return dataframe, errors


# --- GET TODAY'S MATCHUPS ---
def get_today_matchups():
    today = datetime.now().strftime("%Y-%m-%d")
    url = f"https://statsapi.mlb.com/api/v1/schedule?sportId=1&date={today}"
    response = requests.get(url).json()
    matchups = []
    for date in response.get("dates", []):
        for game in date.get("games", []):
            away = game["teams"]["away"]["team"]["name"]
            home = game["teams"]["home"]["team"]["name"]
            if away in TEAM_NAME_MAP_REV and home in TEAM_NAME_MAP_REV:
                matchups.append(f"{TEAM_NAME_MAP_REV[away]} @ {TEAM_NAME_MAP_REV[home]}")
    return matchups

# --- UI ---
matchups = get_today_matchups()
tab1, tab2, tab3 = st.tabs(["Season Stats", "11-Day Stats", "Weather"])

# === TAB 1 ===
with tab1:
    with st.expander("ℹ️ How to Use", expanded=False):
        st.markdown("""
        1. Select a matchup from today's schedule.
        2. Choose how many stats you want to weight (1–4).
        3. Select the stat types and set your weights.
        4. Click **Run Model + Rank** to view the top hitters.
        """)

    selected_matchup = st.selectbox("Select Today's Matchup", matchups if matchups else ["No matchups available"])
    team1, team2 = selected_matchup.split(" @ ")

    st.markdown("### 🎯 Stat Weights")
    num_stats = st.slider("How many stats do you want to weight?", 1, 4, 2)
    available_stats = ["EV", "Barrel %", "xSLG", "FB %", "RightFly", "LeftFly"]
    default_weights = {1: [1.0], 2: [0.5, 0.5], 3: [0.33, 0.33, 0.34], 4: [0.25, 0.25, 0.25, 0.25]}
    weight_defaults = default_weights.get(num_stats, [1.0])

    stat_selections = []
    weight_inputs = []
    for i in range(num_stats):
        col1, col2 = st.columns(2)
        stat = col1.selectbox(f"Stat {i+1}", available_stats, index=i % len(available_stats), key=f"stat_{i}")
        weight = col2.number_input(f"Weight {i+1}", min_value=0.0, max_value=1.0, value=weight_defaults[i], step=0.01, key=f"w_{i}")
        stat_selections.append(stat)
        weight_inputs.append(weight)

    if st.button("⚡ Run Model + Rank (Season Stats)"):
        with st.spinner("🧮 Crunching season stats... please wait!"):
            from scrape_stats import run_scrape
            raw_output = run_scrape(team1, team2)
            lines = raw_output.split("\n")
            batter_lines = []
            reading = False
            for line in lines:
                if "Batter Stats:" in line:
                    reading = True
                    continue
                if "Pitcher Stats:" in line:
                    break
                if reading and line.strip():
                    batter_lines.append(line)

            handedness_df = pd.read_csv("handedness.csv")
            handedness_dict = dict(zip(handedness_df["Name"].str.lower().str.strip(), handedness_df["Side"]))

            def get_stat_value(name, stats, stat_key):
                handed = handedness_dict.get(name.lower().strip(), "R")
                if stat_key == "RightFly":
                    return stats.get("PullAir %") if handed == "R" else stats.get("OppoAir %")
                elif stat_key == "LeftFly":
                    return stats.get("PullAir %") if handed == "L" else stats.get("OppoAir %")
                else:
                    return stats.get(stat_key)

            results = []
            for line in batter_lines:
                parts = [x.strip() for x in line.split("|")]
                stat_dict = {}
                for p in parts[1:]:
                    if ": " in p:
                        k, v = p.split(": ")
                        try:
                            stat_dict[k.strip()] = float(v)
                        except:
                            stat_dict[k.strip()] = None
                stat_dict["Name"] = parts[0]
                values = [get_stat_value(stat_dict["Name"], stat_dict, s) for s in stat_selections]
                if None not in values:
                    score = sum(w * v for w, v in zip(weight_inputs, values))
                    results.append((stat_dict["Name"], score))

            results.sort(key=lambda x: x[1], reverse=True)
            df = pd.DataFrame(results, columns=["Player", "Score"])
            st.markdown("### 🏆 Ranked Hitters (Season)")
            st.dataframe(df, use_container_width=True)

# === TAB 2 ===
with tab2:
    with st.expander("ℹ️ 11-Day Stats How to Use", expanded=False):
        st.markdown("""
        1. Set the EV, Barrel %, and FB % weights.
        2. Use **Run Selected Game** for one matchup.
        3. Use **Run All Games** to calculate every matchup and keep each game's rankings separate.
        """)

    available_7d_stats = ["EV", "Barrel %", "FB %"]
    default_weights_7d = [0.33, 0.33, 0.34]

    st.markdown("### 🎯 11-Day Stat Weights")
    weight_inputs_7d = []
    for i, stat in enumerate(available_7d_stats):
        col1, col2 = st.columns(2)
        col1.markdown(f"**{stat}**")
        weight = col2.number_input(
            f"Weight {stat}",
            min_value=0.0,
            max_value=1.0,
            value=default_weights_7d[i],
            step=0.01,
            key=f"7d_weight_{i}"
        )
        weight_inputs_7d.append(weight)

    total_weight_7d = sum(weight_inputs_7d)
    if abs(total_weight_7d - 1.0) > 0.001:
        st.warning(f"Your weights currently total {total_weight_7d:.2f}. They do not have to equal 1.00, but that changes the score scale.")

    if not matchups:
        st.info("No MLB matchups are available for today.")
    else:
        selected_matchup_7d = st.selectbox(
            "Select Today's Matchup (11-Day)",
            matchups,
            key="7d_matchup"
        )
        team1_7d, team2_7d = selected_matchup_7d.split(" @ ")

        selected_col, all_col = st.columns(2)
        run_selected_7d = selected_col.button(
            "⚡ Run Selected Game",
            key="run_selected_11_day",
            use_container_width=True
        )
        run_all_7d = all_col.button(
            "⚾ Run All Games",
            key="run_all_11_day",
            use_container_width=True
        )

        today_7d = datetime.now().strftime("%Y-%m-%d")
        eleven_days_ago_7d = (datetime.now() - timedelta(days=11)).strftime("%Y-%m-%d")

        handedness_df_7d = pd.read_csv("handedness.csv")
        handedness_dict_7d = dict(zip(
            handedness_df_7d["Name"].str.lower().str.strip(),
            handedness_df_7d["Side"]
        ))

        if run_selected_7d:
            with st.spinner(f"📈 Fetching 11-day player data for {selected_matchup_7d}..."):
                df_7d, selected_errors = calculate_11_day_scores(
                    team1_7d,
                    team2_7d,
                    weight_inputs_7d,
                    handedness_dict_7d,
                    eleven_days_ago_7d,
                    today_7d
                )

            st.markdown(f"### 🏆 {selected_matchup_7d}")
            if df_7d.empty:
                st.error("No data found for the selected players.")
            else:
                st.dataframe(df_7d, use_container_width=True, hide_index=True)

            if selected_errors:
                with st.expander(f"⚠️ Skipped players ({len(selected_errors)})"):
                    st.code("\n".join(selected_errors))

        if run_all_7d:
            st.markdown("### 🏆 All Games — 11-Day Rankings")
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

                    with st.expander(matchup, expanded=True):
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
                            with st.expander(f"⚠️ Skipped players ({len(game_errors)})"):
                                st.code("\n".join(game_errors))

                except Exception as error:
                    with st.expander(matchup, expanded=True):
                        st.error(f"Could not process this game: {error}")

                progress_bar.progress(matchup_number / total_matchups)

            status_text.success(
                f"Finished. Produced rankings for {successful_games} of {total_matchups} games."
            )


# === TAB 3 ===
with tab3:
    st.markdown("### 🌬️ Weather Conditions (via RotoGrinders)")
    selected_weather_matchup = st.selectbox("Select Today's Matchup (Weather)", matchups, key="weather_matchup")
    team1, team2 = selected_weather_matchup.split(" @ ")
    keywords = [STADIUM_KEYWORDS.get(team1, "").lower(), STADIUM_KEYWORDS.get(team2, "").lower()]
    # st.markdown(f"🧪 Debug: Matching Stadium Keywords → {keywords}")

    try:
        url = "https://rotogrinders.com/weather/mlb"
        response = requests.get(url, headers={"User-Agent": "Mozilla/5.0"})
        soup = BeautifulSoup(response.text, "html.parser")
        blocks = soup.find_all("div", class_="module")

        found = False
        all_locations = []

        for block in blocks:
            location_div = block.find("span", class_="game-weather-stadium")
            if not location_div :
                continue

            location = location_div.get_text()[2:].strip().lower()
            all_locations.append(location)

            weather_data = block.find_all("div", class_="weather-gametime-set")

            if len(weather_data) == 0:
                if any(k in location or location in k for k in keywords if k):
                    st.success(f"✅ Location match: `{location}`")
                    st.markdown(f"Game is played inside a dome")
                    found = True
                continue

            temp = weather_data[0].find_all("span", recursive=False)[-2].find("span", class_="weather-gametime-value bold").get_text()
            precipitation = weather_data[0].find_all("span", recursive=False)[-1].find("span", class_="weather-gametime-value bold").get_text()
            wind_dir = weather_data[1].find_all("span", recursive=False)[-2].find("span", class_="weather-gametime-value bold").get_text()
            wind_speed = weather_data[1].find_all("span", recursive=False)[-1].find("span", class_="weather-gametime-value bold").get_text()

            # Find all <path> elements within the <svg>
            paths = block.find_all("span", class_="weather-gametime-icon")[-1].find('svg').find_all('path')

            # Target the third <path> (index 2) which has rotate
            target_path = paths[2]

            # Get the style attribute
            style = target_path.get('style')
            style_dict = dict(item.strip().split(':') for item in style.split(';') if item)
            transform = style_dict.get('transform', '')

            # Extract rotation angle from the transform
            rotation_match = re.search(r'rotate\(([\d.]+)deg\)', transform)
            if rotation_match:
                rotation_angle = float(rotation_match.group(1))
            else:
                rotation_angle = 0.0

            if any(k in location or location in k for k in keywords if k):
                st.success(f"✅ Location match: `{location}`")

                col1, col2 = st.columns(2)
                with col1:
                    st.markdown(f"**💨 Wind Speed:**")
                    st.markdown(f"**🧭 Wind Direction:**")
                    st.markdown(f"**🌧️ Precipitation::**")
                    st.markdown(f"**🌡️Temperature::**")
                with col2:
                    st.markdown(f"`{wind_speed} MPH`")
                    img = Image.open("arrow.png")
                    img = img.rotate(360 - rotation_angle, expand=True)
                    st.image(img)
                    st.markdown(f"`{precipitation}`")
                    st.markdown(f"`{temp}`")
                found = True
                break

        if not found:
            st.warning("⚠️ No wind data found for this matchup.")
            st.markdown("### 🗺️ All Locations Found on RotoGrinders:")
            st.code("\n".join(all_locations))

    except Exception as e:
        st.error(f"Error loading weather data: {e}")
