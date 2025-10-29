"""
Fantasy Football Rankings Web App
---------------------------------

A Flask-based web application that aggregates fantasy football player rankings
from multiple public sources (NFL.com, RotoPat/NBC Sports, and FantasyPros),
combines them into an averaged ranking, and serves both an interactive frontend
and a JSON API.

Key Features:
- Fetches live weekly rankings from external sources.
- Allows users to add or remove players from a persistent roster.
- Stores player data in a database (SQLite locally, Supabase Postgres in production).
- Exposes clean JSON endpoints for integration or automation.

Tech Stack:
- Flask (backend & templating)
- SQLAlchemy (ORM / DB abstraction)
- BeautifulSoup + Requests (web scraping)
- Hosted on Render with Supabase as the managed database.

Author: JT Henrie
Repository: https://github.com/JTH10/fantasy-football-webapp
License: MIT
"""

# mypy: ignore-missing-imports

# stdlib
import datetime
import json
import os
import logging
import re
from collections import defaultdict
from html import unescape
from typing import Any, Dict, List, Optional

# third-party
import requests
from bs4 import BeautifulSoup
from flask import Flask, jsonify, render_template, request
from sqlalchemy import create_engine, text
from sqlalchemy.exc import SQLAlchemyError
from urllib.parse import urlparse, urlunparse, parse_qsl, urlencode


# ---------- Config ----------
BASE_DIR = os.path.dirname(os.path.abspath(__file__))

def _use_psycopg3_driver(url: str) -> str:
    """Force SQLAlchemy to use psycopg3 driver for PostgreSQL."""
    if not url or not url.startswith("postgresql"):
        return url
    parsed = urlparse(url)
    scheme = "postgresql+psycopg"
    return urlunparse(parsed._replace(scheme=scheme))

def _ensure_ssl_in_url(url: str) -> str:
    """Add ?sslmode=require if not present (needed for Supabase)."""
    if not url or not url.startswith("postgres"):
        return url
    parsed = urlparse(url)
    qs = dict(parse_qsl(parsed.query))
    qs.setdefault("sslmode", "require")
    new = parsed._replace(query=urlencode(qs))
    return urlunparse(new)

DB_URL = os.environ.get("DATABASE_URL")
if DB_URL:
    DB_URL = _use_psycopg3_driver(_ensure_ssl_in_url(DB_URL))
else:
    DB_URL = f"sqlite:///{os.path.join(BASE_DIR, 'players.db')}"

engine = create_engine(DB_URL, pool_pre_ping=True)

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)
logger.info("DB URL scheme in use: %s", DB_URL.split(":", 1)[0])  # expect postgresql+psycopg or sqlite



PLAYERS_TEMPLATE = os.path.join(BASE_DIR, "players.json")
NOT_RANKED = 1000
CURRENT_SEASON = datetime.datetime.now().year
NFL_SEASON_START = datetime.date(2025, 9, 2)  # manually update each season - Tuesday before 1st regular season game.
POSITION_ORDER = ["QB", "WR", "RB", "TE", "K", "DEF"]

DEFAULT_PLAYERS = [
    {"name": "Patrick Mahomes", "position": "QB"},
    {"name": "Brian Thomas Jr.", "position": "WR"},
    {"name": "Terry McLaurin", "position": "WR"},
    {"name": "Chris Olave", "position": "WR"},
    {"name": "Deebo Samuel Sr.", "position": "WR"},
    {"name": "Wan'Dale Robinson", "position": "WR"},
    {"name": "Bijan Robinson", "position": "RB"},
    {"name": "Tony Pollard", "position": "RB"},
    {"name": "Quinshon Judkins", "position": "RB"},
    {"name": "Rhamondre Stevenson", "position": "RB"},
    {"name": "Woody Marks", "position": "RB"},
    {"name": "Kyle Pitts", "position": "RB"},
    {"name": "Trey McBride", "position": "TE"},
    {"name": "Brandon Aubrey", "position": "K"},
    {"name": "Buffalo Bills", "position": "DEF"}
]
# ---------- Helpers ----------
def normalize_name(name: str) -> str:
    name = unescape(name).lower()
    name = re.sub(r"[’‘‛❛❜ʻʼʽʾʿˈˊ]", "'", name)
    name = re.sub(r'\b(jr\.?|sr\.?|ii|iii|iv|v)\b', '', name)
    name = re.sub(r"[^\w\s']", '', name)
    name = re.sub(r'\s+', ' ', name)
    return name.strip()

