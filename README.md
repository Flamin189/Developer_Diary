# Developer Diary

Developer Diary is a personal developer productivity application for recording daily notes, tasks and development progress.

## Features

- User registration and login
- Google Sign-In
- Daily developer notes
- Developer categories
- Tags
- Pin notes
- Archive notes
- Note history
- Daily task list
- Task completion tracking
- Dashboard
- Note search
- Calendar
- File attachments
- Admin user management

## Architecture

Frontend:
HTML + CSS + JavaScript

Backend:
FastAPI + Python

Database:
PostgreSQL

Authentication:
Email/password + Google OAuth

## Project Structure

developer-diary/
├── frontend/
│   └── index.html
├── backend/
│   ├── app.py
│   └── requirements.txt
├── .gitignore
└── README.md

## Local Backend

cd backend

python -m venv .venv

Windows PowerShell:

.\.venv\Scripts\Activate.ps1

Install dependencies:

pip install -r requirements.txt

Run:

python -m uvicorn app:app --reload

Backend:

http://127.0.0.1:8000

API documentation:

http://127.0.0.1:8000/docs

## Environment Variables

The backend requires:

DATABASE_URL
SECRET_KEY
GOOGLE_CLIENT_ID
GOOGLE_CLIENT_SECRET
FRONTEND_URL

Never commit the .env file.

## Deployment

Frontend:
Cloudflare Pages

Backend:
Render

Database:
Supabase PostgreSQL