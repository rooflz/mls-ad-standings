import json
import re
import time
import urllib.parse
import urllib.request
from datetime import datetime
from pathlib import Path

FEED_URL = "https://mls-assist.theintelligenceplatform.com/data/schedule/mls-next-2-academy-division-26-27.json"
OUTPUT_HTML = "index.html"
MATCHES_CACHE = "u13_carolinas_data.json"

CAROLINAS_U13_TEAMS = [
    "Carolina Core FC",
    "Carolina Velocity FC",
    "Charleston SC",
    "Charlotte Eagles",
    "Charlotte Independence Soccer Club",
    "Fox Soccer Academy Carolinas",
    "Lowcountry United",
    "Queen City Mutiny FC",
    "Triangle United Soccer Association",
    "Wake FC",
]

TEAM_LOOKUP = {
    "carolina velocity": "Carolina Velocity FC",
    "velocity": "Carolina Velocity FC",
    "carolina core": "Carolina Core FC",
    "charleston": "Charleston SC",
    "charlotte eagles": "Charlotte Eagles",
    "charlotte independence": "Charlotte Independence Soccer Club",
    "fox soccer": "Fox Soccer Academy Carolinas",
    "lowcountry": "Lowcountry United",
    "queen city": "Queen City Mutiny FC",
    "mutiny": "Queen City Mutiny FC",
    "triangle united": "Triangle United Soccer Association",
    "wake fc": "Wake FC",
    "wake": "Wake FC",
}

# Regional home markets used when venue feeds omit city and state
TEAM_HOME_ANCHORS = {
    "Carolina Core FC": "High Point, NC",
    "Carolina Velocity FC": "Raleigh, NC",
    "Charleston SC": "Charleston, SC",
    "Charlotte Eagles": "Charlotte, NC",
    "Charlotte Independence Soccer Club": "Charlotte, NC",
    "Fox Soccer Academy Carolinas": "Charlotte, NC",
    "Lowcountry United": "Dorchester, SC",
    "Queen City Mutiny FC": "Charlotte, NC",
    "Triangle United Soccer Association": "Chapel Hill, NC",
    "Wake FC": "Holly Springs, NC",
}

# Known complex overrides: takes priority over the home team's nominal market
VENUE_SPECIFIC_ANCHORS = {
    "soccer.com": ("Soccer.com Center", "Efland, NC"),
    "efland": ("Soccer.com Center", "Efland, NC"),
    "wakemed": ("WakeMed Soccer Park", "Cary, NC"),
    "wral": ("WRAL Soccer Park", "Raleigh, NC"),
    "ting": ("Ting Park", "Holly Springs, NC"),
    "orthocarolina": ("OrthoCarolina Sportsplex", "Pineville, NC"),
    "bryan park": ("Bryan Park Soccer Complex", "Browns Summit, NC"),
    "truist point": ("Truist Point", "High Point, NC"),
    "woodland": ("Woodland High School", "Dorchester, SC"),
}


def resolve_team(name: str) -> str | None:
    if not name:
        return None
    cleaned = name.strip().lower()

    for canonical in CAROLINAS_U13_TEAMS:
        if canonical.lower() == cleaned:
            return canonical

    for key, canonical in TEAM_LOOKUP.items():
        if re.search(r"\b" + re.escape(key) + r"\b", cleaned):
            return canonical

    return None


def clean_venue_for_maps_search(venue_str: str) -> str:
    """Strips field annotations and numbers so Google Maps geocodes the facility accurately."""
    cleaned = re.sub(r"\(.*?\)", "", venue_str)
    cleaned = re.sub(
        r"(?:-|\b)\s*(?:Stadium|Field\s*#?\d+|Pitch\s*#?\d+|Auxiliary)\b",
        "",
        cleaned,
        flags=re.I,
    )
    cleaned = re.sub(r"\s*-\s*$", "", cleaned)
    cleaned = re.sub(r"\s+", " ", cleaned).strip()
    return cleaned if cleaned else venue_str.strip()


