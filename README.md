# Pathway Analytics — Student Placement Data Analysis System

A Flask and MySQL application with three modules: Overview, Students, and Reports. Manage student skills and placement offers, see the saved records immediately, review the complete student register, and generate an individual report by registration number.

## Requirements

- Python 3.10+
- MySQL 8.0+ running locally

## Local setup

1. Create and activate a virtual environment:

   ```powershell
   py -m venv .venv
   .\.venv\Scripts\Activate.ps1
   ```

2. Install dependencies:

   ```powershell
   pip install -r requirements.txt
   ```

3. Configure the local `.env` file (copy `.env.example` if you need a starting point). It is excluded from Git. The supplied local MySQL connection values are prefilled; update them if your local server differs. Set a unique secret key for your installation.

4. Create the database and tables:

   ```powershell
   flask --app app init-db
   ```

   The command creates the database named by `MYSQL_DATABASE` and the normalized tables. It does not erase existing data.

5. Start the development server:

   ```powershell
   flask --app app run
   ```

   Open `http://127.0.0.1:5000` to view and manage placement data.

## Modules

- **Overview:** placement rate, department comparison, package statistics and charts, and skill demand.
- **Students:** Portal 1 adds students and skills; Portal 2 records company offers and outcomes. Saved student and offer entries are listed underneath the forms and can be reviewed or updated.
- **Reports:** the all-students report lists each student's registration number, department, graduation year, skills, placement status, and offer details. The individual report searches by registration number and includes the student's details and complete offer history.

Student, skill, and offer records are persisted to the MySQL tables described below.

## Data definitions

- **Eligible-student denominator:** every student row currently present in `students`. The placement-rate report counts distinct eligible students with at least one placement record whose status is `Placed`. The dashboard includes all graduation years; filter the cohort at the data/query layer if a year-specific report is required.
- **Placement status:** `Offered` means an offer is recorded but not yet finalized; `Placed` is a confirmed outcome; `Rejected` is not a placement. A student's displayed status is `Placed` if any offer is finalized as placed, otherwise `Not placed`.
- **Duplicate placement records:** an exact duplicate for the same student, company, role, and placement date is rejected by a database unique constraint. A student may still have multiple distinct offers.
- **Package units:** `package_lpa` means annual compensation in Indian lakh rupees per annum (LPA). Mean and median are calculated only for records with status `Placed`.
- **Privacy:** reports display registration numbers and placement information to support authorized administrative use. Avoid importing names, phone numbers, addresses, or other unnecessary sensitive data, and share individual reports responsibly.
- **Access:** the dashboard is open without login. Forms include CSRF tokens. Do not expose this version to the public internet; use a production WSGI server and add access control before deployment.

## Database entities

- `students(student_id, roll_no, department, graduation_year)`
- `skills(skill_id, skill_name)`
- `student_skills(student_id, skill_id)`
- `placements(placement_id, student_id, company, role, package_lpa, placement_date, status)`

## Notes

The database schema is created by `flask --app app init-db`. Use fictional or approved student data only. The built-in Flask server is for local development; the dashboard has no login or access control, so keep it on a trusted local network unless access control is added before deployment.
