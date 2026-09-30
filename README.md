# CareConnect — Campus Health Platform

A full-stack healthcare management platform for a university health center. Students, doctors, nurses and administrators each get their own portal, connected through one shared backend: a booking made by a student appears in the doctor's schedule, and a prescription written by the doctor appears on the student's dashboard. Includes an AI chatbot powered by Google Gemini.

Built for the Software Engineering course at Al Akhawayn University.

## Features

**Students**
- Register, log in, and manage their profile and emergency contact
- Book, reschedule, and cancel appointments from doctors' real availability (past dates, passed time slots and already-booked slots are blocked)
- Add and remove allergies, medications and conditions
- View visit history, including upcoming appointments and completed visits
- See prescriptions and referrals issued by their doctors (read-only)
- Share their location from the Emergency page, which alerts the nursing staff
- Ask the AI chatbot health and booking questions

**Doctors**
- Manage their profile, professional experience, and weekly availability
- See all upcoming appointments booked by students, with the reason for the visit
- Open a patient's records: allergies, medications, conditions, prescriptions and recent visits
- Mark an appointment as completed with a diagnosis, which adds it to the student's visit history
- Issue prescriptions, create and track referrals, and add medical records

**Nurses**
- See today's patients and upcoming appointments
- Receive and resolve emergency requests sent by students
- View health-center statistics

**Administrators**
- Dashboard with key statistics (users, appointments, active emergencies)
- Search and filter all users by role
- Create student, doctor and nurse accounts
- Deactivate or reactivate accounts (deactivated users cannot log in, and deactivated doctors are hidden from booking)
- View all appointments and cancel them if needed

## Demo accounts

These accounts are created automatically the first time the backend starts:

| Role | Username | Password |
|---|---|---|
| Student | `alexandra` | `password123` |
| Doctor | `sarah.chen` | `doctor123` |
| Doctor | `emily.carter` | `doctor123` |
| Doctor | `elena.rodriguez` | `doctor123` |
| Nurse | `nurse.amina` | `nurse123` |
| Admin | `admin` | `admin123` |

To see the portals working together, log in as `alexandra` and book an appointment with Dr. Sarah Chen, then log in as `sarah.chen` to see the booking, write a prescription and complete the visit, and log back in as `alexandra` to see the result.

## Tech stack

| Layer | Technologies |
|---|---|
| Backend | Python, FastAPI, SQLAlchemy, Pydantic |
| Database | SQLite (PostgreSQL-ready through `DATABASE_URL`) |
| Auth | JWT tokens, bcrypt password hashing, role-based access for 4 roles |
| AI | Google Gemini API |
| Frontend | HTML, CSS, vanilla JavaScript |

## Architecture

```
frontend/  (HTML + JS pages)  ──HTTP/JSON──▶  backend/  (FastAPI REST API, 61 endpoints)
                                                 ├── SQLAlchemy models (12 tables)
                                                 ├── JWT auth + role checks (student, doctor, nurse, admin)
                                                 ├── Booking rules (no past slots, no double booking)
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

The API runs at http://127.0.0.1:8000, and interactive docs are at http://127.0.0.1:8000/docs. The database and the demo accounts are created automatically on first start. To reset everything, stop the server and delete `backend/careconnect.db`.

**2. Start the frontend**

In a second terminal:

```bash
cd frontend
python -m http.server 5500
```

Open http://localhost:5500/Login.html and log in with one of the demo accounts, or register a new one.

## Team

Built by a team of five students for the Software Engineering course at Al Akhawayn University.

- **Kenza Qribis**: led the project and reviewed and refined every part of the system. Co-developed the FastAPI backend and the Gemini chatbot, co-developed the frontend, and contributed to the paper and presentation. After the course, added the admin portal, connected the doctor, student and nurse portals, and fixed booking and date-handling bugs.
- **Aya Ben Hammadi**: backend development
- **Ali El Hardouz**: backend development
- **Meriem Laghafi**: frontend development
- **Zaineb Lamghari**: project paper and presentation

## Notes

This is a course project configured for local development. The demo passwords are for testing only. Before any real deployment, CORS should be restricted to the frontend's domain, a strong `SECRET_KEY` must be set, the demo accounts removed, and the database moved to PostgreSQL.