def parse_location(raw_loc, home_team: str | None = None) -> dict:
    """Parses event_location into display text and an anchored Google Maps search URL."""
    if not raw_loc:
        return {"display": "TBD", "maps_url": None}

    venue = ""
    field = ""
    city = ""
    state = ""
    addr = ""

    if isinstance(raw_loc, str):
        venue = raw_loc.strip()
    elif isinstance(raw_loc, dict):
        venue = (
            raw_loc.get("name")
            or raw_loc.get("venue_name")
            or raw_loc.get("facility")
            or raw_loc.get("venue")
            or raw_loc.get("title")
            or ""
        )
        field = (
            raw_loc.get("field")
            or raw_loc.get("pitch")
            or raw_loc.get("field_name")
            or ""
        )
        addr = (
            raw_loc.get("address")
            or raw_loc.get("street")
            or raw_loc.get("street_address")
            or ""
        )
        city = raw_loc.get("city") or ""
        state = raw_loc.get("state") or ""

    if not venue and not addr:
        return {"display": "TBD", "maps_url": None}

    # 1. Display Label (keeps field & turf numbers for the card title)
    display_parts = [p.strip() for p in [venue, field] if p and p.strip()]
    display = " - ".join(display_parts) if display_parts else (addr or "TBD")

    # 2. Check for venue-specific overrides (e.g. Soccer.com -> Efland, NC)
    venue_lower = f"{venue} {addr}".lower()
    override_name = None
    override_region = None

    for key, (canonical_name, canonical_region) in VENUE_SPECIFIC_ANCHORS.items():
        if key in venue_lower:
            override_name = canonical_name
            override_region = canonical_region
            break

    # 3. Geocoding query formulation
    search_venue = override_name or clean_venue_for_maps_search(venue)
    query_parts = []

    if addr:
        query_parts.append(addr)
    elif search_venue:
        query_parts.append(search_venue)

    if city and state:
        query_parts.append(f"{city}, {state}")
    elif state:
        query_parts.append(state)
    elif override_region:
        query_parts.append(override_region)
    elif home_team and home_team in TEAM_HOME_ANCHORS:
        query_parts.append(TEAM_HOME_ANCHORS[home_team])

    maps_query = ", ".join(query_parts)
    encoded = urllib.parse.quote(maps_query)
    maps_url = f"https://www.google.com/maps/search/?api=1&query={encoded}"

    return {
        "display": display,
        "maps_url": maps_url,
    }


def fetch_all_matches() -> tuple[list, list]:
    url = f"{FEED_URL}?_t={int(time.time() * 1000)}"
    print(f"Fetching season schedule feed from:\n  {url}")

    req = urllib.request.Request(
        url,
        headers={
            "User-Agent": "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko)",
            "Accept": "application/json",
        },
    )

    with urllib.request.urlopen(req, timeout=30) as resp:
        payload = json.loads(resp.read().decode("utf-8"))

    events = payload.get("events", [])
    print(f"Total events in season feed: {len(events)}")

    completed_matches = []
    upcoming_matches = []
    seen_ids = set()

    for ev in events:
        division_str = str(ev.get("division") or "").strip()
        home_squad = str(ev.get("home_squad_name") or "")
        away_squad = str(ev.get("away_squad_name") or "")
        home_org = str(ev.get("home_organisation") or "")
        away_org = str(ev.get("away_organisation") or "")

        is_u13 = any("u13" in s.lower() for s in [division_str, home_squad, away_squad])
        if not is_u13:
            continue

        home_team = resolve_team(home_org) or resolve_team(home_squad)
        away_team = resolve_team(away_org) or resolve_team(away_squad)

        if not home_team or not away_team or home_team == away_team:
            continue

        game_id = str(ev.get("id") or ev.get("game_key") or f"{home_team}_{away_team}_{ev.get('start_time')}")
        if game_id in seen_ids:
            continue
        seen_ids.add(game_id)

        h_score = ev.get("home_score")
        a_score = ev.get("away_score")
        is_completed = ev.get("completed", False)
        start_time = ev.get("start_time", "")
        location_data = parse_location(ev.get("event_location"), home_team=home_team)

        has_scores = h_score is not None and a_score is not None
        is_real_game = has_scores and not (is_completed is False and int(h_score) == 0 and int(a_score) == 0)

        if is_real_game:
            completed_matches.append({
                "game_id": game_id,
                "home": home_team,
                "away": away_team,
                "home_score": int(h_score),
                "away_score": int(a_score),
                "start_time": start_time,
                "location": location_data,
            })
        else:
            upcoming_matches.append({
                "game_id": game_id,
                "home": home_team,
                "away": away_team,
                "start_time": start_time,
                "location": location_data,
            })

    print(f"Matched {len(completed_matches)} completed and {len(upcoming_matches)} upcoming U13 Carolinas games.")
    return completed_matches, upcoming_matches


