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
from logging_setup import setup_logging

# third-party
import requests
from bs4 import BeautifulSoup
from flask import Flask, jsonify, render_template, request
from sqlalchemy import create_engine, text
from sqlalchemy.exc import SQLAlchemyError
from urllib.parse import urlparse, urlunparse, parse_qsl, urlencode


# ---------- Config ----------
# Initialize logging before anything else
setup_logging()

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
    {"name": "Kyle Pitts", "position": "TE"},
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
        logger.info("created new template file at %s", PLAYERS_TEMPLATE)
    except OSError:
        logger.exception("failed to write JSON snapshot to %s", PLAYERS_TEMPLATE)


def _load_template_players():
    _ensure_template_file()
    try:
        with open(PLAYERS_TEMPLATE, "r", encoding="utf-8") as f:
            data = json.load(f)
        logger.info("loaded %d template players from %s", len(data), PLAYERS_TEMPLATE)
        return data
    except FileNotFoundError:
        logger.exception("template players file not found at %s", PLAYERS_TEMPLATE)
        return []
    except json.JSONDecodeError:
        logger.exception("template players file is not valid JSON: %s", PLAYERS_TEMPLATE)
        return []
    except Exception:
        logger.exception("unexpected error loading template players from %s", PLAYERS_TEMPLATE)
        return []


_storage_initialized = False
def _init_storage():
    """Ensure the players table exists and seed it once, with real logging."""
    global _storage_initialized
    if _storage_initialized:
        return

    logger.info("storage_init_start")

    try:
        # Create table if it doesn't exist (works on both SQLite and Postgres)
        with engine.begin() as conn:
            conn.execute(text("""
                CREATE TABLE IF NOT EXISTS players (
                    name TEXT PRIMARY KEY,
                    position TEXT NOT NULL
                )
            """))

            # Optional: case-insensitive uniqueness
            conn.execute(
                text("CREATE UNIQUE INDEX IF NOT EXISTS players_name_lower_idx ON players (lower(name))")
            )

            # Seed from template once if empty, with logging so we can see what's happening on Render
            count = conn.execute(text("SELECT COUNT(*) FROM players")).scalar() or 0
            logger.info("players table row count BEFORE seed: %s", count)

            if count == 0:
                seed_players = _load_template_players()
                logger.info("seeding %d players from template/defaults", len(seed_players))
                if seed_players:
                    conn.execute(
                        text("INSERT INTO players (name, position) VALUES (:name, :position)"),
                        seed_players,
                    )
                count_after = conn.execute(text("SELECT COUNT(*) FROM players")).scalar() or 0
                logger.info("players table row count AFTER seed: %s", count_after)
            else:
                logger.info("seeding skipped: players table already has data")

        logger.info("storage_init_success")

    except Exception:
        # No more silent fail
        logger.exception("storage_init_failed")
        # optional: raise to fail fast
        # raise

    _storage_initialized = True



def load_players() -> List[Player]:
    _init_storage()
    with engine.begin() as conn:
        rows = conn.execute(
            text("SELECT name, position FROM players ORDER BY name")
        ).mappings().all()
    logger.info("load_players returned %d rows", len(rows))
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
        logger.exception("failed to write JSON snapshot to %s", PLAYERS_TEMPLATE)


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

# Ensure storage is initialized as early as possible (and log outcome)
try:
    _init_storage()
    logger.info("Storage initialized at startup")
except Exception:
    logger.exception("Storage init at startup failed")

@app.route('/')
def home():
    week_param = request.args.get('week')
    week = int(week_param) if week_param and week_param.isdigit() and 1 <= int(week_param) <= 17 else get_current_nfl_week()
    return render_template("index.html", week=week, position_order=POSITION_ORDER, github="https://github.com/JTH10", author="Justin Henrie")

@app.route('/players', methods=['GET'])
def api_get_players():
    try:
        grouped = group_players_by_position(load_players())
        return jsonify({pos: [p.to_dict() for p in grouped[pos]] for pos in grouped})
    except Exception:
        logger.exception("GET /players failed")
        return jsonify({"error": "internal error"}), 500

@app.route('/players', methods=['POST'])
def api_add_player():
    # 1) parse/validate
    try:
        data = request.get_json(force=True)
    except Exception:
        logger.exception("POST /players invalid JSON")
        return jsonify({"error": "Bad payload"}), 400

    if not data or 'name' not in data or 'position' not in data:
        return jsonify({"error": "Bad payload"}), 400

    name = data['name'].strip()
    position = data['position'].strip()

    try:
        players = load_players()
    except Exception:
        logger.exception("POST /players failed to load existing players")
        return jsonify({"error": "internal error"}), 500

    # 2) uniqueness check
    if any(p.name.strip().lower() == name.lower() for p in players):
        return jsonify({"error": "Player already exists"}), 409

    # 3) save
    try:
        players.append(Player(name, position))
        save_players(players)
        logger.info("POST /players added player=%s position=%s", name, position)
        return jsonify({"message": "Player added"}), 201
    except Exception:
        logger.exception("POST /players failed to save player=%s", name)
        return jsonify({"error": "internal error"}), 500

@app.route('/players/<path:name>', methods=['DELETE'])
def api_delete_player(name):
    name_norm = name.strip().lower()
    try:
        players = load_players()
    except Exception:
        logger.exception("DELETE /players/%s failed to load players", name)
        return jsonify({"error": "internal error"}), 500

    new_players = [p for p in players if p.name.strip().lower() != name_norm]

    if len(new_players) == len(players):
        return jsonify({"error": "Player not found"}), 404

    try:
        save_players(new_players)
        logger.info("DELETE /players removed player=%s", name_norm)
        return jsonify({"message": "Player deleted"}), 200
    except Exception:
        logger.exception("DELETE /players/%s failed to save updated list", name_norm)
        return jsonify({"error": "internal error"}), 500


@app.route('/rankings')
def api_rankings():
    try:
        week_param = request.args.get('week')
        week = int(week_param) if week_param and week_param.isdigit() and 1 <= int(week_param) <= 17 else get_current_nfl_week()
        return jsonify(get_rankings(week))
    except Exception:
        logger.exception("GET /rankings failed")
        return jsonify({"error": "internal error"}), 500


# ---------- Entry Point ----------
if __name__ == "__main__":
    port = int(os.environ.get("PORT", 5050))
    app.run(host="0.0.0.0", port=port)
