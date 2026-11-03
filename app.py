import io
import os
import re
import secrets
from datetime import date
from decimal import Decimal, InvalidOperation
from functools import wraps

import matplotlib

matplotlib.use("Agg")
import matplotlib.pyplot as plt
import mysql.connector
import numpy as np
import pandas as pd
from dotenv import load_dotenv
from flask import Flask, abort, flash, redirect, render_template, request, session, url_for
from mysql.connector import IntegrityError, errorcode

load_dotenv()

app = Flask(__name__)
app.config["SECRET_KEY"] = os.getenv("FLASK_SECRET_KEY")
if not app.config["SECRET_KEY"]:
    raise RuntimeError("Set FLASK_SECRET_KEY in the local .env file.")
app.config["SESSION_COOKIE_HTTPONLY"] = True
app.config["SESSION_COOKIE_SAMESITE"] = "Lax"
app.config["SESSION_COOKIE_SECURE"] = os.getenv("SESSION_COOKIE_SECURE", "false").lower() == "true"

DB_CONFIG = {
    "host": os.getenv("MYSQL_HOST", "127.0.0.1"),
    "port": int(os.getenv("MYSQL_PORT", "3306")),
    "user": os.getenv("MYSQL_USER", "root"),
    "password": os.getenv("MYSQL_PASSWORD", ""),
    "database": os.getenv("MYSQL_DATABASE", "student_placement"),
}
if not re.fullmatch(r"[A-Za-z0-9_]+", DB_CONFIG["database"]):
    raise ValueError("MYSQL_DATABASE may contain only letters, numbers, and underscores.")
PLACEMENT_STATUSES = ("Offered", "Placed", "Rejected")


def get_connection(include_database=True):
    config = DB_CONFIG.copy()
    if not include_database:
        config.pop("database")
    return mysql.connector.connect(**config)


@app.before_request
def require_admin_and_csrf():
    if request.method == "POST":
        expected = session.get("_csrf_token", "")
        supplied = request.form.get("_csrf_token", "")
        if not expected or not secrets.compare_digest(expected, supplied):
            abort(400, description="Invalid form security token.")


@app.context_processor
def inject_csrf_token():
    def csrf_token():
        if "_csrf_token" not in session:
            session["_csrf_token"] = secrets.token_urlsafe(32)
        return session["_csrf_token"]

    return {"csrf_token": csrf_token}


def database_ready(view):
    @wraps(view)
    def wrapped(*args, **kwargs):
        try:
            return view(*args, **kwargs)
        except mysql.connector.Error as exc:
            app.logger.exception("Database request failed")
            flash(
                "Could not connect to the placement database. Check your .env settings "
                "and run the database setup command.",
                "error",
            )
            return render_template("error.html"), 503

    return wrapped


def init_database():
    connection = get_connection(include_database=False)
    cursor = connection.cursor()
    database_name = DB_CONFIG["database"]
    cursor.execute(
        f"CREATE DATABASE IF NOT EXISTS `{database_name}` "
        "CHARACTER SET utf8mb4 COLLATE utf8mb4_unicode_ci"
    )
    cursor.close()
    connection.close()

    connection = get_connection()
    cursor = connection.cursor()
    cursor.execute(
        """
        CREATE TABLE IF NOT EXISTS students (
            student_id INT AUTO_INCREMENT PRIMARY KEY,
            roll_no VARCHAR(40) NOT NULL UNIQUE,
            department VARCHAR(100) NOT NULL,
            graduation_year SMALLINT UNSIGNED NOT NULL,
            created_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP
        ) ENGINE=InnoDB
        """
    )
    cursor.execute(
        """
        CREATE TABLE IF NOT EXISTS skills (
            skill_id INT AUTO_INCREMENT PRIMARY KEY,
            skill_name VARCHAR(100) NOT NULL UNIQUE
        ) ENGINE=InnoDB
        """
    )
    cursor.execute(
        """
        CREATE TABLE IF NOT EXISTS student_skills (
            student_id INT NOT NULL,
            skill_id INT NOT NULL,
            PRIMARY KEY (student_id, skill_id),
            CONSTRAINT fk_student_skills_student FOREIGN KEY (student_id)
                REFERENCES students(student_id) ON DELETE CASCADE,
            CONSTRAINT fk_student_skills_skill FOREIGN KEY (skill_id)
                REFERENCES skills(skill_id) ON DELETE CASCADE
        ) ENGINE=InnoDB
        """
    )
    cursor.execute(
        """
        CREATE TABLE IF NOT EXISTS placements (
            placement_id INT AUTO_INCREMENT PRIMARY KEY,
            student_id INT NOT NULL,
            company VARCHAR(150) NOT NULL,
            role VARCHAR(150) NOT NULL,
            package_lpa DECIMAL(10, 2) NOT NULL,
            placement_date DATE NOT NULL,
            status ENUM('Offered', 'Placed', 'Rejected') NOT NULL DEFAULT 'Offered',
            CONSTRAINT fk_placements_student FOREIGN KEY (student_id)
                REFERENCES students(student_id) ON DELETE CASCADE,
            CONSTRAINT uq_duplicate_placement UNIQUE
                (student_id, company, role, placement_date)
        ) ENGINE=InnoDB
        """
    )
    connection.commit()
    cursor.close()
    connection.close()