def compile_standings(completed: list) -> list:
    stats = {
        t: {
            "team": t,
            "gp": 0, "w": 0, "d": 0, "l": 0,
            "gf": 0, "ga": 0, "gd": 0, "pts": 0,
            "cs": 0, "form": [],
            "home_w": 0, "home_d": 0, "home_l": 0, "home_gf": 0, "home_ga": 0, "home_pts": 0,
            "away_w": 0, "away_d": 0, "away_l": 0, "away_gf": 0, "away_ga": 0, "away_pts": 0,
            "match_logs": [],
        }
        for t in CAROLINAS_U13_TEAMS
    }

    sorted_matches = sorted(completed, key=lambda x: x.get("start_time", ""))

    for m in sorted_matches:
        h, a = m["home"], m["away"]
        hs, as_ = m["home_score"], m["away_score"]
        date_str = m["start_time"][:10] if m.get("start_time") else "Completed"

        for t in (h, a):
            if t not in stats:
                stats[t] = {
                    "team": t, "gp": 0, "w": 0, "d": 0, "l": 0,
                    "gf": 0, "ga": 0, "gd": 0, "pts": 0,
                    "cs": 0, "form": [],
                    "home_w": 0, "home_d": 0, "home_l": 0, "home_gf": 0, "home_ga": 0, "home_pts": 0,
                    "away_w": 0, "away_d": 0, "away_l": 0, "away_gf": 0, "away_ga": 0, "away_pts": 0,
                    "match_logs": [],
                }

        stats[h]["gp"] += 1
        stats[a]["gp"] += 1
        stats[h]["gf"] += hs
        stats[h]["ga"] += as_
        stats[a]["gf"] += as_
        stats[a]["ga"] += hs

        if as_ == 0:
            stats[h]["cs"] += 1
        if hs == 0:
            stats[a]["cs"] += 1

        if hs > as_:
            stats[h]["w"] += 1
            stats[h]["pts"] += 3
            stats[h]["home_w"] += 1
            stats[h]["home_pts"] += 3
            stats[h]["form"].append("W")

            stats[a]["l"] += 1
            stats[a]["away_l"] += 1
            stats[a]["form"].append("L")

            h_res, a_res = "W", "L"
        elif as_ > hs:
            stats[a]["w"] += 1
            stats[a]["pts"] += 3
            stats[a]["away_w"] += 1
            stats[a]["away_pts"] += 3
            stats[a]["form"].append("W")

            stats[h]["l"] += 1
            stats[h]["home_l"] += 1
            stats[h]["form"].append("L")

            h_res, a_res = "L", "W"
        else:
            stats[h]["d"] += 1
            stats[h]["pts"] += 1
            stats[h]["home_d"] += 1
            stats[h]["home_pts"] += 1
            stats[h]["form"].append("D")

            stats[a]["d"] += 1
            stats[a]["pts"] += 1
            stats[a]["away_d"] += 1
            stats[a]["away_pts"] += 1
            stats[a]["form"].append("D")

            h_res, a_res = "D", "D"

        stats[h]["home_gf"] += hs
        stats[h]["home_ga"] += as_
        stats[a]["away_gf"] += as_
        stats[a]["away_ga"] += hs

        stats[h]["match_logs"].append({
            "opponent": a,
            "role": "Home",
            "result": h_res,
            "score": f"{hs} - {as_}",
            "date": date_str,
            "location": m.get("location", {}),
        })
        stats[a]["match_logs"].append({
            "opponent": h,
            "role": "Away",
            "result": a_res,
            "score": f"{as_} - {hs}",
            "date": date_str,
            "location": m.get("location", {}),
        })

    table = list(stats.values())
    for t in table:
        t["gd"] = t["gf"] - t["ga"]
        t["ppg"] = round(t["pts"] / t["gp"], 2) if t["gp"] > 0 else 0.0
        t["gf_per_g"] = round(t["gf"] / t["gp"], 2) if t["gp"] > 0 else 0.0
        t["ga_per_g"] = round(t["ga"] / t["gp"], 2) if t["gp"] > 0 else 0.0
        t["cs_pct"] = round((t["cs"] / t["gp"]) * 100, 1) if t["gp"] > 0 else 0.0
        t["recent_form"] = t["form"][-5:]
        t["match_logs"].reverse()

    table.sort(
        key=lambda x: (x["pts"], x["ppg"], x["gd"], x["gf"], -ord(x["team"][0])),
        reverse=True,
    )

    for idx, t in enumerate(table, 1):
        t["rank"] = idx

    return table