def get_current_nfl_week() -> int:
    delta_days = (datetime.date.today() - NFL_SEASON_START).days
    if delta_days < 0:
        return 1
    return min(delta_days // 7 + 1, 17)

# ---------- Player class ----------
class Player:
    def __init__(self, name: str, position: str) -> None:
        self.name: str = name
        self.position: str = position
        self.nfl_rank: Optional[int] = None
        self.rotopat_rank: Optional[int] = None
        self.fantasypros_rank: Optional[int] = None

    def set_ranks(
        self,
        nfl_rank: Optional[int],
        rotopat_rank: Optional[int],
        fantasypros_rank: Optional[int] = None,
    ) -> None:
        self.nfl_rank = nfl_rank
        self.rotopat_rank = rotopat_rank
        self.fantasypros_rank = fantasypros_rank

    def average_rank(self) -> float | int:
        ranks: List[int] = [
            r for r in [self.nfl_rank, self.rotopat_rank, self.fantasypros_rank]
            if r is not None and r != NOT_RANKED
        ]
        return sum(ranks) / len(ranks) if ranks else NOT_RANKED

    def to_dict(self) -> Dict[str, str]:
        return {"name": self.name, "position": self.position}

# ---------- Persistence ----------
def _ensure_template_file():
    if os.path.exists(PLAYERS_TEMPLATE):
        return
    try:
        with open(PLAYERS_TEMPLATE, "w", encoding="utf-8") as f:
            json.dump(DEFAULT_PLAYERS, f, indent=2)
    except OSError:
        pass


def _load_template_players():
    _ensure_template_file()
    try:
        with open(PLAYERS_TEMPLATE, "r", encoding="utf-8") as f:
            data = json.load(f)
            return [p for p in data if isinstance(p, dict) and "name" in p and "position" in p]
    except (OSError, json.JSONDecodeError):
        return DEFAULT_PLAYERS.copy()


_storage_initialized = False

def _init_storage():
    global _storage_initialized
    if _storage_initialized:
        return

    # Create table if it doesn't exist (works on both SQLite and Postgres)
    with engine.begin() as conn:
        conn.execute(text("""
            CREATE TABLE IF NOT EXISTS players (
                name TEXT PRIMARY KEY,
                position TEXT NOT NULL
            )
        """))

        # Seed from template once if empty
        count = conn.execute(text("SELECT COUNT(*) FROM players")).scalar() or 0
        if count == 0:
            seed_players = _load_template_players()
            if seed_players:
                conn.execute(
                    text("INSERT INTO players (name, position) VALUES (:name, :position)"),
                    seed_players
                )

    _storage_initialized = True


def load_players() -> List[Player]:
    _init_storage()
    with engine.begin() as conn:
        rows = conn.execute(
            text("SELECT name, position FROM players ORDER BY name")
        ).mappings().all()
    return [Player(row["name"], row["position"]) for row in rows]


def save_players(player_objs: List[Player]) -> None:
    _init_storage()
    serialized = [p.to_dict() for p in player_objs]

    # Simple full-rewrite is fine for a small table like this
    with engine.begin() as conn:
        conn.execute(text("DELETE FROM players"))
        if serialized:
            conn.execute(
                text("INSERT INTO players (name, position) VALUES (:name, :position)"),
                serialized
            )

    # Optional: keep JSON snapshot for readability/manual edits
    try:
        with open(PLAYERS_TEMPLATE, "w", encoding="utf-8") as f:
            json.dump(serialized, f, indent=2)
    except OSError:
        pass


# ---------- Grouping ----------
def group_players_by_position(players: List[Player]) -> Dict[str, List[Player]]:
    grouped = defaultdict(list)
    for player in players:
        grouped[player.position].append(player)
    ordered = {pos: grouped.get(pos, []) for pos in POSITION_ORDER}
    for pos, lst in grouped.items():
        if pos not in ordered:
            ordered[pos] = lst
    return ordered

# ---------- NFL.com ----------
def fetch_nfl_rankings(position: str, week: int) -> BeautifulSoup:
    url = f"https://fantasy.nfl.com/research/rankings?leagueId=0&position={position}&statSeason={CURRENT_SEASON}&statType=weekStats&week={week}"
    try:
        resp = requests.get(url, timeout=10)
        return BeautifulSoup(resp.text, "html.parser")
    except Exception:
        return BeautifulSoup("", "html.parser")

def get_nfl_ranks(doc: BeautifulSoup, player_names: List[str]) -> Dict[str, int]:
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
def fetch_rotopat_doc(position: str, week: int) -> BeautifulSoup:
    roto_position = "te-k-def" if position in ["TE", "K", "DEF"] else position.lower()
    url = f"https://www.nbcsports.com/fantasy/football/news/{CURRENT_SEASON}-week-{week}-fantasy-football-rankings-{roto_position}"
    try:
        r = requests.get(url, timeout=10)
        return BeautifulSoup(r.content, "html.parser")
    except Exception:
        return BeautifulSoup("", "html.parser")

def get_rotopat_ranks(doc: BeautifulSoup, player_names: List[str]) -> Dict[str, int]:
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
def fetch_fantasypros_ecr(position: str) -> Dict[str, Any]:
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

def get_fantasypros_ranks(data: Dict[str, Any], player_names: List[str]) -> Dict[str, int]:
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
def get_rankings(week: Optional[int] = None) -> Dict[str, List[Dict[str, Any]]]:
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
    port = int(os.environ.get("PORT", 5050))
    app.run(host="0.0.0.0", port=port)