def parse_skills(raw_skills):
    return sorted({skill.strip() for skill in raw_skills.split(",") if skill.strip()}, key=str.casefold)


def add_student_skills(cursor, student_id, skills):
    for skill in skills:
        cursor.execute(
            "INSERT INTO skills (skill_name) VALUES (%s) "
            "ON DUPLICATE KEY UPDATE skill_id = LAST_INSERT_ID(skill_id)",
            (skill,),
        )
        cursor.execute(
            "INSERT IGNORE INTO student_skills (student_id, skill_id) VALUES (%s, LAST_INSERT_ID())",
            (student_id,),
        )


def fetch_dashboard_data():
    connection = get_connection()
    cursor = connection.cursor(dictionary=True)
    cursor.execute(
        """
        SELECT s.student_id, s.roll_no, s.department, s.graduation_year,
               CASE WHEN EXISTS (
                    SELECT 1 FROM placements p
                    WHERE p.student_id = s.student_id AND p.status = 'Placed'
               ) THEN 'Placed' ELSE 'Not placed' END AS placement_status
        FROM students s
        ORDER BY s.graduation_year DESC, s.department, s.roll_no
        """
    )
    students = cursor.fetchall()
    cursor.execute(
        """
        SELECT ss.student_id, sk.skill_name
        FROM student_skills ss JOIN skills sk ON sk.skill_id = ss.skill_id
        ORDER BY sk.skill_name
        """
    )
    student_skill_rows = cursor.fetchall()
    cursor.execute(
        """
        SELECT p.placement_id, p.student_id, s.roll_no, s.department, p.company, p.role,
               p.package_lpa, p.placement_date, p.status
        FROM placements p JOIN students s ON s.student_id = p.student_id
        ORDER BY p.placement_date DESC, p.placement_id DESC
        """
    )
    placements = cursor.fetchall()
    cursor.execute("SELECT DISTINCT student_id FROM placements WHERE status = 'Placed'")
    placed_ids = {row["student_id"] for row in cursor.fetchall()}
    cursor.execute(
        "SELECT package_lpa FROM placements WHERE status = 'Placed' ORDER BY placement_id"
    )
    package_values = [float(row["package_lpa"]) for row in cursor.fetchall()]
    cursor.close()
    connection.close()

    skill_map = {}
    student_map = {}
    for row in student_skill_rows:
        skill_map.setdefault(row["student_id"], []).append(row["skill_name"])
    for student in students:
        student["skills"] = ", ".join(skill_map.get(student["student_id"], []))
        student["offers"] = []
        student_map[student["student_id"]] = student
    for placement in placements:
        student = student_map[placement["student_id"]]
        student["offers"].append(placement)
    for student in students:
        student["offer_count"] = len(student["offers"])
        student["offers_summary"] = "; ".join(
            f"{offer['company']} — {offer['role']} "
            f"({float(offer['package_lpa']):.2f} LPA, {offer['status']})"
            for offer in student["offers"]
        )

    student_df = pd.DataFrame(students)
    eligible_count = len(student_df)
    placed_count = len(placed_ids)
    placement_rate = (placed_count / eligible_count * 100) if eligible_count else 0

    package_summary = {
        "mean": float(np.mean(package_values)) if package_values else None,
        "median": float(np.median(package_values)) if package_values else None,
        "count": len(package_values),
    }

    if student_df.empty:
        departments = []
        skills = []
    else:
        by_department = (
            student_df.assign(is_placed=student_df["placement_status"].eq("Placed"))
            .groupby("department", as_index=False)
            .agg(eligible=("student_id", "count"), placed=("is_placed", "sum"))
        )
        by_department["rate"] = (
            by_department["placed"] / by_department["eligible"] * 100
        ).round(1)
        departments = by_department.sort_values("department").to_dict("records")
        skill_rows = [row["skill_name"] for row in student_skill_rows]
        if skill_rows:
            skills = (
                pd.Series(skill_rows, name="skill")
                .value_counts()
                .rename_axis("skill")
                .reset_index(name="students")
                .to_dict("records")
            )
        else:
            skills = []

    return {
        "students": students,
        "placements": placements,
        "departments": departments,
        "placement_rate": round(placement_rate, 1),
        "eligible_count": eligible_count,
        "placed_count": placed_count,
        "package_summary": package_summary,
        "package_values": package_values,
        "skills": skills[:10],
    }