def generate_html_dashboard(standings: list, upcoming: list, output_filename: str):
    generated_at = datetime.now().strftime("%B %d, %Y at %I:%M %p")
    json_standings = json.dumps(standings)
    json_upcoming = json.dumps(sorted(upcoming, key=lambda x: x.get("start_time", "")))

    html = f"""<!DOCTYPE html>
<html lang="en">
<head>
    <meta charset="UTF-8">
    <meta name="viewport" content="width=device-width, initial-scale=1.0">
    <title>MLS NEXT | U13 Carolinas Analytics & Standings</title>
    <script src="https://cdn.tailwindcss.com"></script>
    <link rel="preconnect" href="https://fonts.googleapis.com">
    <link rel="preconnect" href="https://fonts.gstatic.com" crossorigin>
    <link href="https://fonts.googleapis.com/css2?family=Plus+Jakarta+Sans:wght@400;500;600;700;800&display=swap" rel="stylesheet">
    <style>
        body {{
            font-family: 'Plus Jakarta Sans', sans-serif;
            background-color: #080c14;
            color: #f1f5f9;
        }}
        .table-sort-header {{
            cursor: pointer;
            user-select: none;
            transition: color 0.15s ease;
        }}
        .table-sort-header:hover {{
            color: #38bdf8;
        }}
    </style>
</head>
<body class="min-h-screen py-8 px-4 sm:px-6 lg:px-8">
    <div class="max-w-7xl mx-auto space-y-8">
        
        <!-- Header -->
        <header class="bg-slate-900/90 border border-slate-800 rounded-2xl p-6 backdrop-blur-md shadow-2xl flex flex-col md:flex-row md:items-center md:justify-between gap-4">
            <div>
                <div class="flex items-center space-x-3 mb-1">
                    <span class="inline-flex items-center px-2.5 py-0.5 rounded-full text-xs font-semibold bg-cyan-950 text-cyan-400 border border-cyan-800">
                        MLS NEXT Academy
                    </span>
                    <span class="text-xs font-medium text-slate-400">U13 Carolinas Conference</span>
                </div>
                <h1 class="text-2xl sm:text-3xl font-extrabold text-white tracking-tight">Soccer Standings & Match Intelligence</h1>
                <p class="text-xs text-slate-400 mt-1">Live Feed Synchronized &bull; {generated_at}</p>
            </div>
            
            <div class="flex items-center gap-3">
                <input id="searchInput" type="text" placeholder="Filter team..." 
                    class="bg-slate-950 border border-slate-700 rounded-xl px-4 py-2 text-sm text-slate-200 placeholder-slate-500 focus:outline-none focus:ring-2 focus:ring-cyan-500 transition w-full md:w-56" />
                <button onclick="exportToCSV()" class="bg-slate-800 hover:bg-slate-700 text-slate-200 border border-slate-700 px-3 py-2 rounded-xl text-sm font-semibold transition">
                    Export CSV
                </button>
            </div>
        </header>

        <!-- KPI Cards -->
        <div id="statsRow" class="grid grid-cols-2 md:grid-cols-4 gap-4"></div>

        <!-- Standings Table -->
        <div class="space-y-2">
            <div class="flex items-center justify-between px-1">
                <h2 class="text-lg font-bold text-white flex items-center gap-2">
                    <span>Conference Table</span>
                    <span class="text-xs text-slate-400 font-normal">(Click any team to expand match history & splits)</span>
                </h2>
            </div>

            <div class="bg-slate-900/60 border border-slate-800 rounded-2xl shadow-xl overflow-hidden backdrop-blur-md">
                <div class="overflow-x-auto">
                    <table class="w-full text-left text-sm" id="standingsTable">
                        <thead class="bg-slate-950/80 text-slate-400 uppercase tracking-wider text-[11px] font-semibold border-b border-slate-800">
                            <tr>
                                <th class="py-4 px-3 text-center w-12">#</th>
                                <th class="py-4 px-4 table-sort-header" onclick="sortTable('team')">Team</th>
                                <th class="py-4 px-3 text-center table-sort-header" onclick="sortTable('gp')">GP</th>
                                <th class="py-4 px-3 text-center table-sort-header" onclick="sortTable('w')">W</th>
                                <th class="py-4 px-3 text-center table-sort-header" onclick="sortTable('d')">D</th>
                                <th class="py-4 px-3 text-center table-sort-header" onclick="sortTable('l')">L</th>
                                <th class="py-4 px-3 text-center table-sort-header" onclick="sortTable('gf')">GF</th>
                                <th class="py-4 px-3 text-center table-sort-header" onclick="sortTable('ga')">GA</th>
                                <th class="py-4 px-3 text-center table-sort-header" onclick="sortTable('gd')">GD</th>
                                <th class="py-4 px-3 text-center table-sort-header" onclick="sortTable('pts')">PTS</th>
                                <th class="py-4 px-3 text-center table-sort-header" onclick="sortTable('ppg')">PPG</th>
                                <th class="py-4 px-3 text-center table-sort-header hidden md:table-cell" onclick="sortTable('cs')">CS</th>
                                <th class="py-4 px-3 text-center table-sort-header hidden lg:table-cell" onclick="sortTable('gf_per_g')">GF/G</th>
                                <th class="py-4 px-3 text-center table-sort-header hidden lg:table-cell" onclick="sortTable('ga_per_g')">GA/G</th>
                                <th class="py-4 px-4 text-center">Form</th>
                                <th class="py-4 px-3 text-center w-8"></th>
                            </tr>
                        </thead>
                        <tbody id="tableBody" class="divide-y divide-slate-800/60 text-slate-300 font-medium"></tbody>
                    </table>
                </div>
            </div>
        </div>

        <!-- Upcoming Schedule & Opponent Strength -->
        <div class="space-y-4 pt-4 border-t border-slate-800/80">
            <div class="flex flex-col sm:flex-row sm:items-center sm:justify-between gap-3">
                <div>
                    <h2 class="text-xl font-extrabold text-white tracking-tight flex items-center gap-2">
                        <span>Upcoming Schedule & Opponent Strength</span>
                    </h2>
                    <p class="text-xs text-slate-400">Match difficulty rating and opponent scouting analytics</p>
                </div>
                
                <div class="flex items-center gap-2">
                    <label for="teamFocusSelect" class="text-xs text-slate-400 font-semibold uppercase tracking-wider">Scout Team:</label>
                    <select id="teamFocusSelect" onchange="renderUpcomingSchedule()" 
                        class="bg-slate-900 border border-slate-700 text-slate-200 text-xs font-semibold rounded-xl px-3 py-2 focus:ring-2 focus:ring-cyan-500 focus:outline-none">
                        <option value="ALL">All Conference Games</option>
                    </select>
                </div>
            </div>

            <div id="upcomingScheduleGrid" class="grid grid-cols-1 md:grid-cols-2 lg:grid-cols-3 gap-4"></div>
        </div>

        <footer class="text-center text-xs text-slate-500 py-6 border-t border-slate-800/40">
            Ranked by: PTS &rarr; PPG &rarr; Goal Differential &rarr; Goals For. Sourced from MLS NEXT TIP feed.
        </footer>
    </div>

    <script>
        const initialData = {json_standings};
        const upcomingMatches = {json_upcoming};
        let currentData = [...initialData];
        let currentSort = {{ column: 'pts', ascending: false }};
        let expandedTeam = null;

        const teamSelect = document.getElementById("teamFocusSelect");
        initialData.forEach(t => {{
            const opt = document.createElement("option");
            opt.value = t.team;
            opt.textContent = t.team;
            if (t.team === "Carolina Velocity FC") opt.selected = true;
            teamSelect.appendChild(opt);
        }});

        function renderKPIs(data) {{
            const topTeam = [...data].sort((a,b) => b.pts - a.pts)[0] || {{ team: '-', pts: 0, ppg: 0 }};
            const topOffense = [...data].sort((a,b) => b.gf - a.gf)[0] || {{ team: '-', gf: 0, gf_per_g: 0 }};
            const topDefense = [...data].sort((a,b) => a.ga - b.ga)[0] || {{ team: '-', ga: 0, ga_per_g: 0 }};
            const topCleanSheets = [...data].sort((a,b) => b.cs - a.cs)[0] || {{ team: '-', cs: 0, cs_pct: 0 }};

            const cards = [
                {{ label: "1st Place Leader", val: topTeam.team, sub: `${{topTeam.pts}} PTS &bull; ${{topTeam.ppg}} PPG` }},
                {{ label: "Top Attack", val: topOffense.team, sub: `${{topOffense.gf}} Goals (${{topOffense.gf_per_g}}/match)` }},
                {{ label: "Meanest Defense", val: topDefense.team, sub: `${{topDefense.ga}} Conceded (${{topDefense.ga_per_g}}/match)` }},
                {{ label: "Clean Sheet Leader", val: topCleanSheets.team, sub: `${{topCleanSheets.cs}} Matches (${{topCleanSheets.cs_pct}}%)` }}
            ];

            document.getElementById("statsRow").innerHTML = cards.map(c => `
                <div class="bg-slate-900/60 border border-slate-800 rounded-2xl p-4 shadow-lg backdrop-blur-sm">
                    <div class="text-[11px] font-semibold tracking-wider text-slate-400 uppercase mb-1">${{c.label}}</div>
                    <div class="text-sm font-bold text-white truncate">${{c.val}}</div>
                    <div class="text-xs text-cyan-400 font-medium mt-0.5">${{c.sub}}</div>
                </div>
            `).join('');
        }}

        function getFormPills(formArray) {{
            if (!formArray || formArray.length === 0) return '<span class="text-slate-600 text-xs">-</span>';
            return `<div class="flex items-center justify-center gap-1">` +
                formArray.map(r => {{
                    if (r === 'W') return `<span class="w-5 h-5 rounded-full flex items-center justify-center text-[10px] font-bold bg-emerald-950 text-emerald-400 border border-emerald-800">W</span>`;
                    if (r === 'D') return `<span class="w-5 h-5 rounded-full flex items-center justify-center text-[10px] font-bold bg-slate-800 text-slate-300 border border-slate-700">D</span>`;
                    return `<span class="w-5 h-5 rounded-full flex items-center justify-center text-[10px] font-bold bg-rose-950 text-rose-400 border border-rose-800">L</span>`;
                }}).join('') + `</div>`;
        }}

        function toggleExpandRow(teamName) {{
            expandedTeam = expandedTeam === teamName ? null : teamName;
            renderTable(currentData);
        }}

        function renderTable(data) {{
            const tbody = document.getElementById("tableBody");
            let html = "";

            data.forEach((t, index) => {{
                const gdClass = t.gd > 0 ? "text-emerald-400" : (t.gd < 0 ? "text-rose-400" : "text-slate-400");
                const gdFormatted = t.gd > 0 ? `+${{t.gd}}` : t.gd;
                const isExpanded = expandedTeam === t.team;
                const chevronClass = isExpanded ? "rotate-180 text-cyan-400" : "text-slate-500";

                html += `
                    <tr class="hover:bg-slate-800/40 cursor-pointer transition select-none" onclick="toggleExpandRow('${{t.team.replace(/'/g, "\\\\'")}}')">
                        <td class="py-3.5 px-3 text-center text-xs font-semibold text-slate-500">${{t.rank || index + 1}}</td>
                        <td class="py-3.5 px-4 font-bold text-white flex items-center gap-2">
                            <span>${{t.team}}</span>
                        </td>
                        <td class="py-3.5 px-3 text-center text-slate-400">${{t.gp}}</td>
                        <td class="py-3.5 px-3 text-center text-slate-300">${{t.w}}</td>
                        <td class="py-3.5 px-3 text-center text-slate-400">${{t.d}}</td>
                        <td class="py-3.5 px-3 text-center text-slate-400">${{t.l}}</td>
                        <td class="py-3.5 px-3 text-center text-slate-300">${{t.gf}}</td>
                        <td class="py-3.5 px-3 text-center text-slate-400">${{t.ga}}</td>
                        <td class="py-3.5 px-3 text-center font-bold ${{gdClass}}">${{gdFormatted}}</td>
                        <td class="py-3.5 px-3 text-center font-extrabold text-cyan-400 text-base">${{t.pts}}</td>
                        <td class="py-3.5 px-3 text-center text-slate-300">${{t.ppg.toFixed(2)}}</td>
                        <td class="py-3.5 px-3 text-center text-slate-400 hidden md:table-cell">${{t.cs}}</td>
                        <td class="py-3.5 px-3 text-center text-slate-400 hidden lg:table-cell">${{t.gf_per_g.toFixed(2)}}</td>
                        <td class="py-3.5 px-3 text-center text-slate-400 hidden lg:table-cell">${{t.ga_per_g.toFixed(2)}}</td>
                        <td class="py-3.5 px-4 text-center">${{getFormPills(t.recent_form)}}</td>
                        <td class="py-3.5 px-3 text-center">
                            <svg class="w-4 h-4 transform transition-transform duration-200 ${{chevronClass}}" fill="none" stroke="currentColor" viewBox="0 0 24 24">
                                <path stroke-linecap="round" stroke-linejoin="round" stroke-width="2" d="M19 9l-7 7-7-7" />
                            </svg>
                        </td>
                    </tr>
                `;

                if (isExpanded) {{
                    const logs = t.match_logs || [];
                    const logRows = logs.length > 0 ? logs.map(m => {{
                        const badge = m.result === 'W' 
                            ? '<span class="px-2 py-0.5 text-[10px] font-bold rounded-full bg-emerald-950 text-emerald-400 border border-emerald-800">WIN</span>'
                            : (m.result === 'D' 
                                ? '<span class="px-2 py-0.5 text-[10px] font-bold rounded-full bg-slate-800 text-slate-300 border border-slate-700">DRAW</span>'
                                : '<span class="px-2 py-0.5 text-[10px] font-bold rounded-full bg-rose-950 text-rose-400 border border-rose-800">LOSS</span>');
                        
                        const locObj = m.location || {{}};
                        const locText = locObj.display || "";
                        const mapsUrl = locObj.maps_url;

                        return `
                            <div class="flex items-center justify-between text-xs py-2 border-b border-slate-800/80 last:border-0">
                                <div class="flex items-center gap-3">
                                    <span class="text-slate-500 font-mono w-20">${{m.date}}</span>
                                    <span class="px-1.5 py-0.5 rounded text-[10px] font-semibold ${{m.role === 'Home' ? 'bg-slate-800 text-slate-300' : 'bg-slate-900 text-slate-500 border border-slate-800'}}">${{m.role}}</span>
                                    <span class="font-bold text-white">${{m.opponent}}</span>
                                    ${{mapsUrl ? `
                                        <a href="${{mapsUrl}}" target="_blank" rel="noopener noreferrer" 
                                           class="text-[11px] text-slate-500 hover:text-cyan-400 transition hidden sm:inline-flex items-center gap-1" title="${{locText}}">
                                            &bull; <span>${{locText}}</span>
                                        </a>
                                    ` : ''}}
                                </div>
                                <div class="flex items-center gap-3">
                                    <span class="font-mono font-bold text-slate-200">${{m.score}}</span>
                                    ${{badge}}
                                </div>
                            </div>
                        `;
                    }}).join('') : '<div class="text-xs text-slate-500 py-2">No completed matches recorded yet.</div>';

                    html += `
                        <tr class="bg-slate-950/70 border-b border-slate-800">
                            <td colspan="16" class="p-4 sm:p-6">
                                <div class="grid grid-cols-1 lg:grid-cols-3 gap-6">
                                    <div class="lg:col-span-2 space-y-2">
                                        <div class="text-xs font-bold text-slate-300 uppercase tracking-wider flex items-center justify-between">
                                            <span>Full Season Match Results (${{logs.length}})</span>
                                            <span class="text-[10px] text-slate-500 font-normal">Most recent first</span>
                                        </div>
                                        <div class="bg-slate-900/80 border border-slate-800 rounded-xl p-3 max-h-64 overflow-y-auto space-y-0.5">
                                            ${{logRows}}
                                        </div>
                                    </div>

                                    <div class="space-y-2">
                                        <div class="text-xs font-bold text-slate-300 uppercase tracking-wider">Performance Splits</div>
                                        <div class="bg-slate-900/80 border border-slate-800 rounded-xl p-4 space-y-4">
                                            <div>
                                                <div class="flex justify-between text-xs font-semibold text-slate-400 mb-1">
                                                    <span>Home Form</span>
                                                    <span class="text-white">${{t.home_w}}W - ${{t.home_d}}D - ${{t.home_l}}L (${{t.home_pts}} pts)</span>
                                                </div>
                                                <div class="text-[11px] text-slate-500 flex justify-between">
                                                    <span>Goals: ${{t.home_gf}} scored, ${{t.home_ga}} conceded</span>
                                                </div>
                                            </div>

                                            <div class="border-t border-slate-800 pt-3">
                                                <div class="flex justify-between text-xs font-semibold text-slate-400 mb-1">
                                                    <span>Away Form</span>
                                                    <span class="text-white">${{t.away_w}}W - ${{t.away_d}}D - ${{t.away_l}}L (${{t.away_pts}} pts)</span>
                                                </div>
                                                <div class="text-[11px] text-slate-500 flex justify-between">
                                                    <span>Goals: ${{t.away_gf}} scored, ${{t.away_ga}} conceded</span>
                                                </div>
                                            </div>

                                            <div class="border-t border-slate-800 pt-3 flex justify-between text-xs">
                                                <span class="text-slate-400">Clean Sheet Rate</span>
                                                <span class="font-bold text-cyan-400">${{t.cs_pct}}% (${{t.cs}} matches)</span>
                                            </div>
                                        </div>
                                    </div>
                                </div>
                            </td>
                        </tr>
                    `;
                }}
            }});

            tbody.innerHTML = html;
        }}

        function getOpponentStrengthBadge(rank, ppg) {{
            if (rank <= 3 || ppg >= 2.0) {{
                return '<span class="px-2 py-0.5 rounded text-[10px] font-bold bg-rose-950 text-rose-400 border border-rose-800">TOUGH (Top 3)</span>';
            }} else if (rank <= 6 || ppg >= 1.2) {{
                return '<span class="px-2 py-0.5 rounded text-[10px] font-bold bg-amber-950 text-amber-400 border border-amber-800">MODERATE (Mid-Table)</span>';
            }} else {{
                return '<span class="px-2 py-0.5 rounded text-[10px] font-bold bg-emerald-950 text-emerald-400 border border-emerald-800">FAVORABLE (Lower Half)</span>';
            }}
        }}

        function renderUpcomingSchedule() {{
            const selected = document.getElementById("teamFocusSelect").value;
            const container = document.getElementById("upcomingScheduleGrid");

            const filtered = upcomingMatches.filter(m => {{
                if (selected === "ALL") return true;
                return m.home === selected || m.away === selected;
            }});

            if (filtered.length === 0) {{
                container.innerHTML = `
                    <div class="col-span-full bg-slate-900/40 border border-slate-800 rounded-2xl p-8 text-center text-slate-500 text-sm">
                        No upcoming fixtures remaining on schedule for the selected filter.
                    </div>
                `;
                return;
            }}

            container.innerHTML = filtered.map(m => {{
                const homeStats = initialData.find(t => t.team === m.home) || {{ rank: '-', ppg: 0, form: [] }};
                const awayStats = initialData.find(t => t.team === m.away) || {{ rank: '-', ppg: 0, form: [] }};
                
                const isHome = selected === m.home;
                const oppStats = isHome ? awayStats : homeStats;
                
                const formattedDate = m.start_time 
                    ? new Date(m.start_time).toLocaleDateString(undefined, {{ weekday: 'short', month: 'short', day: 'numeric' }})
                    : "Scheduled";
                const formattedTime = m.start_time 
                    ? new Date(m.start_time).toLocaleTimeString(undefined, {{ hour: 'numeric', minute: '2-digit' }})
                    : "";

                const loc = m.location || {{}};
                const locDisplay = loc.display || "TBD";
                const mapsUrl = loc.maps_url;

                return `
                    <div class="bg-slate-900/70 border border-slate-800 hover:border-slate-700 transition rounded-2xl p-5 shadow-lg flex flex-col justify-between space-y-4">
                        <div>
                            <div class="flex items-center justify-between text-xs text-slate-400 mb-3 border-b border-slate-800 pb-2">
                                <span class="font-bold text-cyan-400">${{formattedDate}} ${{formattedTime ? '&bull; ' + formattedTime : ''}}</span>
                                ${{mapsUrl ? `
                                    <a href="${{mapsUrl}}" target="_blank" rel="noopener noreferrer" 
                                       class="inline-flex items-center gap-1.5 text-slate-400 hover:text-cyan-400 transition truncate max-w-[190px] group" 
                                       title="Open in Google Maps: ${{locDisplay}}">
                                        <svg class="w-3.5 h-3.5 flex-shrink-0 text-slate-500 group-hover:text-cyan-400 transition" fill="none" stroke="currentColor" viewBox="0 0 24 24">
                                            <path stroke-linecap="round" stroke-linejoin="round" stroke-width="2" d="M17.657 16.657L13.414 20.9a1.998 1.998 0 01-2.827 0l-4.244-4.243a8 8 0 1111.314 0z"/>
                                            <path stroke-linecap="round" stroke-linejoin="round" stroke-width="2" d="M15 11a3 3 0 11-6 0 3 3 0 016 0z"/>
                                        </svg>
                                        <span class="truncate underline decoration-dotted decoration-slate-600 underline-offset-2">${{locDisplay}}</span>
                                    </a>
                                ` : `
                                    <span class="truncate max-w-[190px] text-slate-500" title="${{locDisplay}}">${{locDisplay}}</span>
                                `}}
                            </div>

                            <div class="space-y-3">
                                <div class="flex items-center justify-between">
                                    <div class="flex items-center gap-2">
                                        <span class="text-[10px] font-bold text-slate-500 px-1.5 py-0.5 rounded bg-slate-800">H</span>
                                        <span class="font-bold ${{selected === m.home ? 'text-cyan-400 underline decoration-cyan-500/50' : 'text-white'}}">${{m.home}}</span>
                                    </div>
                                    <div class="text-right text-xs">
                                        <span class="text-slate-400 font-mono">Rank #${{homeStats.rank}}</span>
                                        <span class="text-slate-500 text-[10px] ml-1">(${{homeStats.ppg.toFixed(2)}} PPG)</span>
                                    </div>
                                </div>

                                <div class="flex items-center justify-between">
                                    <div class="flex items-center gap-2">
                                        <span class="text-[10px] font-bold text-slate-500 px-1.5 py-0.5 rounded bg-slate-800">A</span>
                                        <span class="font-bold ${{selected === m.away ? 'text-cyan-400 underline decoration-cyan-500/50' : 'text-white'}}">${{m.away}}</span>
                                    </div>
                                    <div class="text-right text-xs">
                                        <span class="text-slate-400 font-mono">Rank #${{awayStats.rank}}</span>
                                        <span class="text-slate-500 text-[10px] ml-1">(${{awayStats.ppg.toFixed(2)}} PPG)</span>
                                    </div>
                                </div>
                            </div>
                        </div>

                        ${{selected !== "ALL" ? `
                            <div class="pt-3 border-t border-slate-800/80 flex items-center justify-between text-xs">
                                <span class="text-slate-400 font-semibold">Opponent Threat:</span>
                                ${{getOpponentStrengthBadge(oppStats.rank, oppStats.ppg)}}
                            </div>
                        ` : ''}}
                    </div>
                `;
            }}).join('');
        }}

        function sortTable(key) {{
            if (currentSort.column === key) {{
                currentSort.ascending = !currentSort.ascending;
            }} else {{
                currentSort.column = key;
                currentSort.ascending = false;
            }}

            currentData.sort((a, b) => {{
                let valA = a[key];
                let valB = b[key];
                if (typeof valA === 'string') return currentSort.ascending ? valA.localeCompare(valB) : valB.localeCompare(valA);
                return currentSort.ascending ? valA - valB : valB - valA;
            }});

            renderTable(currentData);
        }}

        document.getElementById("searchInput").addEventListener("input", (e) => {{
            const term = e.target.value.toLowerCase();
            renderTable(currentData.filter(t => t.team.toLowerCase().includes(term)));
        }});

        function exportToCSV() {{
            const headers = ["Rank", "Team", "GP", "W", "D", "L", "GF", "GA", "GD", "PTS", "PPG", "CS", "CS%"];
            const rows = currentData.map((t, idx) => [
                idx + 1, `"${{t.team}}"`, t.gp, t.w, t.d, t.l, t.gf, t.ga, t.gd, t.pts, t.ppg, t.cs, `${{t.cs_pct}}%`
            ]);
            const csv = [headers.join(","), ...rows.map(r => r.join(","))].join("\\n");
            const blob = new Blob([csv], {{ type: "text/csv;charset=utf-8;" }});
            const link = document.createElement("a");
            link.href = URL.createObjectURL(blob);
            link.download = `u13_carolinas_standings.csv`;
            link.click();
        }}

        renderKPIs(currentData);
        renderTable(currentData);
        renderUpcomingSchedule();
    </script>
</body>
</html>
"""
    with open(output_filename, "w", encoding="utf-8") as f:
        f.write(html)
    print(f"Generated complete dashboard -> {Path(output_filename).resolve()}")


if __name__ == "__main__":
    completed_matches, upcoming_matches = fetch_all_matches()

    with open(MATCHES_CACHE, "w", encoding="utf-8") as f:
        json.dump({
            "completed": completed_matches,
            "upcoming": upcoming_matches,
        }, f, indent=2)

    standings = compile_standings(completed_matches)
    generate_html_dashboard(standings, upcoming_matches, OUTPUT_HTML)