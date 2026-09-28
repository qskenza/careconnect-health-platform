# CareConnect — Campus Health Platform

A full-stack healthcare management platform for a university health center. Students, doctors, and nurses each get their own dashboard for appointments, medical records, prescriptions, and emergency requests, plus an AI chatbot powered by Google Gemini.

Built for the Software Engineering course at Al Akhawayn University.

## Features

**Students**
- Register, log in, and manage their profile and emergency contact
- Book, reschedule, and cancel appointments from doctors' real availability
- View medical records, visit history, prescriptions, and referrals
- Send emergency requests to the nursing staff
- Ask the AI chatbot health and booking questions

**Doctors**
- Manage their profile, professional experience, and weekly availability
- View their schedule and patient list
- Add medical records, issue prescriptions, and create referrals

**Nurses**
- See today's patients and upcoming appointments
- Handle and resolve emergency requests
- View health-center statistics

## Tech stack

| Layer | Technologies |
|---|---|
| Backend | Python, FastAPI, SQLAlchemy, Pydantic |
| Database | SQLite (PostgreSQL-ready through `DATABASE_URL`) |
| Auth | JWT tokens, bcrypt password hashing, role-based access |
| AI | Google Gemini API |
| Frontend | HTML, CSS, vanilla JavaScript |

## Architecture

```
frontend/  (HTML + JS pages)  ──HTTP/JSON──▶  backend/  (FastAPI REST API, ~50 endpoints)
                                                 ├── SQLAlchemy models (12 tables)
                                                 ├── JWT auth + role checks
                                                 └── Gemini chatbot
```

## Getting started

**1. Start the backend**

```bash
cd backend
python -m venv venv
source venv/bin/activate        # Windows: venv\Scripts\activate
pip install -r requirements.txt
cp .env.example .env            # then add your own SECRET_KEY and GOOGLE_API_KEY
python main.py
```

The API runs at http://127.0.0.1:8000, and interactive docs are at http://127.0.0.1:8000/docs. The database is created automatically on first start.

**2. Start the frontend**

In a second terminal:

```bash
cd frontend
python -m http.server 5500
```

Open http://localhost:5500/Login.html and register a student, doctor, or nurse account.

## Team

Built by a team of five students for the Software Engineering course at Al Akhawayn University.

- **Kenza Qribis**: led the project and reviewed and refined every part of the system. Co-developed the FastAPI backend and the Gemini chatbot, co-developed the frontend, and contributed to the paper and presentation.
- **Aya Ben Hammadi**: backend development
- **Ali El Hardouz**: backend development
- **Meriem Laghafi**: frontend development
- **Zaineb Lamghari**: project paper and presentation

## Notes

This is a course project configured for local development. Before any real deployment, CORS should be restricted to the frontend's domain, a strong `SECRET_KEY` must be set, and the database should move to PostgreSQL.