@app.route("/")
@database_ready
def index():
    data = fetch_dashboard_data()
    return render_template("index.html", **data)


@app.get("/students")
@database_ready
def students_page():
    data = fetch_dashboard_data()
    return render_template("students.html", **data)


def fetch_student_report(roll_no):
    connection = get_connection()
    cursor = connection.cursor(dictionary=True)
    cursor.execute(
        """
        SELECT student_id, roll_no, department, graduation_year
        FROM students
        WHERE roll_no = %s
        """,
        (roll_no,),
    )
    student = cursor.fetchone()
    if student is None:
        cursor.close()
        connection.close()
        return None

    cursor.execute(
        """
        SELECT sk.skill_name
        FROM student_skills ss JOIN skills sk ON sk.skill_id = ss.skill_id
        WHERE ss.student_id = %s
        ORDER BY sk.skill_name
        """,
        (student["student_id"],),
    )
    student["skills"] = [row["skill_name"] for row in cursor.fetchall()]
    cursor.execute(
        """
        SELECT placement_id, company, role, package_lpa, placement_date, status
        FROM placements
        WHERE student_id = %s
        ORDER BY placement_date DESC, placement_id DESC
        """,
        (student["student_id"],),
    )
    student["placements"] = cursor.fetchall()
    student["placement_status"] = (
        "Placed"
        if any(placement["status"] == "Placed" for placement in student["placements"])
        else "Not placed"
    )
    cursor.close()
    connection.close()
    return student


@app.get("/reports")
@database_ready
def reports_page():
    data = fetch_dashboard_data()
    roll_no = request.args.get("roll_no", "").strip()
    student_report = fetch_student_report(roll_no) if roll_no else None
    return render_template(
        "reports.html",
        **data,
        searched_roll_no=roll_no,
        student_report=student_report,
    )


@app.post("/students")
def create_student():
    roll_no = request.form.get("roll_no", "").strip()
    department = request.form.get("department", "").strip()
    graduation_year = request.form.get("graduation_year", "").strip()
    skills = parse_skills(request.form.get("skills", ""))
    try:
        year = int(graduation_year)
        if (
            not roll_no
            or len(roll_no) > 40
            or not department
            or len(department) > 100
            or any(len(skill) > 100 for skill in skills)
            or year < 2000
            or year > 2200
        ):
            raise ValueError
    except ValueError:
        flash("Enter a roll number, department, and valid graduation year.", "error")
        return redirect(url_for("students_page"))

    connection = get_connection()
    cursor = connection.cursor()
    try:
        cursor.execute(
            "INSERT INTO students (roll_no, department, graduation_year) VALUES (%s, %s, %s)",
            (roll_no, department, year),
        )
        student_id = cursor.lastrowid
        add_student_skills(cursor, student_id, skills)
        connection.commit()
        flash("Student added successfully.", "success")
    except IntegrityError as exc:
        connection.rollback()
        if exc.errno == errorcode.ER_DUP_ENTRY:
            flash("That roll number is already in the system.", "error")
        else:
            raise
    finally:
        cursor.close()
        connection.close()
    return redirect(url_for("students_page"))


@app.post("/students/<int:student_id>/skills")
def update_student_skills(student_id):
    skills = parse_skills(request.form.get("skills", ""))
    if any(len(skill) > 100 for skill in skills):
        flash("Each skill must be 100 characters or fewer.", "error")
        return redirect(url_for("students_page"))
    connection = get_connection()
    cursor = connection.cursor()
    cursor.execute("SELECT student_id FROM students WHERE student_id = %s", (student_id,))
    if cursor.fetchone() is None:
        cursor.close()
        connection.close()
        flash("Student not found.", "error")
        return redirect(url_for("students_page"))
    cursor.execute("DELETE FROM student_skills WHERE student_id = %s", (student_id,))
    add_student_skills(cursor, student_id, skills)
    connection.commit()
    cursor.close()
    connection.close()
    flash("Student skills updated.", "success")
    return redirect(url_for("students_page"))


