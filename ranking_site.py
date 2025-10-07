import os
import re
import json
import datetime
import requests
from bs4 import BeautifulSoup
from collections import defaultdict
from flask import Flask, jsonify, request, render_template
from html import unescape

# ---------- Config ----------
BASE_DIR = os.path.dirname(os.path.abspath(__file__))
PLAYERS_FILE = os.path.join(BASE_DIR, "players.json")
NOT_RANKED = 1000
CURRENT_SEASON = datetime.datetime.now().year
NFL_SEASON_START = datetime.date(2025, 9, 2)  # manually update each season
POSITION_ORDER = ["QB", "WR", "RB", "TE", "K", "DEF"]

# ---------- Helpers ----------
def normalize_name(name):
    name = unescape(name).lower()
    name = re.sub(r"[’‘‛❛❜ʻʼʽʾʿˈˊ]", "'", name)
    name = re.sub(r'\b(jr\.?|sr\.?|ii|iii|iv|v)\b', '', name)
    name = re.sub(r"[^\w\s']", '', name)
    name = re.sub(r'\s+', ' ', name)
    return name.strip()

def get_current_nfl_week():
    delta_days = (datetime.date.today() - NFL_SEASON_START).days
    if delta_days < 0:
        return 1
    return min(delta_days // 7 + 1, 17)

# ---------- Player class ----------
class Player:
    def __init__(self, name, position):
        self.name = name
        self.position = position
        self.nfl_rank = None
        self.rotopat_rank = None
        self.fantasypros_rank = None

    def set_ranks(self, nfl_rank, rotopat_rank, fantasypros_rank=None):
        self.nfl_rank = nfl_rank
        self.rotopat_rank = rotopat_rank
        self.fantasypros_rank = fantasypros_rank

    def average_rank(self):
        ranks = [r for r in [self.nfl_rank, self.rotopat_rank, self.fantasypros_rank] if r is not None and r != NOT_RANKED]
        return sum(ranks) / len(ranks) if ranks else NOT_RANKED

    def to_dict(self):
        return {"name": self.name, "position": self.position}

# ---------- Persistence ----------
def load_players():
    if not os.path.exists(PLAYERS_FILE):
        default_players = [
            {"name": "Patrick Mahomes", "position": "QB"},
            {"name": "Brian Thomas Jr.", "position": "WR"},
            {"name": "Terry McLaurin", "position": "WR"},
            {"name": "Chris Olave", "position": "WR"},
            {"name": "Deebo Samuel Sr.", "position": "WR"},
            {"name": "Wan'Dale Robinson", "position": "WR"},
            {"name": "Darnell Mooney", "position": "WR"},
            {"name": "Bijan Robinson", "position": "RB"},
            {"name": "Tony Pollard", "position": "RB"},
            {"name": "Quinshon Judkins", "position": "RB"},
            {"name": "Rhamondre Stevenson", "position": "RB"},
            {"name": "Trey McBride", "position": "TE"},
            {"name": "Brandon Aubrey", "position": "K"},
            {"name": "Minnesota Vikings", "position": "DEF"},
            {"name": "Denver Broncos", "position": "DEF"}
        ]
        with open(PLAYERS_FILE, "w") as f:
            json.dump(default_players, f, indent=2)
    with open(PLAYERS_FILE, "r") as f:
        data = json.load(f)
    return [Player(p["name"], p["position"]) for p in data]

def save_players(player_objs):
    with open(PLAYERS_FILE, "w") as f:
        json.dump([p.to_dict() for p in player_objs], f, indent=2)

# ---------- Grouping ----------
def group_players_by_position(players):
    grouped = defaultdict(list)
    for player in players:
        grouped[player.position].append(player)
    ordered = {pos: grouped.get(pos, []) for pos in POSITION_ORDER}
    for pos, lst in grouped.items():
        if pos not in ordered:
            ordered[pos] = lst
    return ordered

# ---------- NFL.com ----------
def fetch_nfl_rankings(position, week):
    url = f"https://fantasy.nfl.com/research/rankings?leagueId=0&position={position}&statSeason={CURRENT_SEASON}&statType=weekStats&week={week}"
    try:
        resp = requests.get(url, timeout=10)
        return BeautifulSoup(resp.text, "html.parser")
    except Exception:
        return BeautifulSoup("", "html.parser")

def get_nfl_ranks(doc, player_names):
    ranks = {}
    for name in player_names:
        ranks[name] = NOT_RANKED
        try:
            matches = doc.find_all(string=lambda s: s and name.lower() in s.lower())
            node = matches[0] if matches else None
            if node:
                tr = node.find_parent("tr")
                if tr:
                    for td in tr.find_all("td"):
                        m = re.search(r'(\d+)', td.get_text(strip=True))
                        if m:
                            ranks[name] = int(m.group(1))
                            break
        except Exception:
            ranks[name] = NOT_RANKED
    return ranks

# ---------- RotoPat ----------
def fetch_rotopat_doc(position, week):
    roto_position = "te-k-def" if position in ["TE", "K", "DEF"] else position.lower()
    url = f"https://www.nbcsports.com/fantasy/football/news/{CURRENT_SEASON}-week-{week}-fantasy-football-rankings-{roto_position}"
    try:
        r = requests.get(url, timeout=10)
        return BeautifulSoup(r.content, "html.parser")
    except Exception:
        return BeautifulSoup("", "html.parser")

def get_rotopat_ranks(doc, player_names):
    ranks = {}
    for name in player_names:
        normalized_target = normalize_name(name)
        ranks[name] = NOT_RANKED
        try:
            cell = next((td for td in doc.find_all("td") if normalized_target in normalize_name(td.get_text())), None)
            row = cell.find_parent("tr") if cell else None
            if row:
                bold = row.find("b")
                if bold:
                    m = re.search(r'(\d+)', bold.get_text(strip=True))
                    if m:
                        ranks[name] = int(m.group(1))
                else:
                    for td in row.find_all("td"):
                        m = re.search(r'(\d+)', td.get_text(strip=True))
                        if m:
                            ranks[name] = int(m.group(1))
                            break
        except Exception:
            ranks[name] = NOT_RANKED
    return ranks

# ---------- FantasyPros ----------
def fetch_fantasypros_ecr(position):
    pos_map = {"QB": "qb", "RB": "rb", "WR": "wr", "TE": "te", "K": "k", "DEF": "dst"}
    pos_param = pos_map.get(position.upper())
    if not pos_param:
        return {}
    url = f"https://www.fantasypros.com/nfl/rankings/{pos_param}.php?scoring=PPR"
    try:
        r = requests.get(url, timeout=10)
        match = re.search(r'var ecrData = (\{.*?\});', r.text, re.DOTALL)
        return json.loads(match.group(1)) if match else {}
    except Exception:
        return {}

def get_fantasypros_ranks(data, player_names):
    ranks = {}
    for name in player_names:
        normalized_target = normalize_name(name)
        ranks[name] = NOT_RANKED
        for player in data.get("players", []):
            if normalized_target in normalize_name(player.get("player_name", "")):
                ranks[name] = player.get("rank_ecr", NOT_RANKED)
                break
    return ranks

# ---------- Ranking logic ----------
def get_rankings(week=None):
    week = week or get_current_nfl_week()
    players = load_players()
    grouped = group_players_by_position(players)
    all_rankings = {}

    for position, players in grouped.items():
        player_names = [p.name for p in players]
        nfl_doc = fetch_nfl_rankings(position, week)
        roto_doc = fetch_rotopat_doc(position, week)
        fp_data = fetch_fantasypros_ecr(position)

        nfl_ranks = get_nfl_ranks(nfl_doc, player_names)
        roto_ranks = get_rotopat_ranks(roto_doc, player_names)
        fp_ranks = get_fantasypros_ranks(fp_data, player_names)

        for player in players:
            player.set_ranks(
                nfl_ranks.get(player.name, NOT_RANKED),
                roto_ranks.get(player.name, NOT_RANKED),
                fp_ranks.get(player.name, NOT_RANKED)
            )

        sorted_players = sorted(players, key=lambda p: p.average_rank() if p.average_rank() != NOT_RANKED else 9999)
        all_rankings[position] = [
            {
                "name": player.name,
                "nfl_rank": None if player.nfl_rank == NOT_RANKED else player.nfl_rank,
                "rotopat_rank": None if player.rotopat_rank == NOT_RANKED else player.rotopat_rank,
                "fantasypros_rank": None if player.fantasypros_rank == NOT_RANKED else player.fantasypros_rank,
                "average_rank": None if player.average_rank() == NOT_RANKED else round(player.average_rank(), 2),
            } for player in sorted_players
        ]

    return all_rankings

# ---------- Flask App ----------
app = Flask(__name__, template_folder="templates", static_folder="static")

@app.route('/')
def home():
    week_param = request.args.get('week')
    week = int(week_param) if week_param and week_param.isdigit() and 1 <= int(week_param) <= 17 else get_current_nfl_week()
    return render_template("index.html", week=week, position_order=POSITION_ORDER, github="https://github.com/JTH10", author="Justin Henrie")

@app.route('/players', methods=['GET'])
def api_get_players():
    grouped = group_players_by_position(load_players())
    return jsonify({pos: [p.to_dict() for p in grouped[pos]] for pos in grouped})

@app.route('/players', methods=['POST'])
def api_add_player():
    data = request.get_json()
    if not data or 'name' not in data or 'position' not in data:
        return jsonify({"error": "Bad payload"}), 400
    players = load_players()
    if any(p.name.strip().lower() == data['name'].strip().lower() for p in players):
        return jsonify({"error": "Player already exists"}), 409
    players.append(Player(data['name'].strip(), data['position'].strip()))
    save_players(players)
    return jsonify({"message": "Player added"}), 201

@app.route('/players/<path:name>', methods=['DELETE'])
def api_delete_player(name):
    name_norm = name.strip().lower()
    players = load_players()
    new_players = [p for p in players if p.name.strip().lower() != name_norm]
    if len(new_players) == len(players):
        return jsonify({"error": "Player not found"}), 404
    save_players(new_players)
    return jsonify({"message": "Player deleted"}), 200

@app.route('/rankings')
def api_rankings():
    week_param = request.args.get('week')
    week = int(week_param) if week_param and week_param.isdigit() and 1 <= int(week_param) <= 17 else get_current_nfl_week()
    return jsonify(get_rankings(week))

# ---------- Entry Point ----------
if __name__ == "__main__":
    port = int(os.environ.get("PORT", 5000))
    app.run(host="0.0.0.0", port=port)
