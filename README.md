# Fantasy Football Rankings Webapp

A web application that aggregates and displays weekly fantasy football player rankings from multiple websites.

## Features

- Fetches and combines fantasy football rankings from NFL.com, RotoPat, and FantasyPros.
- Displays rankings by player position (QB, WR, RB, TE, K, DEF).
- Allows users to select the NFL week (1-17) to view rankings for that week.
- Clean, responsive interface using Flask and Jinja2 templates.
- Simple REST API endpoints for players and rankings.
- Easily extendable scraping and ranking logic.

## Installation

1. Clone the repository:

   ```bash
   git clone https://github.com/JTH10/fantasy-football-rankings-webapp.git
   cd fantasy-football-rankings-webapp
Create and activate a virtual environment:

bash
Copy code
python3 -m venv venv
source venv/bin/activate  # On Windows use `venv\Scripts\activate`
Install dependencies:

bash
Copy code
pip install -r requirements.txt
Usage
Run the Flask app locally:

bash
Copy code
python ranking_site.py
Then open your browser at http://127.0.0.1:5000 to view the webapp.

API Endpoints
GET /players — Returns all players grouped by position.

POST /players — Add a new player (JSON payload: {"name": "Player Name", "position": "Position"}).

DELETE /players/<name> — Deletes a player by name.

GET /rankings?week=<week_number> — Returns rankings for the specified week.

Technologies
Python 3

Flask (web framework)

Requests (HTTP requests)

BeautifulSoup (HTML parsing)

Jinja2 (templating)

JSON for data persistence

Future Improvements
Extract scraping logic into separate modules.

Add user authentication for managing player lists.

Improve error handling and input validation.

Deploy the app with Render or similar platforms.


Feel free to explore the code, and if you have any questions or want to contribute, please reach out!