@app.post("/placements")
def create_placement():
    student_id = request.form.get("student_id", "").strip()
    company = request.form.get("company", "").strip()
    role = request.form.get("role", "").strip()
    package_text = request.form.get("package_lpa", "").strip()
    placement_date = request.form.get("placement_date", "").strip()
    status = request.form.get("status", "")
    try:
        student_id_value = int(student_id)
        package = Decimal(package_text)
        placement_day = date.fromisoformat(placement_date)
        if (
            not company
            or len(company) > 150
            or not role
            or len(role) > 150
            or not package.is_finite()
            or package < 0
            or package > Decimal("99999999.99")
            or package.quantize(Decimal("0.01")) != package
            or status not in PLACEMENT_STATUSES
        ):
            raise ValueError
    except (ValueError, InvalidOperation):
        flash("Complete every placement field with valid values.", "error")
        return redirect(url_for("students_page"))

    connection = get_connection()
    cursor = connection.cursor()
    try:
        cursor.execute(
            """
            INSERT INTO placements
                (student_id, company, role, package_lpa, placement_date, status)
            VALUES (%s, %s, %s, %s, %s, %s)
            """,
            (student_id_value, company, role, package, placement_day, status),
        )
        connection.commit()
        flash("Placement record added.", "success")
    except IntegrityError as exc:
        connection.rollback()
        if exc.errno == errorcode.ER_DUP_ENTRY:
            flash("This exact placement record already exists.", "error")
        elif exc.errno == errorcode.ER_NO_REFERENCED_ROW_2:
            flash("Select a student that exists in the system.", "error")
        else:
            raise
    finally:
        cursor.close()
        connection.close()
    return redirect(url_for("students_page"))


@app.post("/placements/<int:placement_id>/status")
def update_placement_status(placement_id):
    status = request.form.get("status", "")
    if status not in PLACEMENT_STATUSES:
        flash("Select a valid placement status.", "error")
        return redirect(url_for("students_page"))
    connection = get_connection()
    cursor = connection.cursor()
    cursor.execute(
        "UPDATE placements SET status = %s WHERE placement_id = %s",
        (status, placement_id),
    )
    connection.commit()
    updated = cursor.rowcount
    if not updated:
        cursor.execute(
            "SELECT placement_id FROM placements WHERE placement_id = %s",
            (placement_id,),
        )
        updated = cursor.fetchone() is not None
    cursor.close()
    connection.close()
    flash("Placement status updated." if updated else "Placement record not found.",
          "success" if updated else "error")
    return redirect(url_for("students_page"))


def make_chart(chart_type):
    data = fetch_dashboard_data()
    figure, axis = plt.subplots(figsize=(8, 3.2))
    if chart_type == "placements":
        departments = data["departments"]
        names = [row["department"] for row in departments]
        rates = [row["rate"] for row in departments]
        axis.bar(names, rates, color="#5b67e8")
        axis.set_ylabel("Placement rate (%)")
        axis.set_ylim(0, 100)
        axis.set_title("Placement rate by department")
        axis.tick_params(axis="x", rotation=20)
    else:
        packages = data["package_values"]
        if packages:
            axis.hist(packages, bins=min(8, max(3, len(set(packages)))), color="#23a889")
        axis.set_xlabel("Package (LPA)")
        axis.set_ylabel("Number of placed offers")
        axis.set_title("Placed package distribution")
    figure.tight_layout()
    image = io.BytesIO()
    figure.savefig(image, format="png", dpi=130, facecolor="white")
    plt.close(figure)
    image.seek(0)
    from flask import send_file

    return send_file(image, mimetype="image/png")


@app.get("/charts/<chart_type>.png")
def charts(chart_type):
    if chart_type not in {"placements", "packages"}:
        return "Chart not found", 404
    return make_chart(chart_type)


@app.cli.command("init-db")
def init_db_command():
    """Create the database and application tables."""
    init_database()
    print(f"Initialized database '{DB_CONFIG['database']}'.")


if __name__ == "__main__":
    app.run(debug=os.getenv("FLASK_DEBUG", "false").lower() == "true")
