# 🏈 Fantasy Football Rankings Web App

A Flask-based web application that aggregates fantasy football player rankings from multiple public sources — **NFL.com**, **RotoPat (NBC Sports)**, and **FantasyPros** — combines them into a single averaged ranking, and serves both an interactive frontend and a JSON API.

---

## 🚀 Features

- Fetches and combines weekly fantasy football rankings from multiple sources  
- Displays rankings by player position (**QB**, **WR**, **RB**, **TE**, **K**, **DEF**)  
- Allows users to select any NFL week (1–17)  
- Supports adding or removing custom players from a persistent roster  
- Stores data in a database — **SQLite locally** and **Supabase (PostgreSQL)** in production  
- Provides clean REST API endpoints for integration or automation  
- Responsive frontend built with **Flask + Jinja2** templates  

---

## 🧰 Tech Stack

- **Python 3.12+**
- **Flask** – web framework  
- **SQLAlchemy** – database abstraction (SQLite / Supabase Postgres)  
- **Requests** + **BeautifulSoup** – web scraping  
- **Gunicorn** – production WSGI server  
- **Render** – hosting platform  
- **Supabase** – managed Postgres database  

---

## ⚙️ Installation (Local Development)

1. **Clone the repository**

    ~~~bash
    git clone https://github.com/JTH10/fantasy-football-webapp.git
    cd fantasy-football-webapp
    ~~~

2. **Create and activate a virtual environment**

    ~~~bash
    python3 -m venv venv
    source venv/bin/activate        # On Windows: venv\Scripts\activate
    ~~~

3. **Install dependencies**

    ~~~bash
    pip install -r requirements.txt
    ~~~

4. **Run the Flask app locally**

    ~~~bash
    python ranking_site.py
    ~~~

    Then open your browser at **http://127.0.0.1:5050** (or the port shown in your terminal).

---

## 🧪 Static Analysis & Type Checking

Use **ruff** and **mypy** to keep code quality high:

~~~bash
pip install ruff mypy
ruff check .
mypy ranking_site.py
~~~

---

## 🌐 Deployment

Deploy on **Render** using **Supabase** for persistence:

- Add your Supabase database and copy its connection URI  
- In Render → *Environment Variables* → add:

    ~~~bash
    DATABASE_URL=postgresql://postgres:<your_password>@db.<your_project>.supabase.co:5432/postgres
    ~~~

- Set the **Start Command**:

    ~~~bash
    gunicorn ranking_site:app
    ~~~

- Deploy via **Manual Deploy → Deploy latest commit**

---

## 📡 API Endpoints

| Method | Endpoint | Description |
|-------:|----------|-------------|
| **GET** | `/players` | Returns all players grouped by position |
| **POST** | `/players` | Adds a new player (`{"name": "Player Name", "position": "Position"}`) |
| **DELETE** | `/players/<name>` | Deletes a player by name |
| **GET** | `/rankings?week=<week_number>` | Returns averaged rankings for the specified week |

---

## ☁️ Render Deployment Guide (with Supabase Persistence)

1. **Push your latest code to GitHub**  
   Ensure `requirements.txt` and `ranking_site.py` are up to date.

2. **Create a Web Service in Render**
   - Render → **New + → Web Service**
   - Connect your GitHub repo
   - Environment: **Python 3**
   - Start Command:

        ~~~bash
        gunicorn ranking_site:app
        ~~~

3. **Add your Supabase connection**
   - Supabase → *Project Settings → Database → Connection string (URI)*  
     Example:

        ~~~
        postgresql://postgres:YOUR_PASSWORD@db.your-project.supabase.co:5432/postgres
        ~~~

   - Render → *Environment* → Add:

        ~~~
        Key: DATABASE_URL
        Value: postgresql://postgres:YOUR_PASSWORD@db.your-project.supabase.co:5432/postgres
        ~~~

4. **Deploy**
   - Click **Manual Deploy → Deploy latest commit**
   - Watch logs for successful startup of `gunicorn ranking_site:app`

5. **Verify persistence**
   - Add/remove a player → Redeploy → Confirm roster persists via Supabase 🎉

---

## 💻 Local Development Options

- If `DATABASE_URL` is **not** set locally, the app automatically falls back to **SQLite** (`players.db`) via SQLAlchemy.  
- To test against Supabase locally, create a `.env` file:

    ~~~bash
    DATABASE_URL=postgresql://postgres:YOUR_PASSWORD@db.your-project.supabase.co:5432/postgres
    PORT=5050
    ~~~

  Then (optional) load it in code:

    ~~~python
    from dotenv import load_dotenv
    load_dotenv()
    ~~~

---

## 💡 Future Improvements

- Modularize scraping logic into standalone modules  
- Add user authentication for personalized rosters  
- Implement caching for faster repeated lookups  
- Add automated testing and CI/CD workflow (GitHub Actions)  
- Enhance frontend visualization of rankings  

---

## 👨‍💻 Author

**Justin Henrie**  
GitHub: [https://github.com/JTH10](https://github.com/JTH10)

---

## 🪪 License

Released under the **MIT License** © 2025 Justin Henrie.  
See [LICENSE](LICENSE) for details.
